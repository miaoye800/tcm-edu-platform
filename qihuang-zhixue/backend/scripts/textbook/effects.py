"""从教材 PDF 抽功效词，并生成**同义合并候选表**。

只做三件事，每件都留证据：

1. 按条目抽 ``【功效】`` 原文（药名、页码、原文都留着，后面做证据链要用）
2. 统计功效词频，得到**闭集词表**
3. 生成同义合并**候选**——注意是候选，最终由人核对

## 归一口径（为什么这么定）

只合并**同义**，绝不合并**上下位**：

- 可以合并：``止咳化痰`` / ``化痰止咳``（字序不同，意思一样）
- 可以合并：``祛风湿`` / ``祛风除湿``（同义异写）
- **不能**合并：``解毒`` / ``清热解毒`` —— 后者是前者的加强版，
  合并成「解毒」就把「清热」这个属性丢了，处方审查会漏判。

所以脚本里有一条硬规则：**一方是另一方的子串时，一律不合并**，
只登记到 ``do_not_merge`` 里，明确告诉复核的人「这两条看着像，但不能合」。

用法::

    python scripts/textbook/effects.py            # 生成表格
    python scripts/textbook/effects.py --stats    # 只看统计
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from scripts.textbook.extract_text import extract

BACKEND = Path(__file__).resolve().parents[2]
OUT_JSON = BACKEND / "data" / "curated" / "effect_synonyms.json"
OUT_MD = BACKEND.parent / "deliverables" / "effect_merge_table.md"

#: 药名行后面紧跟拼音行，例如 ``Máhuáng（《神农本草经》）``。
_PINYIN = re.compile(r"^[A-Za-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüÊ\s]+（《")

_EFFECT = re.compile(r"【功效】[　 ]*(.+?)(?=\n【|\Z)", re.S)

_SPLIT = re.compile(r"[，、；。\s]+")

#: 只统计像「功效」的词：太短没区分度，太长基本是整句描述。
#: 用法前缀。教材对毒性药会写「内服劫痰平喘」「外用拔毒生肌」——
#: 「内服/外用」是给药途径，不是功效本身，归一前应当拆出去。
_ROUTE_PREFIX = ("内服", "外用")

_MIN_LEN = 2
_MAX_LEN = 10

#: 人工已确认的「单字插入同义对」（2026-10-30 核对）。
#:
#: 判据只有一条，不懂中医也能用：**加上那个字以后，能治的范围变小了吗？**
#:   不变 → 那个字是动词/连接字 → 同义，可以合
#:   变小 → 那个字是限定词 → 窄化，**不能合**
#:
#: 对照没进表的那些，正好能看出这条判据：
#:   解毒 / 解酒毒      范围从「所有毒」缩到「酒毒」  → 不合
#:   止泻 / 止泻痢      从「止泻」扩到「连痢一起止」  → 不合（方向也不对）
#:   补肝肾 / 温补肝肾  多了「药性偏温」这个条件      → 不合
#:   润肠 / 润肠燥      从「润肠」缩到「只润燥结」    → 不合
#:
#: 注意：**没进这张表的一律不合并**。默认不合是安全的——不合并只会让召回
#: 少一点（降级到下一层），合错了会静默改变药性，那比漏召回危险得多。
CONFIRMED_INSERTED: set[tuple[str, str]] = {
    ("祛风湿", "祛风除湿"),  # 除 = 连接字
    ("强筋骨", "强筋壮骨"),  # 壮 = 动词
    ("补肝肾", "滋补肝肾"),  # 滋 = 方式
    ("补肝肾", "补益肝肾"),  # 益 = 方式
    ("通经络", "通经活络"),  # 活 = 动词
    ("补肾阳", "补肾助阳"),  # 助 = 方式
    ("利关节", "通利关节"),  # 通 = 连接字
}

#: 没确认合并的单字插入对，以及**为什么**不合并。
#:
#: 分两类，别混：
#:   - 窄化/范围不同 → 真不能合
#:   - 同义但只涉及 1 味药 → 不合更安全（影响面太小，不值得动）
INSERTED_REASONS: dict[tuple[str, str], str] = {
    ("止痛", "止痹痛"): "窄化：「痹」限定只对痹痛",
    ("解毒", "解酒毒"): "窄化：「酒」限定只对酒毒",
    ("补肝肾", "温补肝肾"): "窄化：「温」是药性条件",
    ("止痢", "止泻痢"): "范围不同：止泻痢还管泻",
    ("止泻", "止泻痢"): "范围不同：止泻痢还管痢",
    ("润肠", "润肠燥"): "窄化：只对燥结",
    ("安胎", "安胎元"): "同义，但只涉及 1 味药，不合更安全",
    ("补肾阳", "补肾壮阳"): "同义，但只涉及 1 味药，不合更安全",
    ("通鼻窍", "宣通鼻窍"): "同义，但只涉及 1 味药，不合更安全",
}


@dataclass
class Entry:
    name: str
    page: int
    raw: str
    effects: list[str] = field(default_factory=list)


def _clean_name(line: str) -> str:
    return re.sub(r"[\s　]+", "", line)


def extract_entries(pages: list[str]) -> list[Entry]:
    """按 ``【功效】`` 定位条目，向前回溯药名。

    回溯必须跨页：教材里一味药的条目经常从上一页底部开始，``【功效】`` 落在
    下一页。只在本页内回溯会丢掉这部分（实测 443 条里丢了 107 条）。
    所以先把全文摊成 ``(页码, 行)`` 的流，再在流上回溯。
    """

    stream: list[tuple[int, str]] = []
    for page_no, text in enumerate(pages):
        for line in text.split("\n"):
            stream.append((page_no, line))

    entries: list[Entry] = []
    for index, (page_no, line) in enumerate(stream):
        if "【功效】" not in line:
            continue
        # 功效正文可能跨行，一直读到下一个【字段】或空行为止
        chunks = [line.split("【功效】", 1)[1]]
        for offset in range(index + 1, min(index + 6, len(stream))):
            nxt = stream[offset][1].strip()
            if not nxt or nxt.startswith("【"):
                break
            chunks.append(nxt)
        raw = " ".join(chunks).strip()
        if not raw:
            continue

        name = ""
        for back in range(index - 1, max(index - 40, -1), -1):
            if not _PINYIN.match(stream[back][1].strip()):
                continue
            for up in range(back - 1, max(back - 6, -1), -1):
                candidate = stream[up][1].strip()
                if candidate and not candidate.startswith("本品"):
                    name = _clean_name(candidate)
                    break
            break
        if not name:
            continue
        effects = [t for t in _SPLIT.split(re.sub(r"\s+", "", raw)) if t]
        entries.append(Entry(name=name, page=page_no, raw=raw, effects=effects))
    return entries


def build_vocabulary(entries: list[Entry]) -> tuple[Counter, dict[str, list[str]]]:
    counter: Counter = Counter()
    owners: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        for term in entry.effects:
            if not (_MIN_LEN <= len(term) <= _MAX_LEN):
                continue
            counter[term] += 1
            if entry.name not in owners[term]:
                owners[term].append(entry.name)
    return counter, dict(owners)


def _anagram_key(term: str) -> str:
    return "".join(sorted(term))


def _resolve(canonical: dict[str, str], term: str) -> str:
    """顺着 ``canonical`` 链走到终点，避免 ``A→B``、``B→C`` 这种半截映射。"""

    seen: set[str] = set()
    while term in canonical and term not in seen:
        seen.add(term)
        term = canonical[term]
    return term


def _single_insert(short: str, long: str) -> str | None:
    """``short`` 是否由 ``long`` 删掉**一个字**得到；返回被删的那个字。

    这条规则专门对付「祛风湿 / 祛风除湿」这类同义异写：二元组 Jaccard 只有
    0.25，靠相似度永远抓不到，但它们确实只是多了一个连接字。

    它也会抓到「解毒 / 解酒毒」这类**窄化**（多了「酒」就限定了范围），
    所以结果只进「待核对」档，不自动合并。
    """

    if len(long) != len(short) + 1:
        return None
    for index in range(len(long)):
        if long[:index] + long[index + 1 :] == short:
            return long[index]
    return None


def cluster(
    counter: Counter, *, min_count: int = 1
) -> tuple[list[list[str]], list[tuple[str, str, str]], list[tuple[str, str]]]:
    """返回 ``(高置信同义组, 单字插入待核对对, 禁止合并对)``。

    分三档的原因：三类的可信度完全不同，混在一起复核会被带偏。

    1. **高置信**：字序不同但字完全一样（止咳化痰 / 化痰止咳）。
    2. **待核对**：只差一个连接字（祛风湿 / 祛风除湿）。同义与窄化混杂。
    3. **禁止合并**：一方是另一方的子串（解毒 / 清热解毒）——那是上下位，
       合并会丢信息。
    """

    terms = [t for t, c in counter.items() if c >= min_count]

    # ---- 1. 字序不同、字完全相同 ----
    buckets: dict[str, list[str]] = defaultdict(list)
    for term in terms:
        buckets[_anagram_key(term)].append(term)
    groups: list[list[str]] = []
    grouped: set[str] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda t: (-counter[t], -len(t), t))
        groups.append(members)
        grouped.update(members)

    # ---- 2. 二元组相似度：**故意不做** ----
    #
    # 试过 Jaccard >= 0.6，结果产出了两个假合并：
    #   散瘀解毒消痈 / 散瘀解毒消肿  —— 消痈 ≠ 消肿
    #   暖肾固精缩尿 / 益肾固精缩尿  —— 暖肾 ≠ 益肾
    # 两条都只差一个字，相似度 0.67，但医学含义不同。
    # 这种合并不会报错，只会悄悄改变药性——比漏合并危险得多。
    # 所以这里只保留「字完全一样、只是顺序不同」这一种确定安全的规则。

    # ---- 3. 单字插入：待核对 ----
    ordered = sorted(terms, key=lambda t: (-counter[t], -len(t), t))
    inserted: list[tuple[str, str, str]] = []
    inserted_set: set[str] = set()
    for i, short in enumerate(ordered):
        for long in ordered[i + 1 :]:
            extra = _single_insert(short, long)
            if extra is None:
                continue
            inserted.append((short, long, extra))
            inserted_set.update([short, long])
    inserted.sort(key=lambda item: -(counter[item[0]] + counter[item[1]]))

    # ---- 4. 其余子串对：禁止合并 ----
    blocked: list[tuple[str, str]] = []
    for i, short in enumerate(ordered):
        if counter[short] < 2:
            continue
        for long in ordered[i + 1 :]:
            if len(long) <= len(short) or short not in long:
                continue
            if counter[long] < 2:
                continue
            if short in inserted_set and long in inserted_set:
                continue
            blocked.append((short, long))
    blocked = blocked[:60]
    return groups, inserted, blocked


def build(counter: Counter, owners: dict[str, list[str]]) -> dict:
    groups, inserted, blocked = cluster(counter)

    canonical: dict[str, str] = {}
    rows = []
    for members in groups:
        head = members[0]
        for member in members:
            if member != head:
                canonical[member] = head
        rows.append(
            {
                "canonical": head,
                "variants": members[1:],
                "counts": {m: counter[m] for m in members},
                "herbs": {m: owners.get(m, [])[:4] for m in members},
            }
        )
    rows.sort(key=lambda r: -sum(r["counts"].values()))

    # ---- 人工确认过的单字插入对，并入 canonical ----
    #
    # 保留次数多的那个写法（教材主流写法），把另一个映射过去。
    # 用 _resolve 兜一层链式，避免 short 本身已被 14 组映射走的情况。
    confirmed_rows = []
    for short, long in sorted(CONFIRMED_INSERTED):
        keep, drop = (
            (short, long) if counter[short] >= counter[long] else (long, short)
        )
        keep = _resolve(canonical, keep)
        if keep == drop:
            continue
        canonical[drop] = keep
        confirmed_rows.append(
            {
                "canonical": keep,
                "variants": [drop],
                "counts": {keep: counter[keep], drop: counter[drop]},
                "herbs": {keep: owners.get(keep, [])[:4], drop: owners.get(drop, [])[:4]},
            }
        )
    confirmed_rows.sort(key=lambda r: -sum(r["counts"].values()))

    return {
        "version": "effect_synonyms_v2",
        "source": "《中药学》第十版（钟赣生）【功效】字段",
        "stats": {
            "herbs": len({n for names in owners.values() for n in names}),
            "terms": len(counter),
            "singletons": sum(1 for c in counter.values() if c == 1),
            "groups": len(rows),
            "confirmed_inserted": len(confirmed_rows),
            "inserted_pairs": len(inserted),
            "blocked_pairs": len(blocked),
        },
        "canonical": canonical,
        "groups": rows,
        "confirmed_groups": confirmed_rows,
        "inserted_pairs": [
            {
                "short": short,
                "long": long,
                "extra": extra,
                "confirmed": (short, long) in CONFIRMED_INSERTED,
                "canonical": canonical.get(long) or canonical.get(short) or "",
                "reason": INSERTED_REASONS.get((short, long), ""),
                "counts": {short: counter[short], long: counter[long]},
                "herbs": {short: owners.get(short, [])[:3], long: owners.get(long, [])[:3]},
            }
            for short, long, extra in inserted
        ],
        "do_not_merge": [{"narrow": a, "broad": b} for a, b in blocked],
        "route_prefixed": [
            {"term": t, "route": t[:2], "effect": t[2:], "count": counter[t], "herbs": owners.get(t, [])[:3]}
            for t in sorted(counter, key=lambda t: -counter[t])
            if t.startswith(_ROUTE_PREFIX)
        ],
        "vocabulary": [
            {"term": t, "count": c, "herbs": owners.get(t, [])[:6]}
            for t, c in counter.most_common()
        ],
    }


def render_markdown(payload: dict) -> str:
    stats = payload["stats"]
    lines = [
        "# 功效词同义合并候选表",
        "",
        f"来源：{payload['source']}",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 抽到的药材（按名称去重） | {stats['herbs']} |",
        f"| 不同功效词 | {stats['terms']} |",
        f"| 只出现 1 次的词 | {stats['singletons']} |",
        f"| 高置信同义组 | {stats['groups']} |",
        f"| 只差一个字：已合并 | {stats['confirmed_inserted']} |",
        f"| 只差一个字：保持原样 | {stats['inserted_pairs'] - stats['confirmed_inserted']} |",
        f"| 禁止合并（上下位） | {stats['blocked_pairs']} |",
        "",
        "> **结论先说**：教材本身的用词相当统一，601 个功效词里真正需要归一的只有",
        f"> **{len(payload['canonical'])} 条**（14 组字序重排 + 7 对单字插入），占 3.5%。",
        "> 这其实是好消息——闭集词表干净，P4 抽取可以直接拿它当白名单用。",
        "",
        "## 一、建议直接合并（高置信）",
        "",
        "**只有一种情况**：字完全一样、只是顺序不同（止咳化痰 / 化痰止咳）。",
        "第一列是建议保留的写法（取教材里出现最多的）。",
        "",
        "> 我试过按「相似度 ≥0.6」再放宽一档，结果产出了两个假合并：",
        "> `散瘀解毒消痈` / `散瘀解毒消肿`（消痈 ≠ 消肿）、",
        "> `暖肾固精缩尿` / `益肾固精缩尿`（暖肾 ≠ 益肾）。",
        "> 这类合并不会报错，只会悄悄改变药性，比漏合并危险得多，所以整条规则删掉了。",
        "",
        "| # | 保留 | 合并进来 | 出现次数 | 涉及药味（示例） | 确认 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(payload["groups"], 1):
        variants = "、".join(row["variants"]) or "—"
        counts = " / ".join(str(row["counts"][m]) for m in [row["canonical"], *row["variants"]])
        sample = "、".join(row["herbs"][row["canonical"]][:3]) or "—"
        lines.append(
            f"| {index} | {row['canonical']} | {variants} | {counts} | {sample} |  |"
        )

    lines += [
        "",
        "## 二、只差一个字：已定案",
        "",
        "判据只有一条，不懂中医也能用——**加上那个字以后，能治的范围变小了吗？**",
        "",
        "- **不变** → 那个字是动词/连接字 → 同义，合并",
        "- **变小** → 那个字是限定词 → 窄化，不合并",
        "",
        "> 没进「已合并」的一律**保持原样**。这是安全的默认：不合并只会让召回少一点",
        "> （自动降级到下一层匹配），合错了会静默改变药性。",
        "",
        "| # | 短写法 | 长写法 | 省掉的字 | 次数 | 处理 | 理由 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(payload["inserted_pairs"], 1):
        counts = f"{item['counts'][item['short']]} / {item['counts'][item['long']]}"
        if item["confirmed"]:
            action = "**已合并**"
            reason = f"连接/方式字，范围不变 → 并入 `{item['canonical']}`"
        else:
            action = "不合并"
            reason = item.get("reason") or "未确认，默认保持原样"
        lines.append(
            f"| {index} | {item['short']} | {item['long']} | {item['extra']} | "
            f"{counts} | {action} | {reason} |"
        )

    lines += [
        "",
        "## 三、禁止合并（上下位关系）",
        "",
        "合并会丢信息。例如把「清热解毒」并进「解毒」，就看不出这味药还能清热了，",
        "处方审查会因此漏判。列出来是为了防止复核时手滑。",
        "",
        "| 窄（下位） | 宽（上位） | 处理 |",
        "| --- | --- | --- |",
    ]
    for item in payload["do_not_merge"]:
        lines.append(f"| {item['narrow']} | {item['broad']} | 保留两条 |")

    if payload["route_prefixed"]:
        lines += [
            "",
            "## 四、需要剥离「内服 / 外用」前缀",
            "",
            "教材对毒性药会写成「内服劫痰平喘」「外用拔毒生肌」。**内服/外用是给药途径，不是功效**，",
            "混在功效词里会让同一味药出现两个看起来矛盾的条目。建议 P4 抽取时拆成独立字段：",
            "`route`（内服/外用）+ `effect`（劫痰平喘）。",
            "",
            "| 原文 | 途径 | 功效 | 次数 | 药味 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in payload["route_prefixed"]:
            lines.append(
                f"| {item['term']} | {item['route']} | {item['effect']} | "
                f"{item['count']} | {'、'.join(item['herbs'][:2])} |"
            )

    lines += [
        "",
        "## 五、全量词表（按出现次数排序）",
        "",
        "| 功效词 | 次数 | 药味示例 |",
        "| --- | --- | --- |",
    ]
    for item in payload["vocabulary"][:150]:
        lines.append(
            f"| {item['term']} | {item['count']} | {'、'.join(item['herbs'][:3])} |"
        )
    lines.append("")
    lines.append(
        f"（共 {stats['terms']} 条，此处列出前 150 条，全量见 `data/curated/effect_synonyms.json`）"
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成功效词同义合并候选表")
    parser.add_argument("--stats", action="store_true", help="只打印统计，不写文件")
    args = parser.parse_args()

    pages = extract(
        Path(__file__).resolve().parents[4]
        / "中药学 (新世纪第四版；全国中医药行业高等教育十三五规划教材；"
        "全国高等中医药院校规划教材第十版) (钟赣生) "
        "(z-library.sk, 1lib.sk, z-lib.sk).pdf"
    )
    entries = extract_entries(pages)
    counter, owners = build_vocabulary(entries)
    payload = build(counter, owners)

    if args.stats:
        print(json.dumps(payload["stats"], ensure_ascii=False, indent=2))
        print("前 10 组：", [g["canonical"] for g in payload["groups"][:10]])
        return

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"entries={len(entries)} terms={payload['stats']['terms']} groups={payload['stats']['groups']}")
    print(f"json={OUT_JSON}")
    print(f"md={OUT_MD}")


if __name__ == "__main__":
    main()
