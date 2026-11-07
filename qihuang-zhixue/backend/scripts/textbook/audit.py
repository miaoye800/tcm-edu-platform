# -*- coding: utf-8 -*-
"""P4 独立复核：不复用 ``selfcheck`` 的判据，从教材原文重新验一遍。

``selfcheck.py`` 是**自检**——它用归一化后的数据内部对照（引文 vs 字段、
编号 vs 条数）。自检全绿只能说明「前后一致」，不能说明「和教材一致」：
S04 只验「功效词是不是【功效】原文的子串」，而「生用：清热泻火」确实是
子串，所以石膏那个把分组标记当功效词的问题它看不见。

这里是**外部复核**，只看三件不需要中医知识的事：

1. 页码对不对得上——药名得出现在它自己那页附近
2. 引文是不是真的在教材里——拿整本书当干草堆找针，不是拿它自己那段
3. 数字守恒——【应用】里有几个编号，就该有几条主治

跑法::

    python -m scripts.textbook.audit
    python -m scripts.textbook.audit --strict   # 有硬伤就退出码 1
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from scripts.textbook.extract_text import DEFAULT_PDF, extract

BACKEND_ROOT = Path(__file__).resolve().parents[2]
NORMALIZED = BACKEND_ROOT / "data" / "curated" / "textbook_normalized.json"

#: 【应用】的编号条目。和 normalize._APP_MARK 同源，但这里独立写一份——
#: 复核如果复用被测代码的正则，被测代码写错了复核也一起错。
_APP_MARK = re.compile(r"(?<![\d.．])(\d{1,2})[.．、](?!\d)")

#: 证名里不该出现的动词。出现了说明「证名 + 说明」没切开。
_VERBS = ("治疗", "用于", "本品", "常与", "均可")

#: 毒性的四种写法。用于「原文没提毒性」的判定。
_TOX_WORDS = re.compile(r"大毒|小毒|有毒")


def squash(text: str) -> str:
    """压掉全部空白。PDF 断行会让同一句话散在几行，不压就搜不到。"""

    return re.sub(r"\s+", "", text or "")


def audit(herbs: list[dict], pages: list[str]) -> tuple[list[tuple[str, str]], dict]:
    """返回 ``([(类别, 说明)], 统计)``。空列表表示没查出问题。"""

    problems: list[tuple[str, str]] = []
    book = squash("".join(pages))

    # ---- A. 页码对得上：药名必须出现在它自己那页 ±2 内 ----
    off_page = 0
    for herb in herbs:
        page = herb.get("page")
        if not isinstance(page, int):
            continue
        window = squash("".join(pages[max(0, page - 1) : page + 2]))
        if squash(herb["name"]) not in window:
            off_page += 1
            problems.append(("A 页码", f"{herb['name']} 第 {page} 页附近找不到药名"))

    # ---- B. 引文逐字来自教材：在**整本书**里找，不只在它自己那段 ----
    ghost_quote = 0
    for herb in herbs:
        for field, quote in (herb.get("quotes") or {}).items():
            if quote and squash(quote) not in book:
                ghost_quote += 1
                problems.append(
                    ("B 引文", f"{herb['name']} 的【{field}】引文教材里没有")
                )

    # ---- C. 归一值落在自己那段的引文里 ----
    # 功效词允许是**同义归一**后的写法（祛风除湿 → 祛风湿），字面不在原文里，
    # 所以只统计不报错；五味/归经/毒性不允许归一，对不上就是错。
    normalized_effect = 0
    for herb in herbs:
        quote = squash((herb.get("quotes") or {}).get("功效", ""))
        normalized_effect += sum(
            1 for term in herb.get("effects") or [] if squash(term) not in quote
        )
        nature = squash((herb.get("quotes") or {}).get("药性", ""))
        for field in ("flavors", "meridians"):
            for value in herb.get(field) or []:
                if value not in nature:
                    problems.append(
                        ("C 归一", f"{herb['name']} 的 {value} 不在【药性】引文里")
                    )
        toxicity = herb.get("toxicity") or ""
        # 「无毒」有两种来源：原文写了（stated），和原文没提、按无毒处理（inferred）。
        # 后者字面上当然找不到，不算错——但要看住「原文写了有毒、字段却是无毒」。
        if (
            toxicity == "无毒"
            and herb.get("toxicity_source") == "stated"
            and not _TOX_WORDS.search(nature)
        ):
            problems.append(("C 归一", f"{herb['name']} 标为原文给出的无毒，原文没写"))

    # ---- D. 主治条数守恒 ----
    for herb in herbs:
        raw = (herb.get("quotes") or {}).get("应用", "")
        expected = 0
        for number in (int(m.group(1)) for m in _APP_MARK.finditer(raw)):
            if number == expected + 1:
                expected = number
        got = len(herb.get("indications") or [])
        if expected and got != expected:
            problems.append(
                ("D 条数", f"{herb['name']} 【应用】有 {expected} 条，抽出 {got} 条")
            )

    # ---- E. 证名混进动词 ----
    for herb in herbs:
        for item in herb.get("indications") or []:
            syndrome = item.get("syndrome") or ""
            if any(verb in syndrome for verb in _VERBS):
                problems.append(("E 证名", f"{herb['name']} 的证名「{syndrome}」带动词"))

    # ---- F. 功效词残留分组标记 ----
    for herb in herbs:
        stray = [t for t in herb.get("effects") or [] if "：" in t or ":" in t]
        if stray:
            problems.append(("F 功效", f"{herb['name']} 的功效词带冒号：{stray}"))

    stats = {
        "entries": len(herbs),
        "pages": len(pages),
        "off_page": off_page,
        "ghost_quote": ghost_quote,
        "synonym_effects": normalized_effect,
        "categories": dict(Counter(h.get("category") or "(空)" for h in herbs)),
        "empty": dict(
            Counter(
                field
                for herb in herbs
                for field in ("nature", "category", "dosage_text")
                if not herb.get(field)
            )
        ),
    }
    return problems, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P4 教材抽取的外部复核")
    parser.add_argument("--strict", action="store_true", help="有硬伤时退出码为 1")
    args = parser.parse_args(argv)

    payload = json.loads(NORMALIZED.read_text(encoding="utf-8"))
    herbs = payload["herbs"]
    pages = extract(DEFAULT_PDF)

    problems, stats = audit(herbs, pages)

    print(f"条目 {stats['entries']} / 教材 {stats['pages']} 页")
    print(f"页码对不上: {stats['off_page']}")
    print(f"引文在教材里找不到: {stats['ghost_quote']}")
    print(f"同义归一后的功效词（字面不在原文，正常）: {stats['synonym_effects']}")
    print(f"空核心字段: {stats['empty'] or '无'}")
    print(f"分类: {len(stats['categories'])} 章")

    if not problems:
        print("\n外部复核通过：没有硬伤。")
        return 0

    print(f"\n问题 {len(problems)} 条：")
    for kind, detail in problems:
        print(f"  [{kind}] {detail}")
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
