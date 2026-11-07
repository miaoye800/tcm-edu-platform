"""知识点树扩展脚本的硬约束。

这个脚本会改真实库里的 `knowledge_node`，而节点 id 被学习进度、考点、图谱实体
引用着。所以测试守的不是「结果好看」，而是三条不能破的规矩：

1. **活下来的节点 id 一律不许变**（改名字可以，换 id 不行）；
2. **同一味药只许有一个节点**；
3. **有引用的节点不许删**——宁可留着报出来，也不能悄悄毁掉用户的进度或题目。
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from app.path.store import list_nodes, upsert_node
from app.quiz.storage import init_quiz_db

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = BACKEND_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "knowledge.db"
    _load_script("seed_knowledge_tree").seed(db_path)
    init_quiz_db(db_path)
    return db_path


def _snapshot(db_path: Path) -> dict[str, tuple[str, str]]:
    with sqlite3.connect(db_path) as conn:
        return {
            node_id: (name, parent_id)
            for node_id, name, parent_id in conn.execute(
                "SELECT id, name, parent_id FROM knowledge_node"
            )
        }


def _point_id(db_path: Path, name: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM knowledge_node WHERE level = 'point' AND name = ?", (name,)
        ).fetchone()
    assert row, f"树上没有 {name}"
    return row[0]


def test_surviving_node_ids_never_change(seeded_db: Path) -> None:
    """删掉的节点可以没有，但活下来的那些 id 必须还是原来那个 id。"""

    before = _snapshot(seeded_db)
    _load_script("extend_knowledge_tree").extend(seeded_db)
    after = _snapshot(seeded_db)

    survivors = set(before) & set(after)
    assert survivors, "一个节点都没活下来，说明脚本把树推平了"
    # 允许改名的只有显式对照表里的那几处；其余节点的名字必须原样。
    renames = {"紫苏", "祛风湿散寒药", "祛风湿清热药"}
    for node_id in survivors:
        old_name = before[node_id][0]
        if old_name in renames:
            continue
        assert after[node_id][0] == old_name, f"{node_id} 被改名：{old_name} → {after[node_id][0]}"


def test_every_herb_with_data_gets_exactly_one_node(seeded_db: Path) -> None:
    module = _load_script("extend_knowledge_tree")
    module.extend(seeded_db)

    herbs = module.load_herbs()
    outline = module.load_outline()
    entry_names = {n for chapter in outline for section in chapter["sections"] for n in section["herbs"]}
    expected = set(herbs) & entry_names

    with sqlite3.connect(seeded_db) as conn:
        rows = conn.execute("SELECT name, COUNT(*) FROM knowledge_node WHERE level = 'point' GROUP BY name")
        counts = dict(rows.fetchall())

    missing = sorted(expected - set(counts))
    assert not missing, f"这些药有数据却没有节点，出不了题：{missing}"
    duplicated = {name: n for name, n in counts.items() if n > 1}
    assert not duplicated, f"同名药节点不止一个：{duplicated}"


def test_extension_is_idempotent(seeded_db: Path) -> None:
    module = _load_script("extend_knowledge_tree")
    module.extend(seeded_db)
    before = _snapshot(seeded_db)

    stats = module.extend(seeded_db)
    assert stats["points_added"] == 0
    assert stats["subtypes_added"] == 0
    assert stats["domains_added"] == 0
    assert stats["points_removed"] == []
    assert stats["subtypes_removed"] == []
    assert stats["points_renamed"] == []
    assert stats["subtypes_renamed"] == []
    assert _snapshot(seeded_db) == before


def test_duplicate_herb_node_collapses_to_the_textbook_section(seeded_db: Path) -> None:
    """库里真的有两个 `当归`：活血调经药下一个、补血药下一个，教材只认补血药。

    预期要按**教材**定，不能按库里顺序取第一个——seed 造出来的第一个恰恰是错的那个，
    拿它当预期，测试就会把「保留错的」当成正确行为。
    """

    with sqlite3.connect(seeded_db) as conn:
        correct_parent = conn.execute(
            "SELECT id FROM knowledge_node WHERE level = 'subtype' AND name = '补血药'"
        ).fetchone()[0]
        duplicates = conn.execute(
            "SELECT id FROM knowledge_node WHERE level = 'point' AND name = '当归'"
        ).fetchall()
        wrong_parent = conn.execute(
            "SELECT id FROM knowledge_node WHERE level = 'subtype' AND id != ? LIMIT 1",
            (correct_parent,),
        ).fetchone()[0]
    assert len(duplicates) == 2, "seed 应该造出两个同名当归节点，前提变了要重看这个测试"

    upsert_node(
        {"id": "d99.s9.p9", "name": "当归", "level": "point", "parent_id": wrong_parent},
        seeded_db,
    )

    _load_script("extend_knowledge_tree").extend(seeded_db)

    with sqlite3.connect(seeded_db) as conn:
        rows = conn.execute(
            "SELECT id, parent_id FROM knowledge_node WHERE level = 'point' AND name = '当归'"
        ).fetchall()
    assert len(rows) == 1, f"当归应该只剩一个节点，实际 {rows}"
    assert rows[0][1] == correct_parent, f"留下的应该是教材那一节（补血药）下的，实际 {rows}"


def test_point_with_references_is_kept_and_reported(seeded_db: Path) -> None:
    """`安息香` 教材没立条目，但有引用时不许删——删了用户的进度就没了。"""

    stale_id = _point_id(seeded_db, "安息香")
    with sqlite3.connect(seeded_db) as conn:
        conn.execute(
            "INSERT INTO exam_point(id, node_id, name, exam_years_json, frequency, source, created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            ("ep_stale", stale_id, "安息香", "[]", 0.3, "教材自编：测试", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()

    stats = _load_script("extend_knowledge_tree").extend(seeded_db)

    with sqlite3.connect(seeded_db) as conn:
        still_there = conn.execute(
            "SELECT COUNT(*) FROM knowledge_node WHERE id = ?", (stale_id,)
        ).fetchone()[0]
    assert still_there == 1
    assert {"id": stale_id, "name": "安息香", "references": 1} in stats["points_kept_with_references"]
    assert all(item["id"] != stale_id for item in stats["points_removed"])


def test_stale_point_without_references_is_retired(seeded_db: Path) -> None:
    stale_id = _point_id(seeded_db, "谷芽")

    stats = _load_script("extend_knowledge_tree").extend(seeded_db)

    with sqlite3.connect(seeded_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_node WHERE id = ?", (stale_id,)).fetchone()[0] == 0
    assert {"id": stale_id, "name": "谷芽"} in stats["points_removed"]


def test_section_rename_keeps_the_id(seeded_db: Path) -> None:
    """教材叫「祛风寒湿药」，seed 叫「祛风湿散寒药」——改名字，id 不动。"""

    before = _snapshot(seeded_db)
    with sqlite3.connect(seeded_db) as conn:
        old_id = conn.execute(
            "SELECT id FROM knowledge_node WHERE level = 'subtype' AND name = '祛风湿散寒药'"
        ).fetchone()[0]

    _load_script("extend_knowledge_tree").extend(seeded_db)

    after = _snapshot(seeded_db)
    assert old_id in after
    assert after[old_id][0] == "祛风寒湿药"
    assert before[old_id][0] == "祛风湿散寒药"


def test_tree_stays_a_dag_after_extension(seeded_db: Path) -> None:
    module = _load_script("extend_knowledge_tree")
    module.extend(seeded_db)

    with sqlite3.connect(seeded_db) as conn:
        edges = conn.execute(
            "SELECT from_node_id, to_node_id FROM prerequisite_edge WHERE order_sensitivity = 'strict'"
        ).fetchall()
    adjacency: dict[str, list[str]] = {}
    for source, target in edges:
        adjacency.setdefault(source, []).append(target)

    visiting: set[str] = set()
    done: set[str] = set()

    def walk(node: str) -> None:
        assert node not in visiting, f"先后边成环，经过 {node}"
        if node in done:
            return
        visiting.add(node)
        for nxt in adjacency.get(node, []):
            walk(nxt)
        visiting.discard(node)
        done.add(node)

    for node in list(adjacency):
        walk(node)


def test_nodes_are_reachable_from_a_domain(seeded_db: Path) -> None:
    """不许出现挂在空气里的节点：每个 point 都要能顺着 parent_id 走到 domain。"""

    _load_script("extend_knowledge_tree").extend(seeded_db)
    nodes = {node["id"]: node for node in list_nodes(seeded_db)}

    for node_id, node in nodes.items():
        current = node
        hops = 0
        while current.get("parent_id"):
            parent = nodes.get(str(current["parent_id"]))
            assert parent is not None, f"{node_id} 的祖先 {current['parent_id']} 不存在"
            current = parent
            hops += 1
            assert hops < 10, f"{node_id} 的父链过长，可能有环"
        assert current["level"] == "domain", f"{node_id} 的根不是 domain，而是 {current['level']}"
