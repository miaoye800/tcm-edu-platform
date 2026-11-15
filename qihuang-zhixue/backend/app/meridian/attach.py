"""把一份已确认的计划贴到地图刻度上。

**为什么要单独一层**：造图时（P1）已经把 460 个知识点分配到穴位小节，
但那是"这门学科的图长什么样"，与"这个人这个学期学哪些"是两件事。确认一份
计划之后，地图要能回答的是后者：这条路线上每一个点，落在哪条经、哪个穴、
第几个刻度。

所以这里只做三件事，不写任何东西：

1. 按 ``map_point_slot`` 把 ``node_id`` 映射到 ``section_id``；
2. 沿 ``map_section`` 补上教材定位（第几章第几节、穴名、代号）；
3. 把落不到刻度的点单独列出来（``unmapped``），**不静默丢弃**——
   用户会问"我计划里有 12 个点，地图上怎么只有 10 个"。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from app.meridian.store import DB_PATH, init_db
from app.profile.store import connect


def _rows(conn: sqlite3.Connection, node_ids: Iterable[str], layer_id: str | None) -> dict[str, dict[str, Any]]:
    ids = [str(node_id) for node_id in node_ids if node_id]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    sql = f"""
        SELECT s.node_id, s.tick_index, sec.id AS section_id, sec.layer_id, sec.title,
               sec.chapter_no, sec.book_chapter_no, sec.chapter_title, sec.part,
               sec.section_no, sec.virtual, sec.ord, sec.slot_ord,
               a.id AS acupoint_id, a.code AS acupoint_code, a.name AS acupoint_name,
               a.channel_id
        FROM map_point_slot s
        JOIN map_section sec ON sec.id = s.section_id
        JOIN meridian_acupoint a ON a.id = sec.acupoint_id
        WHERE s.node_id IN ({placeholders})
    """
    params: list[Any] = list(ids)
    if layer_id:
        sql += " AND sec.layer_id = ?"
        params.append(layer_id)
    rows = conn.execute(sql, params).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["virtual"] = bool(item.get("virtual"))
        out[str(item.pop("node_id"))] = item
    return out


def attach_nodes(
    node_ids: Iterable[str],
    *,
    layer_id: str | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, dict[str, Any]]:
    """``node_id`` → 穴位/章节定位。查不到的节点不出现在结果里。

    ``layer_id`` 默认 **None = 不限图层**。以前默认 ``"herb"``，一条跨六门课的
    路线只有中药学的点贴得上刻度，其余全进 ``unmapped``——用户看到的就是
    "地图上只有零星几个穴位，别的都找不到"。穴位本身带图层前缀（``herb:c08.s1``），
    一个点最多落在一节里，按图层过滤对正确性没有帮助，只会在跨学科时误伤。
    """

    init_db(db_path)
    with closing(connect(Path(db_path))) as conn:
        # 必须开 row_factory：`connect` 只负责并发参数，行还是裸 tuple。
        # 少了这一行，`dict(row)` 会把 18 列的 tuple 当成键值对解，报
        # "dictionary update sequence element #0 has length 18; 2 is required"。
        # 之前没被发现是因为单测里 map_* 表是空的——循环一次都没进去。
        conn.row_factory = sqlite3.Row
        return _rows(conn, node_ids, layer_id)


def attach_plan(
    nodes: Iterable[dict[str, Any]],
    *,
    layer_id: str | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    """给整份计划贴刻度。``nodes`` 是 ``NodePlan.model_dump()`` 的列表或裸 dict。

    返回 ``{"layer_id", "mapped": {node_id: ...}, "unmapped": [node_id], "coverage": float}``。
    覆盖率低于 1 时调用方要把 ``unmapped`` 交给用户看，而不是自己吞掉。
    """

    ids = [str(node.get("node_id") or "") for node in nodes if isinstance(node, dict)]
    mapped = attach_nodes(ids, layer_id=layer_id, db_path=db_path)
    unmapped = [node_id for node_id in ids if node_id and node_id not in mapped]
    total = len([node_id for node_id in ids if node_id])
    return {
        "layer_id": layer_id,
        "mapped": mapped,
        "unmapped": unmapped,
        "coverage": round(len(mapped) / total, 4) if total else 1.0,
    }


def tick_of(node_id: str, *, layer_id: str | None = None, db_path: Path | str = DB_PATH) -> int | None:
    """单点的刻度序号。对话里说"这一关在地图上第几个刻度"要用。"""

    item = attach_nodes([node_id], layer_id=layer_id, db_path=db_path).get(node_id)
    if item is None:
        return None
    return int(item["tick_index"])


__all__ = ["attach_nodes", "attach_plan", "tick_of"]
