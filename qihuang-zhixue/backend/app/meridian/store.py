"""经脉学习图的持久层：6 张表 + 造图结果的读写。

设计上的三条硬约束（来自计划 §5，改动前先想清楚）：

1. **槽位按穴位建，不按左右实例建**。同一穴位左右各一个光点，但进度只有一份，
   否则会出现"左边学了右边没学"。
2. **一具身体，一穴一节。** 全学科的节一次性分配到 362 个穴位上，**穴位全局唯一**
   （``assign.assign_all`` 用共享游标保证）。学科仍然是 ``map_layer``，但它现在只是
   「这一段属于哪门课」的标签，用于配色与筛选，**不再是各自独立的一套映射**。
   早期版本让每门课都从手太阴肺经第一个穴排起，同一个「中府」于是同时是中药学
   第 1 节、内科学第 1 节——用户看到的现象就是"一个穴位代表好几门课"。
3. **零外键到 ``knowledge_node``**。``custom:`` / ``remedial:`` 两类节点不在那棵树里，
   硬外键会把已上线功能直接拦死。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from app.profile.store import DB_PATH, connect


SCHEMA_SQL = (
    # ① 经脉：几何资产，全学科共用
    """
    CREATE TABLE IF NOT EXISTS meridian_channel (
        id           TEXT PRIMARY KEY,
        abbr         TEXT NOT NULL,
        title        TEXT NOT NULL,
        kind         TEXT NOT NULL CHECK(kind IN ('meridian','extra')),
        view_bias    TEXT NOT NULL DEFAULT 'front' CHECK(view_bias IN ('front','back','side')),
        path_points  TEXT NOT NULL DEFAULT '[]',
        path_d       TEXT NOT NULL DEFAULT '',
        path_len     REAL,
        geometry_version TEXT NOT NULL DEFAULT '',
        sort_hint    INTEGER NOT NULL DEFAULT 0
    )
    """,
    # ② 穴位：真实穴位，全学科共用的槽位
    """
    CREATE TABLE IF NOT EXISTS meridian_acupoint (
        id          TEXT PRIMARY KEY,
        code        TEXT NOT NULL,
        name        TEXT NOT NULL,
        pinyin      TEXT NOT NULL DEFAULT '',
        channel_id  TEXT NOT NULL REFERENCES meridian_channel(id),
        side        TEXT NOT NULL DEFAULT 'L' CHECK(side IN ('L','R','M')),
        t           REAL NOT NULL CHECK(t >= 0 AND t <= 1),
        x3d         REAL NOT NULL DEFAULT 0,
        y3d         REAL NOT NULL DEFAULT 0,
        z3d         REAL NOT NULL DEFAULT 0,
        skin_tri    INTEGER NOT NULL DEFAULT -1,
        skin_bary   TEXT NOT NULL DEFAULT '',
        verified    INTEGER NOT NULL DEFAULT 0 CHECK(verified IN (0,1)),
        locate_hint TEXT NOT NULL DEFAULT '',
        tier        INTEGER NOT NULL DEFAULT 1,
        UNIQUE(channel_id, code, side)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_acupoint_channel_t ON meridian_acupoint(channel_id, t)",
    "CREATE INDEX IF NOT EXISTS idx_acupoint_verified ON meridian_acupoint(verified)",
    # ②b 模型资产：许可必须留痕，否则 CC BY 4.0 就是违约
    """
    CREATE TABLE IF NOT EXISTS meridian_model_asset (
        id          TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        source_url  TEXT NOT NULL,
        license     TEXT NOT NULL,
        attribution TEXT NOT NULL,
        systems     TEXT NOT NULL DEFAULT '[]',
        file_path   TEXT NOT NULL,
        file_bytes  INTEGER NOT NULL DEFAULT 0,
        geometry_version TEXT NOT NULL
    )
    """,
    # ③ 学科图层
    """
    CREATE TABLE IF NOT EXISTS map_layer (
        id             TEXT PRIMARY KEY,
        title          TEXT NOT NULL,
        kind           TEXT NOT NULL CHECK(kind IN ('container','point_native')),
        graph_version  TEXT NOT NULL,
        color_token    TEXT NOT NULL,
        ord            INTEGER NOT NULL,
        visible_default INTEGER NOT NULL DEFAULT 0,
        authoring_meta TEXT NOT NULL DEFAULT '{}'
    )
    """,
    # ④ 小节（= 穴位）
    """
    CREATE TABLE IF NOT EXISTS map_section (
        id            TEXT PRIMARY KEY,
        layer_id      TEXT NOT NULL REFERENCES map_layer(id),
        chapter_key   TEXT NOT NULL,
        title         TEXT NOT NULL,
        acupoint_id   TEXT NOT NULL REFERENCES meridian_acupoint(id),
        slot_ord      INTEGER NOT NULL CHECK(slot_ord BETWEEN 1 AND 3),
        point_count   INTEGER NOT NULL DEFAULT 0,
        ord           INTEGER NOT NULL,
        -- 完整教材定位。地图要能自己答「这是第几章第几节」，
        -- 不让前端回头去读 data/syllabus/*.json（那是后端的资产）。
        chapter_no    INTEGER NOT NULL DEFAULT 0,
        -- 书上印的章号。多数教材与 chapter_no 相同，但方剂学的各论从「第一章」
        -- 重新起算，chapter_no（内部连续 1–27）与书上号（各论 1–21）不是一回事。
        -- 地图上要说「各论第一章 解表剂」，靠这一列；拿 chapter_no 说就错了。
        book_chapter_no INTEGER NOT NULL DEFAULT 0,
        chapter_title TEXT NOT NULL DEFAULT '',
        part          TEXT NOT NULL DEFAULT '',
        section_no    INTEGER NOT NULL DEFAULT 0,
        -- 教材该章本来不分节，只能以章为单位落一个穴位单元
        virtual       INTEGER NOT NULL DEFAULT 0,
        UNIQUE(acupoint_id, layer_id, slot_ord)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_section_layer_ord ON map_section(layer_id, ord)",
    "CREATE INDEX IF NOT EXISTS idx_section_acupoint ON map_section(acupoint_id)",
    # ⑤ 知识点 → 小节
    """
    CREATE TABLE IF NOT EXISTS map_point_slot (
        node_id     TEXT PRIMARY KEY,
        section_id  TEXT NOT NULL REFERENCES map_section(id),
        tick_index  INTEGER NOT NULL,
        UNIQUE(section_id, tick_index)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_point_slot_section ON map_point_slot(section_id, tick_index)",
)


def init_db(db_path: Path | str = DB_PATH) -> None:
    """建齐 6 张表。全部 ``IF NOT EXISTS``，老库直接兼容。

    ``CREATE TABLE IF NOT EXISTS`` 对已存在的表不会补列，所以新加的教材定位
    字段单独走一遍 ``_migrate``。不这么做，老库跑起来会在 ``INSERT`` 时报
    "no such column: chapter_no"，而建表语句看起来毫无问题。
    """

    with closing(connect(Path(db_path))) as conn:
        for sql in SCHEMA_SQL:
            conn.execute(sql)
        _migrate(conn)
        conn.commit()


#: 后加的列。``(表, 列, 声明)``。全是带默认值的加法，不动已有数据。
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("map_section", "chapter_no", "INTEGER NOT NULL DEFAULT 0"),
    ("map_section", "book_chapter_no", "INTEGER NOT NULL DEFAULT 0"),
    ("map_section", "chapter_title", "TEXT NOT NULL DEFAULT ''"),
    ("map_section", "part", "TEXT NOT NULL DEFAULT ''"),
    ("map_section", "section_no", "INTEGER NOT NULL DEFAULT 0"),
    ("map_section", "virtual", "INTEGER NOT NULL DEFAULT 0"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


# --------------------------------------------------------------------------- 经脉 / 穴位


def upsert_channel(
    *,
    channel_id: str,
    abbr: str,
    title: str,
    kind: str = "meridian",
    view_bias: str = "front",
    sort_hint: int = 0,
    geometry_version: str = "",
    path_points: Sequence[Sequence[float]] | None = None,
    db_path: Path | str = DB_PATH,
) -> None:
    """写入一条经脉。笛卡尔坐标归 P2，这里只保证名录与排序可用。"""

    with closing(connect(Path(db_path))) as conn:
        conn.execute(
            """
            INSERT INTO meridian_channel
                (id, abbr, title, kind, view_bias, sort_hint, geometry_version, path_points)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                abbr=excluded.abbr, title=excluded.title, kind=excluded.kind,
                view_bias=excluded.view_bias, sort_hint=excluded.sort_hint,
                geometry_version=excluded.geometry_version,
                path_points=excluded.path_points
            """,
            (
                channel_id,
                abbr,
                title,
                kind,
                view_bias,
                sort_hint,
                geometry_version,
                json.dumps([list(p) for p in (path_points or [])], ensure_ascii=False),
            ),
        )
        conn.commit()


def upsert_acupoint(
    *,
    acupoint_id: str,
    code: str,
    name: str,
    channel_id: str,
    t: float,
    pinyin: str = "",
    side: str = "L",
    tier: int = 1,
    db_path: Path | str = DB_PATH,
) -> None:
    """写入一个穴位。``side``：中线穴为 ``M``，体侧穴为 ``L``（代表左右这一对）。

    体侧穴**只建一行**：进度与槽位按 ``(channel_id, code)`` 归并，右侧实例由渲染层
    镜像生成。分开建两行会让「学完左边右边没学」变成合法状态。
    """

    with closing(connect(Path(db_path))) as conn:
        conn.execute(
            """
            INSERT INTO meridian_acupoint
                (id, code, name, pinyin, channel_id, side, t, tier)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                code=excluded.code, name=excluded.name, pinyin=excluded.pinyin,
                channel_id=excluded.channel_id, side=excluded.side, t=excluded.t,
                tier=excluded.tier
            """,
            (acupoint_id, code, name, pinyin, channel_id, side, t, tier),
        )
        conn.commit()


def list_channels(db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    with closing(connect(Path(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, abbr, title, kind, view_bias, sort_hint FROM meridian_channel ORDER BY sort_hint, id"
        ).fetchall()
    return [dict(r) for r in rows]


def list_acupoints(db_path: Path | str = DB_PATH, *, channel_id: str | None = None) -> list[dict[str, Any]]:
    """按 ``(channel_id, t)`` 排好序返回。**顺序就是通经时气血行走的顺序。**"""

    sql = (
        "SELECT id, code, name, pinyin, channel_id, side, t, verified "
        "FROM meridian_acupoint"
    )
    params: list[Any] = []
    if channel_id:
        sql += " WHERE channel_id = ?"
        params.append(channel_id)
    sql += " ORDER BY channel_id, t"
    with closing(connect(Path(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def count_acupoints(db_path: Path | str = DB_PATH) -> int:
    with closing(connect(Path(db_path))) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM meridian_acupoint").fetchone()[0])


# --------------------------------------------------------------------------- 图层


def upsert_layer(
    *,
    layer_id: str,
    title: str,
    kind: str,
    graph_version: str,
    color_token: str,
    ord: int,
    visible_default: int = 0,
    authoring_meta: dict[str, Any] | None = None,
    db_path: Path | str = DB_PATH,
) -> None:
    with closing(connect(Path(db_path))) as conn:
        conn.execute(
            """
            INSERT INTO map_layer
                (id, title, kind, graph_version, color_token, ord, visible_default, authoring_meta)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, kind=excluded.kind,
                graph_version=excluded.graph_version, color_token=excluded.color_token,
                ord=excluded.ord, visible_default=excluded.visible_default,
                authoring_meta=excluded.authoring_meta
            """,
            (
                layer_id,
                title,
                kind,
                graph_version,
                color_token,
                ord,
                visible_default,
                json.dumps(authoring_meta or {}, ensure_ascii=False),
            ),
        )
        conn.commit()


def get_layer(layer_id: str, db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    with closing(connect(Path(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM map_layer WHERE id = ?", (layer_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["authoring_meta"] = json.loads(out.get("authoring_meta") or "{}")
    return out


# --------------------------------------------------------------------------- 小节


def replace_sections(
    layer_id: str,
    rows: Iterable[dict[str, Any]],
    *,
    db_path: Path | str = DB_PATH,
) -> int:
    """整层替换小节。阶段是「造图 → 分配 → 落库」的一次性资产，不是用户数据。

    调用方负责先跑 ``assign`` 拿到 ``acupoint_id`` / ``slot_ord``；这里只写不判定。
    ``map_point_slot`` 一并重建，避免残留指向已删小节的孤儿行。

    行里若自带 ``layer_id`` 就以它为准——全学科一次分配时，一批行跨六个图层。
    需要整体重建请用 ``replace_all_sections``。
    """

    payload = list(rows)
    with closing(connect(Path(db_path))) as conn:
        conn.execute(
            "DELETE FROM map_point_slot WHERE section_id IN (SELECT id FROM map_section WHERE layer_id = ?)",
            (layer_id,),
        )
        conn.execute("DELETE FROM map_section WHERE layer_id = ?", (layer_id,))
        _insert_sections(conn, payload, default_layer=layer_id)
        conn.commit()
    return len(payload)


def replace_all_sections(
    rows: Iterable[dict[str, Any]],
    *,
    db_path: Path | str = DB_PATH,
) -> int:
    """**清空全部小节**再写入。一穴一节模式下，六门课共用一套穴位，只能整体重建。

    按层逐个 ``replace_sections`` 是不行的：先写学科 A 占了肺经全部穴位，学科 B
    再来分配时看到的仍是一张空图，于是把 B 也排到肺经上——两个学科又叠在一起了。
    分配必须一次性对全表做，落库也只能一次性全替。
    """

    payload = list(rows)
    with closing(connect(Path(db_path))) as conn:
        conn.execute("DELETE FROM map_point_slot")
        conn.execute("DELETE FROM map_section")
        _insert_sections(conn, payload, default_layer=None)
        conn.commit()
    return len(payload)


def _insert_sections(
    conn: sqlite3.Connection,
    payload: Sequence[dict[str, Any]],
    *,
    default_layer: str | None,
) -> None:
    conn.executemany(
        """
        INSERT INTO map_section
            (id, layer_id, chapter_key, title, acupoint_id, slot_ord, point_count, ord,
             chapter_no, book_chapter_no, chapter_title, part, section_no, virtual)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                r["id"],
                r.get("layer_id") or default_layer,
                r["chapter_key"],
                r["title"],
                r["acupoint_id"],
                r["slot_ord"],
                int(r.get("point_count") or 0),
                int(r["ord"]),
                int(r.get("chapter_no") or 0),
                # 老调用方不传 book_no 时就等于 chapter_no（多数教材两者相同）
                int(r.get("book_chapter_no") or r.get("chapter_no") or 0),
                str(r.get("chapter_title") or ""),
                str(r.get("part") or ""),
                int(r.get("section_no") or 0),
                1 if r.get("virtual") else 0,
            )
            for r in payload
        ],
    )
    for r in payload:
        for i, node_id in enumerate(r.get("node_ids") or [], start=1):
            conn.execute(
                """
                INSERT INTO map_point_slot (node_id, section_id, tick_index)
                VALUES (?,?,?)
                ON CONFLICT(node_id) DO UPDATE SET
                    section_id=excluded.section_id, tick_index=excluded.tick_index
                """,
                (node_id, r["id"], i),
            )


def load_sections_ordered(layer_id: str, db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    with closing(connect(Path(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM map_section WHERE layer_id = ? ORDER BY ord", (layer_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def sections_by_acupoint(layer_id: str, db_path: Path | str = DB_PATH) -> dict[str, list[dict[str, Any]]]:
    """穴位 → 该层挂在它上面的小节（≤3 条，按 ``slot_ord``）。

    地图渲染要的是这个视角：点开一个穴位，看到 1–3 个小节。
    """

    out: dict[str, list[dict[str, Any]]] = {}
    for row in load_sections_ordered(layer_id, db_path):
        out.setdefault(row["acupoint_id"], []).append(row)
    for lst in out.values():
        lst.sort(key=lambda r: r["slot_ord"])
    return out


def layer_stats(layer_id: str, db_path: Path | str = DB_PATH) -> dict[str, Any]:
    """给验收用的统计：小节数、章数、占用穴位数、槽位分布。"""

    with closing(connect(Path(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT COUNT(*) AS sections,
                   COUNT(DISTINCT chapter_key) AS chapters,
                   COUNT(DISTINCT acupoint_id) AS acupoints,
                   SUM(CASE WHEN slot_ord >= 2 THEN 1 ELSE 0 END) AS multi_slot,
                   SUM(CASE WHEN slot_ord = 3 THEN 1 ELSE 0 END) AS triple_slot,
                   COALESCE(SUM(point_count), 0) AS points
            FROM map_section WHERE layer_id = ?
            """,
            (layer_id,),
        ).fetchone()
        slots = conn.execute(
            "SELECT COUNT(*) FROM map_point_slot WHERE section_id IN "
            "(SELECT id FROM map_section WHERE layer_id = ?)",
            (layer_id,),
        ).fetchone()[0]
    return {
        "sections": int(row["sections"] or 0),
        "chapters": int(row["chapters"] or 0),
        "acupoints": int(row["acupoints"] or 0),
        "multi_slot": int(row["multi_slot"] or 0),
        "triple_slot": int(row["triple_slot"] or 0),
        "points": int(row["points"] or 0),
        "point_slots": int(slots or 0),
    }


def describe_point(node_id: str, db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    """一个知识点在地图上的完整定位（P8 的 ``_node_hint`` 用它）。

    对话里用户说"这个""它"时，模型需要知道他现在停在哪一关——但**只能给结构，
    不能给描述文本**：把节点描述塞进 system，模型会把整段教材念一遍。这里返回的
    是"学科 · 章 · 小节 · 穴名（代号）"，一句话能念完，也够模型判断该举哪类例子。
    """

    init_db(db_path)
    with closing(connect(Path(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT n.id, n.name, n.level, n.parent_id, n.exam_frequency,
                   s.tick_index, sec.id AS section_id, sec.title AS section_title,
                   sec.chapter_no, sec.book_chapter_no, sec.chapter_title, sec.part,
                   sec.section_no, sec.virtual, sec.layer_id,
                   l.title AS layer_title,
                   a.name AS acupoint_name, a.code AS acupoint_code,
                   a.channel_id, c.title AS channel_title
            FROM knowledge_node n
            LEFT JOIN map_point_slot s ON s.node_id = n.id
            LEFT JOIN map_section sec ON sec.id = s.section_id
            LEFT JOIN map_layer l ON l.id = sec.layer_id
            LEFT JOIN meridian_acupoint a ON a.id = sec.acupoint_id
            LEFT JOIN meridian_channel c ON c.id = a.channel_id
            WHERE n.id = ?
            """,
            (node_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["virtual"] = bool(item.get("virtual"))
    item["exam_frequency"] = float(item.get("exam_frequency") or 0.0)
    return item


__all__ = [
    "DB_PATH",
    "SCHEMA_SQL",
    "count_acupoints",
    "describe_point",
    "get_layer",
    "init_db",
    "layer_stats",
    "list_acupoints",
    "list_channels",
    "load_sections_ordered",
    "replace_sections",
    "sections_by_acupoint",
    "upsert_acupoint",
    "upsert_channel",
    "upsert_layer",
]
