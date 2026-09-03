"""SQLite knowledge tree and prerequisite graph storage."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.path.constants import DEFAULT_REL_TYPE, HIERARCHY_REL_TYPE, KNOWLEDGE_LEVELS, ORDER_SENSITIVITIES
from app.profile.store import DB_PATH, connect


class PrerequisiteCycleError(ValueError):
    """Raised when a prerequisite write would make the graph cyclic."""

    def __init__(self, cycle_path: Sequence[str]) -> None:
        self.cycle_path = list(cycle_path)
        super().__init__(f"prerequisite graph contains a cycle: {' -> '.join(self.cycle_path)}")


def init_db(db_path: Path = DB_PATH) -> None:
    """Create path-owned tables. CREATE IF NOT EXISTS keeps existing installations compatible."""

    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_node (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                level TEXT NOT NULL CHECK(level IN ('domain', 'subtype', 'point')),
                parent_id TEXT REFERENCES knowledge_node(id),
                description TEXT NOT NULL DEFAULT '',
                exam_frequency REAL NOT NULL DEFAULT 0 CHECK(exam_frequency >= 0 AND exam_frequency <= 1),
                sort_hint INTEGER NOT NULL DEFAULT 0,
                extra_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prerequisite_edge (
                from_node_id TEXT NOT NULL REFERENCES knowledge_node(id),
                to_node_id TEXT NOT NULL REFERENCES knowledge_node(id),
                rel_type TEXT NOT NULL DEFAULT 'prerequisite',
                order_sensitivity TEXT NOT NULL CHECK(order_sensitivity IN ('strict', 'free')),
                reason TEXT NOT NULL,
                PRIMARY KEY(from_node_id, to_node_id, rel_type)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_node_parent ON knowledge_node(parent_id, sort_hint)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prerequisite_edge_from ON prerequisite_edge(from_node_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prerequisite_edge_to ON prerequisite_edge(to_node_id)")
        conn.commit()


def upsert_node(node: Mapping[str, Any], db_path: Path = DB_PATH) -> None:
    """Insert or update one deterministic node ID."""

    level = str(node["level"])
    if level not in KNOWLEDGE_LEVELS:
        raise ValueError(f"unsupported knowledge level: {level}")
    exam_frequency = float(node.get("exam_frequency", 0))
    if not 0 <= exam_frequency <= 1:
        raise ValueError("exam_frequency must be between 0 and 1")
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO knowledge_node(id, name, level, parent_id, description, exam_frequency, sort_hint, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, level=excluded.level,
                parent_id=excluded.parent_id, description=excluded.description,
                exam_frequency=excluded.exam_frequency, sort_hint=excluded.sort_hint, extra_json=excluded.extra_json
            """,
            (node["id"], node["name"], level, node.get("parent_id"), node.get("description", ""), exam_frequency,
             int(node.get("sort_hint", 0)), json.dumps(node.get("extra", {}), ensure_ascii=False, sort_keys=True)),
        )
        conn.commit()


def upsert_edge(edge: Mapping[str, Any], db_path: Path = DB_PATH) -> None:
    """Write an edge only when the resulting complete graph remains a DAG."""

    sensitivity = str(edge["order_sensitivity"])
    if sensitivity not in ORDER_SENSITIVITIES:
        raise ValueError(f"unsupported order sensitivity: {sensitivity}")
    rel_type = str(edge.get("rel_type", DEFAULT_REL_TYPE))
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute("BEGIN")
        # Parent-child containment is represented by knowledge_node.parent_id.
        # Remove the legacy semantic edge before recording its graph-reuse view.
        if rel_type == HIERARCHY_REL_TYPE:
            conn.execute(
                "DELETE FROM prerequisite_edge WHERE from_node_id = ? AND to_node_id = ? AND rel_type = ?",
                (edge["from_node_id"], edge["to_node_id"], DEFAULT_REL_TYPE),
            )
        conn.execute(
            """INSERT INTO prerequisite_edge(from_node_id, to_node_id, rel_type, order_sensitivity, reason)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(from_node_id, to_node_id, rel_type) DO UPDATE SET
                   order_sensitivity=excluded.order_sensitivity, reason=excluded.reason""",
            (edge["from_node_id"], edge["to_node_id"], rel_type, sensitivity, edge["reason"]),
        )
        cycle = find_cycle(_edge_rows(conn))
        if cycle:
            conn.rollback()
            raise PrerequisiteCycleError(cycle)
        conn.commit()


