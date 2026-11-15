"""掌握度的 ID 空间对齐（P0-1）。

**问题**：测评事件写的是 ``herb_mahuang``（知识图谱的实体 id），规划器查的是
``d01.s1.p1``（知识树的点 id）。两边对不上时，``get_mastery`` 的返回里一个都命中
不了 planner 的 key，于是所有点的掌握度都取默认 0.35 ——「补救关」永远不触发，
「已经学会的别重复排」也永远不会生效。这不是显示问题，是规划结果错。

**修法**：只在**读取时**做一层映射，不改历史事件。历史事件是既成事实，改它等于
伪造学习记录；映射表可以随时改进，事件不能。

映射顺序（确定性，不调模型）：

1. 已经是知识树里的 id → 原样；
2. ``herb_xxx`` / ``formula_xxx`` 前缀 → 去掉前缀得到名称；
3. 按名称精确匹配知识节点；同名有多个时取**最深**的那个（point 优先于 subtype/domain）；
4. 匹配不到 → 丢弃（不硬塞给某个方向：错配比缺失更糟）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from app.path.store import DB_PATH, list_nodes

#: 图谱实体的前缀。它们来自 ``kg_entity`` 的 id 命名，不是知识树的层级 id。
_ENTITY_PREFIXES = ("herb_", "formula_", "acupoint_", "symptom_", "syndrome_")


def _strip_prefix(kc_id: str) -> str:
    for prefix in _ENTITY_PREFIXES:
        if kc_id.startswith(prefix):
            return kc_id[len(prefix) :]
    return kc_id


def resolve_kc(kc_id: str, *, by_name: dict[str, list[dict[str, Any]]] | None = None, db_path: Path = DB_PATH) -> str | None:
    """把一个外部 kc_id 落到知识树上的一个点 id。落不到返回 ``None``。"""

    catalog = by_name if by_name is not None else name_index(db_path)
    lookup = _strip_prefix(str(kc_id or ""))
    candidates = catalog.get(lookup) or []
    if not candidates:
        # 有些事件用中文名直接当 id（``麻黄``），再试一次原样。
        candidates = catalog.get(str(kc_id or "")) or []
    if not candidates:
        return None
    depth = {"point": 0, "subtype": 1, "domain": 2}
    return str(sorted(candidates, key=lambda node: (depth.get(str(node.get("level")), 3), str(node.get("id"))))[0]["id"])


def name_index(db_path: Path = DB_PATH) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    try:
        for node in list_nodes(db_path=db_path):
            index.setdefault(str(node.get("name") or ""), []).append(node)
    except Exception:  # noqa: BLE001 - 图读不到时退化成"原样映射"，不让规划整体失败
        return {}
    return index


def alias_mastery(
    mastery: dict[str, dict[str, Any]],
    *,
    known_ids: Iterable[str] | None = None,
    db_path: Path = DB_PATH,
) -> dict[str, dict[str, Any]]:
    """把掌握度表的 key 对齐到知识树 id。

    同名多条事件合并时取 **更高** 的 p_mastery 与更多 attempts：同一个知识点
    用两种 id 都记过，说明用户学过两次，不该因为映射丢掉其中一次。
    """

    known = set(known_ids) if known_ids is not None else None
    index = name_index(db_path)
    merged: dict[str, dict[str, Any]] = {}
    for kc_id, state in mastery.items():
        if known is not None and kc_id in known:
            target = kc_id
        else:
            target = resolve_kc(kc_id, by_name=index, db_path=db_path)
            if target is None:
                continue
        current = merged.get(target)
        if current is None:
            merged[target] = dict(state)
            continue
        p_mastery = max(float(current.get("p_mastery") or 0.0), float(state.get("p_mastery") or 0.0))
        attempts = max(int(current.get("attempts") or 0), int(state.get("attempts") or 0))
        last_seen = current.get("last_seen") or state.get("last_seen")
        merged[target] = {"p_mastery": round(p_mastery, 4), "attempts": attempts, "last_seen": last_seen}
    return merged


__all__ = ["alias_mastery", "name_index", "resolve_kc"]
