"""GraphRAG：先定位实体，再沿图扩展，最后拼结构化上下文。

和「普通 RAG」的区别，一句话：

- 普通 RAG 检索**相似的文本**——你问「麻黄为什么配桂枝」，它给你两段都提到
  麻黄的段落，能不能对上全靠模型自己看出来。
- GraphRAG 检索**相连的事实**——它先认出「麻黄」「桂枝」两个实体，再沿
  ``相须`` 这条边把教材原文「麻桂相须，增强发汗解表之力」取出来。答案是查到的，
  不是猜出来的。

三段式（对应 ``AskResult`` 的三个字段）：

1. **种子（seeds）**：问题里直接出现的药名/方名/证候名。中文没有空格，
   这里用「名字长度倒序 + 子串包含 + 区间占用」匹配，不需要分词器。
   一个都没命中时才退回模糊检索——模糊检索容易把「麻黄」匹到「麻黄根」，
   所以只当兜底。**上游已经做了向量检索的话**，把命中的实体 id 用 ``seed_ids``
   传进来即可，不必再解析一遍问题文本。
2. **扩展（subgraph）**：从种子出发 BFS 一跳，只走 T1+T2。**T3 相似边永不进
   上下文**——它们回答不了「为什么」。
3. **上下文（context）**：把子图整理成按类型分组的结构化事实，每条事实都带
   教材出处。``answer`` 是在这个结构上生成的确定性摘要，**默认不调模型**：
   能用查到的原文说清楚的事，不要让模型再说一遍。

``answer`` 里绝不出现「可能」「一般来说」这类词——查不到就写查不到。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.kb.ingest import load_herbs
from app.kb.models import Herb
from app.kg import store
from app.kg.constants import Tier
from app.kg.store import KgEdge, KgEntity
from app.kg.traverse import Subgraph, neighbors
from app.profile.store import DB_PATH

#: 一次问答最多从几个种子出发。太多会把图扩成一张毛毯，反而没有信息量。
MAX_SEEDS = 3


@dataclass(frozen=True)
class Seed:
    entity_id: str
    name: str
    kind: str
    matched_by: str  # mention / name / alias / fuzzy
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.entity_id,
            "name": self.name,
            "kind": self.kind,
            "matched_by": self.matched_by,
            "score": self.score,
        }


@dataclass
class AskResult:
    question: str
    seeds: list[Seed]
    subgraph: Subgraph
    context: dict[str, Any]
    answer: str
    citations: list[dict[str, Any]]
    method: str = "kg_expand_v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "seeds": [seed.as_dict() for seed in self.seeds],
            "answer": self.answer,
            "context": self.context,
            "citations": self.citations,
            "graph": {
                "center_ids": [seed.entity_id for seed in self.seeds],
                "nodes": [
                    {"id": entity.id, "name": entity.name, "kind": entity.kind}
                    for entity in self.subgraph.entities
                ],
                "edges": [_edge_payload(edge) for edge in self.subgraph.edges],
                "truncated": self.subgraph.truncated,
            },
            "method": self.method,
        }


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #


def ask(
    question: str,
    *,
    db_path: Path = DB_PATH,
    hops: int = 1,
    limit: int = 40,
    max_seeds: int = MAX_SEEDS,
    seed_ids: Sequence[str] | None = None,
    expand: bool = True,
) -> AskResult:
    """问题 → 结构化答案。

    ``seed_ids`` 用于**从检索结果接着走**：向量检索已经给出实体 id 时（
    ``SearchHit.id``），不必再回头解析一遍问题文本——那等于把检索白做了，
    而且问题措辞和实体名不完全一致时还会解析不出来。给了就只按 id 走，
    不给才走名字匹配。

    ``expand=False`` 是**对照模式**：只做实体识别，不做图扩展。它不是给人用的
    答案，是用来证明「扩展这一步到底贡献了什么」——同一个问题，关闭扩展后
    关系 0 条、原文 0 条，答案里只剩「认出了哪两味药」，没有任何依据。
    """

    text = (question or "").strip()
    seeds = (
        _seeds_from_ids(seed_ids, db_path=db_path, limit=max_seeds)
        if seed_ids
        else _seed(text, db_path=db_path, limit=max_seeds)
    )
    if not seeds:
        return AskResult(
            question=text,
            seeds=[],
            subgraph=Subgraph(center_id=""),
            context=_empty_context(),
            answer="知识库里没有认出这个问题里的药材、方剂或证候。图谱只能从实体出发，认不出实体就查不到东西——这不是「没有关系」，是「没找到起点」。",
            citations=[],
        )

    if not expand:
        names = "、".join(seed.name for seed in seeds)
        return AskResult(
            question=text,
            seeds=seeds,
            subgraph=Subgraph(center_id=seeds[0].entity_id),
            context=_seeds_only_context(seeds),
            answer=(
                f"对照模式：只认出了「{names}」，没有沿图扩展。"
                "所以这里没有任何关系、也没有可翻的教材原文——"
                "要让「为什么配在一起」有依据，必须走这一步图扩展。"
            ),
            citations=[],
            method="kg_seed_only",
        )

    subgraph = _merge_subgraphs(
        [neighbors(seed.entity_id, hops=hops, limit=limit, db_path=db_path) for seed in seeds],
        center_id=seeds[0].entity_id,
    )
    context = _build_context(subgraph, seeds, db_path=db_path)
    return AskResult(
        question=text,
        seeds=seeds,
        subgraph=subgraph,
        context=context,
        answer=_compose_answer(text, seeds, context, subgraph),
        citations=_citations(subgraph),
    )


# --------------------------------------------------------------------------- #
# 1. 种子：问题 → 实体
# --------------------------------------------------------------------------- #


def _seeds_from_ids(ids: Sequence[str], *, db_path: Path, limit: int) -> list[Seed]:
    """检索已经给出实体 id 时直接建种子。

    只认图里真实存在的实体：查不到就跳过，**不按名字去猜**——猜错一个种子
    会让后面整张子图都是别人的关系。
    """

    seeds: list[Seed] = []
    for entity_id in ids:
        if len(seeds) >= limit:
            break
        entity = store.get_entity(str(entity_id), db_path)
        if entity is None:
            continue
        seeds.append(
            Seed(
                entity_id=entity.id,
                name=entity.name,
                kind=entity.kind,
                matched_by="retrieval",
                score=1.0,
            )
        )
    return seeds


def detect_seeds(
    text: str,
    *,
    db_path: Path = DB_PATH,
    limit: int = MAX_SEEDS,
    fuzzy: bool = True,
) -> list[Seed]:
    """问题 → 实体提及。**只认文本里原样出现的实体名**。

    ``fuzzy=False`` 是不带兜底的严格模式：认不出就是空列表，不回头去
    ``search_entities`` 猜。路由判定与图谱扩展门槛都必须用严格模式——
    模糊回落会把「抗生素用法 中药」这种问题硬凑出一个实体，于是本该走
    普通 RAG 的问题被扩成一张无关的邻域图。

    默认 ``fuzzy=True`` 保持 ``ask()`` 原有行为：严格模式没命中时再走一次
    模糊匹配，给低分并标 ``matched_by=fuzzy``，让前端看得出这是弱命中。
    """

    if not text:
        return []

    # 名字长度倒序 + 区间占用：避免「麻黄」从「麻黄汤」里再抠一遍，
    # 也避免「牵牛」和「牵牛子」被当成两个实体。
    entities = sorted(
        (entity for entity in store.list_entities(db_path=db_path) if len(entity.name) >= 2),
        key=lambda entity: len(entity.name),
        reverse=True,
    )
    consumed: list[tuple[int, int]] = []
    seeds: list[Seed] = []
    for entity in entities:
        start = text.find(entity.name)
        if start < 0:
            continue
        end = start + len(entity.name)
        if any(start < taken_end and taken_start < end for taken_start, taken_end in consumed):
            continue
        consumed.append((start, end))
        seeds.append(
            Seed(
                entity_id=entity.id,
                name=entity.name,
                kind=entity.kind,
                matched_by="mention",
                score=1.0,
            )
        )
        if len(seeds) >= limit:
            break

    if seeds or not fuzzy:
        return seeds

    # 兜底：问题里没写完整实体名时走模糊匹配。
    # 这里给低分并标注 matched_by=fuzzy，前端要能看出这是弱命中。
    for entity, matched_by in store.search_entities(text, limit=limit, db_path=db_path):
        seeds.append(
            Seed(
                entity_id=entity.id,
                name=entity.name,
                kind=entity.kind,
                matched_by=matched_by,
                score=0.6 if matched_by == "fuzzy" else 0.9,
            )
        )
    return seeds


#: 内部旧名，保持既有调用点与测试不变。
_seed = detect_seeds


# --------------------------------------------------------------------------- #
# 2. 扩展：合并多个种子的邻域
# --------------------------------------------------------------------------- #


def _merge_subgraphs(subgraphs: Iterable[Subgraph], center_id: str) -> Subgraph:
    entities: dict[str, KgEntity] = {}
    edges: dict[int, KgEdge] = {}
    depth: dict[str, int] = {}
    truncated = False
    total_nodes = 0
    for subgraph in subgraphs:
        truncated = truncated or subgraph.truncated
        # 多个种子的邻域会大量重叠，「并起来一共有几个节点」没有精确值。
        # 取「各邻域截断前节点数的最大值」当口径：它不会因为两个种子重叠而
        # 虚增成一个吓人的数字，也不会小于真正被看到过的节点数。
        total_nodes = max(total_nodes, subgraph.total_nodes)
        for entity in subgraph.entities:
            entities.setdefault(entity.id, entity)
        for edge in subgraph.edges:
            if edge.id is not None:
                edges.setdefault(edge.id, edge)
        for node_id, level in subgraph.depth.items():
            depth[node_id] = min(level, depth.get(node_id, level))
    return Subgraph(
        center_id=center_id,
        entities=list(entities.values()),
        edges=sorted(edges.values(), key=lambda edge: (edge.tier, -(edge.confidence or 0), edge.id or 0)),
        depth=depth,
        truncated=truncated,
        total_nodes=max(total_nodes, len(entities)),
    )


# --------------------------------------------------------------------------- #
# 3. 上下文：子图 → 结构化事实
# --------------------------------------------------------------------------- #


def _build_context(subgraph: Subgraph, seeds: list[Seed], *, db_path: Path) -> dict[str, Any]:
    by_id = {entity.id: entity for entity in subgraph.entities}
    monograph = {herb.id: herb for herb in load_herbs()}
    seed_ids = [seed.entity_id for seed in seeds]

    herbs = [
        _herb_fact(entity, monograph.get(entity.id))
        for entity in subgraph.entities
        if entity.kind == "herb"
    ]
    syndromes = [
        _syndrome_fact(entity, subgraph, by_id)
        for entity in subgraph.entities
        if entity.kind == "syndrome"
    ]
    formulas = [
        _formula_fact(entity, subgraph, by_id, monograph)
        for entity in subgraph.entities
        if entity.kind == "formula"
    ]
    relations = _collapse_relations(
        [
            _relation_fact(edge, by_id)
            for edge in subgraph.edges
            if edge.tier in (int(Tier.CONSTRAINT), int(Tier.FACT))
        ]
    )
    return {
        "center": [
            {"id": seed.entity_id, "name": seed.name, "kind": seed.kind}
            for seed in seeds
            if seed.entity_id in by_id
        ],
        "herbs": herbs,
        "syndromes": syndromes,
        "formulas": formulas,
        "relations": relations,
        "constraints": [
            item for item in relations if item["tier"] == int(Tier.CONSTRAINT)
        ],
        "seed_ids": seed_ids,
        "truncated": subgraph.truncated,
    }


def _herb_fact(entity: KgEntity, herb: Herb | None) -> dict[str, Any]:
    if herb is None:
        # 有身份、没内容。明说，不要用空字段假装「查到了但没有」。
        return {
            "id": entity.id,
            "name": entity.name,
            "status": entity.status,
            "has_monograph": False,
            "note": "教材提到这味药，但知识库还没有它的性味功效条目",
        }
    return {
        "id": entity.id,
        "name": entity.name,
        "status": entity.status,
        "has_monograph": True,
        "category": str(herb.category),
        "subcategory": herb.subcategory,
        "nature": str(herb.nature),
        "flavor": [str(item) for item in herb.flavor],
        "meridians": [str(item) for item in herb.meridians],
        "effects": list(herb.effects),
        "indications": [
            {
                "syndrome": item.syndrome,
                "description": item.description,
                "origin": item.origin,
            }
            for item in herb.indications
        ],
        "dosage": {"text": herb.dosage.text, "min_g": herb.dosage.min_g, "max_g": herb.dosage.max_g},
        "toxicity": str(herb.toxicity),
    }


def _syndrome_fact(entity: KgEntity, subgraph: Subgraph, by_id: dict[str, KgEntity]) -> dict[str, Any]:
    treated_by = []
    for edge in subgraph.edges:
        if edge.relation_type != "药证链":
            continue
        other_id = edge.target_id if edge.source_id == entity.id else edge.source_id
        other = by_id.get(other_id)
        if other is not None and other.kind == "herb":
            treated_by.append(other.name)
    return {
        "id": entity.id,
        "name": entity.name,
        "treated_by": sorted(set(treated_by)),
        "verified": bool(entity.extra.get("verified", False)),
        "derived_from": entity.extra.get("derived_from", ""),
    }


def _formula_fact(
    entity: KgEntity,
    subgraph: Subgraph,
    by_id: dict[str, KgEntity],
    monograph: dict[str, Herb],
) -> dict[str, Any]:
    composition = []
    for edge in subgraph.edges:
        if edge.relation_type != "组成":
            continue
        other_id = edge.target_id if edge.source_id == entity.id else edge.source_id
        other = by_id.get(other_id)
        if other is not None and other.kind == "herb":
            composition.append(other.name)
    return {
        "id": entity.id,
        "name": entity.name,
        "composition": sorted(set(composition)),
        "composition_size": entity.extra.get("composition_size"),
        "incomplete": bool(entity.extra.get("incomplete", True)),
    }


def _relation_fact(edge: KgEdge, by_id: dict[str, KgEntity]) -> dict[str, Any]:
    source = by_id.get(edge.source_id)
    target = by_id.get(edge.target_id)
    return {
        "edge_id": edge.id,
        "source": {"id": edge.source_id, "name": source.name if source else edge.source_id},
        "target": {"id": edge.target_id, "name": target.name if target else edge.target_id},
        "relation_type": edge.relation_type,
        "tier": edge.tier,
        "label": edge.label,
        "derived": bool(edge.derived),
        "confidence": edge.confidence,
        "evidence": edge.evidence,
    }


def _empty_context() -> dict[str, Any]:
    return {
        "center": [],
        "herbs": [],
        "syndromes": [],
        "formulas": [],
        "relations": [],
        "constraints": [],
        "seed_ids": [],
        "truncated": False,
    }


def _seeds_only_context(seeds: list[Seed]) -> dict[str, Any]:
    """对照模式的上下文：只有起点，没有边。"""

    context = _empty_context()
    context["center"] = [
        {"id": seed.entity_id, "name": seed.name, "kind": seed.kind} for seed in seeds
    ]
    context["seed_ids"] = [seed.entity_id for seed in seeds]
    return context


def _collapse_relations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把「同一对实体、同一种关系」的多条边并成一条事实。

    图里必须保留多条：``同类鉴别`` 的两条边各自带着自己那一侧的教材原文
    （麻黄条 p149、桂枝条 p150），删掉任何一条都会丢溯源。

    但**呈现**时它们是一件事。不合并的话，界面上会出现两行一模一样的
    「桂枝 —同类鉴别→ 麻黄」，读起来像系统重复插入，而不像两条证据。
    合并后 ``edge_ids`` 保留全部边号，``evidence`` 累积全部原文，溯源能力不降。
    """

    order: list[tuple[str, str, str]] = []
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        source_id = item["source"]["id"]
        target_id = item["target"]["id"]
        relation = item["relation_type"]
        if relation in _SYMMETRIC_RELATIONS:
            source_id, target_id = sorted((source_id, target_id))
        key = (source_id, target_id, relation)
        kept = merged.get(key)
        if kept is None:
            merged[key] = {**item, "edge_ids": [item["edge_id"]] if item["edge_id"] else []}
            order.append(key)
            continue
        for evidence in item["evidence"]:
            if evidence not in kept["evidence"]:
                kept["evidence"].append(evidence)
        if item["edge_id"] and item["edge_id"] not in kept["edge_ids"]:
            kept["edge_ids"].append(item["edge_id"])
        if (item["confidence"] or 0) > (kept["confidence"] or 0):
            kept["confidence"] = item["confidence"]
    return [merged[key] for key in order]


