"""把已经抽好、过了门禁的 442 条教材条目灌进 ``herb_draft``（P4.4 第一步）。

## 为什么要有这一步

在它之前，「复核」只存在于 ``deliverables/review_sheet.html`` 这个单页里：
结论存 ``localStorage``，点「导出」得到一个 JSON，再靠人记得回填。
这套流程有三个洞：

1. **没有签字**：谁看的、什么时候看的，库里查不到。
2. **没有门槛**：任何人可以跳过复核直接把抽取产物写进 ``data/herbs/``。
3. **没有反推**：只有「通过」两个字，看不出人到底改了什么、闸门漏了什么。

灌进 ``herb_draft`` 之后，这三件事分别由 ``status`` / ``reviewed_by`` /
``herb_draft_patch`` 承担。

## 初态怎么定（都是算出来的，不是拍脑袋）

| 情况 | 初态 | 理由 |
|---|---|---|
| 门禁有 fail | ``rejected`` | 门禁已经判死，不用浪费人 |
| 与已有手写条目重名 | ``conflict`` | 两份数据都声称自己是对的，必须人裁 |
| 门禁有 warn / 自检有疑点 | ``pending`` | 机器不敢替人签字 |
| 其余 | ``auto_passed`` | 等抽样确认 |

``pending`` 不是「坏」，只是「机器判不了」。442 条里只有 8 条落到这里。

用法::

    python -m scripts.textbook.seed_drafts --dry-run
    python -m scripts.textbook.seed_drafts
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.kg import draft

BACKEND = Path(__file__).resolve().parents[2]
GATED = BACKEND / "data" / "curated" / "textbook_gated.json"
QUEUE = BACKEND / "data" / "curated" / "review_queue.json"
DECISIONS = BACKEND / "data" / "curated" / "review_decisions.json"
HERBS_DIR = BACKEND / "data" / "herbs"
#: 本流水线自己的产物，不算「已有手写条目」。
OWN_PRODUCTS = ("textbook_promoted.json", "textbook_extracted.json")

#: 这批数据的批次号。抽样、签字都挂在批次上，换教材版本要换批次。
BATCH = "textbook-v1"


def curated_names() -> set[str]:
    """``data/herbs/`` 里**别人写的**条目名。

    要排除本流水线自己的两个产物：把它们算进来，重跑时会认为 442 条全都
    「与已有重名」，于是全部落 ``conflict``——一个自己跟自己冲突的假警报。
    """

    names: set[str] = set()
    for path in sorted(HERBS_DIR.glob("*.json")):
        if path.name in OWN_PRODUCTS:
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else [raw]
        names.update(
            str(item["name"]) for item in items if isinstance(item, dict) and item.get("name")
        )
    return names


def adjudicated() -> set[tuple[str, str]]:
    """已经被判成「教材本来如此」的疑点，``{(药名, 自检项)}``。

    不排除它们，``三棱``/``没药`` 会永远停在 ``pending``——人已经判过一次了，
    重跑还让他判第二次，他就开始不看内容直接点通过。
    """

    if not DECISIONS.exists():
        return set()
    payload = json.loads(DECISIONS.read_text(encoding="utf-8"))
    return {
        (str(item.get("herb", "")), str(item.get("code", "")))
        for item in payload.get("decisions") or []
        if item.get("verdict") == "ok"
    }


def build_rows() -> list[dict]:
    gated = json.loads(GATED.read_text(encoding="utf-8"))["herbs"]
    flagged: dict[str, list[str]] = {}
    if QUEUE.exists():
        queue = json.loads(QUEUE.read_text(encoding="utf-8"))
        flagged = {item["name"]: item["issues"] for item in queue.get("queue") or []}
    known = adjudicated()
    curated = curated_names()

    rows: list[dict] = []
    for entry in gated:
        name = entry["name"]
        gate = entry.get("gate") or {}
        hits = list(gate.get("hits") or [])

        # 自检疑点折进门禁报告，这样「要不要人看」只有一处判据。
        for issue in flagged.get(name, []):
            code = issue.split()[0] if issue else "S00"
            if (name, code) in known:
                continue
            hits.append(
                {"code": code, "severity": "warn", "detail": issue, "field": "selfcheck"}
            )

        if name in curated:
            hits.append(
                {
                    "code": "G017",
                    "severity": "warn",
                    "detail": "与 data/herbs 里已有的手写条目重名，需裁定以哪份为准",
                    "field": "name",
                }
            )
            status = "conflict"
        else:
            status = draft.status_from_gate({"hits": hits})

        rows.append(
            {
                "herb_name": name,
                "block_id": f"herb:{name}",
                "page_start": entry.get("page"),
                "page_end": entry.get("page"),
                "payload": entry,
                "gate_report": {"worst": gate.get("worst", "pass"), "hits": hits},
                # 抽取是纯代码，没有模型、没有 prompt。字段留空而不是编一个
                # 「qwen-max」上去——写假来源比不写更糟。
                "model": "",
                "prompt_hash": "",
                "_status": status,
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把教材条目灌进 herb_draft")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    args = parser.parse_args(argv)

    rows = build_rows()
    tally: dict[str, int] = {}
    for row in rows:
        tally[row["_status"]] = tally.get(row["_status"], 0) + 1

    print(f"条目 {len(rows)}")
    for status in draft.STATUSES:
        print(f"  {status:12s} {tally.get(status, 0)}")

    if args.dry_run:
        print("\n--dry-run，未写库。")
        return 0

    created, existing = draft.seed(rows)
    # seed() 只按门禁算初态，conflict 是这里额外判的，补一次流转。
    conflicts = [row["herb_name"] for row in rows if row["_status"] == "conflict"]
    moved = 0
    for name in conflicts:
        record = draft.get_by_name(name)
        if record and record.status != "conflict":
            draft.transition(record.id, "conflict", reviewer="policy:curated-first",
                             note="与已有手写条目重名")
            moved += 1

    print(f"\n新建 {created}，已存在 {existing}，补判 conflict {moved}")
    print(f"当前: {draft.counts()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
