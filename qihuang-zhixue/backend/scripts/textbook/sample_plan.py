"""抽样计划（P4.4）：把「该谁看」变成库里的一张表。

## 为什么抽样要写进库

计划原文是「首批 50 味 100% 人工；之后毒性药 100%、flag 100%、其余 20% 随机」。
这句话如果只活在一份文档里，实际执行会退化成「复核页上有个随机抽查按钮，
点不点随缘」——而系统不知道谁看过什么，于是拦不住任何人把没复核的批次入库。

落成 ``herb_draft_sample`` 之后，「抽满没有」变成一次 SQL 查询，
``promote_draft`` 可以直接拿它当门槛。**这才是抽样规则真正生效的地方。**

## 四条规则

| reason | 覆盖 | 理由 |
|---|---|---|
| ``first50`` | 页码最前的 50 味 | 首批 100% 人工，用来校准后面所有自动判据 |
| ``toxic`` | 毒性 ∈ {小毒,有毒,大毒} | 错一条就是用药事故，不给抽样概率 |
| ``flag`` | 门禁/自检有疑点的 | 机器已经说「我不敢判」了 |
| ``random20`` | 其余随机 20% | 抓系统性偏差，防「已知问题都修了、未知问题全漏」 |
| ``reverse`` | 每批 3 味 | **反向核对**：只看原文先自己写功效，再比对 |

## 反向核对为什么单列

前四条都是「给你看抽取值，判断对不对」——人会被抽取值锚定，看见「清热泻火」
就觉得自己也想说清热泻火。反向核对**先不给看**：只给教材原文，让人自己写，
写完才展开比对。这是唯一能抓出「整批都系统性偏了一点」的方法，
所以它必须和另外四条分开记，也必须有自己的一小撮样本。

## 固定种子

``random20`` 和 ``reverse`` 用固定种子，保证两个人、两次跑看到的是同一批。
抽样要能复现，否则「我抽过了」这句话没法验证。

用法::

    python -m scripts.textbook.sample_plan --dry-run
    python -m scripts.textbook.sample_plan
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from app.kg import draft

BACKEND = Path(__file__).resolve().parents[2]
GATED = BACKEND / "data" / "curated" / "textbook_gated.json"
OUT_MD = BACKEND.parent / "deliverables" / "sample_plan.md"

BATCH = "textbook-v1"

#: 抽样种子。换种子等于换一批样本，**换之前要把已抽的那批看完**。
SEED = 20261104

#: 首批全检条数。
FIRST_BATCH = 50

#: 其余随机抽样比例。
RANDOM_RATE = 0.2

#: 每批反向核对条数。
REVERSE_COUNT = 3

#: 有毒的档位。``无毒`` 和 ``未知`` 都不进 100% 全检——未知不是「可能有毒」，
#: 是「教材没写」，它由 ``flag`` 那条规则兜（G006 warn 会把它标出来）。
TOXIC_TIERS = ("小毒", "有毒", "大毒")


def _toxic(entry: dict) -> bool:
    return str(entry.get("toxicity") or "") in TOXIC_TIERS


def _flagged(record: draft.Draft) -> bool:
    hits = record.gate_report.get("hits") or []
    return bool(hits)


def plan(records: list[draft.Draft], seed: int = SEED) -> list[tuple[int, str]]:
    """算出 ``[(draft_id, reason)]``。纯函数，不碰库，方便测。

    **``rejected`` 的草稿不进抽样**：它们已经按明确规则判掉了（门禁 fail、
    或与手写条目重名），不会再入库。把它们算进样本，等于让人重复确认
    「一个已经决定不用的东西确实不用」——抽样的分母会被撑大，信号被稀释。
    """

    ordered = [
        record
        for record in sorted(records, key=lambda r: (r.page_start or 0, r.id))
        if record.status != "rejected"
    ]
    picks: dict[int, set[str]] = {}

    def mark(draft_id: int, reason: str) -> None:
        picks.setdefault(draft_id, set()).add(reason)

    for record in ordered[:FIRST_BATCH]:
        mark(record.id, "first50")

    for record in ordered:
        if _toxic(record.payload):
            mark(record.id, "toxic")

    for record in ordered:
        if _flagged(record):
            mark(record.id, "flag")

    # 随机 20%：从「前三条都没覆盖到」的池子里抽。
    # 已经 100% 看过的不该再占随机名额——否则随机那 20% 会被前三条吃掉，
    # 实际等于没抽。
    pool = [r for r in ordered if r.id not in picks]
    rng = random.Random(seed)
    take = max(1, round(len(pool) * RANDOM_RATE)) if pool else 0
    for record in rng.sample(pool, min(take, len(pool))):
        mark(record.id, "random20")

    # 反向核对：从「没被否掉」的里面抽。挑已知有疑点的做反向核对是浪费时间——
    # 那些本来就要看。
    #
    # **不能按 `status == 'auto_passed'` 抽**：签完字状态就变成 approved 了，
    # 重跑一次抽样会抽到另一批，等于「我抽过了」这句话没法验证。
    # 抽样必须只依赖数据本身，不依赖审批进度。
    clean = [r for r in ordered if r.status not in ("rejected", "conflict")]
    for record in rng.sample(clean, min(REVERSE_COUNT, len(clean))):
        mark(record.id, "reverse")

    return sorted((draft_id, reason) for draft_id, reasons in picks.items() for reason in reasons)


def render(rows: list[dict], records: list[draft.Draft]) -> str:
    by_id = {r.id: r for r in records}
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["reason"], []).append(row)

    lines = [
        "# P4.4 抽样计划",
        "",
        f"批次 `{BATCH}`　种子 `{SEED}`　样本 `{len(rows)}` 条 / 草稿 `{len(records)}` 条",
        "",
        "「未看」= 还没填结论。**全部填完之前 `promote_draft` 拒绝写 JSON。**",
        "",
    ]
    titles = {
        "first50": "一、首批全检（页码最前 50 味）",
        "toxic": "二、毒性药全检",
        "flag": "三、机器标了疑点的全检",
        "random20": "四、其余随机 20%",
        "reverse": "五、反向核对（先只看原文自己写，再比对）",
    }
    for reason in draft.SAMPLE_REASONS:
        items = groups.get(reason) or []
        if not items:
            continue
        unseen = sum(1 for item in items if not item["verdict"])
        lines += [f"## {titles[reason]}", "", f"共 {len(items)} 条，未看 {unseen} 条。", ""]
        if reason == "reverse":
            lines += ["| 药名 | 页码 | 结论 |", "| --- | --- | --- |"]
            for item in items:
                record = by_id.get(item["draft_id"])
                page = (record.page_start + 1) if record and record.page_start is not None else "?"
                lines.append(f"| {item['herb_name']} | p{page} | {item['verdict'] or '未看'} |")
        else:
            names = "、".join(item["herb_name"] for item in items)
            lines += [names, ""]
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 P4.4 抽样计划")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写库")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)

    records = draft.list_drafts()
    if not records:
        print("herb_draft 是空的，先跑 scripts.textbook.seed_drafts")
        return 1

    pairs = plan(records, args.seed)
    added = dropped = 0
    if not args.dry_run:
        for draft_id, reason in pairs:
            added += int(draft.add_sample(draft_id, BATCH, reason))
        # 计划变了就把**还没看**的旧样本清掉，否则台账里会挂着「不该被抽到」
        # 的条目，人点进去看一条其实不需要看的药。已经填过结论的不动——
        # 那是人工成果，宁可留在台账里显示「已看但不在本批计划内」。
        dropped = draft.drop_unseen_not_in(BATCH, pairs)

    rows = draft.samples(BATCH)
    if not args.dry_run:
        OUT_MD.parent.mkdir(parents=True, exist_ok=True)
        OUT_MD.write_text(render(rows, records), encoding="utf-8")

    tally: dict[str, int] = {}
    for _, reason in pairs:
        tally[reason] = tally.get(reason, 0) + 1
    print(f"草稿 {len(records)}，样本 {len(pairs)}（新增 {added}，清掉计划外 {dropped}）")
    for reason in draft.SAMPLE_REASONS:
        if tally.get(reason):
            print(f"  {reason:10s} {tally[reason]}")
    print(f"未看 {len(draft.pending_samples(BATCH))}")
    if not args.dry_run:
        print(f"md={OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
