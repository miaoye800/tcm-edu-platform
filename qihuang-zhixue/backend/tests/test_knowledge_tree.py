from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from app.path.store import PrerequisiteCycleError, list_edges, list_tree, topo_sort, upsert_edge, upsert_node


def _seed_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "seed_knowledge_tree.py"
    spec = importlib.util.spec_from_file_location("seed_knowledge_tree", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_tree_has_three_complete_levels(tmp_path: Path) -> None:
    db_path = tmp_path / "knowledge.db"
    _seed_module().seed(db_path)
    domains = list_tree(db_path=db_path)
    assert len(domains) == 19
    for domain in domains:
        assert domain["level"] == "domain"
        assert domain["children"]
        for subtype in domain["children"]:
            assert subtype["level"] == "subtype"
            assert len(subtype["children"]) >= 3
            assert all(point["level"] == "point" for point in subtype["children"])


def test_cycle_edge_is_rejected_and_reports_path(tmp_path: Path) -> None:
    db_path = tmp_path / "knowledge.db"
    for node_id in ("a", "b", "c"):
        upsert_node({"id": node_id, "name": node_id, "level": "domain"}, db_path)
    upsert_edge({"from_node_id": "a", "to_node_id": "b", "order_sensitivity": "strict", "reason": "test"}, db_path)
    upsert_edge({"from_node_id": "b", "to_node_id": "c", "order_sensitivity": "free", "reason": "test"}, db_path)
    with pytest.raises(PrerequisiteCycleError, match=r"a -> b -> c -> a"):
        upsert_edge({"from_node_id": "c", "to_node_id": "a", "order_sensitivity": "strict", "reason": "test"}, db_path)
    assert [(edge["from_node_id"], edge["to_node_id"]) for edge in list_edges(db_path=db_path)] == [("a", "b"), ("b", "c")]


def test_topo_sort_obeys_strict_edges_and_groups_free_nodes() -> None:
    nodes = ["total", "container", "a", "b", "c"]
    edges = [
        {"from_node_id": "total", "to_node_id": "container", "rel_type": "hierarchy", "order_sensitivity": "strict"},
        {"from_node_id": "total", "to_node_id": "a", "order_sensitivity": "strict"},
        {"from_node_id": "total", "to_node_id": "b", "order_sensitivity": "strict"},
        {"from_node_id": "a", "to_node_id": "c", "order_sensitivity": "free"},
    ]
    stages = topo_sort(nodes, edges)
    positions = {node_id: index for index, stage in enumerate(stages) for node_id in stage}
    assert positions["total"] < positions["a"]
    assert positions["total"] < positions["b"]
    assert positions["container"] == positions["total"]
    assert positions["a"] == positions["b"]
    assert positions["a"] == positions["c"]


def test_seed_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "knowledge.db"
    seed = _seed_module().seed
    first = seed(db_path)
    second = seed(db_path)
    assert first == second
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_node").fetchone()[0] == first[0]
        assert conn.execute("SELECT COUNT(*) FROM prerequisite_edge").fetchone()[0] == first[1]


def test_seed_marks_hierarchy_separately_and_default_edges_are_prerequisites(tmp_path: Path) -> None:
    db_path = tmp_path / "knowledge.db"
    _seed_module().seed(db_path)
    default_edges = list_edges(db_path=db_path)
    assert default_edges
    assert {edge["rel_type"] for edge in default_edges} == {"prerequisite"}
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM prerequisite_edge WHERE rel_type = 'hierarchy'").fetchone()[0] > 0
        assert conn.execute("SELECT exam_frequency FROM knowledge_node WHERE name = '麻黄'").fetchone()[0] == 0.95
        assert conn.execute("SELECT exam_frequency FROM knowledge_node WHERE name = '雷丸'").fetchone()[0] == 0.2
