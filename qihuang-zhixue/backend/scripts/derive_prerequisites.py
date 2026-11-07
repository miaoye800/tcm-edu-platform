"""前置关系自动推导（P6 第 4 项）。

四条规则（与重构方案 §P6 一致，规则编号沿用方案里的简称）：

R1 父子层级 **strict** —— ``knowledge_node.parent_id`` 决定「先学父节点，再学子节点」。
R2 同 subtype 顺序 **free** —— 同一 subtype 下按 ``sort_hint`` 相邻，只表示建议顺序，
   学生可以按自己的复习节奏互换，不锁死。
R3 方剂组成依赖 **strict** —— 一张方剂的每一味组成药，其知识树节点必须排在该方剂节点之前。
R4 证候依赖 **free** —— 证候 → 关联方剂，可以交叉复习，不设硬先后。

为什么要有这个脚本，而不是继续在 ``seed_knowledge_tree.py`` 里写死：

- 树是会长大的（``extend_knowledge_tree.py``）。新加的节点如果只跑 ``build_kg.py``，
  前置边不会自动补上，学习计划就会出现「没有任何前置的孤点」——它不是「可以直接学」，
  而是「没人告诉系统该先学什么」。这两件事必须能分开看见。
- 规则只依赖 ``knowledge_node.parent_id`` / ``sort_hint`` 和 ``kg_edge`` 的组成关系，
  属于**纯推导**：同一份输入跑两次结果完全一样，不需要人工维护边表。

为什么 R3/R4 现在大概率产出 0 条：

它们的端点是**方剂/证候的知识树节点**，而当前知识树只覆盖中药学 21 章的**药材**
（domain/subtype/point 三级，共 209 个 point，无方剂、无证候节点）。所以脚本不编造端点，
而是把「有多少条关系因为端点不在树里而推不出来」如实报成 ``blocked``——按项目红线，
「不知道」必须显式说出来。

安全约定：

- 一律走 ``app/path/store.upsert_edge``，复用它的事务 + DAG 环检测；成环的边会被拒绝并回滚。
- 已存在的边**不覆盖**：灵敏度（strict/free）不一致只报 ``conflict``。
  改灵敏度会改变学习计划的排布语义（strict = 必须按序，free = 可并行），
  那是需要人工确认的迁移，不该由推导脚本顺手改掉。
- ``--dry-run`` 打印新增/冲突/环路径，不写库。

Run from backend:

    python scripts/derive_prerequisites.py --dry-run
    python scripts/derive_prerequisites.py
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Mapping

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.path import store as path_store
from app.path.constants import DEFAULT_REL_TYPE, HIERARCHY_REL_TYPE
from app.profile.store import DB_PATH, connect

#: 规则元数据。``sensitivity`` 是方案里写定的顺序灵敏度，不要在别处另立常量。
RULES: dict[str, dict[str, str]] = {
    "R1": {
        "name": "父子层级",
        "rel_type": HIERARCHY_REL_TYPE,
        "sensitivity": "strict",
        "reason": "父节点是子节点的上位概念，未掌握父节点时子节点的定位无从谈起",
    },
    "R2": {
        "name": "同章节顺序",
        "rel_type": DEFAULT_REL_TYPE,
        "sensitivity": "free",
        "reason": "同一子类下的药材可按个人复习安排互换顺序",
    },
    "R3": {
        "name": "方剂组成依赖",
        "rel_type": DEFAULT_REL_TYPE,
        "sensitivity": "strict",
        "reason": "组成药是理解方剂配伍的前提，必须先掌握",
    },
    "R4": {
        "name": "证候依赖",
        "rel_type": DEFAULT_REL_TYPE,
        "sensitivity": "free",
        "reason": "证候与方剂可交叉复习，不设硬性先后",
    },
}


# --------------------------------------------------------------------------- #
# 规则：候选边
# --------------------------------------------------------------------------- #


def r1_hierarchy(nodes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """父 → 子，strict。三个层级（domain / subtype / point）都覆盖。"""

    pairs = []
    for node in nodes:
        parent = node.get("parent_id")
        if not parent:
            continue
        pairs.append(
            _edge(
                "R1", parent, str(node["id"]),
                f"{RULES['R1']['reason']}（{node['name']}）",
            )
        )
    return pairs


def r2_sibling_order(nodes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """同一父节点下按 ``sort_hint`` 相邻成对，free。

    只连**相邻**两个，不连「每一个都比后面所有的大」——否则一个 30 味药的子类会
    产生 435 条边，学习计划里全是冗余约束，而且任意两味之间都被判定成「有先后」，
    等于把「建议顺序」退化成「必须顺序」。
    """

    siblings: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for node in nodes:
        parent = node.get("parent_id")
        if not parent:
            continue
        siblings[str(parent)].append(
            (int(node.get("sort_hint") or 0), str(node["id"]), str(node["name"]))
        )

    pairs = []
    for parent, items in siblings.items():
        items.sort()
        for left, right in zip(items, items[1:]):
            pairs.append(
                _edge(
                    "R2", left[1], right[1],
                    f"{RULES['R2']['reason']}（{left[2]} → {right[2]}）",
                )
            )
    return pairs


def r3_formula_composition(
    db_path: Path, node_of: Mapping[str, str | None], tree: set[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """组成药节点 → 方剂节点，strict。端点不在树里的记为 blocked。"""

    pairs: list[dict[str, Any]] = []
    blocked = {"total": 0, "herb_without_node": 0, "formula_without_node": 0, "samples": []}

    for herb_id, herb_name, formula_id, formula_name in _composition_edges(db_path):
        blocked["total"] += 1
        source = node_of.get(herb_id)
        target = node_of.get(formula_id)
        if not source:
            blocked["herb_without_node"] += 1
            _remember(blocked, f"药材「{herb_name}」未挂知识树节点")
            continue
        if not target:
            blocked["formula_without_node"] += 1
            _remember(blocked, f"方剂「{formula_name}」未挂知识树节点")
            continue
        if source not in tree or target not in tree:
            blocked["formula_without_node"] += 1
            _remember(blocked, f"「{herb_name}」或「{formula_name}」的节点 id 已不存在")
            continue
        pairs.append(
            _edge(
                "R3", source, target,
                f"{RULES['R3']['reason']}（{formula_name} 含 {herb_name}）",
            )
        )
    return pairs, blocked


def r4_syndrome_dependency(
    db_path: Path, node_of: Mapping[str, str | None], tree: set[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """证候节点 → 方剂节点，free。端点不在树里的记为 blocked。"""

    pairs: list[dict[str, Any]] = []
    blocked = {"total": 0, "syndrome_without_node": 0, "formula_without_node": 0, "samples": []}

    for syndrome_id, syndrome_name, formula_id, formula_name in _syndrome_formula_edges(db_path):
        blocked["total"] += 1
        source = node_of.get(syndrome_id)
        target = node_of.get(formula_id)
        if not source:
            blocked["syndrome_without_node"] += 1
            _remember(blocked, f"证候「{syndrome_name}」未挂知识树节点")
            continue
        if not target:
            blocked["formula_without_node"] += 1
            _remember(blocked, f"方剂「{formula_name}」未挂知识树节点")
            continue
        if source not in tree or target not in tree:
            blocked["syndrome_without_node"] += 1
            _remember(blocked, f"「{syndrome_name}」或「{formula_name}」的节点 id 已不存在")
            continue
        pairs.append(
            _edge(
                "R4", source, target,
                f"{RULES['R4']['reason']}（{syndrome_name} 常用 {formula_name}）",
            )
        )
    return pairs, blocked


# --------------------------------------------------------------------------- #
# 比对与写入
# --------------------------------------------------------------------------- #


def derive(db_path: Path = DB_PATH, *, dry_run: bool = True) -> dict[str, Any]:
    """跑四条规则，返回可打印的报告。``dry_run=False`` 时把新增边写库。"""

    nodes = path_store.list_nodes(db_path)
    tree = {str(node["id"]) for node in nodes}
    node_of = _kg_node_map(db_path)

    existing: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in _all_edges(db_path):
        existing[(str(edge["from_node_id"]), str(edge["to_node_id"]))] = edge

    candidates: list[dict[str, Any]] = []
    blocked: dict[str, dict[str, Any]] = {}
    for builder in (r1_hierarchy, r2_sibling_order):
        candidates.extend(builder(nodes))
    for rule, (pairs, miss) in (
        ("R3", r3_formula_composition(db_path, node_of, tree)),
        ("R4", r4_syndrome_dependency(db_path, node_of, tree)),
    ):
        candidates.extend(pairs)
        blocked[rule] = miss

    report: dict[str, Any] = {
        "nodes": len(nodes),
        "existing_edges": len(existing),
        "rules": {code: {"new": 0, "exists": 0, "conflict": 0, "cycle": 0} for code in RULES},
        "blocked": blocked,
        "written": 0,
        "cycle_paths": [],
        "conflicts": [],
        "dry_run": dry_run,
    }

    # 环检测在内存里做：把候选边逐条加进现有边表，加一条判一次，
    # 这样报出来的是「具体哪一条会成环」，而不是笼统的「图有环」。
    graph: dict[str, list[str]] = defaultdict(list)
    for (src, dst) in existing:
        graph[src].append(dst)

    accepted: list[dict[str, Any]] = []
    for candidate in candidates:
        code = candidate["rule"]
        key = (candidate["from_node_id"], candidate["to_node_id"])
        if key in existing:
            current = existing[key]
            if str(current.get("rel_type")) != candidate["rel_type"] or str(
                current.get("order_sensitivity")
            ) != candidate["sensitivity"]:
                report["rules"][code]["conflict"] += 1
                report["conflicts"].append(
                    {
                        "rule": code,
                        "pair": key,
                        "expected": f"{candidate['rel_type']}/{candidate['sensitivity']}",
                        "actual": f"{current.get('rel_type')}/{current.get('order_sensitivity')}",
                        "reason": str(current.get("reason") or ""),
                    }
                )
            else:
                report["rules"][code]["exists"] += 1
            continue

        graph[candidate["from_node_id"]].append(candidate["to_node_id"])
        cycle = path_store.find_cycle(
            [
                {"from_node_id": src, "to_node_id": dst}
                for src, targets in graph.items()
                for dst in targets
            ]
        )
        if cycle:
            graph[candidate["from_node_id"]].pop()
            report["rules"][code]["cycle"] += 1
            report["cycle_paths"].append({"rule": code, "cycle": cycle})
            continue

        report["rules"][code]["new"] += 1
        accepted.append(candidate)

    if not dry_run:
        report["written"] = _write(accepted, db_path, report)
    return report


def _write(
    accepted: Iterable[dict[str, Any]], db_path: Path, report: dict[str, Any]
) -> int:
    """逐条写库。``upsert_edge`` 自带环检测与回滚，这里只负责统计失败。"""

    written = 0
    for edge in accepted:
        try:
            path_store.upsert_edge(
                {
                    "from_node_id": edge["from_node_id"],
                    "to_node_id": edge["to_node_id"],
                    "rel_type": edge["rel_type"],
                    "order_sensitivity": edge["sensitivity"],
                    "reason": edge["reason"],
                },
                db_path,
            )
        except path_store.PrerequisiteCycleError as error:
            # 内存里已经判过环，走到这里说明现有边表在两次读取之间变过。
            report["cycle_paths"].append({"rule": edge["rule"], "cycle": list(error.cycle_path)})
            report["rules"][edge["rule"]]["cycle"] += 1
            continue
        written += 1
    return written


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #


def _all_edges(db_path: Path) -> list[dict[str, Any]]:
    edges = path_store.list_edges(db_path=db_path)
    edges += path_store.list_edges(db_path=db_path, rel_type=HIERARCHY_REL_TYPE)
    return edges


def _kg_node_map(db_path: Path) -> dict[str, str | None]:
    """``kg_entity.id`` → ``knowledge_node_id``。图谱实体与树节点的唯一桥梁。"""

    with closing(connect(db_path)) as conn:
        try:
            rows = conn.execute("SELECT id, knowledge_node_id FROM kg_entity").fetchall()
        except sqlite3.Error:
            # 还没跑过 kg_align：图谱不存在，R3/R4 自然全 blocked，不必报错。
            return {}
    return {str(row[0]): (str(row[1]) if row[1] else None) for row in rows}


def _composition_edges(db_path: Path) -> list[tuple[str, str, str, str]]:
    """``组成`` 边：药 → 方。方向在 build_kg 里就是这么写的（不要反）。"""

    return _kg_edges(db_path, "组成", "herb", "formula")


def _syndrome_formula_edges(db_path: Path) -> list[tuple[str, str, str, str]]:
    return _kg_edges(db_path, "证方链", "syndrome", "formula")


def _kg_edges(
    db_path: Path, relation_type: str, source_kind: str, target_kind: str
) -> list[tuple[str, str, str, str]]:
    with closing(connect(db_path)) as conn:
        try:
            rows = conn.execute(
                """SELECT s.id, s.name, t.id, t.name
                   FROM kg_edge e
                   JOIN kg_entity s ON s.id = e.source_id
                   JOIN kg_entity t ON t.id = e.target_id
                   WHERE e.relation_type = ? AND s.kind = ? AND t.kind = ?
                   ORDER BY s.name, t.name""",
                (relation_type, source_kind, target_kind),
            ).fetchall()
        except sqlite3.Error:
            return []
    return [(str(row[0]), str(row[1]), str(row[2]), str(row[3])) for row in rows]


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def _edge(rule: str, source: str, target: str, reason: str) -> dict[str, Any]:
    meta = RULES[rule]
    return {
        "rule": rule,
        "from_node_id": source,
        "to_node_id": target,
        "rel_type": meta["rel_type"],
        "sensitivity": meta["sensitivity"],
        "reason": reason,
    }


def _remember(bucket: dict[str, Any], sample: str, limit: int = 5) -> None:
    if len(bucket["samples"]) < limit and sample not in bucket["samples"]:
        bucket["samples"].append(sample)


def _print_report(report: dict[str, Any]) -> None:
    print(f"知识树节点 {report['nodes']}，现有前置/层级边 {report['existing_edges']} 条")
    print()
    for code, meta in RULES.items():
        row = report["rules"][code]
        print(
            f"{code} {meta['name']}（{meta['rel_type']}/{meta['sensitivity']}）："
            f"新增 {row['new']}，已存在 {row['exists']}，冲突 {row['conflict']}，成环 {row['cycle']}"
        )

    for code, miss in report["blocked"].items():
        if not miss["total"]:
            continue
        print()
        print(f"{code} 推不出端点：共 {miss['total']} 条")
        for key in ("herb_without_node", "formula_without_node", "syndrome_without_node"):
            if miss.get(key):
                print(f"  - {key}: {miss[key]}")
        for sample in miss["samples"]:
            print(f"    例：{sample}")

    if report["conflicts"]:
        print()
        print(f"灵敏度/类型冲突 {len(report['conflicts'])} 条（**不覆盖**，需人工确认后迁移）：")
        by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in report["conflicts"]:
            by_rule[item["rule"]].append(item)
        for code, items in sorted(by_rule.items()):
            sample = items[0]
            print(
                f"  - {code}: {len(items)} 条，例 {sample['pair']}"
                f"（现为 {sample['actual']}，推导值为 {sample['expected']}）"
            )
        print("  说明：改 order_sensitivity 会改变学习计划的排布语义（strict=必须按序，free=可并行），")
        print("        推导脚本不动它；确需统一时先评审 planner 行为再单独迁移。")

    if report["cycle_paths"]:
        print()
        print(f"检测到环 {len(report['cycle_paths'])} 条，相关边已跳过：")
        for item in report["cycle_paths"][:5]:
            print(f"  - {item['rule']}: {' → '.join(item['cycle'])}")

    print()
    if report["dry_run"]:
        print("--dry-run：未写库")
    else:
        print(f"已写入 {report['written']} 条")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按四条规则推导学习前置关系")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="SQLite 路径")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写库")
    args = parser.parse_args(argv)

    report = derive(args.db, dry_run=args.dry_run)
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