def list_tree(domain_id: str | None = None, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute("SELECT id, name, level, parent_id, description, exam_frequency, sort_hint, extra_json FROM knowledge_node ORDER BY sort_hint, id").fetchall()
    nodes = [_node_row(row) for row in rows]
    by_id = {node["id"]: node for node in nodes}
    if domain_id is not None:
        root = by_id.get(domain_id)
        if root is None or root["level"] != "domain":
            return []
        wanted = _descendant_ids(domain_id, nodes)
        nodes = [node for node in nodes if node["id"] in wanted]
    for node in nodes:
        node["children"] = []
    result: list[dict[str, Any]] = []
    available = {node["id"]: node for node in nodes}
    for node in nodes:
        if node["parent_id"] in available:
            available[node["parent_id"]]["children"].append(node)
        elif node["level"] == "domain":
            result.append(node)
    return result


def list_nodes(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """Return the flat knowledge catalog used by the structured planner."""

    init_db(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """SELECT id, name, level, parent_id, description, exam_frequency,
                      sort_hint, extra_json
               FROM knowledge_node ORDER BY sort_hint, id"""
        ).fetchall()
    return [
        {key: value for key, value in _node_row(row).items() if key != "children"}
        for row in rows
    ]


def list_edges(
    domain_id: str | None = None,
    db_path: Path = DB_PATH,
    rel_type: str = DEFAULT_REL_TYPE,
) -> list[dict[str, Any]]:
    """List one semantic edge type; public callers default to true prerequisites."""

    init_db(db_path)
    with closing(connect(db_path)) as conn:
        edges = _edge_rows(conn, rel_type=rel_type)
        if domain_id is not None:
            node_rows = conn.execute("SELECT id, parent_id FROM knowledge_node").fetchall()
            ids = _descendant_ids(domain_id, [{"id": row[0], "parent_id": row[1]} for row in node_rows])
            edges = [edge for edge in edges if edge["from_node_id"] in ids and edge["to_node_id"] in ids]
    return edges


def find_cycle(edges: Iterable[Mapping[str, Any]]) -> list[str] | None:
    """Return one directed cycle path, or None for a DAG."""

    graph: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        graph[str(edge["from_node_id"])].append(str(edge["to_node_id"]))
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node_id: str) -> list[str] | None:
        state[node_id] = 1
        stack.append(node_id)
        for child in graph[node_id]:
            if state.get(child) == 1:
                return stack[stack.index(child):] + [child]
            if state.get(child, 0) == 0:
                cycle = visit(child)
                if cycle:
                    return cycle
        stack.pop()
        state[node_id] = 2
        return None

    for node_id in sorted(graph):
        if state.get(node_id, 0) == 0:
            cycle = visit(node_id)
            if cycle:
                return cycle
    return None


def topo_sort(nodes: Iterable[Mapping[str, Any] | str], edges: Iterable[Mapping[str, Any]]) -> list[list[str]]:
    """Return strict-order layers; IDs in one layer can run in parallel (including free-linked nodes)."""

    node_ids = [str(node["id"]) if isinstance(node, Mapping) else str(node) for node in nodes]
    unique_ids = list(dict.fromkeys(node_ids))
    edge_list = [
        edge for edge in edges
        if edge.get("rel_type", DEFAULT_REL_TYPE) == DEFAULT_REL_TYPE
    ]
    strict_edges = [edge for edge in edge_list if edge.get("order_sensitivity") == "strict"]
    if cycle := find_cycle(strict_edges):
        raise PrerequisiteCycleError(cycle)

    # A free edge is a planning hint: its connected nodes should be offered in
    # the same parallel batch once every strict predecessor of the batch is done.
    parent = {node_id: node_id for node_id in unique_ids}

    def find(node_id: str) -> str:
        while parent[node_id] != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for edge in edge_list:
        start, end = str(edge["from_node_id"]), str(edge["to_node_id"])
        if edge.get("order_sensitivity") == "free" and start in parent and end in parent:
            union(start, end)
    groups: dict[str, list[str]] = defaultdict(list)
    for node_id in unique_ids:
        groups[find(node_id)].append(node_id)
    children: dict[str, list[str]] = defaultdict(list)
    indegree = {group_id: 0 for group_id in groups}
    for edge in strict_edges:
        start, end = str(edge["from_node_id"]), str(edge["to_node_id"])
        if start in parent and end in parent:
            source, target = find(start), find(end)
            if source == target:
                raise ValueError("a strict prerequisite cannot be inside one free parallel group")
            if target not in children[source]:
                children[source].append(target)
                indegree[target] += 1
    layers: list[list[str]] = []
    ready = sorted(group_id for group_id, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        layer = sorted(node_id for group_id in ready for node_id in groups[group_id])
        layers.append(layer)
        visited += len(layer)
        following: list[str] = []
        for group_id in ready:
            for child in children[group_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    following.append(child)
        ready = sorted(following)
    if visited != len(unique_ids):
        raise PrerequisiteCycleError(find_cycle(strict_edges) or [])
    return layers


def _edge_rows(conn: sqlite3.Connection, rel_type: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT from_node_id, to_node_id, rel_type, order_sensitivity, reason FROM prerequisite_edge"
    params: tuple[str, ...] = ()
    if rel_type is not None:
        sql += " WHERE rel_type = ?"
        params = (rel_type,)
    rows = conn.execute(sql + " ORDER BY from_node_id, to_node_id", params).fetchall()
    return [dict(zip(("from_node_id", "to_node_id", "rel_type", "order_sensitivity", "reason"), row, strict=True)) for row in rows]


def _node_row(row: Sequence[Any]) -> dict[str, Any]:
    return {"id": row[0], "name": row[1], "level": row[2], "parent_id": row[3], "description": row[4], "exam_frequency": row[5], "sort_hint": row[6], "extra": json.loads(row[7]), "children": []}


def _descendant_ids(root_id: str, nodes: Iterable[Mapping[str, Any]]) -> set[str]:
    children: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.get("parent_id") is not None:
            children[str(node["parent_id"])].append(str(node["id"]))
    result, pending = {root_id}, [root_id]
    while pending:
        parent = pending.pop()
        for child in children[parent]:
            if child not in result:
                result.add(child)
                pending.append(child)
    return result
