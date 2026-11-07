"""盲测：只给模型 6 段能力说明 + 20 条问句，看它选不选得对。

**不给源码、不给判据表、不给答案**——这是唯一能验证「说明书写得够不够清楚」的
办法。``plan()`` 的判据表验的是规则实现，这里验的是**描述本身**能不能让人（模型）
看懂该调哪个能力；两者都不达标才算真不达标。

默认只打印提示词与评分表，不花钱：

    python scripts/run_kg_blindtest.py

真跑（会调模型、产生费用）：

    python scripts/run_kg_blindtest.py --live --model deepseek-chat

结果写 ``docs/kg-m1-blindtest.md``，**不进 CI**（无网络环境不可复现）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.kg import contract  # noqa: E402

#: 20 条盲测问句。与 ``tests/kg_judgement_set.json`` **不重叠**：那份是规则的
#: 考卷，这份是说明书的考卷，用同一批题就等于自己考自己。
BLIND_CASES: tuple[tuple[str, str | None], ...] = (
    ("麻黄和桂枝为什么常一起用", "ask"),
    ("人参和藜芦能一起用吗", "ask"),
    ("麻黄和桂枝有什么区别", "compare"),
    ("麻黄到麻黄汤中间经过什么", "path"),
    ("麻黄的功效是什么", None),
    ("咽喉肿痛用什么药", "lookup"),
    ("淮山是不是山药", "lookup"),
    ("白术和苍术怎么区分", "compare"),
    ("麻黄能治哪些证", "ask"),
    ("麻黄汤由哪些药组成", "ask"),
    ("丁香和郁金同方会有什么问题", "ask"),
    ("黄芪和当归为什么要配在一起", "ask"),
    ("桂枝的用量是多少", None),
    ("白芍和赤芍的异同", "compare"),
    ("生姜和干姜是怎么扯上关系的", "path"),
    ("生地黄和熟地黄哪个更适合阴虚", "compare"),
    ("附子中毒怎么解救", None),
    ("这题我错了，错在哪", "attribute"),
    ("处方：人参9g，藜芦3g，有问题吗", "review"),
    ("阿莫西林和中药能一起吃吗", None),
)

SYSTEM_PROMPT = """你是一个中药学教学系统的调度器。你只能从下面这些能力里选一个，或者回答 none。
只输出一个词（能力名或 none），不要解释、不要标点、不要代码块。

{descriptions}

补充约定：
- 用户只是问某味药的性味/功效/用量这类普通属性时，选 none（走普通检索即可）。
- 问教材没收录的药材或西药时，选 none。
- 只输出一个词。
"""


def build_prompt() -> str:
    return SYSTEM_PROMPT.format(descriptions=contract.describe_all())


def _normalize(reply: str) -> str | None:
    text = (reply or "").strip().strip("`。.、'\"")
    low = text.lower()
    if low.startswith("none") or "none" in low[:12]:
        return None
    for name in contract.CAPABILITY_ORDER:
        if name in low:
            return name
    return None


async def _live(model: str) -> list[tuple[str, str | None, str | None]]:
    from app.config import get_settings
    from app.llm.clients import DeepSeekClient

    settings = get_settings()
    api_key = settings.deepseek_api_key or ""
    if not api_key:
        raise SystemExit("没有配置 DEEPSEEK_API_KEY，改跑离线模式（不加 --live）")
    client = DeepSeekClient(model_name=model, api_key=api_key)
    rows: list[tuple[str, str | None, str | None]] = []
    for question, expected in BLIND_CASES:
        reply = await client.chat(
            [
                {"role": "system", "content": build_prompt()},
                {"role": "user", "content": question},
            ],
            temperature=0,
        )
        rows.append((question, expected, _normalize(str(reply))))
    return rows


def _report(rows: list[tuple[str, str | None, str | None]], model: str) -> str:
    lines = [
        "# 契约层盲测结果",
        "",
        f"- 模型：`{model}`",
        f"- 题数：{len(rows)}",
        "- 输入：`contract.describe_all()` 六段说明 + 问句；**不给源码、不给判据表**",
        "",
        "| # | 问句 | 期望 | 实际 | |",
        "|---|---|---|---|---|",
    ]
    correct = 0
    for index, (question, expected, got) in enumerate(rows, start=1):
        ok = expected == got
        correct += ok
        lines.append(
            f"| {index} | {question} | {expected or 'none'} | {got or 'none'} | {'✓' if ok else '✗'} |"
        )
    lines.append("")
    lines.append(f"**命中 {correct}/{len(rows)}**（门槛 17/20 = 85%）")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="真调模型（会产生费用）")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if not args.live:
        print(build_prompt())
        print("=" * 78)
        print("评分表（未运行）：")
        for index, (question, expected) in enumerate(BLIND_CASES, start=1):
            print(f"{index:>2}. {question}  ->  {expected or 'none'}")
        print("=" * 78)
        print("加 --live 才会真的调模型并把结果写进 docs/kg-m1-blindtest.md")
        return 0

    rows = asyncio.run(_live(args.model))
    report = _report(rows, args.model)
    print(report)
    out = args.out or Path(__file__).resolve().parents[2] / "docs" / "kg-m1-blindtest.md"
    out.write_text(report + "\n", encoding="utf-8")
    print(f"\n已写入 {out}")
    correct = sum(1 for _, expected, got in rows if expected == got)
    return 0 if correct >= 17 else 1


if __name__ == "__main__":
    raise SystemExit(main())
