"""按教材目录补齐知识点树：从「每节 3 味代表药」扩到全部有数据的药材。

## 为什么要扩

`exam_point.node_id` 有外键指向 `knowledge_node`，题目只能挂在真实节点上。
`seed_knowledge_tree.py` 的树是**抽样**的：各论一共 117 个药节点，其中只有 53 味
有教材数据，其余 100 多味药既出不了题、也没有学习路径入口。题库覆盖卡在 55 味，
根因在这里。

## 两条硬约束（决定了实现方式）

1. **id 一旦分配就永不改。** 节点 id 被 `user_path_state`（实测 27 条）/
   `user_resource` / `resource_task` / `immersive_sessions` / `assessment_session` /
   `exam_point` / `graph_entity` 引用，库里已有 133 个用户。**按位置重排 id 会让
   「三七」的进度静默记到别的药上。**
   所以本脚本只做三件事：追加新节点、改父子关系、改显示名。已有节点的 id 一律不动。
   `id` 从此只表示「这是哪个节点」，不表示「这是第几个」。

   （`mastery_events` / `review_states` 的 `kc_id` 存的是 `herb_huangqi` 这种**药材**
   id，不是节点 id；它们不受本脚本影响，但清理节点时仍一并检查，免得哪天改成节点 id
   就出事。）

2. **名字只从教材来，不靠相似度猜。** 章 / 节 / 药名全部取自
   `data/curated/textbook_entries.json`（按教材页序抽取，等于目录顺序）。
   seed 的节名与教材不一致的只有两处，写成 `SECTION_ALIASES` 并注明教材原标题；
   seed 的药名与教材不一致的只有一处（紫苏→紫苏叶），写成 `POINT_RENAMES`。
   `贝母` 这种「教材有川贝母、浙贝母两条」的歧义情况**不猜**，留给人工。

## 教材没有、seed 却有的节点怎么办

seed 建过 `安息香`、`谷芽`、`沙参`、`白芥子`、`神曲`、`藿香` 这几个药节点，
教材根本没给它们立条目（有的是附药，有的是别名，有的教材用另一个名字且没有数据）。
这些节点点进去什么都没有，按「编出来的分类不该留在树上」退掉。
**有引用的不退**——删掉会连带毁掉用户进度或已出的题，这种报出来让人看。

## 同一味药有两个节点怎么办

实测库里 `当归` 有两个节点：`d12.s2.p1`（活血调经药）和 `d17.s2.p1`（补血药）。
教材只把当归放在补血药，前一个是 seed 建错的。**迁移前就存在**，不是本脚本造成的，
但旧实现用「名字 → 单个节点」的字典，第二个同名的直接把第一个覆盖掉，
这种重复既发现不了也清理不了。现在按名字存列表，保留教材分配那一节下的，
其余**有引用的不删**，报出来。

## 教材没有节的章怎么办

化湿药、温里药、理气药、消食药、开窍药、驱虫药这几章教材本来就**不分节**，
seed 当年编出了「芳香化湿药 / 燥湿健脾药」这类二级分类。这些是 seed 的编排，
不是教材的。本脚本把它们并成一个与章同名的节——**编出来的分类不该留在树上**。
被并掉的节如果没有子节点了，就删掉。

用法（在 backend 下）::

    python scripts/extend_knowledge_tree.py --dry-run
    python scripts/extend_knowledge_tree.py
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.path.store import DB_PATH, connect, list_nodes, upsert_edge, upsert_node

ENTRIES_PATH = BACKEND_ROOT / "data" / "curated" / "textbook_entries.json"
HERBS_DIR = BACKEND_ROOT / "data" / "herbs"

#: 教材节名 → seed 节名。**只收语义相同、教材确实用另一个名字的地方**，
#: 且逐条注明教材怎么写。不做相似度匹配：`祛风湿散寒药` 和 `祛风寒湿药` 是同一节，
#: 但 `涩肠止泻药` 和 `敛肺涩肠药` 不是一回事（后者多了敛肺），所以不并。
#:
#: 方向是「教材 → seed」：主循环按教材目录走，拿到教材节名，去认领 seed 那个节点。
#: 反过来查（拿教材名当 key 里的 seed 名）会一条也命中不了，于是每节都新建一个
#: 同义节点、旧的变空再被删掉——实测过，`subtypes_renamed` 空着、`subtypes_added`
#: 多出 24 个就是这么来的。
SECTION_ALIASES: dict[tuple[str, str], str] = {
    ("祛风湿药", "祛风寒湿药"): "祛风湿散寒药",
    ("祛风湿药", "祛风湿热药"): "祛风湿清热药",
}

#: 各论节点 id 形如 `d01`、`d01.s1`、`d01.s1.p1`。`general` 是总论，不在此列。
_SECTION_ID = re.compile(r"^d\d\d(\.|$)")

#: seed 的药节点名 → 教材条目名。**逐条人工确认过，不做相似度匹配。**
#:
#: 只有一处：seed 叫 `紫苏`，教材条目是 `紫苏叶`。三条独立证据指向同一个结论——
#: 教材第 151 页条目名是「紫苏叶」、`紫苏` 是它的别名、而库里 `d01.s1.p3` 上挂的
#: 考点本来就叫「紫苏叶」（出题脚本靠前缀猜到了，但节点名没改）。改节点名让两边对上。
#:
#: 刻意**不收**的情况：`贝母`（教材是川贝母、浙贝母两条，二选一就是猜）、
#: `藿香`（教材是广藿香，但没有药材数据）、`白芥子`/`神曲`/`谷芽`/`沙参`/`安息香`
#: （教材没有对应条目，见 `_retire_stale_points`）。
POINT_RENAMES: dict[str, str] = {"紫苏": "紫苏叶"}

#: 引用节点 id 的列。**删节点前必须查全**：漏一列，用户的进度就静默丢了，
#: 或者外键当场炸。`mastery_events` / `review_states` 用的是 `kc_id`（知识组件）。
_NODE_REFERENCES: tuple[tuple[str, str], ...] = (
    ("exam_point", "node_id"),
    ("user_path_state", "node_id"),
    ("user_resource", "node_id"),
    ("resource_task", "node_id"),
    ("immersive_sessions", "node_id"),
    ("assessment_session", "node_id"),
    ("graph_entity", "knowledge_node_id"),
    ("mastery_events", "kc_id"),
    ("review_states", "kc_id"),
)


def load_herbs() -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for path in sorted(HERBS_DIR.glob("*.json")):
        for herb in json.loads(path.read_text(encoding="utf-8")):
            name = str(herb.get("name") or "").strip()
            if not name:
                continue
            current = merged.get(name)
            if current is None or len(json.dumps(herb, ensure_ascii=False)) > len(
                json.dumps(current, ensure_ascii=False)
            ):
                merged[name] = herb
    return merged


def load_outline() -> list[dict[str, Any]]:
    """章 → 节 → 条目，全部按教材**首次出现顺序**。

    没有节标题的章用一个与章同名的节承接（教材本身不分节）。
    """

    payload = json.loads(ENTRIES_PATH.read_text(encoding="utf-8"))
    chapters: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for entry in payload.get("entries") or []:
        chapter_name = str(entry.get("chapter") or "").strip()
        herb_name = str(entry.get("name") or "").strip()
        if not chapter_name or not herb_name:
            continue
        section_name = str(entry.get("section") or "").strip() or chapter_name
        chapter = index.get(chapter_name)
        if chapter is None:
            chapter = {"name": chapter_name, "sections": [], "_sections": {}}
            index[chapter_name] = chapter
            chapters.append(chapter)
        section = chapter["_sections"].get(section_name)
        if section is None:
            section = {"name": section_name, "herbs": []}
            chapter["_sections"][section_name] = section
            chapter["sections"].append(section)
        if herb_name not in section["herbs"]:
            section["herbs"].append(herb_name)
    for chapter in chapters:
        chapter.pop("_sections", None)
    return chapters


def alias_only_names(herbs: dict[str, dict[str, Any]], entry_names: set[str]) -> set[str]:
    """配伍原文里的写法被单独建了药材条目，但教材没给它立条目。

    实测这 9 条（乌头/大戟/官桂/巴豆/杏仁/牙硝/牵牛/生白蜜/砒霜）的特征：
    `chapter` 为空（不是从教材抽的），且 `aliases` 指向教材里的正名——
    乌头→川乌、官桂→肉桂、牙硝→芒硝、砒霜→砒石、生白蜜→蜂蜜。
    它们是为十八反/十九畏的关系解析建的桩，**不该在树上占一个知识点**。

    判据方向容易写反：不是「谁把 X 当别名」，而是「X 自己指向谁」。
    """

    result: set[str] = set()
    for name, herb in herbs.items():
        if name in entry_names:
            continue
        points_to_entry = [a for a in (herb.get("aliases") or []) if a in entry_names]
        if points_to_entry and not herb.get("chapter"):
            result.add(name)
    return result


def _numeric_suffixes(ids: list[str], pattern: str) -> set[int]:
    return {int(m.group(1)) for value in ids if (m := re.fullmatch(pattern, value))}


def _next_free(used: set[int]) -> int:
    index = 1
    while index in used:
        index += 1
    return index


def _exam_frequency(herb: dict[str, Any] | None) -> float:
    tags = (herb or {}).get("tags") or {}
    if tags.get("high_frequency_exam") is True:
        return 0.85
    if tags.get("learning_difficulty") in (4, 5):
        return 0.4
    return 0.6


def _reference_counts(db_path: Path) -> dict[str, int]:
    """每个节点 id 被引用的总次数。缺表就当没有——测试库不一定建全。"""

    counts: dict[str, int] = {}
    with connect(db_path) as conn:
        for table, column in _NODE_REFERENCES:
            try:
                rows = conn.execute(
                    f"SELECT {column}, count(*) FROM {table} GROUP BY {column}"
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            for node_id, total in rows:
                if node_id:
                    counts[str(node_id)] = counts.get(str(node_id), 0) + int(total)
    return counts


def _apply_point_renames(
    points_by_name: dict[str, list[dict[str, Any]]], stats: dict[str, Any], add_node
) -> None:
    """把 seed 的旧名改成教材条目名，id 不动。

    必须在主循环**之前**跑：主循环按名字找节点，旧名不先改掉，它会找不到、
    然后新建一个同义节点——两个节点同一味药，就是 v1 那批重复节点的来源。
    """

    for old_name, new_name in POINT_RENAMES.items():
        nodes = points_by_name.pop(old_name, [])
        for node in nodes:
            add_node({**node, "name": new_name})
            node["name"] = new_name
            stats["points_renamed"].append(
                {"id": node["id"], "from": old_name, "to": new_name}
            )
        if nodes:
            points_by_name.setdefault(new_name, []).extend(nodes)


def _dedupe_points(
    db_path: Path,
    assigned: dict[str, str],
    points_by_name: dict[str, list[dict[str, Any]]],
    stats: dict[str, Any],
    dry_run: bool,
) -> None:
    """同名药节点只留一个。

    实测库里 `当归` 有两个节点：`d12.s2.p1`（活血调经药）和 `d17.s2.p1`（补血药）。
    教材只把当归放在补血药，前一个是 seed 建错的。**迁移前就存在**，不是本脚本造成的——
    但旧实现用「名字 → 单个节点」的字典，第二个同名的直接把第一个覆盖掉，
    于是这种重复既不会被发现、也不会被清理。

    保留哪一个：教材分配到的节（`assigned`）下的那个；都不在就留引用多的。
    其余**有引用的不删**，报出来——删了会连带毁掉用户进度或已出的题。
    """

    refs = _reference_counts(db_path)
    for name, group in list(points_by_name.items()):
        if len(group) < 2:
            continue
        target = assigned.get(name)
        keep = next((n for n in group if n.get("parent_id") == target), None)
        if keep is None:
            keep = max(group, key=lambda n: (refs.get(str(n["id"]), 0), n["id"]))
        survivors = [keep]
        for other in group:
            if other is keep:
                continue
            used = refs.get(str(other["id"]), 0)
            if used:
                survivors.append(other)
                stats["duplicates_kept_with_references"].append(
                    {"id": other["id"], "name": name, "kept": keep["id"], "references": used}
                )
                continue
            stats["points_removed"].append(
                {"id": other["id"], "name": name, "reason": "duplicate"}
            )
            if dry_run:
                continue
            _delete_node(db_path, other["id"])
        points_by_name[name] = survivors


def _delete_node(db_path: Path, node_id: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "DELETE FROM prerequisite_edge WHERE from_node_id = ? OR to_node_id = ?",
            (node_id, node_id),
        )
        conn.execute("DELETE FROM knowledge_node WHERE id = ?", (node_id,))
        conn.commit()


def _retire_stale_points(
    db_path: Path,
    points_by_name: dict[str, list[dict[str, Any]]],
    entry_names: set[str],
    stats: dict[str, Any],
    dry_run: bool,
) -> None:
    """退掉「教材没有这个条目、seed 却建了节点」的药节点。

    判断用**内存里的** `points_by_name`，不查库：改名（紫苏→紫苏叶）和重挂父节点
    都还没落库，查库会把刚改好的节点当成陈旧节点报出来——试运行时实测过，
    紫苏改名后仍被列进 `points_kept_with_references`。

    只处理各论下的节点（id 以 `d` 加三位数字开头，形如 `d13.s2.p1`）——总论里的
    `升降浮沉`、`性味归经` 本来就不是药材条目，是教材正文里的概念，不在本条清理范围。

    **有引用的不动。** `贝母` 上挂着 1 道十八反题：教材确实用「贝母」这个写法，
    删了题就没了；改名成川贝母或浙贝母都是猜。这种报出来让人看，不自动处理。
    """

    refs = _reference_counts(db_path)
    for name, group in list(points_by_name.items()):
        if name in entry_names:
            continue
        survivors: list[dict[str, Any]] = []
        for node in group:
            if not _SECTION_ID.match(str(node.get("parent_id") or "")):
                survivors.append(node)
                continue
            used = refs.get(str(node["id"]), 0)
            if used:
                survivors.append(node)
                stats["points_kept_with_references"].append(
                    {"id": node["id"], "name": name, "references": used}
                )
                continue
            stats["points_removed"].append({"id": node["id"], "name": name})
            if not dry_run:
                _delete_node(db_path, node["id"])
        if survivors:
            points_by_name[name] = survivors
        else:
            points_by_name.pop(name, None)


def extend(db_path: Path = DB_PATH, *, dry_run: bool = False) -> dict[str, Any]:
    herbs = load_herbs()
    outline = load_outline()
    entry_names = {n for c in outline for s in c["sections"] for n in s["herbs"]}
    skipped_alias = alias_only_names(herbs, entry_names)

    nodes = list_nodes(db_path)
    domains = {n["name"]: n for n in nodes if n["level"] == "domain"}
    subtypes: dict[tuple[str, str], dict[str, Any]] = {
        (n["parent_id"], n["name"]): n for n in nodes if n["level"] == "subtype"
    }
    points_by_name: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        if node["level"] == "point":
            points_by_name.setdefault(node["name"], []).append(node)
    domain_ids = [n["id"] for n in nodes if n["level"] == "domain"]
    subtype_ids = [n["id"] for n in nodes if n["level"] == "subtype"]
    point_ids = [n["id"] for n in nodes if n["level"] == "point"]

    stats: dict[str, Any] = {
        "domains_added": 0,
        "subtypes_added": 0,
        "subtypes_renamed": [],
        "points_renamed": [],
        "points_added": 0,
        "points_reparented": [],
        "points_removed": [],
        "points_kept_with_references": [],
        "duplicates_kept_with_references": [],
        "subtypes_removed": [],
        "entries_without_herb_data": 0,
        "sort_hint_updated": 0,
        "herbs": len(herbs),
        "textbook_entries": len(entry_names),
    }

    def add_node(payload: dict[str, Any]) -> None:
        if not dry_run:
            upsert_node(payload, db_path)

    _apply_point_renames(points_by_name, stats, add_node)

    general_id = domains.get("中药学总论", {}).get("id", "general")
    #: 教材目录里确实存在的节 id。`_retire_empty_subtypes` 靠它区分
    #: 「教材有这一节、只是暂时没数据」和「seed 编出来的分类、现在空了」。
    outline_subtype_ids: set[str] = set()
    #: 教材把每味药放在哪一节。`_dedupe_points` 靠它判断同名节点里哪个是对的。
    assigned: dict[str, str] = {}

    for chapter_index, chapter in enumerate(outline, 1):
        chapter_name = chapter["name"]
        domain = domains.get(chapter_name)
        if domain is None:
            domain_id = f"d{_next_free(_numeric_suffixes(domain_ids, r'd(\d+)')):02d}"
            domain_ids.append(domain_id)
            add_node(
                {
                    "id": domain_id,
                    "name": chapter_name,
                    "level": "domain",
                    "description": f"《中药学》{chapter_name}章节",
                    "exam_frequency": 0.8,
                    "sort_hint": chapter_index,
                }
            )
            stats["domains_added"] += 1
            domain = {"id": domain_id, "name": chapter_name, "level": "domain", "sort_hint": chapter_index}
            domains[chapter_name] = domain
        else:
            _touch_sort_hint(domain, chapter_index, stats, add_node)

        for section_index, section in enumerate(chapter["sections"], 1):
            section_name = section["name"]
            alias = SECTION_ALIASES.get((chapter_name, section_name))
            key = (domain["id"], section_name)
            subtype = subtypes.get(key)
            if subtype is None and alias:
                # 教材用的是另一个名字：把 seed 那个节点改名，id 不动。
                subtype = subtypes.get((domain["id"], alias))
                if subtype is not None:
                    add_node({**subtype, "name": section_name})
                    subtypes.pop((domain["id"], alias))
                    subtypes[key] = {**subtype, "name": section_name}
                    stats["subtypes_renamed"].append(
                        {"id": subtype["id"], "from": alias, "to": section_name}
                    )
                    subtype = subtypes[key]
            if subtype is None:
                pattern = re.escape(domain["id"]) + r"\.s(\d+)"
                subtype_id = f"{domain['id']}.s{_next_free(_numeric_suffixes(subtype_ids, pattern))}"
                subtype_ids.append(subtype_id)
                add_node(
                    {
                        "id": subtype_id,
                        "name": section_name,
                        "level": "subtype",
                        "parent_id": domain["id"],
                        "exam_frequency": 0.75,
                        "sort_hint": section_index,
                    }
                )
                stats["subtypes_added"] += 1
                subtype = {
                    "id": subtype_id,
                    "name": section_name,
                    "level": "subtype",
                    "parent_id": domain["id"],
                    "sort_hint": section_index,
                }
                subtypes[key] = subtype
            else:
                _touch_sort_hint(subtype, section_index, stats, add_node)
            outline_subtype_ids.add(subtype["id"])

            point_index = 0
            for herb_name in section["herbs"]:
                if herb_name in skipped_alias:
                    continue
                # 同名节点可能不止一个（实测库里 `当归` 同时挂在活血调经药和补血药下）。
                # 优先挑**正挂在目标节**的那个，剩下的交给 `_dedupe_points` 收尾。
                candidates = points_by_name.get(herb_name) or []
                point = next(
                    (c for c in candidates if c.get("parent_id") == subtype["id"]), None
                )
                if point is None and candidates:
                    point = candidates[0]
                if point is None and herb_name not in herbs:
                    # 教材有这个条目，但我们没抽到它的数据，且树里也没有它。
                    # 建一个空节点只会给一个点进去什么都没有的入口，不建。
                    stats["entries_without_herb_data"] += 1
                    continue
                point_index += 1
                assigned[herb_name] = subtype["id"]
                if point is None:
                    pattern = re.escape(subtype["id"]) + r"\.p(\d+)"
                    point_id = f"{subtype['id']}.p{_next_free(_numeric_suffixes(point_ids, pattern))}"
                    point_ids.append(point_id)
                    add_node(
                        {
                            "id": point_id,
                            "name": herb_name,
                            "level": "point",
                            "parent_id": subtype["id"],
                            "exam_frequency": _exam_frequency(herbs.get(herb_name)),
                            "sort_hint": point_index,
                        }
                    )
                    stats["points_added"] += 1
                    point = {
                        "id": point_id,
                        "name": herb_name,
                        "level": "point",
                        "parent_id": subtype["id"],
                        "sort_hint": point_index,
                    }
                    points_by_name.setdefault(herb_name, []).append(point)
                else:
                    # id 不动，只改挂在谁下面——教材把它归到这一节。
                    if point["parent_id"] != subtype["id"]:
                        stats["points_reparented"].append(
                            {
                                "id": point["id"],
                                "name": herb_name,
                                "from": point["parent_id"],
                                "to": subtype["id"],
                            }
                        )
                        add_node({**point, "parent_id": subtype["id"], "sort_hint": point_index})
                        point["parent_id"] = subtype["id"]
                    _touch_sort_hint(point, point_index, stats, add_node)

    _dedupe_points(db_path, assigned, points_by_name, stats, dry_run)
    _retire_stale_points(db_path, points_by_name, entry_names, stats, dry_run)
    _retire_empty_subtypes(
        db_path,
        outline_subtype_ids,
        {
            str(node["parent_id"])
            for group in points_by_name.values()
            for node in group
            if node.get("parent_id")
        },
        stats,
        dry_run,
    )
    if not dry_run:
        _rebuild_edges(db_path, general_id, stats)

    stats["dry_run"] = dry_run
    stats["points_in_tree"] = sum(len(group) for group in points_by_name.values())
    # 边是整片重算的，这两个数是**重算后的总数**，不是本次改了几条——
    # 幂等运行时它们不变，别拿它们判断「有没有改动」。
    stats["edges_total"] = {
        "hierarchy": stats.pop("hierarchy_edges", 0),
        "prerequisite": stats.pop("prerequisite_edges", 0),
    }
    stats["points_without_herb_data"] = sorted(
        name for name in points_by_name if name not in herbs
    )
    return stats


def _touch_sort_hint(node: dict[str, Any], hint: int, stats: dict[str, Any], add_node) -> None:
    if node["sort_hint"] == hint:
        return
    stats["sort_hint_updated"] = stats.get("sort_hint_updated", 0) + 1
    node["sort_hint"] = hint
    add_node({**node, "sort_hint": hint})


def _retire_empty_subtypes(
    db_path: Path,
    keep: set[str],
    used: set[str],
    stats: dict[str, Any],
    dry_run: bool,
) -> None:
    """删掉没有子节点、且**教材目录里没有**的节。

    seed 编出来过「芳香化湿药 / 燥湿健脾药」这类二级分类，教材的化湿药根本不分节。
    并成一个与章同名的节之后，这些节就空了，该退掉。

    `keep` 里的是教材自己的节——它们暂时没数据（空节）也留着，否则每次运行都会
    「建了又删」，脚本就不幂等了。

    `used` 是**这次重挂之后**还有药挂着的节 id。不能查库判断「有没有子节点」：
    试运行时改动都还在内存里，查库会看到旧的父子关系，于是每个该退的节都退不掉
    （实测 `subtypes_removed` 一直是 0）。
    """

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name FROM knowledge_node WHERE level = 'subtype'"
        ).fetchall()
    rows = [row for row in rows if row[0] not in keep and row[0] not in used]
    for node_id, name in rows:
        stats["subtypes_removed"].append({"id": node_id, "name": name})
        if dry_run:
            continue
        with connect(db_path) as conn:
            conn.execute("DELETE FROM prerequisite_edge WHERE from_node_id = ? OR to_node_id = ?", (node_id, node_id))
            conn.execute("DELETE FROM knowledge_node WHERE id = ?", (node_id,))
            conn.commit()


def _rebuild_edges(db_path: Path, general_id: str, stats: dict[str, Any]) -> None:
    """重算各论的层级边和先后边。

    这两类边**都是派生的**（层级来自 parent_id，先后来自排序），所以整片重算比
    增量补更安全：增量补会在节序变化时造出环——实测过一次
    `d17.s2 -> d17.s3 -> d17.s4 -> d17.s2`，因为 seed 的 s2..s4 编号和教材节序不一致。
    """

    with connect(db_path) as conn:
        nodes = [
            dict(zip(("id", "name", "level", "parent_id", "sort_hint"), row))
            for row in conn.execute(
                "SELECT id, name, level, parent_id, sort_hint FROM knowledge_node ORDER BY sort_hint, id"
            )
        ]
        section_ids = [n["id"] for n in nodes if _SECTION_ID.match(n["id"])]
        if section_ids:
            placeholders = ",".join("?" * len(section_ids))
            conn.execute(
                f"DELETE FROM prerequisite_edge WHERE from_node_id IN ({placeholders}) "
                f"OR to_node_id IN ({placeholders})",
                section_ids + section_ids,
            )
            conn.commit()

    by_parent: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        if node["parent_id"]:
            by_parent.setdefault(node["parent_id"], []).append(node)

    for parent_id, children in by_parent.items():
        children.sort(key=lambda n: (n["sort_hint"], n["id"]))
        for child in children:
            upsert_edge(
                {
                    "from_node_id": parent_id,
                    "to_node_id": child["id"],
                    "rel_type": "hierarchy",
                    "order_sensitivity": "strict",
                    "reason": "章节层级归属",
                },
                db_path,
            )
            stats["hierarchy_edges"] = stats.get("hierarchy_edges", 0) + 1

    domains = [n for n in nodes if n["level"] == "domain" and n["id"] != general_id]
    domains.sort(key=lambda n: (n["sort_hint"], n["id"]))
    previous: str | None = None
    for domain in domains:
        if previous:
            upsert_edge(
                {
                    "from_node_id": previous,
                    "to_node_id": domain["id"],
                    "order_sensitivity": "free",
                    "reason": "章节间可按复习安排并行，不设硬性先后",
                },
                db_path,
            )
        previous = domain["id"]

    for parent_id, children in by_parent.items():
        if len(children) < 2:
            continue
        strict = parent_id != general_id
        for left, right in zip(children, children[1:]):
            upsert_edge(
                {
                    "from_node_id": left["id"],
                    "to_node_id": right["id"],
                    "order_sensitivity": "strict" if strict else "free",
                    "reason": "按教材编排顺序学习",
                },
                db_path,
            )
            stats["prerequisite_edges"] = stats.get("prerequisite_edges", 0) + 1


def main() -> None:
    parser = argparse.ArgumentParser(description="按教材目录补齐知识点树")
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(extend(args.db_path, dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
