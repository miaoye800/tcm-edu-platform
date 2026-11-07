"""给抽样计划里的每一条填复核结论（P4.4 第一遍）。

## 这一遍是「机器复核」，不是「中医复核」

复核页问的那个问题——**右边这些字能不能在左边原文里找到、切开的位置对不对**——
本来就不需要中医知识，它是找不同。既然是找不同，就该先让一个**独立**的
检查器过一遍，人只处理它判不了的。

「独立」是硬要求：这里复用 ``scripts.textbook.audit``（外部复核），
**不复用** ``gates.py`` / ``selfcheck.py`` 的判据。复用被测代码的判据，
被测代码写错了复核也一起错——那就成了自己给自己盖章。

## 结论怎么落

| 情况 | verdict | 谁签的 |
|---|---|---|
| 独立检查零问题 | ``ok`` | ``auto:audit-v2`` |
| 独立检查有问题 | ``bad`` | ``auto:audit-v2``，note 写清哪一条 |
| 人亲自看过 | ``ok``/``bad`` | ``human:*`` |

``reviewed_by`` 必须如实写。把机器过的签成人工过的，等于把「没人看过」
伪装成「有人看过」——这比不复核更糟，因为它会让人**不再去看**。

## 反向核对不在这里

反向核对要「先只看原文自己写，再比对」，机器做不了这件事（它就是被比对的
那一方）。所以它单独出 ``deliverables/reverse_check.md``，由人填。

用法::

    python -m scripts.textbook.verify_samples --dry-run
    python -m scripts.textbook.verify_samples
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from app.kg import draft
from scripts.textbook.audit import audit, squash
from scripts.textbook.extract_text import DEFAULT_PDF, extract

BACKEND = Path(__file__).resolve().parents[2]
NORMALIZED = BACKEND / "data" / "curated" / "textbook_normalized.json"
BATCH = "textbook-v1"
REVIEWER = "auto:audit-v2"

#: 剂量数字必须能在【用法用量】原文里找到。允许 1 位小数。
_NUM = re.compile(r"\d+(?:\.\d+)?")


def extra_problems(herb: dict) -> list[str]:
    """独立检查器没覆盖、但「找不同」必须问的两件事。"""

    problems: list[str] = []
    dosage = squash((herb.get("quotes") or {}).get("用法用量", ""))
    if dosage:
        for value in (herb.get("dose_min"), herb.get("dose_max")):
            if value is None:
                continue
            text = f"{float(value):g}"
            if text not in dosage:
                problems.append(f"剂量 {text}g 不在【用法用量】原文里")

    raw_effects = squash((herb.get("quotes") or {}).get("功效", ""))
    if raw_effects:
        for term in herb.get("effects_raw") or []:
            if squash(term) not in raw_effects:
                problems.append(f"功效词「{term}」不在本药【功效】原文里")

    if not (herb.get("quotes") or {}).get("药性"):
        problems.append("缺【药性】原文，性味归经无法回溯")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="给抽样条目填复核结论")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    parser.add_argument("--batch", default=BATCH)
    args = parser.parse_args(argv)

    pending = draft.pending_samples(args.batch)
    if not pending:
        print("抽样已全部填完。")
        return 0

    herbs = {item["name"]: item for item in json.loads(NORMALIZED.read_text(encoding="utf-8"))["herbs"]}
    pages = extract(DEFAULT_PDF)
    problems, _ = audit(list(herbs.values()), pages)

    by_herb: dict[str, list[str]] = {}
    for kind, detail in problems:
        for name in herbs:
            if detail.startswith(name):
                by_herb.setdefault(name, []).append(f"[{kind}] {detail}")
                break

    tally: Counter = Counter()
    for item in pending:
        name = item["herb_name"]
        herb = herbs.get(name)
        if herb is None:
            tally["missing"] += 1
            continue
        found = by_herb.get(name, []) + extra_problems(herb)
        verdict = "bad" if found else "ok"
        tally[verdict] += 1
        if args.dry_run:
            continue
        draft.set_sample_verdict(
            item["draft_id"], item["batch"], item["reason"], verdict,
            note="；".join(found)[:400], reviewer=REVIEWER,
        )

    print(f"待填 {len(pending)}，零问题 {tally['ok']}，有问题 {tally['bad']}，缺数据 {tally['missing']}")
    if args.dry_run:
        print("--dry-run，未写库。")
        return 0
    print(f"剩余未看 {len(draft.pending_samples(args.batch))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
