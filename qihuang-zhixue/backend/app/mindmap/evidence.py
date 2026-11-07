"""素材包：按问法去「真的取回」这个问法需要的东西。

这是本轮最核心的一处改动。原来的 ``retrieve_resource_docs()`` 只认单味药：
向量召回 → 按 id 查药材 → 查不到就 ``continue``。于是「解表药」「十八反」
「入肺经的温性药」全部拿不到任何素材，模型只能写「知识要点 → 待补充资料」。

``build_evidence_pack()`` 换了个顺序：**先问这是什么题，再按题去取料**。

    分类章节 → knowledge_node 子树 + 挂在子树下的药材
    功效反查 → 功效倒排（451 味药的 effects 全部进索引）
    性味归经 → 字段过滤
    关系禁忌 → kg_edge 的十八反/十九畏/相须…
    证治对应 → 证名 → 药证链 + 证方链
    应试专题 → tags.high_frequency_exam + common_confusions + 同类鉴别
    对比鉴别 → 两味药全字段对齐 + 同类鉴别边
    单味药   → 药材全字段 + 药证链 + 配伍
    域外     → 空包，明确标注，交给模型写并全树标 model

每一条素材都带 ``evidence`` 等级：``textbook``（结构化字段/教材抽取）、
``graph``（图谱关系）。没取到的部分不假装取到——那是「推断」。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.agents.resource_context import ResourceDoc, ResourceWhitelist, build_whitelist, retrieve_resource_docs
from app.config import get_settings
from app.kb.ingest import load_herbs, load_syndromes
from app.kb.models import Herb
from app.mindmap.intents import Anchors, MapIntent, compare_pair

#: 每条素材的来源等级。前端底栏三档占比就是数这三种。
EVIDENCE_TEXTBOOK = "textbook"
EVIDENCE_GRAPH = "graph"
EVIDENCE_MODEL = "model"

#: 素材包的整体规模上限。超过就会把 prompt 撑到两万 token 以上。
MAX_HERBS = 12
MAX_LINKS = 60
MAX_TREE_NODES = 40
#: 单个分支下最多摆几味药。与 MindmapNode.children 的 12 上限留一档余量。
MAX_LEAVES = 10


@dataclass
class EvidenceItem:
    """一条素材。``kind`` 决定下游怎么用它。"""

    kind: str  # herb | node | edge | syndrome | formula | effect
    label: str
    evidence: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidencePack:
    intent: MapIntent
    herbs: list[ResourceDoc] = field(default_factory=list)
    #: 知识树节点（分类题用）：{id,name,level,parent_id}
    nodes: list[dict[str, str]] = field(default_factory=list)
    #: 关系边：{source, target, type, label}
    edges: list[dict[str, str]] = field(default_factory=list)
    syndromes: list[dict[str, Any]] = field(default_factory=list)
    formulas: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    #: 一级分支建议（结构原型的槽位）。模型可以改，但不给槽位它就会写成八股。
    branch_slots: list[str] = field(default_factory=list)
    #: 已经分好组的内容（亚类 → 药名 / 功效 → 药名）。
    #: 反查与筛选这两种题天然是分组的，分组信息不能靠下游从截断后的 herbs 里再猜。
    groups: dict[str, list[str]] = field(default_factory=dict)
    whitelist: ResourceWhitelist = field(default_factory=lambda: {"herbs": set(), "herb_ids": set(), "syndromes": set(), "formulas": set()})
    #: 覆盖不到的说明，会原样出现在对话栏与状态条。
    notes: list[str] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return self.intent.degraded or not (self.herbs or self.nodes or self.edges or self.syndromes)

    @property
    def herb_names(self) -> list[str]:
        return [doc["name"] for doc in self.herbs if doc["name"]]

    def sources(self) -> list[dict[str, str]]:
        refs: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for doc in self.herbs:
            for source in doc["sources"]:
                key = (str(source.get("ref", "")), str(source.get("quote", "")))
                if key in seen:
                    continue
                seen.add(key)
                refs.append(
                    {
                        "name": doc["name"],
                        "type": str(source.get("type", "本地知识库")),
                        "ref": str(source.get("ref", "本地中药学知识库")),
                        "quote": str(source.get("quote", "")),
                    }
                )
        for item in self.syndromes[:6]:
            for source in item.get("sources") or []:
                key = (str(source.get("ref", "")), str(source.get("quote", "")))
                if key in seen:
                    continue
                seen.add(key)
                refs.append(
                    {
                        "name": str(item.get("name", "")),
                        "type": str(source.get("type", "教材")),
                        "ref": str(source.get("ref", "本地证候库")),
                        "quote": str(source.get("quote", "")),
                    }
                )
        if not refs and not self.degraded:
            refs.append({"name": self.intent.topic, "type": "本地知识库", "ref": "本地中药学知识库", "quote": ""})
        return refs

    def summary(self) -> dict[str, Any]:
        """给 SSE / 日志看的摘要。不含正文。"""

        return {
            "kind": self.intent.kind,
            "archetype": self.intent.archetype,
            "degraded": self.degraded,
            "herbs": len(self.herbs),
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "syndromes": len(self.syndromes),
            "slots": len(self.branch_slots),
        }


async def build_evidence_pack(intent: MapIntent, *, docs: list[ResourceDoc] | None = None, db_path: Path | None = None) -> EvidencePack:
    """按问法取料。``docs`` 传入时直接复用（测试与旧调用方），避免重复检索。"""

    pack = EvidencePack(intent=intent)
    kind = intent.kind

    if kind == "concept":
        pack.notes.append("主题不在中药学教材语料内，以下内容由模型整理")
        return pack

    if kind == "taxonomy":
        _fill_taxonomy(pack)
    elif kind == "reverse":
        _fill_reverse(pack)
    elif kind == "filter":
        _fill_filter(pack)
    elif kind == "relation":
        _fill_relation(pack, db_path=db_path)
    elif kind == "syndrome":
        _fill_syndrome(pack, db_path=db_path)
    elif kind == "comparison":
        _fill_comparison(pack, docs=docs)
    elif kind == "exam":
        _fill_exam(pack)
    else:
        _fill_monograph(pack, docs=docs)
        # 单味药是唯一还需要向量召回的问法：「细辛的用量」里点名了细辛，
        # 但「桂枝类方怎么用」这种没点名的情况只能靠语义检索兜住。
        if not pack.herbs:
            try:
                pack.herbs = list(await retrieve_resource_docs(intent.topic))
            except Exception:  # noqa: BLE001 - 检索失败不该让整条任务炸炉
                pack.herbs = []
            if pack.herbs:
                pack.branch_slots = _monograph_slots(pack.herbs[0])

    if not pack.branch_slots:
        pack.branch_slots = _default_slots(pack)
    pack.whitelist = _whitelist(pack)
    return pack


# ---------------------------------------------------------------------------
# 各问法的取料
# ---------------------------------------------------------------------------


def _fill_monograph(pack: EvidencePack, *, docs: list[ResourceDoc] | None) -> None:
    names = pack.intent.anchors.herbs
    pack.herbs = list(docs or []) or _docs_for(names) or _search_docs(pack.intent.topic)
    if not pack.herbs:
        pack.notes.append("没在药材主表里找到这味药")
        return
    pack.branch_slots = _monograph_slots(pack.herbs[0])


def _monograph_slots(primary: ResourceDoc) -> list[str]:
    """单味药的六个语义轴。只留资料里真有内容的——空槽位会诱导模型去编。"""

    return [
        slot
        for slot, present in (
            ("性味归经", bool(primary["nature"] or primary["meridians"])),
            ("功效", bool(primary["effects"])),
            ("主治", bool(primary["indications"])),
            ("配伍应用", bool(primary["combinations"])),
            ("相似药鉴别", bool(primary["differentiation"])),
            ("用量与注意", bool(primary["dosage"].get("text") or primary["cautions"])),
        )
        if present
    ]


def _fill_taxonomy(pack: EvidencePack) -> None:
    name = (pack.intent.anchors.categories or pack.intent.anchors.subcategories or (None,))[0]
    if not name:
        pack.notes.append("没认出是哪一类")
        return
    nodes = _tree_subtree(name)
    pack.nodes = nodes
    if not nodes:
        pack.notes.append(f"知识树里没有「{name}」这一节")
        return
    node_ids = [row["id"] for row in nodes]
    herb_names = _herb_names_in_nodes(node_ids)
    pack.herbs = _docs_for(tuple(herb_names))[:MAX_HERBS]
    # 槽位就是真实的亚类名：分类题的骨架由教材目录决定，不该由模型编。
    pack.branch_slots = [row["name"] for row in nodes if row["level"] == "subtype"][:8] or [row["name"] for row in nodes if row["level"] == "point"][:8]


def _fill_reverse(pack: EvidencePack) -> None:
    effects = _expand_effects(list(pack.intent.anchors.effects))
    if not effects:
        pack.notes.append("没认出要反查的功效")
        return
    hits = _herbs_by_effect(effects)
    pack.effects = list(hits.keys())
    pack.groups = {effect: names[:MAX_LEAVES] for effect, names in list(hits.items())[:8]}
    # 每个分支各自取几味，而不是全局取前 12 味：全局截断会让后面的分支全是空的。
    names = [name for group in pack.groups.values() for name in group[:4]]
    pack.herbs = _docs_for(tuple(dict.fromkeys(names)))[:MAX_HERBS]
    pack.branch_slots = list(pack.groups.keys())


def _fill_filter(pack: EvidencePack) -> None:
    anchors = pack.intent.anchors
    matched = _herbs_by_fields(anchors)
    if not matched:
        pack.notes.append("按这组性味归经没筛到药材")
        return
    # 按亚类分组而不是药类：问句已经把药类限死了（「性寒的清热药」），
    # 再按药类分组只会得到一根独苗分支。
    groups: dict[str, list[str]] = {}
    for herb in matched:
        key = herb.subcategory or str(herb.category)
        groups.setdefault(key, []).append(herb.name)
    pack.groups = {key: names[:MAX_LEAVES] for key, names in list(groups.items())[:8]}
    names = [name for group in pack.groups.values() for name in group[:3]]
    pack.herbs = _docs_for(tuple(dict.fromkeys(names)))[:MAX_HERBS]
    pack.branch_slots = list(pack.groups.keys())


def _fill_relation(pack: EvidencePack, *, db_path: Path | None) -> None:
    types = list(pack.intent.anchors.relations) or ["十八反"]
    if "十九畏" in pack.intent.topic:
        types = ["十九畏"]
    herbs = pack.intent.anchors.herbs
    # 「A 配 B」问的是这两味药之间的关系，**只要二者之间那条边**。
    # 找不到就走配伍条目（见下），不要退回「凡是牵涉这两味药的边」——
    # 那会拿「白芍反藜芦」来回答「桂枝配白芍」，答非所问（实测踩过）。
    if len(herbs) >= 2:
        edges = _edges_of_types(types, seeds=herbs, db_path=db_path, both=True)
    else:
        edges = _edges_of_types(types, seeds=herbs, db_path=db_path)
    pack.edges = edges
    names = {edge["source"] for edge in edges} | {edge["target"] for edge in edges}
    pack.herbs = _docs_for(tuple(sorted(names)))[:MAX_HERBS]
    pack.branch_slots = sorted({edge["source"] for edge in edges})[:8]
    if not edges:
        # 图谱里没有这条关系时，别硬凑：两味药的配伍依据在各自的 combinations 里
        # （「本品配桂枝，治…」），那是教材原文，比从 3531 条边里随便抓一条强。
        pack.herbs = _docs_for(pack.intent.anchors.herbs)[:2]
        pack.notes.append(f"图谱里没有「{'、'.join(types)}」记录，改按药材配伍条目整理")
        pack.branch_slots = ["同用依据", "各自功效", "使用注意"]


def _fill_syndrome(pack: EvidencePack, *, db_path: Path | None) -> None:
    names = pack.intent.anchors.syndromes
    table = load_syndromes()
    from app.agents.resource_context import formula_name_by_id

    for name in names:
        item = table.get(name) or {}
        pack.syndromes.append(
            {
                "name": name,
                "description": str(item.get("description", "")),
                "symptoms": list(item.get("symptoms") or [])[:8],
                "tongue": str(item.get("tongue", "")),
                "pulse": str(item.get("pulse", "")),
                "related_herbs": list(item.get("related_herbs") or [])[:10],
                "related_formulas": [formula_name_by_id(str(value)) for value in (item.get("related_formulas") or [])][:6],
                # 证候条目本身很薄（教材没有单列证候表，是从主治反推的），
                # 真正的「这个证怎么表现」在各药的主治描述里——那是教材原文。
                "herb_notes": _syndrome_notes(name),
                "sources": list(item.get("sources") or []),
            }
        )
    herb_names = [name for item in pack.syndromes for name in item["related_herbs"]]
    if len(herb_names) < 3:
        herb_names = list(dict.fromkeys([*herb_names, *_herbs_by_syndrome(names, db_path=db_path)]))
    pack.herbs = _docs_for(tuple(dict.fromkeys(herb_names)))[:MAX_HERBS]
    pack.formulas = list(dict.fromkeys(formula for item in pack.syndromes for formula in item["related_formulas"]))
    if not pack.formulas:
        pack.formulas = _formulas_of_syndrome(names, db_path=db_path)
    pack.branch_slots = ["主症", "病机", "常用药", "代表方", "辨证要点"]


def _fill_comparison(pack: EvidencePack, *, docs: list[ResourceDoc] | None) -> None:
    pair = compare_pair(pack.intent)
    if pair is None:
        pack.herbs = list(docs or [])
        pack.notes.append("只有一味药，找不到可比的对象")
        pack.branch_slots = ["性味归经", "功效", "主治", "用量"]
        return
    pack.herbs = _docs_for(pair)[:2]
    pack.branch_slots = ["共同点", "性味归经", "功效侧重", "主治差异", "配伍差异", "使用注意"]


def _fill_exam(pack: EvidencePack) -> None:
    category = (pack.intent.anchors.categories or pack.intent.anchors.subcategories or (None,))[0]
    herbs = [herb for herb in _herbs() if (category is None or str(herb.category) == category or herb.subcategory == category)]
    # 高频考点优先，其次是被标了「易混」的药。
    herbs.sort(key=lambda herb: (-int(bool(herb.tags and herb.tags.high_frequency_exam)), -len(herb.tags.common_confusions) if herb.tags else 0, herb.name))
    pack.herbs = _docs_for(tuple(herb.name for herb in herbs))[:MAX_HERBS]
    pack.branch_slots = ["高频考点", "易混药对", "鉴别要点", "记忆线索"]
    if not herbs:
        pack.notes.append("没找到这一类的高频考点")


def _default_slots(pack: EvidencePack) -> list[str]:
    if pack.intent.kind == "comparison":
        return ["共同点", "差异", "选用指征"]
    return ["要点"]


# ---------------------------------------------------------------------------
# 知识库 / 图谱查询
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _herbs() -> tuple[Herb, ...]:
    return tuple(load_herbs())


def docs_for_names(names: tuple[str, ...]) -> list[ResourceDoc]:
    """按名字取整条药材记录。archetypes 用它给亚类找一味代表药。"""

    return _docs_for(names)


def _docs_for(names: tuple[str, ...]) -> list[ResourceDoc]:
    """按名字取整条药材记录（与检索结果同构）。

    不调向量库：分类/反查/筛选这三种问法要的是「这一批确定的药」，
    向量召回只会把不相关的药混进来。
    """

    from app.agents.resource_context import _herb_to_doc  # 复用同一套字段映射

    index = {herb.name: herb for herb in _herbs()}
    docs: list[ResourceDoc] = []
    for name in dict.fromkeys(names):
        herb = index.get(name)
        if herb is None:
            continue
        docs.append(_herb_to_doc(herb, []))
    return docs


def _db_path() -> Path | None:
    path = Path(get_settings().qihuang_db_path)
    return path if path.exists() else None


def _connect() -> sqlite3.Connection | None:
    path = _db_path()
    if path is None:
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _tree_subtree(name: str, *, max_rows: int = MAX_TREE_NODES) -> list[dict[str, str]]:
    """取知识树里某一节及其下层（最多三层）。

    找不到节点时返回空——**不猜**。「解表药」在树上是 ``d01``，
    但「解表」不是，两者必须区分开。
    """

    conn = _connect()
    if conn is None:
        return []
    try:
        row = conn.execute(
            "SELECT id,name,level FROM knowledge_node WHERE name=? ORDER BY CASE level WHEN 'domain' THEN 0 WHEN 'subtype' THEN 1 ELSE 2 END LIMIT 1",
            (name,),
        ).fetchone()
        if row is None:
            # 「发散风寒药」这类亚类名也能作为题目出现。
            row = conn.execute("SELECT id,name,level FROM knowledge_node WHERE name LIKE ? LIMIT 1", (f"%{name}%",)).fetchone()
        if row is None:
            return []
        root: dict[str, str] = {"id": row["id"], "name": row["name"], "level": row["level"], "parent_id": ""}
        rows: list[dict[str, str]] = [root]
        frontier = [row["id"]]
        for _ in range(2):
            if not frontier or len(rows) >= max_rows:
                break
            placeholders = ",".join("?" for _ in frontier)
            children = conn.execute(
                f"SELECT id,name,level,parent_id FROM knowledge_node WHERE parent_id IN ({placeholders}) ORDER BY sort_hint,name",
                tuple(frontier),
            ).fetchall()
            frontier = []
            for child in children:
                if len(rows) >= max_rows:
                    break
                rows.append({"id": child["id"], "name": child["name"], "level": child["level"], "parent_id": child["parent_id"] or ""})
                frontier.append(child["id"])
        return rows
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _herb_names_in_nodes(node_ids: list[str]) -> list[str]:
    conn = _connect()
    if conn is None:
        return []
    try:
        placeholders = ",".join("?" for _ in node_ids)
        rows = conn.execute(
            f"SELECT name FROM kg_entity WHERE kind='herb' AND knowledge_node_id IN ({placeholders}) ORDER BY name",
            tuple(node_ids),
        ).fetchall()
        return [row["name"] for row in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _expand_effects(effects: list[str]) -> list[str]:
    """把「安神」展开成「养心安神 / 安神益智 / 镇惊安神…」。

    学生问的是大类，教材写的是细分。不展开的话，反查题只会得到一个分支、
    十味药挤在一条线上；展开了才是「哪些药、各自主打什么」。
    """

    known: dict[str, None] = {}
    for herb in _herbs():
        for effect in herb.effects:
            known.setdefault(effect, None)
    expanded: list[str] = []
    for effect in effects:
        variants = [term for term in known if effect in term]
        # 细分的放前面：它们才是真的分组；大类本身留作兜底。
        expanded.extend(sorted(variants, key=len, reverse=True)[:6])
        # 反向也要展开：「止咳平喘」在教材里常分成「止咳」「平喘」两条功效，
        # 只按包含关系找会把这两条漏掉，结果一个分支挂十几味药。
        parts = [term for term in known if len(term) >= 2 and term in effect and term != effect]
        expanded.extend(sorted(parts, key=len, reverse=True)[:4])
        expanded.append(effect)
    return list(dict.fromkeys(expanded))


def _herbs_by_effect(effects: list[str]) -> dict[str, list[str]]:
    """功效倒排。返回 {命中功效: [药名]}，按命中数降序。

    直接用子串匹配而不是同义词表：``effect_synonyms`` 用来**归一**提问，
    而这里的提问已经是知识库里原样存在的功效词了（``intents._effects_in``
    只认能匹配上的词），再归一一次反而会把「养心安神」并成「安神」，
    丢掉学生真正想问的细分。
    """

    groups: dict[str, list[str]] = {}
    for effect in effects:
        for herb in _herbs():
            if any(effect in item or item in effect for item in herb.effects):
                groups.setdefault(effect, []).append(herb.name)
    return {key: value for key, value in sorted(groups.items(), key=lambda item: -len(item[1])) if value}


def _herbs_by_fields(anchors: Anchors) -> list[Herb]:
    matched: list[Herb] = []
    for herb in _herbs():
        # 问句里点了药类就必须按药类收窄：「性寒的清热药」问的不是所有寒性药，
        # 否则发散风热药（薄荷、桑叶）会混进来，答案直接跑题。
        if anchors.categories and str(herb.category) not in anchors.categories and herb.subcategory not in anchors.categories:
            continue
        if anchors.meridians and not set(anchors.meridians) & set(herb.meridians):
            continue
        if anchors.natures and str(herb.nature) not in anchors.natures:
            continue
        if anchors.flavors and not set(anchors.flavors) & {str(flavor) for flavor in herb.flavor}:
            continue
        matched.append(herb)
    return matched


def _edges_of_types(types: list[str], *, seeds: tuple[str, ...], db_path: Path | None, both: bool = False) -> list[dict[str, str]]:
    conn = _connect()
    if conn is None:
        return []
    try:
        placeholders = ",".join("?" for _ in types)
        rows = conn.execute(
            "SELECT source_id, target_id, relation_type FROM kg_edge "
            f"WHERE relation_type IN ({placeholders}) AND status='approved'",
            tuple(types),
        ).fetchall()
        seed_ids: set[str] = set()
        for name in seeds:
            entity = _resolve(name)
            if entity:
                seed_ids.add(entity)
        names = _names_for([row["source_id"] for row in rows] + [row["target_id"] for row in rows])
        edges: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            key = (row["source_id"], row["target_id"], row["relation_type"])
            if key in seen:
                continue
            if seed_ids:
                if both:
                    if row["source_id"] not in seed_ids or row["target_id"] not in seed_ids:
                        continue
                elif row["source_id"] not in seed_ids and row["target_id"] not in seed_ids:
                    continue
            seen.add(key)
            source = names.get(row["source_id"], "")
            target = names.get(row["target_id"], "")
            # 名字查不出来的边不画：图上出现一个 "教材未收录" 比少一条边更糟。
            if not source or not target:
                continue
            edges.append({
                "source": source,
                "target": target,
                "type": row["relation_type"],
                "label": f"{source}{row['relation_type']}{target}",
            })
            if len(edges) >= MAX_LINKS:
                break
        return edges
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _herbs_by_syndrome(names: tuple[str, ...], *, db_path: Path | None = None) -> list[str]:
    """证 → 药。三条路，按可信度从高到低。

    1. ``kg_edge`` 的药证链（1491 条，教材抽的，最准）；
    2. 主治条目**完全相同**；
    3. 主治条目**互相包含**——「风寒表证」要能接住「外感风寒表证」「风寒表实证」，
       否则学生用书上原话提问反而查不到。
    """

    hits: list[str] = []
    wanted = set(names)

    conn = _connect()
    if conn is not None:
        try:
            rows = conn.execute("SELECT source_id, target_id FROM kg_edge WHERE relation_type='药证链'").fetchall()
            names_by_id = _names_for([row["source_id"] for row in rows] + [row["target_id"] for row in rows])
            for row in rows:
                source = names_by_id.get(row["source_id"], "")
                target = names_by_id.get(row["target_id"], "")
                if source and target in wanted and source not in hits:
                    hits.append(source)
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    cores = {_syndrome_core(name) for name in names if _syndrome_core(name)}
    for herb in _herbs():
        if herb.name in hits:
            continue
        for indication in herb.indications:
            label = indication.syndrome
            # 空证名必须跳过：`"" in "风寒表证"` 恒为真，会把所有空证名的药全捞进来
            # （实测把没药、三棱招进了风寒表证）。
            if not label:
                continue
            if label in wanted or any(item in label or label in item for item in wanted) or any(core in label for core in cores):
                hits.append(herb.name)
                break
    return hits


def _syndrome_core(name: str) -> str:
    """去掉「证 / 虚 / 实」这类修饰，留病位病性。

    「风寒表证」要能接住教材里的「风寒表实证」「外感风寒表证」——学生用书上
    原话提问反而查不到，是这类系统最常见的失败。核心词匹配是两步走的第二步，
    只用来**扩大召回**，命中的仍是教材原文条目，不会造证据。
    """

    core = name
    for suffix in ("证", "型"):
        core = core.removesuffix(suffix)
    return core if len(core) >= 3 else ""


def _syndrome_notes(name: str) -> list[dict[str, str]]:
    """证 → 教材原文里的主治描述。

    ``data/syndromes`` 里的条目只有药与方，没有症状——因为它是从主治反推的。
    要写「这个证有什么表现」，唯一诚实的来源就是各药主治条目里的 description，
    那是教材【应用】的原文。摘不出来就不写，不自己编症状。
    """

    notes: list[dict[str, str]] = []
    for herb in _herbs():
        for indication in herb.indications:
            if not indication.syndrome:
                continue
            if name and (name == indication.syndrome or name in indication.syndrome):
                text = str(indication.description or "").strip()
                if text:
                    notes.append({"herb": herb.name, "text": text[:60]})
                break
    return notes[:8]


def _formulas_of_syndrome(names: tuple[str, ...], *, db_path: Path | None = None) -> list[str]:
    """证 → 方。走 kg_edge 的证方链（119 条），图谱里没有就回退到证候条目的 related_formulas。"""

    conn = _connect()
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT source_id, target_id FROM kg_edge WHERE relation_type='证方链'").fetchall()
        by_id = _names_for([row["source_id"] for row in rows] + [row["target_id"] for row in rows])
        wanted = set(names)
        found: list[str] = []
        for row in rows:
            source = by_id.get(row["source_id"], "")
            target = by_id.get(row["target_id"], "")
            if source in wanted and target:
                found.append(target)
            elif target in wanted and source:
                found.append(source)
        return list(dict.fromkeys(found))[:6]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _names_for(entity_ids: list[str]) -> dict[str, str]:
    """一次查一批实体名，别逐条 ``display_name``。

    十八反 68 条边、每条两个端点，逐个 resolve 要一秒多（实测 2.7s），
    而这里只要名字——一条 SQL 就够。查不到的回退到知识树节点名。
    """

    ids = [value for value in dict.fromkeys(entity_ids) if value]
    if not ids:
        return {}
    conn = _connect()
    if conn is None:
        return {}
    names: dict[str, str] = {}
    try:
        chunk = 400
        for start in range(0, len(ids), chunk):
            part = ids[start : start + chunk]
            placeholders = ",".join("?" for _ in part)
            for row in conn.execute(f"SELECT id,name FROM kg_entity WHERE id IN ({placeholders})", tuple(part)):
                names[row["id"]] = row["name"]
            for row in conn.execute(f"SELECT id,name FROM knowledge_node WHERE id IN ({placeholders})", tuple(part)):
                names.setdefault(row["id"], row["name"])
    except sqlite3.Error:
        return names
    finally:
        conn.close()
    return names


def _resolve(name: str) -> str | None:
    from app.kg.entities import resolve

    try:
        entity = resolve(name)
    except Exception:  # noqa: BLE001
        return None
    return entity.id if entity is not None else None


def _whitelist(pack: EvidencePack) -> ResourceWhitelist:
    """白名单 = 素材里出现过的东西。模型只能引用这些。"""

    base = build_whitelist(pack.herbs)
    base["herbs"] |= {edge["source"] for edge in pack.edges} | {edge["target"] for edge in pack.edges}
    base["herbs"] |= {row["name"] for row in pack.nodes if row["level"] == "point"}
    base["syndromes"] |= {item["name"] for item in pack.syndromes}
    base["formulas"] |= set(pack.formulas)
    return base


def reset_evidence_cache() -> None:
    _herbs.cache_clear()
