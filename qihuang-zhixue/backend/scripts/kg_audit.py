"""知识图谱基线审计：把「图谱为什么假」变成可复现的数字。

Run from backend:

    python scripts/kg_audit.py
    python scripts/kg_audit.py --json
    python scripts/kg_audit.py --db .data/qihuang.db

这个脚本只读，不写库、不调 LLM。重构前后的对比基线就靠它。

输出六块：
1. 实体（graph_entity 按类型）
2. 关系（herb_relation 按 relation_type，并按 T1/T2/T3 汇总）
3. 幽灵节点（名字是原始 id 的实体）
4. 知识树 ↔ 知识库对齐（交集 / 树无药 / 药无树）
5. 缓存健康（graph_cache / graph_detail_cache / __global__）
6. 可解析中心（前端点开不会掉 mock 的比例）—— 这是「假图率」的直接度量
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.kg.constants import Tier, tier_of
from app.kb.ingest import load_herbs
from app.profile.store import DB_PATH, connect

GLOBAL_CENTER_ID = "__global__"

#: 名字里出现这些前缀说明是原始 id 直接当显示名用了，不是真名字。
_RAW_ID_PREFIXES = ("herb_", "formula_", "syn_", "syndrome_", "category_", "node_")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _table_exists(conn: Any, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _count(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _looks_like_raw_id(name: str) -> bool:
    """显示名是原始 id 的判定：以已知前缀开头，或完全没有汉字。"""

    stripped = name.strip()
    if not stripped:
        return True
    if stripped.lower().startswith(_RAW_ID_PREFIXES):
        return True
    return not _CJK.search(stripped)


def audit(db_path: Path) -> dict[str, Any]:
    """采集全部基线指标。任何一块表缺失都返回 0 而不是抛错。"""

    herbs = load_herbs()
    herb_names = {herb.name for herb in herbs}
    herb_ids = {herb.id for herb in herbs}

    with closing(connect(db_path)) as conn:
        # ---- 1. 实体 ----
        #
        # 同样优先读 kg_entity。graph_entity 是改造前的旧表，里面的 id 早就
        # 不在用了，拿它统计幽灵节点只会报出改造前的老问题。
        entities: Counter[str] = Counter()
        ghost_rows: list[dict[str, str]] = []
        if _table_exists(conn, "kg_entity"):
            for kind, name, entity_id in conn.execute(
                "SELECT kind, name, id FROM kg_entity"
            ):
                entities[str(kind)] += 1
                if _looks_like_raw_id(str(name)):
                    ghost_rows.append(
                        {"id": str(entity_id), "name": str(name), "type": str(kind)}
                    )
        elif _table_exists(conn, "graph_entity"):
            for entity_type, name, entity_id in conn.execute(
                "SELECT entity_type, name, id FROM graph_entity"
            ):
                entities[str(entity_type)] += 1
                if _looks_like_raw_id(str(name)):
                    ghost_rows.append(
                        {"id": str(entity_id), "name": str(name), "type": str(entity_type)}
                    )

        # ---- 2. 关系 ----
        #
        # 优先读 ``kg_edge``（P2 之后的新图）。``herb_relation`` 是**改造前**的
        # 旧表，还躺在库里没删；只读它会把「915/1571 是相似边」这种改造前的
        # 结论当成现状打印出来，看着像 P2–P4 白做了。
        relation_counts: Counter[str] = Counter()
        if _table_exists(conn, "kg_edge"):
            for (relation_type,) in conn.execute("SELECT relation_type FROM kg_edge"):
                relation_counts[str(relation_type)] += 1
        elif _table_exists(conn, "herb_relation"):
            for (relation_type,) in conn.execute(
                "SELECT relation_type FROM herb_relation"
            ):
                relation_counts[str(relation_type)] += 1

        # ---- 4. 知识树 ↔ 知识库 ----
        tree_points: list[str] = []
        tree_all = 0
        if _table_exists(conn, "knowledge_node"):
            tree_all = _count(conn, "SELECT COUNT(*) FROM knowledge_node")
            tree_points = [
                str(name)
                for (name,) in conn.execute(
                    "SELECT name FROM knowledge_node WHERE level='point'"
                )
            ]
        tree_point_set = set(tree_points)

        # ---- 5. 缓存 ----
        cache_count = (
            _count(conn, "SELECT COUNT(*) FROM graph_cache")
            if _table_exists(conn, "graph_cache")
            else 0
        )
        detail_cache_count = (
            _count(conn, "SELECT COUNT(*) FROM graph_detail_cache")
            if _table_exists(conn, "graph_detail_cache")
            else 0
        )
        has_global = False
        cached_center_ids: set[str] = set()
        if _table_exists(conn, "graph_cache"):
            has_global = (
                conn.execute(
                    "SELECT 1 FROM graph_cache WHERE center_id=?", (GLOBAL_CENTER_ID,)
                ).fetchone()
                is not None
            )
            cached_center_ids = {
                str(center_id)
                for (center_id,) in conn.execute("SELECT center_id FROM graph_cache")
            }

        # ---- 6. 可解析中心 ----
        # 前端链路：default-center 给树 id → /api/graph/{树id} → 用节点名去知识库找药。
        # 找不到就 404，前端回落 mock。所以「树节点名 ∈ 知识库药名」才是真能打开的比例。
        point_ids = [
            (str(node_id), str(name))
            for node_id, name in conn.execute(
                "SELECT id, name FROM knowledge_node WHERE level='point'"
            )
        ] if _table_exists(conn, "knowledge_node") else []

    resolvable_points = [(nid, name) for nid, name in point_ids if name in herb_names]
    unresolvable_points = [(nid, name) for nid, name in point_ids if name not in herb_names]
    cached_and_resolvable = [
        (nid, name) for nid, name in resolvable_points if nid in cached_center_ids
    ]

    tier_counts: Counter[int] = Counter()
    for relation_type, count in relation_counts.items():
        tier_counts[int(tier_of(relation_type))] += count
    total_edges = sum(relation_counts.values())

    return {
        "db_path": str(db_path),
        "entities": {
            "total": sum(entities.values()),
            "by_type": dict(sorted(entities.items())),
        },
        "edges": {
            "total": total_edges,
            "by_relation": dict(sorted(relation_counts.items(), key=lambda kv: -kv[1])),
            "by_tier": {f"T{tier}": tier_counts.get(tier, 0) for tier in (1, 2, 3)},
            "similarity_share": round(tier_counts.get(int(Tier.SIMILARITY), 0) / total_edges, 4)
            if total_edges
            else 0.0,
            "never_produced": sorted(
                {
                    "相使",
                    "相畏",
                    "相杀",
                    "相恶",
                    "性味相反",
                }
                - set(relation_counts)
            ),
        },
        "ghosts": {
            "count": len(ghost_rows),
            "items": ghost_rows,
        },
        "alignment": {
            "tree_nodes": tree_all,
            "tree_points": len(tree_points),
            "tree_points_unique": len(tree_point_set),
            "herbs": len(herbs),
            "overlap": len(tree_point_set & herb_names),
            "tree_without_herb": len(tree_point_set - herb_names),
            "herb_without_tree": len(herb_names - tree_point_set),
            "tree_without_herb_sample": sorted(tree_point_set - herb_names)[:15],
            "herb_without_tree_sample": sorted(herb_names - tree_point_set)[:15],
            "herb_ids_missing_from_kb": sorted(
                {g["name"] for g in ghost_rows if g["type"] == "herb"} - herb_names - herb_ids
            )[:15],
        },
        "cache": {
            "graph_cache": cache_count,
            "graph_detail_cache": detail_cache_count,
            "has_global_snapshot": has_global,
            "distinct_centers": len(cached_center_ids),
        },
        "resolvable": {
            "points_total": len(point_ids),
            "points_resolvable": len(resolvable_points),
            "points_unresolvable": len(unresolvable_points),
            "points_cached_and_resolvable": len(cached_and_resolvable),
            "fake_graph_rate": round(len(unresolvable_points) / len(point_ids), 4)
            if point_ids
            else 0.0,
        },
    }


def _print_report(report: dict[str, Any]) -> None:
    e = report["entities"]
    edges = report["edges"]
    align = report["alignment"]
    cache = report["cache"]
    res = report["resolvable"]

    print(f"数据库：{report['db_path']}")
    print()
    print("== 1. 实体 ==")
    print(f"  总计 {e['total']}：{e['by_type']}")
    print()
    print("== 2. 关系 ==")
    print(f"  总计 {edges['total']}，分层 {edges['by_tier']}")
    print(f"  T3 相似边占比：{edges['similarity_share']:.1%}  ← 越高越「大乱炖」")
    for relation_type, count in edges["by_relation"].items():
        tier = int(tier_of(relation_type))
        print(f"    T{tier}  {relation_type:<8} {count:>5}")
    if edges["never_produced"]:
        print(f"  定义了但产出 0 条：{edges['never_produced']}")
    print()
    print("== 3. 幽灵节点（名字是原始 id） ==")
    print(f"  {report['ghosts']['count']} 个")
    for item in report["ghosts"]["items"]:
        print(f"    {item['id']}  name={item['name']}  type={item['type']}")
    print()
    print("== 4. 知识树 ↔ 知识库对齐 ==")
    print(
        f"  树节点 {align['tree_nodes']}（point {align['tree_points']}，去重 {align['tree_points_unique']}）"
        f" / 药材 {align['herbs']}"
    )
    print(f"  名字交集 {align['overlap']}")
    print(f"  树有药无 {align['tree_without_herb']}   药有树无 {align['herb_without_tree']}")
    if align["tree_without_herb_sample"]:
        print(f"    树有药无示例：{'、'.join(align['tree_without_herb_sample'])}")
    if align["herb_without_tree_sample"]:
        print(f"    药有树无示例：{'、'.join(align['herb_without_tree_sample'])}")
    print()
    print("== 5. 缓存 ==")
    print(
        f"  graph_cache {cache['graph_cache']} 条（去重中心 {cache['distinct_centers']}）"
        f" / graph_detail_cache {cache['graph_detail_cache']} 条"
    )
    print(f"  全图快照 __global__：{'有' if cache['has_global_snapshot'] else '无'}")
    print()
    print("== 6. 可解析中心（决定前端会不会掉 mock） ==")
    print(
        f"  树 point {res['points_total']} 个，能解析到药材 {res['points_resolvable']} 个"
        f"，解析不到 {res['points_unresolvable']} 个"
    )
    print(f"  既有缓存又能解析：{res['points_cached_and_resolvable']} 个")
    print(f"  假图率（点开即回落 mock）：{res['fake_graph_rate']:.1%}")
    print()

    total = edges["total"] or 1
    similar = edges["by_tier"]["T3"]
    print("== 结论 ==")
    print(
        f"  {similar}/{total}（{similar / total:.1%}）的边是「同归经/同功效/同性味」相似边，"
        "回答不了「能不能一起用 / 该往哪走 / 差在哪 / 我为什么错」中的任何一个。"
    )
    print(
        f"  知识树与知识库名字只对上 {align['overlap']}/{align['tree_points_unique']}，"
        f"所以 {res['points_unresolvable']} 个树节点点开必然 404 → 前端回落 mock。"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="知识图谱基线审计（只读）")
    parser.add_argument("--db", default=None, help="SQLite 路径，默认用 QIHUANG_DB_PATH")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    db_path = Path(args.db) if args.db else DB_PATH
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 2

    report = audit(db_path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
