"""P4 教材抽取管道的回归测试（parse → normalize → gates）。

这条管道 2772 行、零测试，改一个正则没人知道坏在哪。这里只钉住
**踩过的坑**，不追求覆盖率：

- 药名靠拼音行锚定，不靠【药性】——教材有 2 处写成【性味】，靠字段会漏
- 药名中间是全角空格（「麻　黄」），不去掉就搜不到
- 【应用】的证名与说明之间是**全角空格 U+3000**，压掉就再也切不开
- 毒性可能写在**第二个句号之后**（川乌），只扫首句会把大毒读成无毒
- G008 是红线：引文必须是教材原文子串，编的一句都进不来
- G015 兜住 G006 漏的：原文写「有毒」、字段是「无毒」必须 fail
"""

from __future__ import annotations

from scripts.textbook.gates import SourceIndex, check
from scripts.textbook.normalize import (
    normalize_one,
    parse_nature,
    split_effect_preps,
    split_indications,
)
from scripts.textbook.parse import parse

# --------------------------------------------------------------------------- #
# 一、解析：靠拼音行切条目
# --------------------------------------------------------------------------- #

_MAHUANG_PAGE = """第八章　解表药
第一节　发散风寒药
麻　黄
Máhuáng（《神农本草经》）
【药性】辛、微苦，温。归肺、膀胱经。
【功效】发汗解表，宣肺平喘，利水消肿。
【应用】1.风寒感冒　本品发汗解表，为发汗解表之要药。
【用法用量】煎服，2～10g。
"""

_CHUANWU_PAGE = """第二十章　祛风湿药
川乌
Chuānwū（《神农本草经》）
【药性】辛、苦，热。归心、肝、肾、脾经。生川乌有大毒，制川乌有毒。
【功效】祛风除湿，温经止痛。
【应用】1.风寒湿痹　本品辛热升散苦燥。
【用法用量】制川乌煎服，1.5～3g，宜先煎、久煎。
【使用注意】生品内服宜慎，孕妇忌用。不宜与半夏、瓜蒌同用。
"""


def test_parse_uses_pinyin_anchor_and_strips_name_space() -> None:
    """药名在拼音行的上一行，且「麻　黄」中间的全角空格必须去掉。"""

    entries = parse([_MAHUANG_PAGE])

    assert [e.name for e in entries] == ["麻黄"]
    assert entries[0].chapter == "解表药"
    assert entries[0].section == "发散风寒药"


def test_parse_keeps_fullwidth_space_inside_indications() -> None:
    """全角空格是「证名 ｜ 说明」的分界，不是排版垃圾，不能被压掉。"""

    entries = parse([_MAHUANG_PAGE])
    indications = split_indications(entries[0].fields["应用"])

    assert [item["syndrome"] for item in indications] == ["风寒感冒"]
    assert indications[0]["description"].startswith("本品发汗解表")


def test_parse_drops_catalog_pages() -> None:
    """目录页排版和正文一样（药名 + 拼音行），靠「有没有【功效】」区分。"""

    catalog = "麻　黄\nMáhuáng（《神农本草经》）\n"
    assert parse([catalog]) == []


# --------------------------------------------------------------------------- #
# 二、归一：毒性取全串最严
# --------------------------------------------------------------------------- #


def test_toxicity_scans_whole_field_not_only_first_sentence() -> None:
    """川乌把毒性写在第二个句号后：生川乌有大毒，制川乌有毒。"""

    raw = "辛、苦，热。归心、肝、肾、脾经。生川乌有大毒，制川乌有毒。"

    nature, flavors, toxicity, meridians = parse_nature(raw)

    assert nature == "热"
    assert set(flavors) == {"辛", "苦"}
    assert meridians == "心、肝、肾、脾"
    # 两档并存时取最严：处方审查宁可多拦一次
    assert toxicity == "大毒"


def test_toxicity_absent_means_inferred_none_not_unknown() -> None:
    """教材的惯例是「有毒的才标」，所以未标按无毒处理，但必须记成 inferred。"""

    herb = normalize_one(parse([_MAHUANG_PAGE])[0], {})

    assert herb.toxicity == "无毒"
    assert herb.toxicity_source == "inferred"


def test_normalize_extracts_dosage_range_with_regex() -> None:
    """剂量用正则，不让模型算——「2～10g」必须拆成 2 和 10。"""

    herb = normalize_one(parse([_MAHUANG_PAGE])[0], {})

    assert (herb.dose_min, herb.dose_max) == (2.0, 10.0)
    assert herb.category == "解表药"


# --------------------------------------------------------------------------- #
# 三、门禁：G008 红线 + G015 毒性一致性
# --------------------------------------------------------------------------- #


