"""``path.focus``：把「我想学麻黄」「合谷在哪」变成地图上的一个坐标。

**确定性匹配，不调模型**。理由不是省钱，是可复核：用户说"我想学清热"时，
他到底指「清热药」这个方向还是「清热燥湿」这一类，连人也要看上下文。
模型会给一个"看起来合理"的答案，然后用户被带到一个他没想去的关。
这里的策略是**取最长匹配**，宁可不猜：

1. ``node_id`` 直查（用户从地图里点的，一定准）；
2. 名称精确；
3. 别名（知识点 ``extra_json.aliases``，如「麻黄汤证」→ 麻黄）；
4. 穴位名 / 代号（合谷、LI4）；
5. 包含匹配，取最长的那个名字；
6. 都不中 → 返回候选让用户选，不瞎猜。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.meridian.store import DB_PATH, describe_point, list_acupoints
from app.path.store import list_nodes

#: 问句里的包装词。剥掉它们再做匹配，否则「我想学麻黄」永远匹配不到「麻黄」。
_PREFIXES = ("我想学", "我想了解", "我想看看", "帮我找", "帮我打开", "带我去", "打开", "我想", "想学", "学一下", "看看", "找一下")
_SUFFIXES = ("在哪", "在哪里", "是哪", "是什么", "怎么学", "怎么记", "讲一讲", "讲讲", "在哪个穴", "对应哪个穴位", "吧", "呢", "吗")


def _strip(text: str) -> str:
    value = " ".join(str(text or "").split())
    changed = True
    while changed:
        changed = False
        for prefix in _PREFIXES:
            if value.startswith(prefix) and len(value) > len(prefix):
                value, changed = value[len(prefix) :].strip(), True
        for suffix in _SUFFIXES:
            if value.endswith(suffix) and len(value) > len(suffix):
                value, changed = value[: -len(suffix)].strip(), True
    return value.strip("「」\"'。，,？?！! ")


def _aliases(node: dict[str, Any]) -> list[str]:
    extra = node.get("extra") or node.get("extra_json") or {}
    if isinstance(extra, str):
        import json

        try:
            extra = json.loads(extra)
        except ValueError:
            extra = {}
    if not isinstance(extra, dict):
        return []
    raw = extra.get("aliases") or extra.get("alias") or []
    if isinstance(raw, str):
        return [raw]
    return [str(item) for item in raw if str(item).strip()]


def _acupoint_index(db_path: Path) -> dict[str, dict[str, Any]]:
    """穴位名/代号 → 该穴位上挂着的一个知识点。"""

    out: dict[str, dict[str, Any]] = {}
    try:
        points = list_acupoints(db_path)
        slots = {}
        from contextlib import closing

        from app.profile.store import connect

        with closing(connect(Path(db_path))) as conn:
            for node_id, acupoint_id in conn.execute(
                "SELECT s.node_id, sec.acupoint_id FROM map_point_slot s JOIN map_section sec ON sec.id = s.section_id"
            ).fetchall():
                slots.setdefault(str(acupoint_id), str(node_id))
    except Exception:  # noqa: BLE001 - 地图没造好时这条能力退化成纯名称匹配
        return out
    for point in points:
        node_id = slots.get(str(point["id"]))
        if node_id:
            out[str(point["name"])] = {"node_id": node_id, "acupoint": point}
            out[str(point["code"]).upper()] = {"node_id": node_id, "acupoint": point}
            out[str(point["code"]).replace(" ", "").upper()] = {"node_id": node_id, "acupoint": point}
    return out


def _result(node_id: str, node_name: str, confidence: float, *, db_path: Path, acupoint: dict[str, Any] | None = None) -> dict[str, Any]:
    detail = describe_point(node_id, db_path) or {}
    layer_id = str(detail.get("layer_id") or "herb")
    acupoint_name = str(detail.get("acupoint_name") or (acupoint or {}).get("name") or "")
    acupoint_code = str(detail.get("acupoint_code") or (acupoint or {}).get("code") or "")
    return {
        "ok": True,
        "node_id": node_id,
        "node_name": node_name,
        "layer_id": layer_id,
        "acupoint": {"name": acupoint_name, "code": acupoint_code} if acupoint_name else {},
        "chapter": str(detail.get("chapter_title") or ""),
        "section": str(detail.get("section_title") or ""),
        "open_url": f"/path?focus={node_id}&layer={layer_id}",
        "confidence": confidence,
        "candidates": [],
    }


def resolve(text: str, *, db_path: Path = DB_PATH) -> dict[str, Any]:
    """把一个说法落到一个 ``node_id`` 上。落不到就给候选。"""

    query = _strip(text)
    nodes = list_nodes(db_path=db_path)
    by_id = {str(node["id"]): node for node in nodes}

    if query in by_id:
        return _result(query, str(by_id[query]["name"]), 0.99, db_path=db_path)

    for node in nodes:
        if str(node["name"]) == query:
            return _result(str(node["id"]), str(node["name"]), 0.95, db_path=db_path)

    for node in nodes:
        if query in _aliases(node):
            return _result(str(node["id"]), str(node["name"]), 0.9, db_path=db_path)

    acupoints = _acupoint_index(db_path)
    hit = acupoints.get(query) or acupoints.get(query.upper())
    if hit is not None:
        node = by_id.get(str(hit["node_id"]))
        name = str(node["name"]) if node else str(hit["acupoint"]["name"])
        return _result(str(hit["node_id"]), name, 0.85, db_path=db_path, acupoint=hit["acupoint"])

    # 包含匹配：取最长的名字。「清热药」与「清热燥湿」都命中「清热」时，
    # 长短决定意图——用户说「清热」多半指大类，说全称才指细分。
    contained = [node for node in nodes if len(str(node["name"])) >= 2 and str(node["name"]) in query]
    if contained:
        best = max(contained, key=lambda item: len(str(item["name"])))
        return _result(str(best["id"]), str(best["name"]), 0.7, db_path=db_path)

    candidates = [
        {"node_id": str(node["id"]), "name": str(node["name"]), "level": str(node.get("level") or "")}
        for node in nodes
        if query and (query in str(node["name"]) or str(node["name"]) in query)
    ][:5]
    return {
        "ok": False,
        "node_id": "",
        "node_name": "",
        "query": query,
        "open_url": "",
        "confidence": 0.0,
        "candidates": candidates,
    }


__all__ = ["resolve"]
