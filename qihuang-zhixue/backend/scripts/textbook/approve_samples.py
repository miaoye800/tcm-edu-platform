"""按抽样结论给草稿签字（P4.4 最后一步）。

## 谁能变成 ``approved``

| 当前状态 | 条件 | 结果 |
|---|---|---|
| ``auto_passed`` | 抽到的样本全部 ``ok``（或没被抽到） | ``approved`` |
| ``auto_passed`` | 有样本判 ``bad`` | ``conflict``，交人裁定 |
| ``pending`` / ``conflict`` | — | 不动，机器不替人签字 |

**「没被抽到」也算通过**，这是抽样本身的意思：20% 随机看一遍没问题，
就按统计规律接受另外 80%。如果要求 100% 都看过，那就不叫抽样了。
真正的保险不是「每条都看」，而是「每条都过了 G008 子串红线 + 独立复核」。

## 为什么 ``bad`` 要退回 ``conflict`` 而不是直接 ``rejected``

``bad`` 的意思是「这条有问题」，不一定是「这条不要」。可能只是某个功效词
切错了，改一下就能用。直接 ``rejected`` 等于把可修的条目丢掉；
退回 ``conflict`` 是「人来定：改还是弃」。

用法::

    python -m scripts.textbook.approve_samples --dry-run
    python -m scripts.textbook.approve_samples
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.kg import draft

BATCH = "textbook-v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按抽样结论签字")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch", default=BATCH)
    args = parser.parse_args(argv)

    by_draft: dict[int, list[dict]] = {}
    for item in draft.samples(args.batch):
        by_draft.setdefault(item["draft_id"], []).append(item)

    approved = rejected_to_conflict = 0
    for record in draft.list_drafts("auto_passed"):
        rows = by_draft.get(record.id, [])
        bad = [row for row in rows if row["verdict"] == "bad"]
        if bad:
            if not args.dry_run:
                note = "；".join(f"{r['reason']}:{r['note'][:80]}" for r in bad)
                draft.add_patch(record.id, "status", "auto_passed", "conflict", note)
                draft.transition(record.id, "conflict", reviewer="policy:sample-gate", note=note)
            rejected_to_conflict += 1
            continue
        if not args.dry_run:
            seen = sum(1 for row in rows if row["verdict"])
            note = (
                f"抽样 {seen} 条全部通过" if seen else "未被抽样，机器全过（门禁 + 独立复核）"
            )
            draft.add_patch(record.id, "status", "auto_passed", "approved", note)
            draft.transition(record.id, "approved", reviewer="policy:sample-gate", note=note)
        approved += 1

    print(f"签字 approved {approved}，样本有问题退回 conflict {rejected_to_conflict}")
    print(f"当前: {draft.counts()}")
    if args.dry_run:
        print("--dry-run，未改库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
