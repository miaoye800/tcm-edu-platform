"""教材抽取的**机器自检**——把「需要人看」的条目从 442 压到几十。

## 为什么要有这一层

P4 的复核卡在一个人力问题上：442 味药，一条条看，非中医专业的人看不动；
不看又不敢入库。解法不是「让人更努力」，是**换问题**——

- 人不用判断「中医上对不对」（那需要专业）
- 人只需要判断「这几个字是不是从原文照抄的、切的位置对不对」（那是找不同）

而这个「切得对不对」里，**大部分能用结构算出来**：编号条目的个数、证名里
有没有混进说明文、功效词在不在自己的【功效】里、引文在不在自己那一页……
这些都不需要懂中医。机器先判一遍，人只看机器判不了的那一小撮。

## 与 gates.py 的分工

``gates.py`` 判「能不能进图谱」（红线，fail 就不进）。
这里判「值不值得人看」——不拦任何东西，只排优先级。

两者故意不合并：门禁改一次要重跑全库，自检是随抽取参数频繁调整的。

用法::

    python -m scripts.textbook.selfcheck            # 打印统计 + 写 review_queue.json
    python -m scripts.textbook.selfcheck --stats    # 只看统计
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from scripts.textbook.extract_text import DEFAULT_PDF, extract

BACKEND = Path(__file__).resolve().parents[2]
IN_JSON = BACKEND / "data" / "curated" / "textbook_normalized.json"
OUT_JSON = BACKEND / "data" / "curated" / "review_queue.json"
DECISIONS = BACKEND / "data" / "curated" / "review_decisions.json"


def load_decisions() -> dict[tuple[str, str], dict]:
    """已裁定为「教材本来如此」的疑点。

    复核的结论必须**落回数据**，否则同一个疑点每次跑自检都会再报一遍，
    队列永远清不干净，人就会开始忽略它——那比不检还糟。

    返回 ``{(药名, 自检项): 裁定}``。缺文件时返回空字典，不报错。
    """

    if not DECISIONS.exists():
        return {}
    payload = json.loads(DECISIONS.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], dict] = {}
    for item in payload.get("decisions") or []:
        if item.get("verdict") != "ok":
            continue
        out[(str(item.get("herb", "")), str(item.get("code", "")))] = item
    return out

#: 教材【应用】里主治条目的编号：``1.风寒感冒``。
#: 与 ``normalize._APP_MARK`` 保持同一口径——**不要求后面跟全角空格**，
#: 否则五倍子第 4 条（教材漏了空格）会被算成「原文 5 条、抽出 6 条」的假警报。
_APP_NO = re.compile(r"(?<![\d.．])(\d{1,2})[.．、](?!\d)")

#: 证名里不该出现的词——出现就说明**说明文被切进了证名**。
#: 这些词是教材描述段落的固定开头，不是证候名。
_SYNDROME_TAINT = ("本品", "用于", "常与", "常配", "如", "治", "可单用", "亦")

#: 说明性词出现在证名末尾，说明切晚了。
_TAIL_TAINT = ("者", "等", "之", "。", "，", "、", "；")


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _check_indication_count(entry: dict) -> list[str]:
    """S01 编号条目数守恒：原文有几个「1. 2. 3.」，就该抽出几条主治。

    漏切是**静默**的：黄芪第 1 条证名有 30 个字，早期正则上限写 20，
    它匹配不上，第 2 条能匹配，于是 marks 非空、第 1 条被悄悄丢掉——
    抽出来 6 条，实际 7 条，看不出来少了什么。这条就是防这个。
    """

    raw = (entry.get("quotes") or {}).get("应用", "")
    numbered = len(set(_APP_NO.findall(raw)))
    got = len(entry.get("indications") or [])
    if numbered and got and numbered != got:
        return [f"S01 原文 {numbered} 条编号主治，抽出 {got} 条"]
    return []


def _check_syndrome_text(entry: dict) -> list[str]:
    """S02/S03 证名是否被说明文污染、边界是否正常。"""

    issues: list[str] = []
    for item in entry.get("indications") or []:
        name = (item.get("syndrome") or "").strip()
        if not name:
            issues.append("S02 有一条主治切不出证名")
            continue
        if len(name) < 2:
            issues.append(f"S03 证名过短：{name!r}")
        if len(name) > 60:
            issues.append(f"S03 证名过长（{len(name)} 字）：{name[:20]}…")
        for word in _SYNDROME_TAINT:
            # 「如」在证名里出现，几乎一定是把举例切了进来；
            # 但「阴虚火旺」这类不会含这些词，所以误报率低。
            if word in name and len(name) > 6:
                issues.append(f"S02 证名疑似混进说明文（含「{word}」）：{name[:24]}")
                break
        if name.endswith(_TAIL_TAINT):
            issues.append(f"S03 证名以标点/虚词结尾：{name[-6:]}")
    return issues


def _check_effects_local(entry: dict) -> list[str]:
    """S04 功效词必须出现在**这味药自己**的【功效】原文里。

    G009 只校验「在 581 词闭集内」——那防的是造词，防不了**串味**：
    A 药的功效词如果被写到 B 药身上，闭集校验照样通过。

    **比对的是 ``effects_raw``（切分后、归一前的原文词）**，不是 ``effects``
    （归一后的规范词）。「祛风湿」归一成「祛风湿」没问题，但「泻火解毒」
    会被归一成「清热解毒」，拿规范词去原文里找必然找不到——那是同义归一的
    正常工作，不是错。
    """

    raw = _squash((entry.get("quotes") or {}).get("功效", ""))
    if not raw:
        return []
    terms = entry.get("effects_raw") or []
    stray = [e for e in terms if _squash(e) not in raw]
    if stray:
        return [f"S04 功效词不在本药【功效】原文里：{stray}"]
    return []


def _check_effects_count(entry: dict) -> list[str]:
    """S05 功效词条数异常——教材单味药很少超过 8 条。

    例外：原文按炮制品分了组（石膏「生用/煅用」、何首乌「制/生」），
    两组加起来超过 8 条是**正常的**，不是切碎。只要每条功效都归了组，
    条数多就不是疑点。
    """

    count = len(entry.get("effects") or [])
    if count == 0:
        return ["S05 无功效"]
    if count > 8:
        prep = entry.get("effect_prep") or {}
        grouped = set(entry.get("effects") or []) <= set(prep)
        if grouped and prep:
            return []
        return [f"S05 功效词 {count} 条，疑似切碎（分隔符用错）"]
    return []


def _check_dosage(entry: dict) -> list[str]:
    """S06 剂量比值异常——多半是把「500mL」当成了克。"""

    low, high = entry.get("dose_min"), entry.get("dose_max")
    if low is None or high is None:
        return []
    if low > 0 and high / low > 30:
        return [f"S06 剂量区间跨度过大：{low:g}~{high:g}g（{high / low:.0f} 倍）"]
    return []


def _check_meridians(entry: dict) -> list[str]:
    """S07 归经个数守恒：原文「归A、B经」里有几个经名，就该抽出几个。

    少一个说明白名单把某个经名挡掉了（比如「三焦」「心包」没进白名单）。
    """

    raw = (entry.get("quotes") or {}).get("药性", "")
    match = re.search(r"[归入]([^。；;]+?)经", raw)
    if not match:
        return []
    pieces = [p.strip() for p in re.split(r"[、，,]", match.group(1)) if p.strip()]
    got = len(entry.get("meridians") or [])
    if pieces and got < len(pieces):
        return [f"S07 原文 {len(pieces)} 个经名，只抽出 {got} 个：{pieces}"]
    return []


def _check_page_locality(entry: dict, pages: list[str], span_end: int | None = None) -> list[str]:
    """S08 引文必须出现在本药**自己的页码区间**内。

    G008 是**全书**搜子串——A 药的引文从 B 药条目里搜到了，也算通过。
    这里收紧到「本条目的起止页」：条目从本页开始、到下一条目的起始页结束
    （教材条目是连续排的，中间不会有别人的正文）。
    """

    page = entry.get("page")
    if not isinstance(page, int):
        return []
    end = span_end if isinstance(span_end, int) and span_end >= page else page + 1
    window = _squash("".join(pages[page : end + 1]))
    if not window:
        return []
    issues: list[str] = []
    for field_name, quote in (entry.get("quotes") or {}).items():
        needle = _squash(quote)
        if needle and needle not in window:
            issues.append(f"S08 【{field_name}】引文不在 p{page + 1}-{end + 1} 内（疑似串味）")
    return issues


def _check_missing(entry: dict) -> list[str]:
    """S09 核心字段缺失（门禁也会拦，这里只用于排序）。"""

    missing = entry.get("missing") or []
    if not missing:
        return []
    return [f"S09 缺字段：{', '.join(missing)}"]


def inspect(entry: dict, pages: list[str], span_end: int | None = None) -> list[str]:
    """对一条归一结果跑全部自检，返回疑点列表（空 = 零疑点）。"""

    issues: list[str] = []
    issues += _check_indication_count(entry)
    issues += _check_syndrome_text(entry)
    issues += _check_effects_local(entry)
    issues += _check_effects_count(entry)
    issues += _check_dosage(entry)
    issues += _check_meridians(entry)
    issues += _check_page_locality(entry, pages, span_end)
    issues += _check_missing(entry)
    return issues


#: 需要人看的最低门槛：命中这些才排进队列。
#: S09 缺字段是「没抽到」，不是「抽错了」——缺就缺，人看了也补不出来
#: （教材本来没写），所以它不参与排序。
_HUMAN_CODES = ("S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08")


def build_queue(
    herbs: list[dict], pages: list[str], decisions: dict[tuple[str, str], dict] | None = None
) -> tuple[list[dict], Counter, Counter]:
    decisions = decisions or {}
    # 条目是连续排版的：第 i 条从自己的页码开始，到下一条的起始页结束。
    # 先按页码排序算出每条的实际区间，S08 才能精确判断「引文在不在自己家里」。
    ordered = sorted(herbs, key=lambda item: item.get("page") or 0)
    spans: dict[int, int] = {}
    for index, entry in enumerate(ordered):
        start = entry.get("page")
        if not isinstance(start, int):
            continue
        nxt = ordered[index + 1].get("page") if index + 1 < len(ordered) else start + 1
        spans[id(entry)] = nxt if isinstance(nxt, int) else start + 1

    queue: list[dict] = []
    code_counter: Counter = Counter()
    adjudicated: Counter = Counter()
    for entry in herbs:
        issues = inspect(entry, pages, spans.get(id(entry)))
        name = entry.get("name", "")
        kept: list[str] = []
        for issue in issues:
            code = issue.split()[0]
            code_counter[code] += 1
            if code not in _HUMAN_CODES:
                continue
            if (name, code) in decisions:
                adjudicated[code] += 1
                continue
            kept.append(issue)
        if kept:
            queue.append(
                {
                    "name": name,
                    "page": entry.get("page", 0),
                    "chapter": entry.get("category", ""),
                    "issues": kept,
                }
            )
    queue.sort(key=lambda item: (-len(item["issues"]), item["page"]))
    return queue, code_counter, adjudicated


def main() -> int:
    parser = argparse.ArgumentParser(description="教材抽取机器自检")
    parser.add_argument("--stats", action="store_true", help="只打印，不写文件")
    args = parser.parse_args()

    if not IN_JSON.exists():
        print(f"缺少 {IN_JSON}，先跑 scripts.textbook.normalize")
        return 1
    herbs = json.loads(IN_JSON.read_text(encoding="utf-8"))["herbs"]
    pages = extract(DEFAULT_PDF)

    queue, counter, adjudicated = build_queue(herbs, pages, load_decisions())

    print(f"条目总数: {len(herbs)}")
    print(f"零疑点（可直接放行）: {len(herbs) - len(queue)}")
    print(f"已裁定为教材原貌: {sum(adjudicated.values())} {dict(sorted(adjudicated.items()))}")
    print(f"需人看: {len(queue)}")
    print(f"按自检项: {dict(sorted(counter.items()))}")

    if args.stats:
        return 0

    OUT_JSON.write_text(
        json.dumps(
            {
                "version": "review_queue_v1",
                "stats": {
                    "total": len(herbs),
                    "clean": len(herbs) - len(queue),
                    "adjudicated": sum(adjudicated.values()),
                    "needs_review": len(queue),
                    "by_code": dict(sorted(counter.items())),
                },
                "queue": queue,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"json={OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
