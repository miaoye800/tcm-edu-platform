"""前置关系推导（``scripts/derive_prerequisites.py``）的契约。

守三件事：

1. **幂等**——树没变时跑第二遍新增必须是 0，否则「自动推导」会变成每天往边表里堆垃圾。
2. **不覆盖既有灵敏度**——``strict`` 与 ``free`` 决定学习计划能不能并行，
   推导脚本只能报冲突，不能顺手改掉。
3. **成环就跳过**——复用 ``path/store`` 的环检测，宁可少一条边，也不能让计划器拿到有环的图。
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from app.path.store import list_edges, list_nodes, upsert_node
from app.path.constants import HIERARCHY_REL_TYPE

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
    db_path = tmp_path / "derive.db"
    _load_script("seed_knowledge_tree").seed(db_path)
    return db_path


def _edge_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM prerequisite_edge").fetchone()[0])


def test_seeded_tree_needs_no_new_edges(seeded_db: Path) -> None:
    """种好的树再推一遍，新增必须是 0——否则说明推导口径和 seed 已经分叉。"""

    module = _load_script("derive_prerequisites")
    before = _edge_count(seeded_db)

    report = module.derive(seeded_db, dry_run=True)

    assert report["rules"]["R1"]["new"] == 0
    assert report["rules"]["R1"]["exists"] > 0
    # 没有 kg 实体表时 R3/R4 无从推导，但也不能报错。
    assert report["blocked"]["R3"]["total"] == 0
    assert _edge_count(seeded_db) == before, "dry-run 不许写库"


def test_missing_hierarchy_edge_is_derived(seeded_db: Path) -> None:
    """手工补进来的节点没有层级边时，推导要把它补上（real-write 模式）。"""

    upsert_node(
        {"id": "d99.p1", "name": "测试药", "level": "point", "parent_id": "d01.s1", "sort_hint": 99},
        seeded_db,
    )
    module = _load_script("derive_prerequisites")

    preview = module.derive(seeded_db, dry_run=True)
    assert preview["rules"]["R1"]["new"] >= 1
    # 预览阶段不写库：这条边此时还没落盘。
    assert ("d01.s1", "d99.p1") not in {
        (edge["from_node_id"], edge["to_node_id"])
        for edge in list_edges(db_path=seeded_db, rel_type=HIERARCHY_REL_TYPE)
    }

    report = module.derive(seeded_db, dry_run=False)
    assert report["written"] >= 1
    pairs = {
        (edge["from_node_id"], edge["to_node_id"])
        for edge in list_edges(db_path=seeded_db, rel_type=HIERARCHY_REL_TYPE)
    }
    assert ("d01.s1", "d99.p1") in pairs

    # 再跑一次：幂等。
    again = module.derive(seeded_db, dry_run=False)
    assert again["written"] == 0


def test_existing_sensitivity_is_reported_not_overwritten(seeded_db: Path) -> None:
    """顺序灵敏度冲突只报告，不改。改了会动到学习计划能不能并行的语义。"""

    before = {
        (edge["from_node_id"], edge["to_node_id"]): edge["order_sensitivity"]
        for edge in list_edges(db_path=seeded_db)
    }
    module = _load_script("derive_prerequisites")

    report = module.derive(seeded_db, dry_run=False)

    after = {
        (edge["from_node_id"], edge["to_node_id"]): edge["order_sensitivity"]
        for edge in list_edges(db_path=seeded_db)
    }
    assert report["rules"]["R2"]["conflict"] >= 1
    for pair, sensitivity in before.items():
        assert after[pair] == sensitivity, f"{pair} 的灵敏度被推导脚本改掉了"


def test_cycle_is_skipped(tmp_path: Path) -> None:
    """父子互指时只能写进一条，另一条判成环并跳过。"""

    db_path = tmp_path / "cycle.db"
    # 外键要求父节点先存在，所以先建两个孤儿，再把 parent 互相接上。
    upsert_node({"id": "a", "name": "A", "level": "domain"}, db_path)
    upsert_node({"id": "b", "name": "B", "level": "domain", "parent_id": "a"}, db_path)
    upsert_node({"id": "a", "name": "A", "level": "domain", "parent_id": "b"}, db_path)
    module = _load_script("derive_prerequisites")

    report = module.derive(db_path, dry_run=False)

    assert report["cycle_paths"], "父子互指必须被环检测挡住"
    assert report["written"] == 1
    assert _edge_count(db_path) == 1


def test_nodes_without_parent_are_ignored(tmp_path: Path) -> None:
    """根节点没有父，不能凭空给它造一条前置。"""

    db_path = tmp_path / "root.db"
    upsert_node({"id": "root", "name": "根", "level": "domain"}, db_path)
    module = _load_script("derive_prerequisites")

    report = module.derive(db_path, dry_run=True)

    assert report["rules"]["R1"]["new"] == 0
    assert list_nodes(db_path)[0]["parent_id"] is None
