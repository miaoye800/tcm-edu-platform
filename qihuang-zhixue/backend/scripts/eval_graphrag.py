"""GraphRAG 验收：20 道真实问题，量化「答的是查到的，还是编的」。

这个脚本存在的理由只有一个：**证明扩展出来的每一条引用都指得回图里那条边。**

普通 RAG 的验收看「召回率」；GraphRAG 不一样，它多了一个可以硬性检查的东西——
引用（citation）必须来自这次扩展出的子图。如果模型（或摘要逻辑）引用了子图
之外的教材句子，那这句话就是无源之水，不管读起来多顺。

跑法：

    .venv/Scripts/python.exe scripts/eval_graphrag.py
    .venv/Scripts/python.exe scripts/eval_graphrag.py --json

判据（与方案 §P5 一致）：

1. ``citations_in_subgraph`` ≥ 95%——每条引用都能按 ``edge_id`` 追回子图里的边；
2. ``tier3_leak`` = 0——T3 相似边（同功效/同归经）永不进上下文；
3. 每条引用都带 ``quote`` 原文，空引文算失败；
4. 认不出实体的问题必须显式说「没找到起点」，不能硬编。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.kg.ask import ask  # noqa: E402
from app.kg.constants import Tier  # noqa: E402
from app.profile.store import DB_PATH  # noqa: E402


#: 20 道题覆盖四类真实问题：配伍理由、方剂归属、两药对比、禁忌。
#: 不写「请介绍一下麻黄」这种作文题——那种题 GraphRAG 和普通 RAG 比不出差别。
QUESTIONS: list[str] = [
    "麻黄和桂枝为什么常一起用",
    "麻黄汤由哪些药组成",
    "黄芪能治什么",
    "人参和藜芦能一起用吗",
    "石膏和知母为什么配在一起",
    "附子配伍干姜的道理",
    "四君子汤由哪些药组成",
    "桂枝汤的组成",
    "黄连和黄芩有什么区别",
    "党参和人参差在哪",
    "柴胡是疏肝还是解表",
    "当归补血汤为什么黄芪用量大",
    "陈皮和半夏为什么同用",
    "茯苓能利水吗",
    "大黄的用法用量",
    "金银花和连翘能一起用吗",
    "细辛和藜芦同用会怎样",
    "白术和苍术的区别",
    "生脉散由哪些药组成",
    "甘草反哪些药",
]

#: 应当「认不出起点」的反例。答出一段像模像样的中药分析才是失败。
NEGATIVE_QUESTIONS: list[str] = [
    "阿司匹林和布洛芬能不能一起吃",
    "这味药和那个偏方冲突吗",
]


@dataclass
class CaseResult:
    question: str
    seeds: list[str]
    nodes: int
    edges: int
    citations: int
    citations_in_subgraph: int
    empty_quotes: int
    tier3_in_context: int
    unmatched_seeds: list[str]

    @property
    def ok(self) -> bool:
        return (
            bool(self.seeds)
            and self.citations > 0
            and self.citations_in_subgraph == self.citations
            and self.empty_quotes == 0
            and self.tier3_in_context == 0
        )


def evaluate(question: str, db_path: Path) -> CaseResult:
    result = ask(question, db_path=db_path)
    payload = result.as_dict()

    edge_ids = {edge["id"] for edge in payload["graph"]["edges"]}
    citations = payload["citations"]
    in_subgraph = sum(1 for item in citations if item.get("edge_id") in edge_ids)

    # 摘要里出现的实体名必须有 context 出处；这里只查种子是否真的进了子图，
    # 因为「种子没进子图」意味着问题被解析成了别的实体。
    node_ids = {node["id"] for node in payload["graph"]["nodes"]}
    unmatched = [seed["id"] for seed in payload["seeds"] if seed["id"] not in node_ids]

    return CaseResult(
        question=question,
        seeds=[f"{seed['name']}({seed['kind']})" for seed in payload["seeds"]],
        nodes=len(payload["graph"]["nodes"]),
        edges=len(payload["graph"]["edges"]),
        citations=len(citations),
        citations_in_subgraph=in_subgraph,
        empty_quotes=sum(1 for item in citations if not str(item.get("quote") or "").strip()),
        tier3_in_context=sum(
            1
            for item in payload["context"]["relations"]
            if item["tier"] == int(Tier.SIMILARITY)
        ),
        unmatched_seeds=unmatched,
    )


def check_negative(question: str, db_path: Path) -> bool:
    payload = ask(question, db_path=db_path).as_dict()
    return not payload["seeds"] and "没找到起点" in payload["answer"]


def main() -> int:
    parser = argparse.ArgumentParser(description="GraphRAG 20 题验收")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是人读表格")
    parser.add_argument("--db", default=str(DB_PATH), help="图谱数据库路径")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 2

    cases = [evaluate(question, db_path) for question in QUESTIONS]
    negatives = {question: check_negative(question, db_path) for question in NEGATIVE_QUESTIONS}

    total_citations = sum(case.citations for case in cases)
    total_in_subgraph = sum(case.citations_in_subgraph for case in cases)
    ratio = total_in_subgraph / total_citations if total_citations else 0.0
    tier3_leak = sum(case.tier3_in_context for case in cases)
    unresolved = [case.question for case in cases if not case.seeds]
    negative_pass = all(negatives.values())

    payload = {
        "questions": len(cases),
        "citation_total": total_citations,
        "citation_in_subgraph": total_in_subgraph,
        "citation_in_subgraph_ratio": round(ratio, 4),
        "tier3_leak": tier3_leak,
        "unresolved_questions": unresolved,
        "negative_pass": negative_pass,
        "failed_cases": [case.question for case in cases if not case.ok],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for case in cases:
            flag = "OK " if case.ok else "FAIL"
            print(
                f"[{flag}] {case.question}\n"
                f"       种子={','.join(case.seeds) or '无'} 节点={case.nodes} 边={case.edges} "
                f"引用={case.citations} 落在子图内={case.citations_in_subgraph}"
                + (f" 空引文={case.empty_quotes}" if case.empty_quotes else "")
                + (f" 种子未进子图={case.unmatched_seeds}" if case.unmatched_seeds else "")
            )
        print("-" * 72)
        print(f"引用落在子图内：{total_in_subgraph}/{total_citations} = {ratio:.1%}（判据 ≥95%）")
        print(f"T3 泄漏进上下文：{tier3_leak} 条（判据 = 0）")
        print(f"认不出实体：{len(unresolved)} 题 {unresolved if unresolved else ''}")
        print(f"反例（应当说没找到起点）：{'通过' if negative_pass else '失败 ' + str(negatives)}")

    passed = ratio >= 0.95 and tier3_leak == 0 and not unresolved and negative_pass
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
