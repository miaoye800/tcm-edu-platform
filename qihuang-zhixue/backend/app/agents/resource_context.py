"""资源生成组共享上下文与检索白名单。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TypedDict

from app.config import get_settings
from app.kb.ingest import load_formulas, load_herbs
from app.kb.models import Herb
from app.kb.retriever import SearchHit, search
from app.kg.traverse import neighbors


class ResourceDoc(TypedDict):
    id: str
    name: str
    aliases: list[str]
    category: str
    subcategory: str
    nature: str
    flavor: list[str]
    meridians: list[str]
    effects: list[str]
    indications: list[dict[str, str]]
    dosage: dict[str, Any]
    cautions: list[str]
    combinations: list[dict[str, Any]]
    differentiation: list[dict[str, str]]
    common_confusions: list[str]
    incompatibility: dict[str, list[str]]
    sources: list[dict[str, Any]]
    evidence_text: str


class ResourceWhitelist(TypedDict):
    herbs: set[str]
    herb_ids: set[str]
    syndromes: set[str]
    formulas: set[str]


async def retrieve_resource_docs(topic: str, top_k: int = 5) -> list[ResourceDoc]:
    """检索资源生成所需文档，优先保留题目中直接点名的药材。

    流程是「向量召回 → 按图补一跳」两段，第二段就是 P5 的 GraphRAG：

    1. ``search()`` 找**相似的文本**。它只会返回文本上像的药，问「麻黄为什么配
       桂枝」，召回到麻黄段落没问题，但「桂枝」可能排在很后面甚至没进 top_k。
    2. ``_expand_by_graph()`` 沿着 ``kg_edge`` 从已召回的药再走一跳，把**图上相连
       的实体**补进来——麻黄 → 桂枝（相须）、麻黄 → 麻黄汤（组成）。

    补进来的必须是 ``ResourceDoc`` 而不是一个裸药名：``build_whitelist`` 是从
    docs 里收集白名单的，只有整条药材记录进来，白名单才会同步扩大，
    ``verify_agent`` 的「模型只能引用检索到的东西」这条红线才不被破坏。
    """

    hits = await search(topic, top_k=top_k)
    hits = _prioritize_exact_herb(topic, hits, limit=top_k)
    docs = _hits_to_docs(hits)
    return docs + _expand_by_graph(docs, hits=hits)


#: 扩展时的关系优先级。数字小的先补。
#:
#: 排序依据是「这条关系能不能直接回答学生的困惑」：相须/相使是配伍理由，
#: 同类鉴别是对比考点，组成是方剂归属；「配伍」是从药材条目的配伍段抽出来的
#: 泛称（3531 条里大多数只是「本品配某某，治某某证」），信息密度低，排后面。
#: 十八反/十九畏属于 T1 硬约束，不参与「补药材」，只影响处方审查。
_EXPAND_PRIORITY: dict[str, int] = {
    "相须": 0,
    "相使": 1,
    "同类鉴别": 2,
    "相恶": 3,
    "相畏": 4,
    "相杀": 5,
    "组成": 6,
    "配伍": 7,
    "药证链": 8,
    "证方链": 9,
}


def _expand_by_graph(docs: list[ResourceDoc], *, hits: list[SearchHit]) -> list[ResourceDoc]:
    """从已召回药材出发，沿 T1/T2 边补一跳邻居。

    三个刻意约束：

    - **只补有专著条目的药**（``data/herbs/*.json`` 里有的）。图里有实体、
      知识库里没内容，补进来也只会让模型对着一堆空字段编。
    - **只补 ``ResourceDoc``**，不补药名——白名单是从 docs 生成的，补裸名字会
      让白名单和正文脱节。
    - **图坏了不影响检索**。新库还没跑 ``build_kg.py`` 时 kg 表可能不存在，
      这里吞掉异常只返回空：扩展是加分项，不该把基础的「查得到麻黄」也拖死。
    """

    settings = get_settings()
    if not settings.kg_expand_enabled or settings.kg_expand_max_docs <= 0 or not docs:
        return []

    existing = {doc["id"] for doc in docs if doc["id"]}
    seed_ids = [hit.id for hit in hits if hit.id and hit.score >= settings.kg_seed_threshold]
    if not seed_ids:
        return []

    db_path = Path(settings.qihuang_db_path)
    # id -> (关系优先级, 负置信度, 关系名)。置信度取负，让高分在升序里排前面。
    candidates: dict[str, tuple[int, float, str]] = {}
    for seed_id in seed_ids:
        try:
            subgraph = neighbors(seed_id, hops=1, limit=40, db_path=db_path)
        except Exception:  # noqa: BLE001 - 图未建好/表缺失时降级为基础检索
            return []
        for edge in subgraph.edges:
            other_id = edge.target_id if edge.source_id == seed_id else edge.source_id
            if other_id in existing:
                continue
            rank = (
                _EXPAND_PRIORITY.get(edge.relation_type, 50),
                -float(edge.confidence or 0),
                edge.relation_type,
            )
            previous = candidates.get(other_id)
            # 同一味药可能同时经「相须」和「配伍」两条边被摸到，取更靠前的那条。
            if previous is None or rank < previous:
                candidates[other_id] = rank

    if not candidates:
        return []

    monograph = {herb.id: herb for herb in load_herbs()}
    # 只留有专著的药，其余不进扩展——补进来也是空字段。
    ranked = sorted(
        (entity_id for entity_id in candidates if entity_id in monograph),
        key=lambda entity_id: (candidates[entity_id][0], candidates[entity_id][1], entity_id),
    )
    expanded: list[ResourceDoc] = []
    for entity_id in ranked[: settings.kg_expand_max_docs]:
        # 邻居的 sources 用它自己药材条目的出处：讲解里引的是「那味药」的原文，
        # 不能借用种子的引用，否则页码对不上正文。
        expanded.append(_herb_to_doc(monograph[entity_id], []))
    return expanded


def build_whitelist(docs: list[ResourceDoc]) -> ResourceWhitelist:
    """从检索结果生成「模型只能引用这些」的白名单。

    方剂这里有个坑：``docs`` 里存的 ``formula_ref`` 是 **id**
    （``formula_yupingfengsan``），而模型生成的节点 ``name`` 是**正名**
    （「玉屏风散」），``verify_agent`` 拿 ``node.name`` 去比白名单——
    只收 id 的话，每一个方剂节点都会被判成「没有依据」删掉，而且不报错，
    只是图里悄悄少了一类节点。

    所以两边都收。名字是为了过校验，id 是为了让下游还能反查。
    """

    herbs = {doc["name"] for doc in docs if doc["name"]}
    herb_ids = {doc["id"] for doc in docs if doc["id"]}
    syndromes = {
        item["syndrome"]
        for doc in docs
        for item in doc["indications"]
        if item.get("syndrome")
    }
    formula_ids = {
        str(combo.get("formula_ref", "")).strip()
        for doc in docs
        for combo in doc["combinations"]
        if str(combo.get("formula_ref", "")).strip()
    }
    formulas = set(formula_ids)
    formulas |= {formula_name_by_id(item) for item in formula_ids}
    return {
        "herbs": herbs,
        "herb_ids": herb_ids,
        "syndromes": syndromes,
        "formulas": formulas,
    }


def profile_hash(profile: dict[str, Any] | None) -> str:
    """稳定计算 profile 摘要，作为资源缓存键的一部分。"""

    normalized = json.dumps(profile or {}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def cache_key_for(topic: str, resource_type: str, profile: dict[str, Any] | None) -> str:
    return f"{topic.strip()}|{resource_type}|{profile_hash(profile)}"


def doc_by_name(docs: list[ResourceDoc]) -> dict[str, ResourceDoc]:
    return {doc["name"]: doc for doc in docs}


def primary_doc(docs: list[ResourceDoc]) -> ResourceDoc | None:
    return docs[0] if docs else None


def source_refs(doc: ResourceDoc | None) -> list[dict[str, str]]:
    if doc is None:
        return []
    refs: list[dict[str, str]] = []
    for source in doc["sources"]:
        refs.append(
            {
                "name": doc["name"],
                "type": str(source.get("type", "本地知识库")),
                "ref": str(source.get("ref", "本地中药学知识库")),
                "quote": str(source.get("quote", "")),
            }
        )
    if not refs:
        refs.append({"name": doc["name"], "type": "本地知识库", "ref": "本地中药学知识库", "quote": ""})
    return refs


def collect_source_refs(docs: list[ResourceDoc]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for doc in docs:
        for source in source_refs(doc):
            key = (source["name"], source["type"], source["ref"])
            if key in seen:
                continue
            seen.add(key)
            refs.append(source)
    return refs


def doc_evidence_text(doc: ResourceDoc) -> str:
    return doc["evidence_text"]


def all_evidence_text(docs: list[ResourceDoc]) -> str:
    return "\n".join(doc["evidence_text"] for doc in docs)


_NAME_BY_ID: dict[str, str] | None = None
_FORMULA_NAME_BY_ID: dict[str, str] | None = None


def reset_herb_index() -> None:
    """知识库变更后清空 id→name 索引，避免继续用旧名字。"""

    global _NAME_BY_ID, _FORMULA_NAME_BY_ID
    _NAME_BY_ID = None
    _FORMULA_NAME_BY_ID = None


def herb_name_by_id(herb_id: str) -> str:
    # 构建全图时一次 _herb_to_doc 会调十几次，逐个遍历知识库会退化成 O(n²)。
    global _NAME_BY_ID
    if _NAME_BY_ID is None:
        _NAME_BY_ID = {herb.id: herb.name for herb in load_herbs()}
    return _NAME_BY_ID.get(herb_id, herb_id)


def formula_name_by_id(formula_id: str) -> str:
    """方剂 id → 正名。查不到就原样返回，不编一个名字。"""

    global _FORMULA_NAME_BY_ID
    if _FORMULA_NAME_BY_ID is None:
        _FORMULA_NAME_BY_ID = {
            key: str(item.get("name") or key) for key, item in load_formulas().items()
        }
    return _FORMULA_NAME_BY_ID.get(formula_id, formula_id)


def _prioritize_exact_herb(topic: str, hits: list[SearchHit], limit: int) -> list[SearchHit]:
    herbs_by_name = {herb.name: herb for herb in load_herbs()}
    exact_names = [name for name in herbs_by_name if name and name in topic]
    if not exact_names:
        return hits[:limit]

    hit_by_name = {hit.name: hit for hit in hits}
    prioritized: list[SearchHit] = []
    for name in exact_names:
        hit = hit_by_name.get(name)
        if hit is None:
            herb = herbs_by_name[name]
            hit = SearchHit(
                name=herb.name,
                score=1.0,
                verified=herb.verified,
                sources=[source.model_dump(mode="json") for source in herb.sources],
                id=herb.id,
            )
        prioritized.append(hit)

    seen = {hit.name for hit in prioritized}
    prioritized.extend(hit for hit in hits if hit.name not in seen)
    return prioritized[:limit]


def _hits_to_docs(hits: list[SearchHit]) -> list[ResourceDoc]:
    # 先按 id 查，查不到再退回按名字。顺序不能反：按名字查是旧做法，
    # 一旦出现同名不同物就取错条目，而 id 是唯一的。
    by_id = {herb.id: herb for herb in load_herbs()}
    by_name = {herb.name: herb for herb in load_herbs()}
    docs: list[ResourceDoc] = []
    for hit in hits:
        herb = by_id.get(hit.id) if hit.id else None
        if herb is None:
            herb = by_name.get(hit.name)
        if herb is None:
            continue
        docs.append(_herb_to_doc(herb, hit.sources))
    return docs


def _herb_to_doc(herb: Herb, hit_sources: list[dict[str, Any]]) -> ResourceDoc:
    indications = [
        {"syndrome": indication.syndrome, "description": indication.description}
        for indication in herb.indications
    ]
    combinations = [
        {
            "with": list(combo.with_),
            "with_names": [herb_name_by_id(item) for item in combo.with_],
            "purpose": combo.purpose,
            "formula_ref": combo.formula_ref,
        }
        for combo in herb.combinations
    ]
    differentiation = [
        {
            "compare_with": item.compare_with,
            "compare_name": herb_name_by_id(item.compare_with),
            "key_points": item.key_points,
        }
        for item in herb.differentiation
    ]
    common_confusions = [herb_name_by_id(item) for item in herb.tags.common_confusions] if herb.tags else []
    sources = hit_sources or [source.model_dump(mode="json") for source in herb.sources]
    doc: ResourceDoc = {
        "id": herb.id,
        "name": herb.name,
        "aliases": list(herb.aliases),
        "category": str(herb.category),
        "subcategory": herb.subcategory,
        "nature": str(herb.nature),
        "flavor": [str(item) for item in herb.flavor],
        "meridians": list(herb.meridians),
        "effects": list(herb.effects),
        "indications": indications,
        "dosage": herb.dosage.model_dump(mode="json"),
        "cautions": list(herb.cautions),
        "combinations": combinations,
        "differentiation": differentiation,
        "common_confusions": common_confusions,
        "incompatibility": herb.incompatibility.model_dump(mode="json") if herb.incompatibility else {},
        "sources": sources,
        "evidence_text": "",
    }
    doc["evidence_text"] = _make_evidence_text(doc)
    return doc


def _make_evidence_text(doc: ResourceDoc) -> str:
    pieces = [
        doc["name"],
        " ".join(doc["aliases"]),
        doc["category"],
        doc["subcategory"],
        doc["nature"],
        " ".join(doc["flavor"]),
        " ".join(doc["meridians"]),
        " ".join(doc["effects"]),
        " ".join(f"{item['syndrome']} {item['description']}" for item in doc["indications"]),
        str(doc["dosage"].get("text", "")),
        str(doc["dosage"].get("usage_notes", "")),
        " ".join(doc["cautions"]),
        " ".join(f"{combo.get('purpose', '')} {combo.get('formula_ref', '')}" for combo in doc["combinations"]),
        " ".join(item["key_points"] for item in doc["differentiation"]),
        " ".join(doc["common_confusions"]),
    ]
    return " ".join(piece for piece in pieces if piece)
