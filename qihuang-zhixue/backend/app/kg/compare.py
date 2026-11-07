"""两药对比 / 两实体路径：纯结构差异，不调模型。

**为什么不需要 LLM**：对比表要回答的是「这两味药差在哪」，而差异是**可枚举**的
——性味、归经、功效、主治、剂量区间、毒性、配伍禁忌，逐项求交并集即可。
让模型写这段，它会把「麻黄发汗力强」这种教材原话换个说法再输出一遍，
既不可溯源，也可能把「桂枝发汗力缓」说反。

教材本身就有现成的鉴别句（``differentiation.key_points``，如「麻黄发汗力强，
兼宣肺平喘、利水消肿；桂枝发汗力缓，兼温通经脉、助阳化气」），
``build_kg.py`` 已把它落成 ``同类鉴别`` 边。**优先用这句原文**，
结构差异只用来补教材没写的维度（剂量、毒性、归经）。

``summary`` 里的每句话都来自 ``differences`` 或教材原文，不生成新知识。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.kb.ingest import load_herbs
from app.kb.models import Herb
from app.kg import store
from app.kg.constants import Tier
from app.kg.entities import display_name, resolve
from app.kg.store import KgEdge, KgEntity
from app.kg.traverse import shortest_path
from app.profile.store import DB_PATH

#: 对比维度顺序固定，前端表格列不会因为数据缺失而跳动。
COMPARE_FIELDS = ("nature", "flavor", "meridians", "effects", "indications", "dosage", "toxicity")


@dataclass(frozen=True)
class Difference:
    field: str
    label: str
    left: Any
    right: Any
    only_left: list[str] = field(default_factory=list)
    only_right: list[str] = field(default_factory=list)
    shared: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "label": self.label,
            "left": self.left,
            "right": self.right,
            "only_left": self.only_left,
            "only_right": self.only_right,
            "shared": self.shared,
        }


def compare(
    left_key: str, right_key: str, *, db_path: Path = DB_PATH
) -> dict[str, Any]:
    """两味药（或任意两个实体）的对比。"""

    left = resolve(left_key, db_path)
    right = resolve(right_key, db_path)
    if left is None or right is None:
        missing = left_key if left is None else right_key
        return {
            "ok": False,
            "reason": f"「{missing}」在教材里没有对应条目，无法对比",
            "left": None,
            "right": None,
        }

    monograph = {herb.id: herb for herb in load_herbs()}
    left_herb = monograph.get(left.id)
    right_herb = monograph.get(right.id)

    if left_herb is None or right_herb is None:
        # 孤儿药材只有身份，没有性味功效。硬凑一张表等于编。
        # 但 T1 硬约束不依赖 monograph——「这两味能不能一起用」必须照答。
        missing_side = left if left_herb is None else right
        return {
            "ok": False,
            "reason": f"「{missing_side.name}」尚无结构化条目，缺性味功效，无法逐项对比",
            "left": _entity_brief(left),
            "right": _entity_brief(right),
            "differences": [],
            "textbook_differentiation": None,
            "shared_relations": [],
            "constraints": _constraints(left, right, db_path),
            "summary": "",
        }

    differences = [
        _difference("nature", "四气", left_herb, right_herb, _nature),
        _difference("flavor", "五味", left_herb, right_herb, _flavors),
        _difference("meridians", "归经", left_herb, right_herb, _meridians),
        _difference("effects", "功效", left_herb, right_herb, _effects),
        _difference("indications", "主治", left_herb, right_herb, _indications),
        _difference("dosage", "用量", left_herb, right_herb, _dosage),
        _difference("toxicity", "毒性", left_herb, right_herb, _toxicity),
    ]

    textbook = _textbook_differentiation(left, right, db_path)
    return {
        "ok": True,
        "left": _entity_brief(left, left_herb),
        "right": _entity_brief(right, right_herb),
        "differences": [item.as_dict() for item in differences],
        "textbook_differentiation": textbook,
        "shared_relations": _shared_relations(left, right, db_path),
        "constraints": _constraints(left, right, db_path),
        "summary": _summary(left.name, right.name, differences, textbook),
    }


def find_path(
    from_key: str, to_key: str, *, max_hops: int = 4, db_path: Path = DB_PATH
) -> dict[str, Any]:
    """两实体间最短关系链。回答「这两味药是怎么扯上关系的」。"""

    start = resolve(from_key, db_path)
    end = resolve(to_key, db_path)
    if start is None or end is None:
        missing = from_key if start is None else to_key
        return {"ok": False, "reason": f"「{missing}」在教材里没有对应条目", "path": []}

    edges = shortest_path(start.id, end.id, max_hops=max_hops, db_path=db_path)
    if not edges:
        return {
            "ok": True,
            "found": False,
            "from": _entity_brief(start),
            "to": _entity_brief(end),
            "path": [],
            "reason": f"{max_hops} 跳以内没有 T1/T2 关系链——不是「没关系」，是「教材没写这条关系」",
        }

    nodes = [start.id]
    steps = []
    for edge in edges:
        current = nodes[-1]
        nxt = edge.target_id if edge.source_id == current else edge.source_id
        nodes.append(nxt)
        steps.append(
            {
                "edge_id": edge.id,
                "from": {"id": current, "name": display_name(current, db_path)},
                "to": {"id": nxt, "name": display_name(nxt, db_path)},
                "relation_type": edge.relation_type,
                "tier": edge.tier,
                "label": edge.label,
                "evidence": edge.evidence[:1],
            }
        )
    return {
        "ok": True,
        "found": True,
        "from": _entity_brief(start),
        "to": _entity_brief(end),
        "hops": len(edges),
        "path": steps,
        "sentence": " → ".join(
            [display_name(nodes[0], db_path)]
            + [f"-[{step['relation_type']}]-> {step['to']['name']}" for step in steps]
        ),
    }


# --------------------------------------------------------------------------- #
# 维度取值
# --------------------------------------------------------------------------- #


def _nature(herb: Herb) -> list[str]:
    return [str(herb.nature)]


def _flavors(herb: Herb) -> list[str]:
    return [str(item) for item in herb.flavor]


def _meridians(herb: Herb) -> list[str]:
    return [str(item) for item in herb.meridians]


def _effects(herb: Herb) -> list[str]:
    return list(herb.effects)


def _indications(herb: Herb) -> list[str]:
    return [item.syndrome for item in herb.indications]


def _dosage(herb: Herb) -> list[str]:
    return [herb.dosage.text]


def _toxicity(herb: Herb) -> list[str]:
    return [str(herb.toxicity)]


def _difference(field_name: str, label: str, left: Herb, right: Herb, extract) -> Difference:
    left_values = [value for value in extract(left) if value]
    right_values = [value for value in extract(right) if value]
    left_set, right_set = set(left_values), set(right_values)
    return Difference(
        field=field_name,
        label=label,
        left=left_values,
        right=right_values,
        only_left=sorted(left_set - right_set),
        only_right=sorted(right_set - left_set),
        shared=sorted(left_set & right_set),
    )


# --------------------------------------------------------------------------- #
# 教材原文与共享关系
# --------------------------------------------------------------------------- #


def _textbook_differentiation(left: KgEntity, right: KgEntity, db_path: Path) -> dict[str, Any] | None:
    """教材明文鉴别句。**优先**用这条，而不是自己组织语言。"""

    for edge in store.edges_touching([left.id, right.id], tiers=(int(Tier.FACT),), db_path=db_path):
        if edge.relation_type != "同类鉴别":
            continue
        pair = {edge.source_id, edge.target_id}
        if pair != {left.id, right.id}:
            continue
        evidence = edge.evidence[0] if edge.evidence else {}
        return {
            "edge_id": edge.id,
            "quote": str(evidence.get("quote") or edge.label),
            "ref": str(evidence.get("ref") or ""),
            "page": evidence.get("page"),
        }
    return None


def _shared_relations(left: KgEntity, right: KgEntity, db_path: Path) -> list[dict[str, Any]]:
    """两味药共同连到的实体——「它们都和谁有关系」比「它们彼此什么关系」更有用。"""

    neighbours_by_edge: dict[str, list[KgEdge]] = {}

    def neighbours(entity_id: str) -> dict[str, dict[str, Any]]:
        edges = neighbours_by_edge.get(entity_id)
        if edges is None:
            edges = store.edges_touching([entity_id], tiers=(int(Tier.FACT),), db_path=db_path)
            neighbours_by_edge[entity_id] = edges
        result: dict[str, dict[str, Any]] = {}
        for edge in edges:
            other = edge.target_id if edge.source_id == entity_id else edge.source_id
            if other in {left.id, right.id}:
                continue
            result[other] = {
                "id": other,
                "name": "",  # 下面统一一次查全
                "relation_type": edge.relation_type,
                "edge_id": edge.id,
            }
        return result

    left_map = neighbours(left.id)
    right_map = neighbours(right.id)
    shared_ids = set(left_map) & set(right_map)
    if not shared_ids:
        return []

    # 名字一次查全。``display_name`` 会把 id 走一遍 resolve（最多 4 次查询），
    # 而每次查询都新开一条连接；75 个共同邻居时，5.6 秒里有 4.5 秒花在关连接上。
    names = store.get_entities(list(shared_ids), db_path)
    for entity_id in shared_ids:
        entity = names.get(entity_id)
        left_map[entity_id]["name"] = entity.name if entity else "教材未收录"

    return [
        left_map[key]
        for key in sorted(shared_ids, key=lambda item: left_map[item]["name"])
    ]


def _constraints(left: KgEntity, right: KgEntity, db_path: Path) -> list[dict[str, Any]]:
    """两味药之间的 T1 硬约束。对比页必须显眼——「能不能一起用」比「差在哪」更急。"""

    items: list[dict[str, Any]] = []
    for edge in store.edges_touching([left.id, right.id], tiers=(int(Tier.CONSTRAINT),), db_path=db_path):
        if {edge.source_id, edge.target_id} != {left.id, right.id}:
            continue
        evidence = edge.evidence[0] if edge.evidence else {}
        items.append(
            {
                "edge_id": edge.id,
                "relation_type": edge.relation_type,
                "page": evidence.get("page"),
                "quote": str(evidence.get("quote") or edge.label),
            }
        )
    return items


def _summary(
    left_name: str, right_name: str, differences: list[Difference], textbook: dict[str, Any] | None
) -> str:
    if textbook:
        return textbook["quote"]
    parts = []
    for item in differences:
        if item.only_left or item.only_right:
            left = "、".join(item.only_left) or "—"
            right = "、".join(item.only_right) or "—"
            parts.append(f"{item.label}：{left_name} {left} / {right_name} {right}")
    if not parts:
        return f"教材没有收录{left_name}与{right_name}的鉴别点，且两者的性味归经功效完全一致——这种情况极少，建议核对。"
    return "；".join(parts) + "。"


def _entity_brief(entity: KgEntity, herb: Herb | None = None) -> dict[str, Any]:
    """实体身份 + 教材出处。

    表格里每一行（性味/归经/功效/剂量…）都取自该药的教材条目，所以出处挂在**这一侧**
    而不是逐行重复——同一个字段集只有一个来源，逐行贴同一句话只会让表变脏。
    出处取 ``sources[0]``：那是一味药的锚点条目，``differentiation`` 之类的补充句
    另有自己的边证据。
    """

    brief: dict[str, Any] = {
        "id": entity.id,
        "name": entity.name,
        "kind": entity.kind,
        "status": entity.status,
    }
    if herb is not None and herb.sources:
        source = herb.sources[0]
        brief["source"] = {"ref": source.ref, "quote": source.quote}
    return brief


__all__ = ["compare", "find_path", "COMPARE_FIELDS"]
