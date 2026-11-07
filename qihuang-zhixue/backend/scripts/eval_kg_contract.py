"""契约层验收：5 个真实问句端到端跑一遍，输出固定格式。

这个脚本不是测试的替代品——它跑的是**产品真会用的那条路**（``plan`` → 能力 →
``normalize`` → 投影），并把人眼要确认的东西（页码、边 id、tier、verdict）打出来。
用法::

    python scripts/eval_kg_contract.py            # 用默认库
    python scripts/eval_kg_contract.py --db x.db  # 指定库

任何一条不达标就以非 0 退出，便于接进 CI 或手工比对。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.kg import contract, projection  # noqa: E402
from app.kg.ask import ask  # noqa: E402
from app.kg.compare import compare, find_path  # noqa: E402
from app.kg.prescription import review  # noqa: E402
from app.profile.store import DB_PATH  # noqa: E402


@dataclass
class Case:
    index: int
    question: str
    capability: str
    note: str
    check: Callable[[dict[str, Any]], list[str]]


def _relations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(payload.get("relations") or [])


def _has_edge(payload: dict[str, Any], edge_id: int) -> bool:
    return any(item.get("edge_id") == edge_id for item in _relations(payload))


def _run_ask(question: str, db_path: Path) -> dict[str, Any]:
    """走完整条路：路由 → ask → 归一化 → **投影**。

    校验用的是投影后的载荷，不是归一化后的。理由很实际：模型看到的就是投影，
    如果边 22160 在投影里被裁掉了，那么「能查到这条边」对用户没有任何意义。
    """

    route = contract.plan(question, db_path=db_path)
    seeds = [seed.entity_id for seed in route.seeds]
    result = ask(question, db_path=db_path, seed_ids=seeds or None)
    normalized = contract.normalize_ask(result)
    projected = projection.project_ask(result)
    payload = dict(projected.payload)
    payload["_normalized_relations"] = len(normalized.get("relations") or [])
    payload["_tokens"] = projected.tokens
    payload["_dropped"] = list(projected.dropped)
    return payload


def _check_case1(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if not _has_edge(payload, 22160):
        problems.append("没拿到边 22160（麻黄-桂枝 相须）")
    pages = [item.get("page") for item in _relations(payload) if item.get("edge_id") == 22160]
    if pages and 149 not in pages:
        problems.append(f"边 22160 的页码不是 149，实际 {pages}")
    if not any(item.get("relation_type") == "相须" for item in _relations(payload)):
        problems.append("没有 相须 关系")
    return problems


def _check_case2(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    textbook = payload.get("textbook_differentiation") or {}
    edge_id = textbook.get("edge_id")
    if edge_id not in (24158, 24159):
        problems.append(f"教材鉴别边不是 24158/24159，实际 {edge_id}")
    if not textbook.get("text_span"):
        problems.append("教材鉴别句没有原文")
    if len(payload.get("differences") or []) < 6:
        problems.append("差异项不足 6 项")
    return problems


def _check_case3(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    constraint = [item for item in _relations(payload) if item.get("tier") == 1]
    if not constraint:
        problems.append("没有 T1 约束（十八反）")
    ids = [item.get("edge_id") for item in constraint]
    if 22132 not in ids:
        problems.append(f"没拿到十八反边 22132，实际 {ids}")
    pages = [item.get("page") for item in constraint]
    if 128 not in pages:
        problems.append(f"十八反页码不是 128，实际 {pages}")
    return problems


def _check_case4(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    chains = [item for item in _relations(payload) if item.get("relation_type") == "药证链"]
    if len(chains) < 3:
        problems.append(f"药证链只有 {len(chains)} 条，期望 ≥3")
    return problems


def _check_case5(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if payload.get("verdict") != "block":
        problems.append(f"verdict={payload.get('verdict')}，期望 block")
    if (payload.get("checks") or {}).get("compatible") != "fail":
        problems.append(f"checks.compatible={payload.get('checks')}，期望 fail")
    return problems


CASES: tuple[Case, ...] = (
    Case(1, "麻黄和桂枝为什么常一起用", "ask", "相须边 22160 / p149", _check_case1),
    Case(2, "麻黄和桂枝有什么区别", "compare", "同类鉴别 24158·24159", _check_case2),
    Case(3, "人参和藜芦能一起用吗", "ask", "十八反边 22132 / p128 / tier=1", _check_case3),
    Case(4, "麻黄能治哪些证", "ask", "≥3 条药证链", _check_case4),
    Case(5, "处方「人参9g，藜芦3g」有没有问题", "review", "verdict=block", _check_case5),
)


def _execute(case: Case, db_path: Path) -> dict[str, Any]:
    question = case.question
    if case.capability == "ask":
        return _run_ask(question, db_path)
    if case.capability == "compare":
        route = contract.plan(question, db_path=db_path)
        ids = [seed.entity_id for seed in route.seeds]
        raw = compare(ids[0], ids[1], db_path=db_path) if len(ids) >= 2 else {"ok": False, "reason": "认不出两味药"}
        return contract.normalize_compare(raw)
    if case.capability == "review":
        return contract.normalize_review(review({"prescription": "人参9g，藜芦3g"}, db_path=db_path))
    raise SystemExit(f"未知能力 {case.capability}")


def _summarize(case: Case, payload: dict[str, Any]) -> str:
    if case.capability == "ask":
        relations = _relations(payload)
        head = relations[0] if relations else {}
        return (
            f"关系 {len(relations)} 条（归一化后 {payload.get('_normalized_relations')}）｜"
            f"首条 {head.get('from')}—{head.get('relation_type')}→{head.get('to')} "
            f"p{head.get('page')}｜投影 {payload.get('_tokens')} token"
        )
    if case.capability == "compare":
        textbook = payload.get("textbook_differentiation") or {}
        return f"差异 {len(payload.get('differences') or [])} 项｜鉴别边 {textbook.get('edge_id')}"
    return f"verdict={payload.get('verdict')}｜checks={payload.get('checks')}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()
    db_path: Path = args.db

    if not db_path.exists():
        print(f"库不存在：{db_path}")
        return 2

    failed = 0
    print(f"库：{db_path}")
    print("=" * 78)
    for case in CASES:
        route = contract.plan(case.question, db_path=db_path)
        # 处方题的路由期望是 review——「有没有问题」不是「有什么区别」，
        # 这条断言本身就在验路由有没有把审查请求认成知识问答。
        route_ok = route.capability == case.capability
        payload = _execute(case, db_path)
        problems = case.check(payload)
        if not route_ok:
            problems = [
                f"路由判成 {route.capability}（{route.rule}），期望 {case.capability}",
                *problems,
            ]
        status = "OK  " if not problems else "FAIL"
        if problems:
            failed += 1
        print(f"[{status}] #{case.index} {case.question}")
        print(f"        能力 {case.capability}（{case.note}）")
        print(f"        {_summarize(case, payload)}")
        for problem in problems:
            print(f"        ! {problem}")
    print("=" * 78)
    print(f"{len(CASES) - failed}/{len(CASES)} 通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
