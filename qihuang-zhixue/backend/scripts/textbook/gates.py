"""P4 抽取门禁：G001–G016。

**G008 是红线**：每个字段的值必须是教材原文的**字面子串**。对不上就置空
并标 ``unverified``，不进图谱。

这条规则的存在理由：旧图谱「虚假」的根源就是拿模型去补教材没写的东西。
模型不会说「我不知道」，它会给你一段看起来很专业的话。门禁的作用是让
这种话**进不来**，而不是事后去分辨哪句是编的。

## 为什么门禁要独立成一层

抽取逻辑改了、教材换版了、归一表更新了，门禁都要重跑一遍。把它和抽取
耦合在一起，就没法回答「这次改动让多少条目掉出门禁」这个问题。

## 三级结果

| 结果 | 含义 | 处理 |
|---|---|---|
| ``pass`` | 全部通过 | 进图谱 |
| ``warn`` | 有可疑但不致命 | 进图谱，标记待复核 |
| ``fail`` | 触及红线或缺核心字段 | **不进图谱** |

用法::

    python -m scripts.textbook.gates
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.kb.models import Category, Flavor, Nature, Toxicity
from app.kg.constants import MERIDIAN_WHITELIST
from scripts.textbook.extract_text import DEFAULT_PDF, extract
from scripts.textbook.normalize import load_canonical
from scripts.textbook.parse import load as load_entries

BACKEND = Path(__file__).resolve().parents[2]
IN_JSON = BACKEND / "data" / "curated" / "textbook_normalized.json"
OUT_JSON = BACKEND / "data" / "curated" / "textbook_gated.json"
OUT_MD = BACKEND.parent / "deliverables" / "textbook_gates.md"

#: 剂量上限。超过这个数基本是抽错了（比如把「500mL」当成克）。
DOSE_CEILING = 500.0

#: 核心字段：缺任何一个都不能进图谱。
CORE_FIELDS = ("category", "nature", "flavors", "meridians", "effects", "dosage")


@dataclass
class GateHit:
    """一条门禁记录。``code`` 形如 ``G008``，``severity`` 决定条目去留。"""

    code: str
    severity: str  # pass / warn / fail
    detail: str
    field_name: str = ""


@dataclass
class GateResult:
    name: str
    page: int
    hits: list[GateHit] = field(default_factory=list)

    @property
    def worst(self) -> str:
        if any(h.severity == "fail" for h in self.hits):
            return "fail"
        if any(h.severity == "warn" for h in self.hits):
            return "warn"
        return "pass"

    @property
    def failed_codes(self) -> list[str]:
        return [h.code for h in self.hits if h.severity == "fail"]


def _squash(text: str) -> str:
    """去掉所有空白，用于子串比对。

    教材原文里同一条句子会被 PDF 断成好几行，中间夹着换行和全角空格。
    比对时两边都压掉空白，剩下的字符序列必须**完全一致**——这就是
    「字面子串」的含义：一个字都不能多、不能少、不能改。
    """

    return re.sub(r"\s+", "", text or "")


class SourceIndex:
    """教材原文索引，供 G008 做子串校验。"""

    def __init__(self, pages: list[str]) -> None:
        self.full = _squash("\n".join(pages))

    def contains(self, quote: str) -> bool:
        needle = _squash(quote)
        return bool(needle) and needle in self.full


def check(herb: dict, source: SourceIndex, whitelist: frozenset[str]) -> GateResult:
    """对单条归一结果跑全部门禁。"""

    result = GateResult(name=herb.get("name", ""), page=herb.get("page", 0))
    add = result.hits.append

    # ---- G001 药名 ----
    name = (herb.get("name") or "").strip()
    if not name or len(name) < 1 or len(name) > 8:
        add(GateHit("G001", "fail", f"药名异常: {name!r}", "name"))

    # ---- G002 分类必须是 21 章之一 ----
    category = herb.get("category") or ""
    try:
        Category(category)
    except ValueError:
        add(GateHit("G002", "fail", f"分类不在 21 章内: {category!r}", "category"))

    # ---- G003 四气 ----
    nature = herb.get("nature") or ""
    try:
        Nature(nature)
    except ValueError:
        add(GateHit("G003", "fail", f"四气不在枚举内: {nature!r}", "nature"))

    # ---- G004 五味 ----
    flavors = herb.get("flavors") or []
    if not flavors:
        add(GateHit("G004", "fail", "无五味", "flavors"))
    for flavor in flavors:
        try:
            Flavor(flavor)
        except ValueError:
            add(GateHit("G004", "fail", f"五味不在枚举内: {flavor!r}", "flavors"))

    # ---- G005 归经 ----
    meridians = herb.get("meridians") or []
    if not meridians:
        add(GateHit("G005", "fail", "无归经", "meridians"))
    for meridian in meridians:
        if meridian not in MERIDIAN_WHITELIST:
            add(GateHit("G005", "fail", f"归经不在 12 经内: {meridian!r}", "meridians"))

    # ---- G006 毒性 ----
    toxicity = herb.get("toxicity") or ""
    try:
        Toxicity(toxicity)
    except ValueError:
        add(GateHit("G006", "fail", f"毒性不在四档内: {toxicity!r}", "toxicity"))
    if toxicity == Toxicity.UNKNOWN.value:
        # 未知不是错，但必须显式告警——「不知道」不能伪装成「没问题」。
        add(GateHit("G006", "warn", "毒性未知（教材未提且无法推断）", "toxicity"))

    # ---- G007 剂量 ----
    low, high = herb.get("dose_min"), herb.get("dose_max")
    if low is not None and high is not None:
        if low > high:
            add(GateHit("G007", "fail", f"剂量区间倒置: {low} > {high}", "dosage"))
        if high > DOSE_CEILING:
            add(GateHit("G007", "fail", f"剂量上限异常: {high}g", "dosage"))
        if low <= 0:
            add(GateHit("G007", "fail", f"剂量下限异常: {low}g", "dosage"))
    elif "dosage" in (herb.get("missing") or []):
        # 非克单位（mL、枚、适量）是真实情况，不算失败，但要提示。
        add(GateHit("G007", "warn", "剂量未抽到克数（可能是 mL/枚/适量）", "dosage"))

    # ---- G008 原文子串红线 ----
    quotes = herb.get("quotes") or {}
    checked = 0
    for field_name, quote in quotes.items():
        if not quote:
            continue
        checked += 1
        if not source.contains(quote):
            add(
                GateHit(
                    "G008",
                    "fail",
                    f"【{field_name}】不是教材原文的子串（疑似改写或编造）",
                    field_name,
                )
            )
    if checked == 0:
        add(GateHit("G008", "fail", "没有任何原文引用，无法证明来自教材", "quotes"))

    # ---- G009 功效词必须在闭集内 ----
    effects = herb.get("effects") or []
    if not effects:
        add(GateHit("G009", "fail", "无功效", "effects"))
    outside = [e for e in effects if e not in whitelist]
    if outside:
        add(GateHit("G009", "fail", f"功效词不在闭集内: {outside}", "effects"))

    # ---- G010 主治 ----
    indications = herb.get("indications") or []
    if not indications:
        add(GateHit("G010", "fail", "无主治", "indications"))
    unnamed = [i for i in indications if not (i.get("syndrome") or "").strip()]
    if unnamed:
        add(
            GateHit(
                "G010",
                "warn",
                f"{len(unnamed)} 条主治切不出证名（整段保留，未猜证名）",
                "indications",
            )
        )

    # ---- G011 字段内部重复 ----
    if len(set(effects)) != len(effects):
        add(GateHit("G011", "warn", "功效词有重复", "effects"))
    if len(set(flavors)) != len(flavors):
        add(GateHit("G011", "warn", "五味有重复", "flavors"))

    # ---- G012 药名与页码一致性 ----
    if not herb.get("page"):
        add(GateHit("G012", "warn", "缺页码，无法回溯", "page"))

    # ---- G013 核心字段齐备 ----
    #
    # 剂量单独处理：教材里有 6 味药本来就不按克写——竹沥「30～50mL」、
    # 胖大海「2～3枚」、炉甘石「外用适量」。这些**抽不到克数是正常的**，
    # 只要 dosage_text 有原文就不算缺，只 warn。
    # 真正算缺的是 dosage_text 完全为空。
    missing = set(herb.get("missing") or [])
    absent = [f for f in CORE_FIELDS if f in missing]
    if "dosage" in absent and (herb.get("dosage_text") or "").strip():
        absent.remove("dosage")
        add(GateHit("G007", "warn", "剂量非克单位（mL/枚/适量），克数留空", "dosage"))
    if absent:
        add(GateHit("G013", "fail", f"缺核心字段: {absent}", "missing"))

    # ---- G014 使用注意里的毒性提示与毒性字段是否矛盾 ----
    #
    # 教材有个别条目【药性】没写毒性，但【使用注意】里写「本品有毒」。
    # 这种情况说明我们的药性解析漏了，必须报出来而不是悄悄当无毒。
    #
    # **只认「本品有毒」这种直接描述。** 教材会在【使用注意】里提到别的药
    # 有毒，比如柴胡条：「大叶柴胡…表面密生环节，有毒，不可当柴胡用」——
    # 那是同形异物的提醒，不是柴胡本身有毒。按宽正则会把柴胡误判成失败。
    caution_text = "".join(herb.get("cautions") or [])
    if toxicity == Toxicity.NONE.value and herb.get("toxicity_source") == "inferred":
        if re.search(r"本品[，、]?(有|小|大)毒", caution_text):
            add(
                GateHit(
                    "G014",
                    "fail",
                    "【药性】未标毒性但【使用注意】写「本品有毒」，毒性推断不可靠",
                    "toxicity",
                )
            )

    # ---- G015 毒性原文与字段必须一致 ----
    #
    # 【药性】里明写了「大毒/小毒/有毒」，字段却是「无毒」——只有一种解释：
    # 解析漏了。川乌就是这么被读成无毒的（原文把毒性写在第二个句号之后：
    # 「辛、苦，热。归心、肝、肾、脾经。生川乌有大毒，制川乌有毒。」）。
    #
    # 这条是 G006 的补位：G006 只保证「值在四档内」，不管值对不对。
    # 「教材说有毒，我们记成无毒」比「毒性未知」更危险——未知只 warn，
    # 记错会直接放行一道该 block 的方子。
    nature_quote = (herb.get("quotes") or {}).get("药性", "")
    if toxicity == Toxicity.NONE.value and re.search(r"大毒|小毒|有毒", nature_quote):
        add(
            GateHit(
                "G015",
                "fail",
                "【药性】原文标了毒性，字段却是「无毒」（解析漏读）",
                "toxicity",
            )
        )

    # ---- G016 功效分组标记不能丢 ----
    #
    # 石膏【功效】「生用：清热泻火，除烦止渴；煅用：收湿，生肌，敛疮，止血。」
    # 何首乌【功效】「制何首乌：补肝肾，…。生何首乌：解毒，…。」
    #
    # 分组标记只写在一组的第一条前面，靠上下文继承。如果切词时把标记当普通
    # 字吃了、或者只留在第一条上，就会得到「生肌」这种不知道属于生用还是
    # 煅用的孤儿功效词——石膏生用清热、煅用收湿是两件不同的事，混了就是说
    # 错药性。所以：原文有冒号，就必须切出分组标记，且标记的键必须是功效词。
    effect_quote = (herb.get("quotes") or {}).get("功效", "")
    if re.search(r"[：:]", effect_quote):
        prep = herb.get("effect_prep") or {}
        effects = set(herb.get("effects") or [])
        orphan = sorted(set(prep) - effects)
        if not prep:
            add(
                GateHit(
                    "G016",
                    "fail",
                    "【功效】原文有「生用：/制…：」分组，却没切出分组标记",
                    "effects",
                )
            )
        elif orphan:
            add(
                GateHit(
                    "G016",
                    "fail",
                    f"功效分组标记指向了不存在的功效词: {orphan}",
                    "effect_prep",
                )
            )

    return result


def run(herbs: list[dict], source: SourceIndex, whitelist: frozenset[str]) -> list[GateResult]:
    return [check(herb, source, whitelist) for herb in herbs]


def render(results: list[GateResult], herbs: list[dict], whitelist_size: int) -> str:
    by_verdict = Counter(r.worst for r in results)
    by_code: Counter = Counter()
    code_sev: dict[str, str] = {}
    for result in results:
        for hit in result.hits:
            if hit.severity == "pass":
                continue
            by_code[hit.code] += 1
            code_sev[hit.code] = hit.severity

    lines = [
        "# P4 抽取门禁报告",
        "",
        "来源：《中药学》第十版（钟赣生）",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 条目总数 | {len(results)} |",
        f"| 通过（可进图谱） | {by_verdict.get('pass', 0)} |",
        f"| 有告警（进图谱但标记待复核） | {by_verdict.get('warn', 0)} |",
        f"| **拦下（不进图谱）** | **{by_verdict.get('fail', 0)}** |",
        f"| 功效闭集规模 | {whitelist_size} 词 |",
        "",
        "> **G008 是红线**：每个字段的引用必须是教材原文的字面子串。",
        "> 对不上说明这一段不是从教材搬的——不管它看起来多专业，一律不进图谱。",
        "",
        "## 一、门禁触发统计",
        "",
        "| 门禁 | 级别 | 触发条目数 | 说明 |",
        "| --- | --- | --- | --- |",
    ]
    describe = {
        "G001": "药名非空且长度合理",
        "G002": "分类必须是教材 21 章之一",
        "G003": "四气在枚举内",
        "G004": "五味在枚举内",
        "G005": "归经在 12 经白名单内",
        "G006": "毒性在四档内（含「毒性未知」）",
        "G007": "剂量区间合理且不超过 500g",
        "G008": "**原文子串红线**",
        "G009": "功效词在教材闭集内",
        "G010": "主治可切分",
        "G011": "字段内部无重复",
        "G012": "有页码可回溯",
        "G013": "核心字段齐备",
        "G014": "毒性推断与使用注意不矛盾",
        "G015": "**毒性原文与字段一致**",
        "G016": "功效分组标记不丢",
    }
    for code in sorted(describe):
        if code in by_code:
            lines.append(
                f"| {code} | {code_sev[code]} | {by_code[code]} | {describe[code]} |"
            )
    if not by_code:
        lines.append("| — | — | 0 | 全部门禁零触发 |")

    failed = [r for r in results if r.worst == "fail"]
    if failed:
        lines += [
            "",
            "## 二、被拦下的条目",
            "",
            "| 药名 | 页码 | 触发门禁 | 原因 |",
            "| --- | --- | --- | --- |",
        ]
        for result in failed[:60]:
            reasons = "；".join(h.detail for h in result.hits if h.severity == "fail")
            codes = "、".join(result.failed_codes)
            lines.append(f"| {result.name} | p{result.page} | {codes} | {reasons[:120]} |")
        if len(failed) > 60:
            lines.append(f"| … | | | 另有 {len(failed) - 60} 条 |")

    warned = [r for r in results if r.worst == "warn"]
    if warned:
        lines += [
            "",
            "## 三、有告警的条目（仍进图谱）",
            "",
            "| 药名 | 页码 | 告警 |",
            "| --- | --- | --- |",
        ]
        for result in warned[:40]:
            notes = "；".join(h.detail for h in result.hits if h.severity == "warn")
            lines.append(f"| {result.name} | p{result.page} | {notes[:100]} |")
        if len(warned) > 40:
            lines.append(f"| … | | 另有 {len(warned) - 40} 条 |")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="P4 抽取门禁")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    if not IN_JSON.exists():
        print(f"缺少 {IN_JSON}，先跑 scripts.textbook.normalize")
        return 1
    payload = json.loads(IN_JSON.read_text(encoding="utf-8"))
    herbs = payload["herbs"]
    whitelist = frozenset(payload.get("effect_whitelist") or [])

    source = SourceIndex(extract(DEFAULT_PDF))
    results = run(herbs, source, whitelist)

    verdicts = Counter(r.worst for r in results)
    print(f"pass={verdicts.get('pass',0)} warn={verdicts.get('warn',0)} fail={verdicts.get('fail',0)}")
    codes = Counter(h.code for r in results for h in r.hits if h.severity != "pass")
    print(f"门禁触发: {dict(sorted(codes.items()))}")

    if args.stats:
        return 0

    # 每条都带上自己的门禁报告：``herb_draft.gate_report_json`` 就是从这里来的。
    # 以前只留了全批统计（``stats.gate_hits``），看不出**哪一味**触发了哪一条——
    # 复核的人拿不到「这条为什么可疑」，就只能从头再判一遍。
    passed: list[dict] = []
    for herb, result in zip(herbs, results):
        if result.worst == "fail":
            continue
        item = dict(herb)
        item["gate"] = {
            "worst": result.worst,
            "hits": [
                {
                    "code": hit.code,
                    "severity": hit.severity,
                    "detail": hit.detail,
                    "field": hit.field_name,
                }
                for hit in result.hits
                if hit.severity != "pass"
            ],
        }
        passed.append(item)
    OUT_JSON.write_text(
        json.dumps(
            {
                "version": "textbook_gated_v1",
                "stats": {
                    "total": len(results),
                    "pass": verdicts.get("pass", 0),
                    "warn": verdicts.get("warn", 0),
                    "fail": verdicts.get("fail", 0),
                    "gate_hits": dict(sorted(codes.items())),
                },
                "herbs": passed,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    OUT_MD.write_text(render(results, herbs, len(whitelist)), encoding="utf-8")
    print(f"json={OUT_JSON}")
    print(f"md={OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