def _gated(herb: dict, pages: list[str], whitelist: frozenset[str] = frozenset()):
    return check(herb, SourceIndex(pages), whitelist)


def _clean_herb(**overrides: object) -> dict:
    base = {
        "name": "麻黄",
        "page": 10,
        "category": "解表药",
        "nature": "温",
        "flavors": ["辛"],
        "meridians": ["肺"],
        "toxicity": "无毒",
        "toxicity_source": "inferred",
        "dose_min": 2.0,
        "dose_max": 10.0,
        "dosage_text": "煎服，2～10g。",
        "effects": ["发汗解表"],
        "indications": [{"syndrome": "风寒感冒", "description": "本品发汗解表"}],
        "cautions": [],
        "missing": [],
        "quotes": {"药性": "辛、微苦，温。归肺、膀胱经。"},
    }
    base.update(overrides)
    return base


def test_g008_rejects_fabricated_quote() -> None:
    """引文不在教材原文里 → fail。这是整条流水线的价值锚点。"""

    herb = _clean_herb(quotes={"药性": "辛、微苦，温。归肺、膀胱经。", "功效": "发汗解表，兼能抗癌"})

    result = _gated(herb, [_MAHUANG_PAGE], frozenset({"发汗解表"}))

    assert result.worst == "fail"
    assert "G008" in result.failed_codes


def test_g008_accepts_quote_split_across_lines() -> None:
    """PDF 断行夹着换行和全角空格，压掉空白后仍算原文子串。"""

    pages = ["【药性】辛、微苦，\n温。归肺、膀胱经。"]
    herb = _clean_herb(quotes={"药性": "辛、微苦，温。归肺、膀胱经。"})

    result = _gated(herb, pages)

    assert "G008" not in result.failed_codes


def test_g015_catches_toxicity_mismatch() -> None:
    """原文写「有毒」、字段是「无毒」——比「未知」更危险，必须 fail。"""

    herb = _clean_herb(
        name="川乌",
        toxicity="无毒",
        quotes={"药性": "辛、苦，热。归心、肝、肾、脾经。生川乌有大毒，制川乌有毒。"},
    )

    result = _gated(herb, [_CHUANWU_PAGE])

    assert result.worst == "fail"
    assert "G015" in result.failed_codes


def test_g006_warns_on_unknown_but_does_not_block() -> None:
    """「教材没写」不能伪装成「没问题」，也不能判成有毒。"""

    herb = _clean_herb(toxicity="毒性未知", toxicity_source="missing")

    result = _gated(herb, [_MAHUANG_PAGE], frozenset({"发汗解表"}))

    assert result.worst == "warn"
    assert "G006" in {hit.code for hit in result.hits}


# --------------------------------------------------------------------------- #
# 四、功效分组标记：石膏「生用/煅用」、何首乌「制/生」
# --------------------------------------------------------------------------- #


def test_effect_prep_scopes_to_whole_group() -> None:
    """「生用：」只写一次，后面几条也要继承它，否则不知道「生肌」是谁的功效。"""

    pairs = split_effect_preps("生用：清热泻火，除烦止渴；煅用：收湿，生肌，敛疮，止血。")

    assert pairs == [
        ("生用", "清热泻火"),
        ("生用", "除烦止渴"),
        ("煅用", "收湿"),
        ("煅用", "生肌"),
        ("煅用", "敛疮"),
        ("煅用", "止血"),
    ]


def test_effect_prep_keeps_term_clean() -> None:
    """分组标记不能留在功效词里——「生用：清热泻火」不是一个功效词。"""

    pairs = split_effect_preps(
        "制何首乌：补肝肾，益精血。生何首乌：解毒，消痈，截疟，润肠通便。"
    )

    terms = [term for _, term in pairs]
    assert terms == ["补肝肾", "益精血", "解毒", "消痈", "截疟", "润肠通便"]
    assert all("：" not in t for t in terms)
    assert {t: p for p, t in pairs}["消痈"] == "生何首乌"


def test_g016_blocks_when_prep_marker_dropped() -> None:
    """原文有「生用：」，字段却没切出分组标记 → fail。"""

    herb = _clean_herb(
        effects=["清热泻火"],
        quotes={"药性": "甘、辛，大寒。归肺、胃经。", "功效": "生用：清热泻火，除烦止渴。"},
    )

    result = _gated(herb, [_MAHUANG_PAGE])

    assert "G016" in result.failed_codes


def test_g016_passes_when_prep_marker_kept() -> None:
    herb = _clean_herb(
        effects=["清热泻火"],
        effect_prep={"清热泻火": "生用"},
        quotes={"药性": "甘、辛，大寒。归肺、胃经。", "功效": "生用：清热泻火，除烦止渴。"},
    )

    result = _gated(herb, [_MAHUANG_PAGE])

    assert "G016" not in result.failed_codes
