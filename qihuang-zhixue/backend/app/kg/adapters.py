"""把 KG 子图适配回旧 ``GraphContent`` 的 JSON 结构。

**为什么要这一层**：前端 ``GraphView.vue``（812 行 three.js）依赖
``{center_id, nodes[{id,name,type,brief}], edges[{source,target,relation_type,label,source_ref}], sources[{出处,原文摘录,定位}]}``
这套字段，``graph_contract.py`` 里也写死了「Do not rename these fields」。
重构不应该逼前端改代码，所以在这里做一次无损结构转换。

与旧契约的两个差别（有意为之）：

1. **不走 Pydantic 校验**。旧 ``GraphSource.原文摘录`` 有 ``max_length=180``，
   新证据里的教材原文常常超过；截断会丢信息。这里返回纯 dict，并由
   ``_assert_edges_are_sourced`` 手工保证「每条边都能溯源」这条红线。
2. **relation_type 允许新类型**（十八反/十九畏/配伍）。旧枚举只有 14 个值，
   硬塞会把新关系降级成错的类别——那正是重构要消灭的问题。
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

from app.kg import store
from app.kg.entities import display_name, kind_of
from app.kg.traverse import Subgraph
from app.profile.store import DB_PATH, connect


def _brief(entity_id: str, db_path: Path) -> str:
    """节点副标题要携带信息，不要像旧版那样一律写「关联药材」。"""

    entity = store.get_entity(entity_id, db_path)
    if entity is None:
        return "教材未收录"
    if entity.kind == "herb":
        if entity.status == "orphan":
            # 教材真的没给它立独立条目（只在「附药」或别名里出现过）
            return "教材未收录"
        if entity.status == "draft":
            # 教材立了条目，但我们还没抽结构化字段。说「教材未收录」是假话。
            return "教材已收录·数据待抽取"
        subcategory = str(entity.extra.get("subcategory") or "")
        category = str(entity.extra.get("category") or "")
        return f"{category}·{subcategory}".strip("·") or "药材"
    if entity.kind == "syndrome":
        count = entity.extra.get("related_herb_count")
        return f"证候·{count} 味药主治" if count else "证候"
    if entity.kind == "formula":
        size = entity.extra.get("composition_size")
        return f"方剂·{size} 味组成" if size else "方剂"
    return entity.kind


def _node_payload(entity_id: str, db_path: Path) -> dict[str, Any]:
    entity = store.get_entity(entity_id, db_path)
    return {
        "id": entity_id,
        "name": entity.name if entity else display_name(entity_id, db_path),
        "type": entity.kind if entity else kind_of(entity_id),
        "brief": _brief(entity_id, db_path),
    }


def _source_payload(edge: store.KgEdge) -> dict[str, str]:
    evidence = edge.evidence[0] if edge.evidence else {}
    page = evidence.get("page")
    location = str(evidence.get("ref") or "")
    if page and f"p{page}" not in location:
        location = f"{location} p{page}".strip()
    return {
        "出处": location or "本地中药学知识库",
        "原文摘录": str(evidence.get("quote") or edge.label),
        "定位": str(evidence.get("rule_id") or edge.relation_type),
    }


def _assert_edges_are_sourced(payload: dict[str, Any]) -> None:
    """红线：不允许出现没有溯源项的边。"""

    sources = payload.get("sources") or []
    for edge in payload.get("edges") or []:
        index = edge.get("source_ref")
        if not isinstance(index, int) or index < 0 or index >= len(sources):
            raise ValueError(f"图谱边缺少有效溯源：{edge}")


def to_graph_content(subgraph: Subgraph, db_path: Path = DB_PATH) -> dict[str, Any]:
    """KG 子图 → 旧前端契约。"""

    nodes = [_node_payload(entity.id, db_path) for entity in subgraph.entities]
    sources: list[dict[str, str]] = []
    edges: list[dict[str, Any]] = []
    index_by_key: dict[tuple[str, str, str], int] = {}

    for edge in subgraph.edges:
        source = _source_payload(edge)
        key = (source["出处"], source["原文摘录"], source["定位"])
        index = index_by_key.setdefault(key, len(sources))
        if index == len(sources):
            sources.append(source)
        edges.append(
            {
                "source": edge.source_id,
                "target": edge.target_id,
                "relation_type": edge.relation_type,
                "label": edge.label,
                "source_ref": index,
                # 旧契约没有这两个字段，但前端用不到、加上便于调试
                "tier": edge.tier,
                "confidence": edge.confidence,
            }
        )

    payload = {
        "center_id": subgraph.center_id,
        "nodes": nodes,
        "edges": edges,
        "sources": sources,
    }
    _assert_edges_are_sourced(payload)
    return payload


def no_edge_brief(entity: store.KgEntity) -> str:
    """实体存在但一条边都没有时，说清楚「缺的是什么」。"""

    if entity.status == "orphan":
        return "教材未收录"
    if entity.status == "draft":
        return "教材已收录·数据待抽取"
    return "暂无关系数据"


def tree_level(center_id: str, db_path: Path = DB_PATH) -> str | None:
    """中心节点在知识树里的层级（domain / subtype / point）；不在树里返回 None。"""

    with closing(connect(db_path)) as conn:
        try:
            row = conn.execute(
                "SELECT level FROM knowledge_node WHERE id=?", (center_id,)
            ).fetchone()
        except Exception:
            return None
    return str(row[0]) if row else None


def missing_brief(center_id: str, db_path: Path = DB_PATH) -> str:
    """中心节点解析不到实体时，说清楚「缺的是哪一类东西」。

    三种情况不能混成一句话：
    - 章节节点（解表药 / 发散风寒药）：本来就不该有药材实体
    - 总论知识点（性味归经 / 升降浮沉）：教材有内容，但不在药材图谱里
    - 真的查不到：教材未收录

    全写成「教材未收录」等于把「不该有」和「缺了」说成一回事。
    """

    level = tree_level(center_id, db_path)
    if level is None:
        return "教材未收录"
    if level in ("domain", "subtype"):
        return "章节节点·暂无图谱关系"
    return "总论知识点·暂无图谱关系"


def placeholder_graph(
    center_id: str,
    db_path: Path = DB_PATH,
    *,
    brief: str = "教材未收录",
    node_type: str | None = None,
) -> dict[str, Any]:
    """节点存在但图谱里没有它时的诚实返回：单节点、无边、无溯源。

    比 404 好——404 会让前端 ``stores/graph.ts`` 回落 ``mockMahuangGraph``，
    用户看到一张 15 个硬编码节点的假图。
    """

    return {
        "center_id": center_id,
        "nodes": [
            {
                "id": center_id,
                "name": display_name(center_id, db_path),
                "type": node_type or kind_of(center_id),
                "brief": brief,
            }
        ],
        "edges": [],
        "sources": [],
    }


def placeholder_for_unresolved(center_id: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """解析不到实体时的诚实单节点图。

    ``/api/graph`` 与 ``/api/kg/neighbors`` 都要这一份判断，所以收进适配层：
    两处各写一遍，早晚会一处说「章节节点」另一处说「教材未收录」，
    同一个节点在两个接口里说法不一致，比缺失更糟。
    """

    level = tree_level(center_id, db_path)
    return placeholder_graph(
        center_id,
        db_path,
        brief=missing_brief(center_id, db_path),
        # 只要它确实是知识树里的节点，类型就是「类属」——包括总论知识点
        # （性味归经 / 升降浮沉）。留默认值会被 ``kind_of`` 猜成 ``herb``，
        # 详情面板于是给一个概念套上「药材」的字段表，等于把概念说成药。
        node_type="category" if level else None,
    )


__all__ = [
    "to_graph_content",
    "placeholder_graph",
    "placeholder_for_unresolved",
    "no_edge_brief",
    "tree_level",
    "missing_brief",
]
