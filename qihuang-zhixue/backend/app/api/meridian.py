"""经脉图的只读接口（``/api/meridian``）。

三件事分开返回，是**体积**决定的，不是洁癖：

* ``/atlas``：14 经目录 + 362 穴坐标 + 六学科图层结构 ≈ 60 KB。首屏只拉它。
* ``/paths``：经脉折线（14 条，合计约 170 KB 原始字符）。只在需要画线时拉，可长缓存。
* ``/point/{node_id}``：单个知识点的正文（麻黄那条「应用」就有 700 字）。
  把正文塞进 atlas 会让首屏多出几百 KB，而用户一次只看一个穴位。

``/asset/{name}`` 返回几何二进制（皮肤 0.58 MB gz），与 atlas 分开是因为它会命中
浏览器缓存，且 P3 要按需加载肌肉层。

这些是公共教学资产（教材目录 + 开源几何），不含用户数据，所以不要求登录。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.meridian import store
from app.meridian.asset import OUT_DIR, GEOMETRY_VERSION, ATTRIBUTION, LICENSES

router = APIRouter(prefix="/api/meridian", tags=["meridian"])

#: `atlas(layer="all")` 的合成图层 id：一具身体装全部学科（一穴一节）。
#: 不是 `map_layer` 里的真实行——它是"所有行合起来"的视图。
LAYER_ALL = "all"

#: 资产白名单。**不拼接用户输入**：``name`` 直接进文件名会让 ``../`` 读走别的文件。
_ASSETS: dict[str, str] = {
    "skin.bin.gz": "skin.bin.gz",
    "skin.bin": "skin.bin",
    "muscle.bin.gz": "muscle.bin.gz",
    "muscle.bin": "muscle.bin",
    "skeleton.bin.gz": "skeleton.bin.gz",
    "skeleton.bin": "skeleton.bin",
}

#: 下架（P10）。肌肉/骨骼的 .bin 仍留在磁盘、白名单也仍能取到（回滚只要放开这里），
#: 但目录里不再**宣传**它们：前端不再请求，首屏清单也不该列两份用不到的几何。
#: 肌肉层是 7.5 mm 体素简化的产物，1.25 m 视距下肌腹是一块块断开的；而穴位坐标
#: 本来就吸附在皮肤三角形上，渲染里面那层对"看穴位在哪"没有帮助。
RETIRED_ASSETS: frozenset[str] = frozenset({"muscle", "skeleton"})


def _manifest() -> dict[str, Any]:
    path = OUT_DIR / "manifest.json"
    if not path.exists():
        return {
            "geometry_version": GEOMETRY_VERSION,
            "assets": [],
            "attribution": ATTRIBUTION,
            "licenses": list(LICENSES),
            "first_screen_bytes": 0,
            "points": 0,
            "paths": 0,
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _acupoints(db_path: Path) -> list[dict[str, Any]]:
    with closing(store.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, code, name, pinyin, channel_id, side, t,
                   x3d, y3d, z3d, verified, skin_tri, tier
              FROM meridian_acupoint
             ORDER BY channel_id, t
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _channels(db_path: Path) -> list[dict[str, Any]]:
    with closing(store.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, abbr, title, kind, view_bias, sort_hint, path_len, geometry_version "
            "FROM meridian_channel ORDER BY sort_hint, id"
        ).fetchall()
    return [dict(r) for r in rows]


def _layers(db_path: Path) -> list[dict[str, Any]]:
    with closing(store.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, title, kind, color_token, ord, visible_default FROM map_layer ORDER BY ord"
        ).fetchall()
    return [dict(r) for r in rows]


def _layer_detail(layer_id: str, db_path: Path) -> dict[str, Any] | None:
    """某一科图层的章 / 节 / 知识点清单（只带名字，不带正文）。

    ``layer_id="all"`` 是**一具身体装全部学科**的视图：没有独立图层，返回一份
    合成的容器，里面是六门课的全部小节。这个模式下穴位全局唯一（``assign_all``
    保证），所以一份清单不会出现"同一个穴位两条小节"。学科仍靠 ``layer_id``
    字段带到前端，用于配色与筛选。
    """

    everything = layer_id == LAYER_ALL
    with closing(store.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        if everything:
            layer = {
                "id": LAYER_ALL,
                "title": "全部学科",
                "kind": "container",
                "graph_version": "",
                "color_token": "--qh-accent",
                "ord": 0,
                "visible_default": 1,
                "authoring_meta": "{}",
            }
            sections = conn.execute(
                """
                SELECT s.id, s.layer_id, s.chapter_key, s.title, s.acupoint_id, s.slot_ord,
                       s.point_count, s.ord, s.chapter_no, s.book_chapter_no, s.chapter_title,
                       s.part, s.section_no, s.virtual
                  FROM map_section s
                  JOIN map_layer l ON l.id = s.layer_id
                 ORDER BY l.ord, s.ord
                """
            ).fetchall()
            ticks = conn.execute(
                """
                SELECT p.section_id, p.node_id, p.tick_index, k.name
                  FROM map_point_slot p
                  JOIN map_section s ON s.id = p.section_id
                  LEFT JOIN knowledge_node k ON k.id = p.node_id
                 ORDER BY p.section_id, p.tick_index
                """
            ).fetchall()
        else:
            layer = conn.execute("SELECT * FROM map_layer WHERE id = ?", (layer_id,)).fetchone()
            if layer is None:
                return None
            sections = conn.execute(
                """
                SELECT id, layer_id, chapter_key, title, acupoint_id, slot_ord, point_count, ord,
                       chapter_no, book_chapter_no, chapter_title, part, section_no, virtual
                  FROM map_section WHERE layer_id = ? ORDER BY ord
                """,
                (layer_id,),
            ).fetchall()
            ticks = conn.execute(
                """
                SELECT p.section_id, p.node_id, p.tick_index, k.name
                  FROM map_point_slot p
                  JOIN map_section s ON s.id = p.section_id
                  LEFT JOIN knowledge_node k ON k.id = p.node_id
                 WHERE s.layer_id = ?
                 ORDER BY p.section_id, p.tick_index
                """,
                (layer_id,),
            ).fetchall()

    by_section: dict[str, list[dict[str, Any]]] = {}
    for row in ticks:
        by_section.setdefault(str(row["section_id"]), []).append(
            {
                "id": row["node_id"],
                # 软引用（``concept:xxx``）在树里没有节点，退回到 id 尾段当展示名，
                # 前端不至于显示一个 ``None``。
                "name": row["name"] or str(row["node_id"]).rsplit(":", 1)[-1],
            }
        )
    out = dict(layer)
    out["authoring_meta"] = json.loads(out.get("authoring_meta") or "{}")
    out["sections"] = [
        {**dict(row), "points": by_section.get(str(row["id"]), [])} for row in sections
    ]
    return out


@router.get("/atlas")
def atlas(layer: str = Query("herb", description="要展开的学科图层")) -> dict[str, Any]:
    """一屏所需的一切：几何清单、14 经、362 穴、图层结构。"""

    db_path = store.DB_PATH
    store.init_db(db_path)
    manifest = _manifest()
    detail = _layer_detail(layer, db_path)
    layers = _layers(db_path)
    if detail is None and layers:
        detail = _layer_detail(str(layers[0]["id"]), db_path)
    acupoints = _acupoints(db_path)
    verified = sum(1 for a in acupoints if a["verified"])
    return {
        "geometry_version": manifest.get("geometry_version", GEOMETRY_VERSION),
        "height_m": 1.716,
        "attribution": manifest.get("attribution", ATTRIBUTION),
        "licenses": manifest.get("licenses", list(LICENSES)),
        "assets": [
            item
            for item in manifest.get("assets", [])
            if str(item.get("name") or "") not in RETIRED_ASSETS
        ],
        "first_screen_bytes": manifest.get("first_screen_bytes", 0),
        "channels": _channels(db_path),
        "acupoints": acupoints,
        "layers": layers,
        "layer": detail,
        # 明确告诉前端「哪些穴是几何吸附的、没人工核过」。verified 全 0 不是缺陷，
        # 是上游与我们都还没做逐穴校核；前端按此画虚线环。
        "precision": {
            "verified": verified,
            "total": len(acupoints),
            "statement": "定位依据 GB/T 12346-2021 · 坐标经几何吸附，未经临床校核",
            "review_url": "/api/meridian/review",
        },
    }


@router.get("/paths")
def paths(channel: str | None = Query(None, description="只取某一条经，如 BL")) -> dict[str, Any]:
    """经脉 3D 折线。左侧一份，右侧由渲染层镜像。"""

    db_path = store.DB_PATH
    store.init_db(db_path)
    with closing(store.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        sql = "SELECT id, title, path_points, path_len FROM meridian_channel"
        params: list[Any] = []
        if channel:
            sql += " WHERE id = ?"
            params.append(channel)
        sql += " ORDER BY sort_hint, id"
        rows = conn.execute(sql, params).fetchall()
    return {
        "channels": [
            {
                "id": row["id"],
                "title": row["title"],
                "length_m": row["path_len"],
                "points": json.loads(row["path_points"] or "[]"),
            }
            for row in rows
        ]
    }


@router.get("/point/{node_id}")
def point(node_id: str) -> dict[str, Any]:
    """单个知识点的正文。**这是「点开穴位有东西看」的落点。**"""

    db_path = store.DB_PATH
    store.init_db(db_path)
    with closing(store.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, name, level, parent_id, description, exam_frequency, extra_json "
            "FROM knowledge_node WHERE id = ?",
            (node_id,),
        ).fetchone()
        if row is None:
            # 软引用（``herb:麻黄`` / ``concept:四气``）不是错误，是"还没进知识树"；
            # 告诉前端展示名，别让它以为接口挂了。
            raise HTTPException(
                status_code=404,
                detail={
                    "node_id": node_id,
                    "reason": "该刻度是软引用，尚未进知识树",
                    "display_name": node_id.rsplit(":", 1)[-1],
                },
            )
        sections = conn.execute(
            """
            SELECT s.layer_id, s.title, s.chapter_title, s.chapter_no, s.book_chapter_no,
                   s.part, s.section_no
              FROM map_point_slot p JOIN map_section s ON s.id = p.section_id
             WHERE p.node_id = ?
             ORDER BY s.layer_id
            """,
            (node_id,),
        ).fetchall()
    meta = json.loads(row["extra_json"] or "{}")
    return {
        "id": row["id"],
        "name": row["name"],
        "level": row["level"],
        "parent_id": row["parent_id"],
        "description": row["description"],
        "exam_frequency": row["exam_frequency"],
        "content": meta.get("content") or {},
        "content_source": meta.get("content_source") or {},
        "sections": [dict(s) for s in sections],
    }


@router.get("/review")
def review() -> dict[str, Any]:
    """``coord_review.csv`` 的内容（坐标复核清单）。没有文件时返回空表而不是 500。"""

    path = OUT_DIR / "coord_review.csv"
    if not path.exists():
        return {"rows": [], "note": "尚未生成复核清单，先跑 python -m app.meridian.crosscheck"}
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",") if lines else []
    rows = [dict(zip(header, line.split(",", len(header) - 1))) for line in lines[1:] if line]
    return {"rows": rows, "count": len(rows)}


@router.get("/asset/{name}")
def asset(name: str) -> FileResponse:
    """几何二进制。白名单键控，不做路径拼接。

    ``.gz`` 走 ``Content-Encoding: gzip`` 而不是 ``Content-Type: application/gzip``：
    后者会让浏览器把字节原样交给 JS，前端得自己解压；前者由浏览器在传输层解掉，
    ``arrayBuffer()`` 直接拿到裸二进制，少一段解压代码也少一次内存拷贝。
    """

    filename = _ASSETS.get(name)
    if filename is None:
        raise HTTPException(status_code=404, detail=f"没有这个资产：{name}")
    path = OUT_DIR / filename
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail="资产还没构建，先跑 python -m app.meridian.asset --build",
        )
    headers = {"Cache-Control": "public, max-age=604800"}
    if filename.endswith(".gz"):
        headers["Content-Encoding"] = "gzip"
    return FileResponse(
        path, media_type="application/octet-stream", filename=filename, headers=headers
    )
