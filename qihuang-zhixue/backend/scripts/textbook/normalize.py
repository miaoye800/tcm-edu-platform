"""把教材条目归一成结构化字段。

**这一层只做「搬运 + 词表对齐」，不做判断。**

具体分工：

| 字段 | 怎么来 | 用不用模型 |
|---|---|---|
| 章节 → ``category`` | 抄教材章名 | 不用 |
| 四气/五味/归经/毒性 | 从【药性】正则抽，对齐白名单 | 不用 |
| 功效 | 从【功效】按标点切，走 canonical 归一 | 不用 |
| 剂量 | 从【用法用量】正则抽数字 | 不用 |
| 禁忌 | 从【使用注意】按句切 | 不用 |

**全程零模型调用。** 教材字段写得规整，正则比模型准，而且可复现。
模型只该用在「开放字段的闭集选择」上，而这里连开放字段都没有。

## 抽不到怎么办

留空 + 标 ``unverified``，**绝不猜**。用模型补教材没写的内容，正是旧图谱
「虚假」的来源。宁可少一个字段，也不进一条编的。

用法::

    python -m scripts.textbook.normalize --stats
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
from scripts.textbook.parse import RawEntry, load as load_entries

BACKEND = Path(__file__).resolve().parents[2]
EFFECT_SYNONYMS = BACKEND / "data" / "curated" / "effect_synonyms.json"
OUT_JSON = BACKEND / "data" / "curated" / "textbook_normalized.json"

#: 四气。按长度降序，保证「微寒」先于「寒」匹配。
_NATURES = ("大寒", "微寒", "大热", "微温", "寒", "凉", "平", "温", "热")

#: 五味。教材会写「微苦」「甘、淡」这类，按长度降序。
_FLAVORS = ("微苦", "微甘", "微辛", "微咸", "酸", "苦", "甘", "辛", "咸", "淡", "涩")

#: 毒性。教材写法：「有毒」「小毒」「大毒」，偶尔「无毒」。
_TOXICITIES = (("大毒", Toxicity.SEVERE), ("小毒", Toxicity.MINOR),
               ("有毒", Toxicity.TOXIC), ("无毒", Toxicity.NONE))

#: 归经。「归肺、膀胱经」和「入肝、肾经」两种写法都有。
_MERIDIAN = re.compile(r"[归入]([^。；;]+?)经")

#: 剂量的数字范围。「2～10g」「1.5~3g」「0.5～1g」。
_DOSE_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*[～~\-–—至]\s*(\d+(?:\.\d+)?)\s*g")
_DOSE_SINGLE = re.compile(r"(\d+(?:\.\d+)?)\s*g")

#: 【应用】里的编号条目，例如 ``1.风寒感冒``。
#:
#: **不要求后面跟全角空格。** 教材排版不统一，五倍子第 4 条是
#: 「4.遗精，滑精本品入肾经能涩精止遗」——漏了那个全角空格。
#: 要求空格就整条丢掉：抽出来 5 条、实际 6 条，而且第 4 条被并进第 3 条，
#: 第 3 条的证名变成「自汗，盗汗本品功能敛肺止汗」。**丢得静默**，最坏的一种。
#:
#: ``(?!\d)`` 是为了放过剂量里的「1.5g」——那个点后面跟数字，不是条目编号。
_APP_MARK = re.compile(r"(?<![\d.．])(\d{1,2})[.．、](?!\d)")

#: 证名与说明的分界：全角空格（教材常规写法）或「本品」（漏空格时的兜底）。
#: 「本品」是全书描述性文字的固定开头，比猜标点可靠。
_APP_DIVIDER = re.compile(r"[\u3000]|本品")

#: 证名长度上限。教材的证名是一串并列病证，本身就很长——黄芪第 1 条
#: 「气虚乏力，食少便溏，水肿尿少，中气下陷，久泻脱肛，便血崩漏」有 30 字。
#: 上限压到 20 会把它判成「不是证名」，然后整段塞进 description。
_MAX_SYNDROME = 60

#: 功效词的切分符。教材用「，、；」；「；」前后常是内服/外用两套。
_EFFECT_SPLIT = re.compile(r"[，、；;。]+")

#: 「内服」是给药途径，不是功效本身，剥掉。
#:
#: **「外用」不能剥**——它是有信息量的限定：教材写「外用消风祛斑」（菟丝子）、
#: 「外用消肿止痛」（栀子），意思和「消风祛斑」「消肿止痛」不一样，剥掉之后
#: 内服外用就混成一件事了。人工 monograph 里这两味也正是写「外用消风祛斑」。
_ROUTE_PREFIX = ("内服",)

#: 功效的分组标记：``生用：`` ``煅用：`` ``制何首乌：`` 之类。
#: 只在冒号前取 2–10 个字，且不含切分符——避免把正常功效词吃进标记里。
_PREP_MARK = re.compile(r"^(?P<prep>[^，、；;。：:]{2,10})[：:]\s*(?P<term>.*)$")

#: 禁忌按句切。
_SENTENCE = re.compile(r"[。；;]")


@dataclass
class NormalizedHerb:
    """归一后的药材。所有字段要么有值，要么明确为空——没有「差不多」。"""

    name: str
    page: int
    category: str = ""
    chapter_no: str = ""
    section: str = ""
    nature: str = ""
    flavors: list[str] = field(default_factory=list)
    toxicity: str = ""
    toxicity_source: str = "inferred"
    meridians: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    effects_raw: list[str] = field(default_factory=list)
    #: 功效词 → 分组标记（生用/煅用/制何首乌…）。没分组的药为空字典。
    effect_prep: dict[str, str] = field(default_factory=dict)
    dosage_text: str = ""
    dose_min: float | None = None
    dose_max: float | None = None
    cautions: list[str] = field(default_factory=list)
    indications: list[dict[str, str]] = field(default_factory=list)
    #: 哪些字段没抽到。空列表表示全齐。
    missing: list[str] = field(default_factory=list)
    #: 各字段的教材原文，供 G008 子串校验与前端展示。
    quotes: dict[str, str] = field(default_factory=dict)


def load_canonical() -> dict[str, str]:
    """功效同义映射（变体 → 保留写法）。缺文件时返回空映射，不报错。"""

    if not EFFECT_SYNONYMS.exists():
        return {}
    payload = json.loads(EFFECT_SYNONYMS.read_text(encoding="utf-8"))
    return dict(payload.get("canonical") or {})


def build_effect_whitelist(entries: list[RawEntry]) -> frozenset[str]:
    """闭集：教材里出现过的全部功效词（归一后）。

    这是 P4 的白名单。抽取时只认这些词，模型也别想造新词。
    """

    canonical = load_canonical()
    terms: set[str] = set()
    for entry in entries:
        for term in split_effects(entry.fields.get("功效", "")):
            terms.add(canonical.get(term, term))
    return frozenset(terms)


def split_effect_preps(raw: str) -> list[tuple[str, str]]:
    """切【功效】，返回 ``[(分组标记, 功效词)]``；标记为空表示原文没分组。

    分组标记是「生用：」「煅用：」「制何首乌：」这类炮制/用法限定词。它**只写在
    一组的第一条前面**，后面几条靠上下文继承::

        石膏：生用：清热泻火，除烦止渴；煅用：收湿，生肌，敛疮，止血。
        何首乌：制何首乌：补肝肾，益精血，乌须发，强筋骨，化浊降脂。
                生何首乌：解毒，消痈，截疟，润肠通便。

    只按「，、；」切的话，前两条会变成「生用：清热泻火」这种带着标记的词，
    后四条则彻底丢掉归属——读者无从知道「生肌」是生用还是煅用，而石膏生用
    清热、煅用收湿是**两件不同的事**，混在一起就是把药性说错了。

    所以标记要**沿用到下一处标记出现为止**，且标记与功效词分开存
    （``effect_prep``），功效词本身保持干净。
    """

    out: list[tuple[str, str]] = []
    prep = ""
    for chunk in _EFFECT_SPLIT.split(raw):
        term = chunk.strip()
        if not term:
            continue
        mark = _PREP_MARK.match(term)
        if mark:
            prep = mark.group("prep").strip()
            term = mark.group("term").strip()
        for prefix in _ROUTE_PREFIX:
            if term.startswith(prefix) and len(term) > len(prefix) + 1:
                term = term[len(prefix):]
                break
        if 2 <= len(term) <= 10:
            out.append((prep, term))
    return out


def split_effects(raw: str) -> list[str]:
    """把【功效】原文切成词条，剥掉「内服」前缀与「生用：」这类分组标记。"""

    return [term for _, term in split_effect_preps(raw)]


#: 毒性四档的严重度。多处出现时取**最严**的一档。
_TOX_RANK = {"无毒": 0, "小毒": 1, "有毒": 2, "大毒": 3}


def pick_toxicity(raw: str) -> str:
    """从【药性】**全串**里挑毒性，取最严重的一档。

    不能只看第一个句号前那段。川乌的教材原文是::

        辛、苦，热。归心、肝、肾、脾经。生川乌有大毒，制川乌有毒。

    毒性写在**第二个句号之后**，只扫首句就会把「大毒」读成「无毒」——
    川乌是十八反、十九畏的主角，读成无毒等于把一道该拦的方子放过去。

    同一味药既写「生品有大毒」又写「制品有毒」时取大毒：这是教学平台的
    处方审查，宁可多拦一次，也不能少拦一次。
    """

    best = ""
    for word, value in _TOXICITIES:
        if word in raw and (not best or _TOX_RANK[value.value] > _TOX_RANK[best]):
            best = value.value
    return best


def parse_nature(raw: str) -> tuple[str, list[str], str, str]:
    """拆【药性】→ ``(四气, 五味[], 毒性, 归经串)``。

    教材格式固定：``辛、微苦，温。归肺、膀胱经。`` 或 ``苦，寒；有毒。归肺、胃经。``
    前半句是五味，四气在逗号后，毒性常在分号后——**但也可能在第二个句号之后**，
    所以毒性单独走 ``pick_toxicity()`` 扫全串。
    """

    # 五味和四气在第一个句号之前；毒性可能在分号后
    head = raw.split("。")[0]
    nature = ""
    for candidate in _NATURES:
        if candidate in head:
            nature = candidate
            break

    # 五味：四气之前的那一段
    flavor_part = head
    if nature:
        flavor_part = head.split(nature)[0]
    flavors: list[str] = []
    for candidate in _FLAVORS:
        if candidate in flavor_part and candidate not in flavors:
            flavors.append(candidate)
    # 「微苦」命中时不要再算「苦」
    flavors = [
        f for f in flavors
        if not any(f != other and f in other for other in flavors)
    ]
    # **按教材原文的出现顺序排。** 白名单里的顺序是「苦在辛前」，
    # 直接用它输出会把教材的「辛、苦」写成「苦、辛」——读起来没差，
    # 但复核页要做「这几个字能不能在原文里找到」的子串比对，
    # 顺序一乱就报假警，人还得回头核对一遍。少一处噪音就少一次怀疑。
    flavors.sort(key=lambda item: flavor_part.find(item))

    toxicity = pick_toxicity(raw)

    meridians: list[str] = []
    if match := _MERIDIAN.search(raw):
        for piece in re.split(r"[、，,]", match.group(1)):
            name = piece.strip()
            if name in MERIDIAN_WHITELIST:
                meridians.append(name)
    return nature, flavors, toxicity, "、".join(meridians)


def parse_dosage(raw: str) -> tuple[float | None, float | None]:
    """从【用法用量】抽剂量区间。

    **数字用正则，不用模型**——模型会把「2～10g」读成「2-10克」再算错。
    """

    if match := _DOSE_RANGE.search(raw):
        return float(match.group(1)), float(match.group(2))
    if match := _DOSE_SINGLE.search(raw):
        value = float(match.group(1))
        return value, value
    return None, None


def split_cautions(raw: str) -> list[str]:
    """【使用注意】按句切。太短的碎片丢掉（「慎用。」这种没信息量）。"""

    out: list[str] = []
    for piece in _SENTENCE.split(raw):
        text = piece.strip()
        if len(text) >= 4:
            out.append(text)
    return out


def _split_head(block: str) -> tuple[str, str]:
    """一段主治里切出「证名 ｜ 说明」。切不出证名就返回空串，**不猜**。

    三棱的【应用】整段是鉴别说明（「三棱所主治的病证与莪术相同……」），
    根本没有证名。以前这段被整段当成证名塞进去，于是图里多出一个叫
    「三棱所主治的病证与莪术相同，二者常相须为用」的证候节点。
    现在返回空串，门禁 G010 会报「切不出证名，整段保留」——这才是实话。
    """

    block = block.strip()
    if not block:
        return "", ""
    match = _APP_DIVIDER.search(block)
    if match is None:
        return "", block
    head = block[: match.start()].strip()
    rest = block[match.start() :].lstrip("\u3000").strip()
    if not (2 <= len(head) <= _MAX_SYNDROME):
        # 分界符前那段不像证名（太长/太短），整段留作说明，证名留空。
        return "", block
    return head, rest


def split_indications(raw: str) -> list[dict[str, str]]:
    """【应用】切主治条目。

    教材有两种写法，都要认：

    1. **带编号**（多主治）：``1.风寒感冒　本品味辛发散…2.胸闷喘咳　本品辛散…``
    2. **不带编号**（单主治）：``血虚津亏，肠燥便秘　本品甘平，质润多脂…``

    证名与说明的分界是**全角空格 U+3000**；教材漏写空格时（五倍子第 4 条）
    退到「本品」。两个都没有就说明这味药的【应用】是整段说明文（三棱），
    证名留空。

    切不出证名时**置空并整段保留**，绝不猜一个证名出来。
    """

    if not raw:
        return []

    marks = list(_APP_MARK.finditer(raw))
    if marks:
        items: list[dict[str, str]] = []
        for index, match in enumerate(marks):
            start = match.end()
            end = marks[index + 1].start() if index + 1 < len(marks) else len(raw)
            syndrome, description = _split_head(raw[start:end])
            items.append({"syndrome": syndrome, "description": description[:400]})
        return items

    syndrome, description = _split_head(raw)
    return [{"syndrome": syndrome, "description": description[:400]}]


def normalize_one(entry: RawEntry, canonical: dict[str, str]) -> NormalizedHerb:
    """单个条目 → ``NormalizedHerb``。抽不到的字段进 ``missing``。"""

    herb = NormalizedHerb(
        name=entry.name,
        page=entry.page,
        section=entry.section,
        chapter_no=entry.chapter_no,
    )

    # ---- 分类：直接抄章名 ----
    try:
        herb.category = Category(entry.chapter).value
    except ValueError:
        herb.missing.append("category")

    # ---- 药性 ----
    nature_raw = entry.fields.get("药性") or entry.fields.get("性味") or ""
    herb.quotes["药性"] = nature_raw
    if nature_raw:
        nature, flavors, toxicity, meridians = parse_nature(nature_raw)
        herb.nature = nature
        herb.flavors = flavors
        herb.meridians = meridians.split("、") if meridians else []
        if toxicity:
            herb.toxicity = toxicity
            herb.toxicity_source = "stated"
        else:
            # 教材的编排惯例：**只有有毒的才标**。所以「未提」按无毒处理，
            # 但必须记成 inferred，让下游知道这是推断而不是原文。
            # 想严格一点的地方（比如 T1 检查）可以只信 stated。
            herb.toxicity = Toxicity.NONE.value
            herb.toxicity_source = "inferred"
    else:
        herb.toxicity = Toxicity.UNKNOWN.value
        herb.toxicity_source = "missing"
    for name, value in (
        ("nature", herb.nature),
        ("flavors", herb.flavors),
        ("meridians", herb.meridians),
    ):
        if not value:
            herb.missing.append(name)

    # ---- 功效 ----
    pairs = split_effect_preps(entry.fields.get("功效", ""))
    herb.quotes["功效"] = entry.fields.get("功效", "")
    herb.effects_raw = [term for _, term in pairs]
    herb.effects = [canonical.get(t, t) for t in herb.effects_raw]
    # 去重但保序
    seen: set[str] = set()
    herb.effects = [e for e in herb.effects if not (e in seen or seen.add(e))]
    # 分组标记跟着归一后的写法走，别让 canonical 改名后对不上
    herb.effect_prep = {
        canonical.get(term, term): prep for prep, term in pairs if prep
    }
    if not herb.effects:
        herb.missing.append("effects")

    # ---- 剂量 ----
    herb.quotes["用法用量"] = entry.fields.get("用法用量", "")
    herb.dosage_text = herb.quotes["用法用量"]
    herb.dose_min, herb.dose_max = parse_dosage(herb.dosage_text)
    if herb.dose_min is None:
        herb.missing.append("dosage")

    # ---- 禁忌 ----
    herb.quotes["使用注意"] = entry.fields.get("使用注意", "")
    herb.cautions = split_cautions(herb.quotes["使用注意"])
    # 使用注意不是每味都有，缺了不算缺失（教材本来就不写）

    # ---- 主治 ----
    herb.quotes["应用"] = entry.fields.get("应用", "")
    herb.indications = split_indications(herb.quotes["应用"])
    if not herb.indications:
        herb.missing.append("indications")

    return herb


def normalize(entries: list[RawEntry]) -> list[NormalizedHerb]:
    canonical = load_canonical()
    return [normalize_one(entry, canonical) for entry in entries]


def main() -> int:
    parser = argparse.ArgumentParser(description="教材条目归一")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    entries = load_entries()
    herbs = normalize(entries)
    whitelist = build_effect_whitelist(entries)

    field_cov: Counter = Counter()
    for herb in herbs:
        for name in herb.missing:
            field_cov[name] += 1
    tox = Counter((h.toxicity, h.toxicity_source) for h in herbs)
    eff_len = Counter(len(h.effects) for h in herbs)

    print(f"条目: {len(herbs)}")
    print(f"功效闭集: {len(whitelist)} 词")
    print(f"缺失字段: {dict(field_cov)}")
    print(f"毒性: {dict(tox)}")
    print(f"功效词数分布: {dict(sorted(eff_len.items()))}")
    print(f"主治条目总数: {sum(len(h.indications) for h in herbs)}")
    print(f"禁忌条数: {sum(len(h.cautions) for h in herbs)}")

    if args.stats:
        return 0

    payload = {
        "version": "textbook_normalized_v1",
        "source": "《中药学》第十版（钟赣生）",
        "effect_whitelist": sorted(whitelist),
        "stats": {
            "entries": len(herbs),
            "effect_terms": len(whitelist),
            "missing_fields": dict(field_cov),
            "toxicity": {f"{k[0]}/{k[1]}": v for k, v in tox.items()},
        },
        "herbs": [
            {
                "name": h.name,
                "page": h.page,
                "category": h.category,
                "chapter_no": h.chapter_no,
                "section": h.section,
                "nature": h.nature,
                "flavors": h.flavors,
                "toxicity": h.toxicity,
                "toxicity_source": h.toxicity_source,
                "meridians": h.meridians,
                "effects": h.effects,
                "effects_raw": h.effects_raw,
                "effect_prep": h.effect_prep,
                "dosage_text": h.dosage_text,
                "dose_min": h.dose_min,
                "dose_max": h.dose_max,
                "cautions": h.cautions,
                "indications": h.indications,
                "missing": h.missing,
                "quotes": h.quotes,
            }
            for h in herbs
        ],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"json={OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