# --------------------------------------------------------------------------- #
# 4. 摘要：结构化事实 → 人话
# --------------------------------------------------------------------------- #


#: 对称关系：A—B 与 B—A 说的是同一件事。
#: 建图时对称关系允许双向各存一条（``相须`` 校验要求双向一致），
#: 摘要里却只能念一次，否则一句话会被读成两条事实。
_SYMMETRIC_RELATIONS = {
    "相须",
    "相使",
    "相恶",
    "相畏",
    "相杀",
    "同类鉴别",
    "配伍",
    "十八反",
    "十九畏",
}


def _unique_relations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一对实体、同一种关系只保留一条。

    实测里 ``同类鉴别`` 对同一对药会存两次（``同类鉴别·桂枝`` 与
    ``同类鉴别·麻黄``），因为两条边各自携带自己那一侧的教材原文标签。
    图里保留是对的——溯源时两边都指向同一页；念给人听就必须合并。
    """

    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        source_id = item["source"]["id"]
        target_id = item["target"]["id"]
        relation = item["relation_type"]
        if relation in _SYMMETRIC_RELATIONS:
            source_id, target_id = sorted((source_id, target_id))
        key = (source_id, target_id, relation)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _compose_answer(
    question: str, seeds: list[Seed], context: dict[str, Any], subgraph: Subgraph
) -> str:
    """确定性摘要。每一句都能在 ``context`` 里找到出处。"""

    if not subgraph.edges:
        names = "、".join(seed.name for seed in seeds)
        return (
            f"图谱里有「{names}」，但它在这个库里没有 T1/T2 关系边。"
            "说明教材还没为它建立可用的结构化条目——不是「没有关系」，是「还没录」。"
        )

    lines: list[str] = []
    herbs = {item["id"]: item for item in context["herbs"]}

    for center in context["center"]:
        if center["kind"] != "herb":
            continue
        herb = herbs.get(center["id"])
        if herb is None:
            continue
        if not herb.get("has_monograph"):
            lines.append(f"{herb['name']}：教材提到过，但知识库还没有它的性味功效条目。")
            continue
        lines.append(
            f"{herb['name']}（{herb.get('category', '')}·{herb.get('subcategory', '')}）"
            f"性味 {''.join(herb.get('flavor') or [])}{herb.get('nature', '')}，"
            f"归{'、'.join(herb.get('meridians') or [])}经；"
            f"功效：{'、'.join(herb.get('effects') or [])}。"
        )
        # 主治分两套列，**不能混成一句**。
        #
        # 同一个药材下教材原文条目和人工归纳的通用证名各有一套（黄芪的
        # 「气虚乏力，食少便溏……」和「脾气虚证」）。拼成一句「教材记载主治：
        # A；B；C；D……」会读成「这味药能治十几个证」，而后半段其实是前半段的
        # 粗粒度归纳，不是新增的适应症。
        indications = herb.get("indications") or []
        textbook_items = [item for item in indications if item.get("origin") == "textbook"]
        manual_items = [item for item in indications if item.get("origin") != "textbook"]
        if textbook_items:
            lines.append("教材原文主治：" + "；".join(item["syndrome"] for item in textbook_items) + "。")
        if manual_items:
            lines.append(
                "通用证名（归纳，非原文）："
                + "、".join(item["syndrome"] for item in manual_items)
                + "。"
            )

    # 关系：先 T1 再 T2，T1 单独一句强调，因为它是「不能一起用」。
    constraints = _unique_relations(context["constraints"])
    if constraints:
        pairs = "；".join(
            f"{item['source']['name']} —{item['relation_type']}→ {item['target']['name']}"
            for item in constraints
        )
        lines.append(f"配伍禁忌（硬约束）：{pairs}。")

    qixing = _unique_relations(
        [
            item for item in context["relations"]
            if item["tier"] == int(Tier.FACT) and item["relation_type"] in {"相须", "相使", "相畏", "相杀", "相恶"}
        ]
    )
    if qixing:
        pairs = "；".join(
            f"{item['source']['name']} —{item['relation_type']}→ {item['target']['name']}"
            f"（{item['label']}）"
            for item in qixing[:6]
        )
        lines.append(f"七情配伍：{pairs}。")

    composition = [
        item for item in context["relations"] if item["relation_type"] == "组成"
    ]
    if composition:
        # 组成边的方向是「药 → 方」（``herb_renshen --组成--> formula_shenfu_tang``），
        # 不是「方 → 药」。旧写法直接套「source 含 target」，念出来是
        # 「人参含参附汤」——把成分当成了容器，语义正好反过来。
        # 按方聚合再念，方向就只取决于实体种类，不取决于建边时的书写习惯。
        herb_ids = {item["id"] for item in context["herbs"]}
        formula_ids = {item["id"] for item in context["formulas"]}
        formula_meta = {item["id"]: item for item in context["formulas"]}
        grouped: dict[str, dict[str, Any]] = {}
        for item in composition:
            source, target = item["source"], item["target"]
            if source["id"] in formula_ids or target["id"] in herb_ids:
                formula, herb = source, target
            else:
                formula, herb = target, source
            entry = grouped.setdefault(
                formula["id"],
                {
                    "name": formula["name"],
                    "members": [],
                    "incomplete": bool(formula_meta.get(formula["id"], {}).get("incomplete", True)),
                },
            )
            if herb["name"] not in entry["members"]:
                entry["members"].append(herb["name"])
        parts = []
        for entry in grouped.values():
            # 教材只录了「某药在某方里」的片段，不是完整方歌。念成
            # 「麻黄汤（麻黄、桂枝）」会被读成「麻黄汤只有两味」——必须标注。
            tail = "，教材已录这几味、可能不全" if entry["incomplete"] else ""
            parts.append(f"{entry['name']}（{'、'.join(entry['members'])}{tail}）")
        lines.append("方剂组成：" + "；".join(parts[:8]) + "。")

    syndromes = context["syndromes"]
    if syndromes:
        lines.append(
            "相关证候："
            + "；".join(
                f"{item['name']}（教材主治药：{'、'.join(item['treated_by']) or '未录'}）"
                for item in syndromes[:6]
            )
            + "。"
        )

    if subgraph.truncated:
        lines.append("（关系太多，已按置信度截断；追问某个具体方向可以拿到完整邻域。）")
    return "".join(lines)


def _citations(subgraph: Subgraph) -> list[dict[str, Any]]:
    """去重后的溯源清单。相同出处+原文只留一条。"""

    seen: set[tuple[str, str]] = set()
    items: list[dict[str, Any]] = []
    for edge in subgraph.edges:
        for evidence in edge.evidence:
            key = (str(evidence.get("ref") or ""), str(evidence.get("quote") or ""))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "edge_id": edge.id,
                    "relation_type": edge.relation_type,
                    "tier": edge.tier,
                    "ref": key[0],
                    "page": evidence.get("page"),
                    "quote": key[1],
                }
            )
    return items


def _edge_payload(edge: KgEdge) -> dict[str, Any]:
    return {
        "id": edge.id,
        "source": edge.source_id,
        "target": edge.target_id,
        "relation_type": edge.relation_type,
        "tier": edge.tier,
        "label": edge.label,
    }


__all__ = ["AskResult", "Seed", "ask", "detect_seeds", "MAX_SEEDS"]
