"""离线预计算药材关系图谱，写入 SQLite。

Run from backend: ``python scripts/build_graph.py``

只走规则层（不调用 LLM），所以可重复执行、结果确定：同一中心节点的缓存被
覆盖，关系表靠 UNIQUE 约束去重。前端 /api/graph/{center_id} 读的就是这里写入
的 graph_cache。
"""

from __future__ import annotations

import argparse
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.graph_contract import GraphContent, GraphEdgeContent, GraphNodeContent, GraphSource
from app.agents.graph_intelligence import GraphAgent, entity_id
from app.graph_storage import init_db, put_entity, put_graph, put_relations, reset_init_cache
from app.kb.ingest import load_herbs
from app.profile.store import DB_PATH, connect

GLOBAL_CENTER_ID = "__global__"


def _node_ids_by_name(db_path: Path) -> dict[str, str]:
    """知识树节点名 → 节点 id。命中时用它当 center_id，前端 default-center 才能对上。"""

    with closing(connect(db_path)) as conn:
        return {str(name): str(node_id) for node_id, name in conn.execute("SELECT id,name FROM knowledge_node")}


def _ensure_relation_schema(db_path: Path) -> None:
    """旧库的 CHECK 约束不含「组成」；该表由本脚本全量重建，空表时直接换新。"""

    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='herb_relation'").fetchone()
        if row and "'组成'" not in str(row[0]):
            conn.execute("DROP TABLE herb_relation")
            conn.commit()
            reset_init_cache(db_path)
            init_db(db_path)


def _merge(contents: list[GraphContent], center_id: str) -> GraphContent:
    """把每个中心节点的子图合并成一张全图，sources 重新编号。"""

    nodes: dict[str, GraphNodeContent] = {}
    sources: list[GraphSource] = []
    source_index: dict[tuple[str, str, str], int] = {}
    edges: list[GraphEdgeContent] = []
    seen: set[tuple[str, str, str, str]] = set()

    for content in contents:
        nodes.update({node.id: node for node in content.nodes})
        local: list[int] = []
        for source in content.sources:
            key = (source.出处, source.原文摘录, source.定位)
            if key not in source_index:
                source_index[key] = len(sources)
                sources.append(source)
            local.append(source_index[key])
        for edge in content.edges:
            key = (edge.source, edge.target, str(edge.relation_type), edge.label)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                GraphEdgeContent(
                    source=edge.source,
                    target=edge.target,
                    relation_type=edge.relation_type,
                    label=edge.label,
                    source_ref=local[edge.source_ref],
                )
            )

    return GraphContent(center_id=center_id, nodes=list(nodes.values()), edges=edges, sources=sources)


def build(db_path: Path = DB_PATH, *, dry_run: bool = False, limit: int | None = None) -> dict[str, object]:
    _ensure_relation_schema(db_path)
    node_ids = _node_ids_by_name(db_path)
    agent = GraphAgent()
    created_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()

    contents: list[GraphContent] = []
    relation_count = 0
    skipped: list[str] = []
    herbs = load_herbs()
    if limit is not None:
        herbs = herbs[:limit]

    for herb in herbs:
        tree_id = node_ids.get(herb.name)
        center_id = tree_id or entity_id("herb", herb.name)
        built = agent.build_rules_only(center_id=center_id, center_name=herb.name)
        if built is None:
            skipped.append(herb.name)
            continue
        content, relations = built
        contents.append(content)
        relation_count += len(relations)
        if dry_run:
            continue

        payload = content.model_dump(mode="json")
        # 两个 key 指向同一份内容：知识树 id（学习路径用）与名字哈希（其它入口用）。
        for key in {center_id, entity_id("herb", herb.name)}:
            put_graph(key, payload, created_at, db_path)
        put_relations([item.model_dump(mode="json") for item in relations], created_at, db_path)
        for node in content.nodes:
            put_entity(
                node.id,
                node.name,
                node.type,
                created_at,
                # 只有真正挂在知识树上的中心节点才写外键，否则会触发 FK 约束。
                knowledge_node_id=tree_id if (tree_id and node.id == center_id) else None,
                db_path=db_path,
            )

    merged = _merge(contents, GLOBAL_CENTER_ID)
    if not dry_run:
        put_graph(GLOBAL_CENTER_ID, merged.model_dump(mode="json"), created_at, db_path)

    return {
        "herbs": len(contents),
        "skipped": skipped,
        "relations": relation_count,
        "global_nodes": len(merged.nodes),
        "global_edges": len(merged.edges),
        "global_sources": len(merged.sources),
        "seconds": round(time.perf_counter() - started, 2),
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="离线预计算药材关系图谱（不调用 LLM）")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="SQLite 路径，默认取应用配置")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 味药，便于排查")
    args = parser.parse_args()

    stats = build(args.db, dry_run=args.dry_run, limit=args.limit)
    print(f"药材        {stats['herbs']}")
    print(f"关系条数    {stats['relations']}")
    print(f"全图节点    {stats['global_nodes']}")
    print(f"全图边      {stats['global_edges']}")
    print(f"全图出处    {stats['global_sources']}")
    print(f"耗时        {stats['seconds']}s")
    if stats["skipped"]:
        print(f"跳过（知识库无此药） {', '.join(stats['skipped'])}")
    if stats["dry_run"]:
        print("dry-run：未写入数据库")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
