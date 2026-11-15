"""设计步：把「讲什么」之后的「长什么样」变成可确认、可渲染、可校验的数据。

三条硬规矩，都是上一轮踩坑换来的：

1. **槽位只写需求，不写资产 id**。模型编一个不存在的 ``asset_id`` 会重演
   ``XiaoqiMascot`` 式坏引用；解析权只属于 ``select`` 步。
2. **设计不可用自由文本表达**。「左图右文、纸底远山、四角角花」这种句子，
   用户判断不了成品观感，系统也没法校验；所以版式、密度、槽位全部落在枚举里，
   自由文本（``rationale``）只给人看，不参与渲染与校验。
3. **同一份设计必须能渲两次**：一次 0.3 保真（确认单里的线框），一次 1.0（成品）。
   这要求几何只有一处定义——十种原型的容量与结构就在这张表里。

十种原型不是十套模板，而是**十种版式语法**：每种只允许特定块类型、特定容量，
超出即报 ``capacity`` 类问题，由逐页回路定向重写，而不是悄悄压字。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .fontpairs import DEFAULT_TYPE_PAIR, coerce_type_pair
from .params import PptParams

# ── 枚举：全部可穷举，写进文档也是这几个值 ─────────────────────────────────

BlockType = Literal["point", "steps", "matrix", "figures", "alert", "quote", "def"]
Purpose = Literal["cover", "agenda", "section", "points", "compare", "matrix", "process", "data", "warning", "summary"]
Density = Literal["airy", "normal", "dense"]
SlotKind = Literal["figure", "evidence", "ornament", "ground"]
SlotStyle = Literal["plate", "line", "photo", "motif", "none"]
SlotAspect = Literal["portrait", "square", "landscape", "any"]
SlotClass = Literal["decorative", "evidence"]
Provenance = Literal["parametric", "library", "generated", "none"]

BLOCK_TYPES: tuple[BlockType, ...] = ("point", "steps", "matrix", "figures", "alert", "quote", "def")
PURPOSES: tuple[Purpose, ...] = ("cover", "agenda", "section", "points", "compare", "matrix", "process", "data", "warning", "summary")
THEME_IDS: tuple[str, ...] = ("paper", "ink", "celadon", "cinnabar")
PAPER_IDS: tuple[str, ...] = ("xuan-plain", "xuan-chain", "xuan-mottle", "ink-night", "celadon-mist", "cinnabar-warm")
GRID_IDS: tuple[str, ...] = ("classic", "wide", "tall")
TYPE_PAIRS: tuple[str, ...] = ("song-kai", "kai-song", "hei-song", "serif-full", "sans-full")
ORNAMENT_SETS: tuple[str, ...] = ("vine-a", "branch-b", "bamboo-d", "fret-e", "scroll-c", "none")

#: 密度三档与内容块上限联动。数字来自 16:9 舞台实测：超过 7 块必然压到地脚。
DENSITY_CAPS: dict[str, int] = {"airy": 3, "normal": 5, "dense": 7}

#: 字号阶梯（cqw）。七级，导出时由同一张表换算成 pt。
TYPE_SCALE: tuple[float, ...] = (7.6, 4.6, 3.2, 2.2, 1.8, 1.5, 1.3)
#: 每页装饰预算。超了就是「花」，不是「雅」。
DECOR_BUDGET = 2
#: 正文对比度下限（WCAG AA）。
MIN_CONTRAST = 4.5

#: 主题的前景/背景色。与 ``frontend/src/config/pptThemes.ts`` 同源；
#: 后端只用来算对比度，不参与渲染。多一个色值就要两边一起改——所以只有两个。
THEME_HEX: dict[str, dict[str, str]] = {
    "paper": {"bg": "FAF7F0", "fg": "1F1B16"},
    "ink": {"bg": "151719", "fg": "F2EFE8"},
    "celadon": {"bg": "F2F6F3", "fg": "173B30"},
    "cinnabar": {"bg": "FBF2ED", "fg": "2A1815"},
}


class ArchetypeSpec(BaseModel):
    """一种版式语法：允许什么块、装得下多少、典型视觉是什么。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    purpose: Purpose
    block_types: tuple[BlockType, ...]
    max_blocks: int
    item_chars: int
    #: 这一页最少需要几个内容块才算「成立」。空页不是版式问题，是内容问题。
    min_blocks: int = 1
    note: str = ""


#: 十种原型。顺序即生成时的候选顺序（同分取靠前者，保证确定性）。
ARCHETYPES: dict[str, ArchetypeSpec] = {
    spec.id: spec
    for spec in (
        ArchetypeSpec(id="cover", label="封面", purpose="cover", block_types=("point",), max_blocks=1, item_chars=24, note="界格 / 满版 / 极简三选一；无图走界格"),
        ArchetypeSpec(id="agenda", label="导览", purpose="agenda", block_types=("steps",), max_blocks=6, item_chars=14, note="序号 + 横线，最多六条"),
        ArchetypeSpec(id="section", label="章节", purpose="section", block_types=("point",), max_blocks=1, item_chars=16, min_blocks=0, note="巨号汉字序号做水印"),
        ArchetypeSpec(id="points", label="要点", purpose="points", block_types=("point",), max_blocks=5, item_chars=40, note="单栏左齐，五条以内"),
        ArchetypeSpec(id="compare", label="对照", purpose="compare", block_types=("matrix",), max_blocks=2, item_chars=30, note="双栏 + 中线，栏题 ≤10 字"),
        ArchetypeSpec(id="matrix", label="矩阵", purpose="matrix", block_types=("matrix",), max_blocks=3, item_chars=8, note="斑马格，3×3 以内"),
        ArchetypeSpec(id="process", label="流程", purpose="process", block_types=("steps",), max_blocks=4, item_chars=12, note="连接线 + 大号数字"),
        ArchetypeSpec(id="data", label="数据", purpose="data", block_types=("figures",), max_blocks=3, item_chars=6, note="数字卡，数值 ≤6 字符"),
        ArchetypeSpec(id="warning", label="警示", purpose="warning", block_types=("alert",), max_blocks=2, item_chars=36, note="朱砂竖条，最多两条"),
        ArchetypeSpec(id="summary", label="收束", purpose="summary", block_types=("point", "def"), max_blocks=4, item_chars=40, note="要点回顾 + 一句定论"),
    )
}

#: purpose → 旧渲染层的 ``kind``。保留这个映射是为了让「设计先落地、渲染后跟上」
#: 这段时间里旧稿子照常能看——升级不等于让已有资源变白屏。
PURPOSE_TO_KIND: dict[str, str] = {
    "cover": "cover",
    "agenda": "agenda",
    "section": "section",
    "points": "bullets",
    "compare": "compare",
    "matrix": "compare",
    "process": "agenda",
    "data": "bullets",
    "warning": "bullets",
    "summary": "summary",
}
KIND_TO_PURPOSE: dict[str, str] = {
    "cover": "cover",
    "agenda": "agenda",
    "section": "section",
    "bullets": "points",
    "compare": "compare",
    "summary": "summary",
}


# ── 契约 ────────────────────────────────────────────────────────────────────


class DesignSlot(BaseModel):
    """一个素材槽位的**需求**：要找什么、什么风格、必需还是可选。

    注意没有 ``asset_id``——那是 ``select`` 的产物。设计单里出现资产 id，
    就等于让模型替素材库做决定，而它并不知道库里有什么。
    """

    model_config = ConfigDict(extra="ignore")

    slot: SlotKind = "figure"
    role: str = Field(default="", max_length=20)
    subject: str = Field(default="无", max_length=40)
    style: SlotStyle = "motif"
    priority: Literal["required", "optional"] = "optional"
    aspect: SlotAspect = "any"
    slot_class: SlotClass = "decorative"
    #: 检索短词。subject 是给人读的画面描述，keywords 才是拿去搜的词。
    keywords: list[str] = Field(default_factory=list)
    #: 这一页的构图（``spec.COMPOSITIONS``）。排版层按它决定图的左右与铺满。
    composition: str = "auto"

    @field_validator("keywords", mode="before")
    @classmethod
    def _clip_words(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:12] for item in value if str(item).strip()][:6]

    @model_validator(mode="after")
    def _bind_class(self) -> DesignSlot:
        # 槽位类别由槽位本身决定，不允许调用方写反：evidence 位绝不能是装饰。
        self.slot_class = "evidence" if self.slot == "evidence" else "decorative"
        return self


class PageDesign(BaseModel):
    model_config = ConfigDict(extra="ignore")

    unit_id: str = Field(default="", max_length=32)
    archetype: str = "points"
    density: Density = "normal"
    slots: list[DesignSlot] = Field(default_factory=list, max_length=4)
    note: str = Field(default="", max_length=120)
    composition: str = "auto"

    @field_validator("archetype")
    @classmethod
    def _known(cls, value: str) -> str:
        text = str(value or "").strip()
        return text if text in ARCHETYPES else "points"

    @field_validator("slots")
    @classmethod
    def _budget(cls, values: list[DesignSlot]) -> list[DesignSlot]:
        # 装饰预算在契约层就卡住，而不是留到校验才发现「设计单自己就超了」。
        return values[:DECOR_BUDGET + 1]

    @property
    def spec(self) -> ArchetypeSpec:
        return ARCHETYPES.get(self.archetype, ARCHETYPES["points"])


class DeckDesign(BaseModel):
    """一份幻灯的设计单。与 ``ResourcePlan`` 同批确认，确认后生成期只读不重拟。"""

    model_config = ConfigDict(extra="ignore")

    theme_id: str = "paper"
    paper: str = "xuan-plain"
    type_pair: str = "song-kai"
    ornament_set: str = "vine-a"
    #: 版式母版（``masters.MASTERS`` 的键）。空 = 老方案，走 scene_art 自由排版。
    master_id: str = Field(default="", max_length=32)
    #: 母版色板变体序号（生成期夹回母版的变体数内）。
    accent_variant: int = Field(default=0, ge=0, le=3)
    #: 视觉方案（``spec.DeckLook`` 的落点）。老设计单没有这几项，照旧走处方名。
    palette: dict[str, str] = Field(default_factory=dict)
    paper_motif: str = ""
    ornament_motif: str = ""
    background: dict[str, Any] = Field(default_factory=dict)
    ornament: dict[str, Any] = Field(default_factory=dict)
    image_treatment: str = "plate"
    mood: str = Field(default="", max_length=120)
    rhythm: str = Field(default="", max_length=160)
    pages: list[PageDesign] = Field(default_factory=list, max_length=24)
    rationale: list[str] = Field(default_factory=list, max_length=8)
    assumptions: list[str] = Field(default_factory=list, max_length=8)
    revision: int = 0

    @field_validator("theme_id")
    @classmethod
    def _theme(cls, value: str) -> str:
        text = str(value or "").strip()
        return text if text in THEME_IDS else "paper"

    @field_validator("master_id", mode="before")
    @classmethod
    def _clip_master(cls, value: Any) -> str:
        return str(value or "").strip()[:32]

    @field_validator("accent_variant", mode="before")
    @classmethod
    def _clamp_variant(cls, value: Any) -> int:
        try:
            return max(0, min(3, int(value)))
        except (TypeError, ValueError):
            return 0


class SlotResolution(BaseModel):
    """一个槽位的解析结果。「这页为什么长这样」的唯一答案。"""

    model_config = ConfigDict(extra="ignore")

    unit_id: str = ""
    slot: str = ""
    #: 这一槽位当时要的是什么。值不一定是字符串（keywords 是列表），
    #: 所以收 Any——这里存的是「为什么选它」的证据，不是渲染输入。
    requested: dict[str, Any] = Field(default_factory=dict)
    result: Literal["parametric", "hit", "substitute", "generated", "none"] = "none"
    asset_id: str = ""
    provenance: Provenance = "none"
    score: int = 0
    license: str = ""
    attribution: str = ""
    file: str = ""
    #: 远程地址（已合成的 wsrv 代理）。素材不落盘，稿子里钉的是这条。
    url: str = ""
    thumb: str = ""
    source: str = ""
    pinned_at: str = ""
    fallback_reason: str = ""


# ── 版式判定：由内容决定，不由页序决定 ──────────────────────────────────────

_AGENDA_HINTS = ("目录", "导览", "纲要", "路线", "全课地图", "本讲", "议程")
_COMPARE_HINTS = ("对比", "比较", "区别", "异同", "鉴别", "辨析", "不同", "差别", "区分", "vs", "VS")
_PROCESS_HINTS = ("步骤", "流程", "操作", "炮制", "工序", "先", "再", "然后")
_DATA_HINTS = ("数据", "统计", "占比", "比例", "数量", "含量", "剂量梯度")
_WARNING_HINTS = ("禁忌", "注意", "警示", "慎用", "不良反应", "毒性", "配伍禁忌")
_MATRIX_HINTS = ("矩阵", "维度", "分类表", "分型", "对照表", "一览")
_SECTION_HINTS = ("第一章", "第二章", "上篇", "下篇", "第一部分", "第二部分", "小结之前")


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def purpose_for_plan_unit(
    label: str,
    intent: str,
    detail: str,
    *,
    index: int,
    total: int,
) -> Purpose:
    """按内容判定这一页该用什么版式。

    顺序即优先级：**先看内容说了什么，再看它在整份里的位置**。升级前是反的
    （``index == 0`` 无条件封面、末页无条件总结），于是「第 8 页想放对照表」
    在数据层无法表达。
    """

    text = f"{label} {intent} {detail}"
    if index == 0:
        return "cover"
    if _has(text, _COMPARE_HINTS):
        return "compare"
    if _has(text, _MATRIX_HINTS):
        return "matrix"
    if _has(text, _PROCESS_HINTS):
        return "process"
    if _has(text, _DATA_HINTS):
        return "data"
    if _has(text, _WARNING_HINTS):
        return "warning"
    if _has(label, _AGENDA_HINTS) and total >= 4:
        return "agenda"
    if _has(label, _SECTION_HINTS):
        return "section"
    # 末页收束：放在 section 之后、points 之前。放在最前面会把「最后一页讲禁忌」
    # 也压成总结页；放在最后又会让「只有标题的末页」落成 section。
    if total >= 3 and index == total - 1:
        return "summary"
    return "points"


def purpose_for_blocks(blocks: list[dict[str, Any]]) -> Purpose:
    """由块结构**重算** purpose。

    不采信模型自报的版式：它经常把两行字标成 compare，渲染层就画出两个空栏。
    同分按声明顺序，保证同一份内容永远得到同一个版式。
    """

    kinds = [str(block.get("type") or "") for block in blocks]
    if not kinds:
        return "section"
    if "matrix" in kinds:
        # 三栏以上是矩阵，两栏是对照——看的是**栏数**，不是块数。
        widest = max((len(block.get("columns") or []) for block in blocks if str(block.get("type") or "") == "matrix"), default=0)
        return "matrix" if widest >= 3 else "compare"
    if "figures" in kinds:
        return "data"
    if "alert" in kinds:
        return "warning"
    if "steps" in kinds:
        steps = sum(len(block.get("items") or []) for block in blocks if str(block.get("type") or "") == "steps")
        return "process" if steps <= 4 else "agenda"
    if "quote" in kinds or "def" in kinds:
        return "summary" if "def" in kinds else "points"
    return "points"


# ── 提案：确定性地把方案落成设计单 ──────────────────────────────────────────


def propose_design(
    topic: str,
    params: PptParams | dict[str, Any],
    plan: Any,
    *,
    theme: str = "",
) -> DeckDesign:
    """由方案生成一份可渲染的设计单。

    版式有两个来源，优先级固定：

    1. **施工图**（``ppt.spec.DeckSpec``）——模型读整句话给的每页角色、密度与素材需求。
       这是主路径：一场给新生的入门试讲里，「中药从哪来」该是流程页、「寒热温凉」该是
       对照页，这些判断只有读过那句话的人给得出。
    2. **关键词**（``purpose_for_plan_unit``）——老方案没有施工图时照旧，行为与升级前
       逐字节一致。

    这两条都不能省：第一条让新版方案排版错落，第二条让已有资源照常能看。
    """

    p = params if isinstance(params, PptParams) else PptParams.parse(params)
    units = _plan_units(plan)
    spec = _deck_spec(plan)
    look = getattr(spec, "look", None) if spec is not None else None
    from .masters import by_id as master_by_id

    master = master_by_id(str(getattr(look, "master_id", "") or "")) if look is not None else None
    # look 的 theme/type_pair 已在 spec 侧回填过（模型显式给过的保留、没给的取
    # 母版默认），这里直接读——再拿母版盖一次会把「用户明说的 cinnabar」抹掉。
    theme_id = ((str(getattr(look, "theme", "") or "") if look else "") or theme or p.theme or "paper").strip()
    if master is not None:
        accent = max(0, min(int(getattr(look, "accent_variant", 0) or 0), len(master.spec.palette_variants) - 1))
        palette = dict(master.spec.palette(accent))
        palette.update(_palette_of(look))
    else:
        accent = 0
        palette = _palette_of(look)
    pages: list[PageDesign] = []
    for index, unit in enumerate(units):
        label = str(unit.get("label") or "")
        intent = str(unit.get("intent") or "")
        detail = str(unit.get("detail") or "")
        page_spec = spec.pages[index] if spec is not None and index < len(spec.pages) else None
        if page_spec is not None:
            archetype = _archetype_for(page_spec.role)
            density = page_spec.density
            composition = _composition_of(page_spec, archetype)
            slots = _slots_for_page(page_spec, archetype, topic or label, theme_id, composition, look)
        else:
            purpose = purpose_for_plan_unit(label, intent, detail, index=index, total=len(units))
            archetype = _archetype_for(purpose)
            density = _density_for(detail, purpose)
            composition = _composition_of(None, archetype)
            slots = _slots_for(archetype, topic, theme_id)
        pages.append(
            PageDesign(
                unit_id=str(unit.get("id") or f"u{index}"),
                archetype=archetype,
                density=density,
                slots=slots,
                note=ARCHETYPES[archetype].note,
                composition=composition,
            )
        )

    return DeckDesign(
        theme_id=theme_id,
        paper=_paper_for(theme_id),
        type_pair=coerce_type_pair(getattr(look, "type_pair", "")) if look else DEFAULT_TYPE_PAIR,
        ornament_set=_ornament_for(theme_id),
        master_id=(master.spec.id if master is not None else ""),
        accent_variant=accent,
        palette=palette,
        paper_motif=_paper_motif_of(look, theme_id),
        ornament_motif=_ornament_motif_of(look),
        background=_background_of(look),
        ornament=_ornament_payload(look),
        image_treatment=str(getattr(look, "image_treatment", "") or "plate") if look else "plate",
        mood=str(getattr(look, "mood", "") or "") if look else "",
        rhythm=str(getattr(look, "rhythm", "") or "") if look else "",
        pages=pages,
        rationale=_rationale(topic, pages, theme_id, look=look),
        assumptions=_assumptions(p, len(units), audience=getattr(spec, "audience", "") if spec is not None else ""),
    )


def _palette_of(look: Any) -> dict[str, str]:
    """自定义配色。半套不如不给：缺底或缺字就整条丢掉，回落主题。"""

    palette = getattr(look, "palette", None)
    if palette is None:
        return {}
    data = palette.model_dump() if hasattr(palette, "model_dump") else dict(palette)
    if not (data.get("bg") and data.get("fg")):
        return {}
    return {key: value for key, value in data.items() if value}


def _paper_motif_of(look: Any, theme_id: str) -> str:
    if look is None:
        return ""
    background = getattr(look, "background", None)
    if background is not None and getattr(background, "kind", "") == "motif":
        return str(getattr(background, "motif", "") or "")
    return "paper-plain"


def _ornament_motif_of(look: Any) -> str:
    if look is None:
        return ""
    ornament = getattr(look, "ornament", None)
    if ornament is None:
        return "none"
    return str(getattr(ornament, "motif", "") or "none")


def _background_of(look: Any) -> dict[str, Any]:
    """背景需求。``kind=asset`` 时带 subject/keywords，交给 select 去找真图。"""

    background = getattr(look, "background", None) if look is not None else None
    if background is None:
        return {}
    return {
        "kind": str(getattr(background, "kind", "motif") or "motif"),
        "motif": str(getattr(background, "motif", "") or ""),
        "subject": str(getattr(background, "subject", "") or ""),
        "keywords": list(getattr(background, "keywords", []) or []),
        "veil": float(getattr(background, "veil", 0.0) or 0.0),
        "tone": str(getattr(background, "tone", "bg") or "bg"),
    }


def _ornament_payload(look: Any) -> dict[str, Any]:
    ornament = getattr(look, "ornament", None) if look is not None else None
    if ornament is None:
        return {}
    return {
        "motif": str(getattr(ornament, "motif", "") or "none"),
        "subject": str(getattr(ornament, "subject", "") or ""),
        "keywords": list(getattr(ornament, "keywords", []) or []),
        "corners": list(getattr(ornament, "corners", []) or []),
        "ask": str(getattr(ornament, "ask", "") or ""),
    }


def _composition_of(page: Any, archetype: str) -> str:
    """这一页怎么摆。方案给了就用方案的，没给就按角色与有没有图推一个。

    ``auto`` 不是一个渲染分支，而是一个承诺：「排版层自己定」。
    这里把它落到具体构图上，设计单才是可核对的。
    """

    from .spec import COMPOSITIONS

    declared = str(getattr(page, "composition", "") or "").strip() if page is not None else ""
    if declared in COMPOSITIONS and declared != "auto":
        return declared
    ask = getattr(page, "material", None) if page is not None else None
    has_image = bool(ask is not None and not ask.empty)
    if archetype == "cover":
        return "text-image-right" if has_image else "centered"
    if archetype in ("section", "summary"):
        return "centered"
    if archetype in ("agenda", "process", "data", "compare", "matrix"):
        return "text-only"
    return "text-image-right" if has_image else "text-only"


def _deck_spec(plan: Any) -> Any:
    """取施工图。坏数据 / 老方案一律返回 None，不影响出设计单。"""

    from .spec import deck_spec_of

    raw = plan.model_dump() if hasattr(plan, "model_dump") else plan
    return deck_spec_of(raw if isinstance(raw, dict) else None)


def _archetype_for(purpose: str) -> str:
    return purpose if purpose in ARCHETYPES else "points"


def _slots_for_page(
    page: Any,
    archetype: str,
    topic: str,
    theme_id: str,
    composition: str = "auto",
    look: Any = None,
) -> list[DesignSlot]:
    """按**这一页自己的素材需求与构图**给槽位。

    以前每页的 ``subject`` 都是 ``topic[:40]``，也就是用户的整句话——九页拿着同一句
    去搜图，搜不到就全空，用户看到的是「素材库里明明有图却没用上」。现在：

    - 主体来自施工图（「人参饮片」「明代本草彩绘」），没写就用这一页的标题；
    - **有没有图位由构图决定**：纯文字/居中/满版页不再硬挂一张图，
      那正是「每页都长一样」的来源；
    - 背景可以是库里的实拍（施工图说 ``kind=asset``），角饰可以是母题、
      库里的纹样，或者明确「不要」。
    """

    ask = getattr(page, "material", None)
    slots: list[DesignSlot] = []
    background = _background_of(look) if look is not None else {}
    if background.get("kind") == "solid":
        # solid 背景 = 明说不要底纹。再挂一个 ground 槽，select 会抽一张纸纹
        # 回来把「纯色」盖掉（ink/modern 母版踩过：深墨底变成米白纸）。
        pass
    elif background.get("kind") == "asset" and (background.get("subject") or background.get("keywords")):
        slots.append(
            DesignSlot(
                slot="ground",
                role="背景图",
                subject=str(background.get("subject") or "")[:40] or "背景",
                style="photo",
                priority="optional",
                aspect="landscape",
                keywords=list(background.get("keywords") or []),
                composition=composition,
            )
        )
    else:
        slots.append(
            DesignSlot(
                slot="ground",
                role="底纹",
                subject="通用",
                style="motif",
                priority="optional",
                aspect="landscape",
                composition=composition,
            )
        )

    ornament = _ornament_payload(look) if look is not None else {}
    wants_ornament = bool(
        (ornament.get("motif") and ornament.get("motif") != "none")
        or ornament.get("subject")
        or ornament.get("keywords")
    )
    if wants_ornament and archetype in ("cover", "section", "summary"):
        slots.append(
            DesignSlot(
                slot="ornament",
                role="角饰",
                subject=str(ornament.get("subject") or "")[:40] or "通用",
                # style=motif 而不是 photo：库里搜不到真纹样时，select 的参数化
                # 阶梯还能兜一朵程序角花——「要真纹样」不该等价于「什么都没有」。
                style="motif",
                priority="optional",
                aspect="square",
                keywords=list(ornament.get("keywords") or []),
                composition=composition,
            )
        )

    treatment = str(getattr(look, "image_treatment", "") or "plate") if look is not None else "plate"
    shows_figure = composition in ("text-image-right", "text-image-left") and treatment != "none"
    if not shows_figure:
        # 满版图页的图在背景上；纯文字页不配图。给一个空槽只会让「缺图」变成结论。
        return slots[: DECOR_BUDGET + 1]

    subject = (ask.subject if ask is not None and not ask.empty else "") or str(getattr(page, "heading", "") or topic)[:40]
    style = ask.style if ask is not None and not ask.empty else treatment
    keywords = list(getattr(ask, "keywords", []) or []) if ask is not None else []
    # 证据位只在内容页成立：封面/章节的图是氛围与器物，拿它当「这就是那味药」
    # 会被证据位规则拒掉（证据位只认 local.herbs 与 api.wellcome），白跑一轮。
    if ask is not None and not ask.empty and ask.slot == "evidence" and archetype not in ("cover", "section", "agenda", "summary"):
        slots.append(
            DesignSlot(
                slot="evidence",
                role="证据图",
                subject=subject,
                style="photo",
                priority=ask.priority,
                aspect="landscape",
                keywords=keywords,
                composition=composition,
            )
        )
        return slots[: DECOR_BUDGET + 1]
    slots.append(
        DesignSlot(
            slot="figure",
            role="主图" if archetype in ("cover", "section") else "配图",
            subject=subject,
            style=style,
            priority=(ask.priority if ask is not None else "optional"),
            aspect="portrait" if archetype in ("cover", "section") else "landscape",
            keywords=keywords,
            composition=composition,
        )
    )
    return slots[: DECOR_BUDGET + 1]


def _slots_for(archetype: str, topic: str, theme_id: str) -> list[DesignSlot]:
    """老方案（没有施工图）的槽位规则，行为与升级前一致。

    ``ground``（底纹）永远是母题生成，零成本零许可；``ornament`` 只在需要「装帧感」
    的三种页面出现；``figure`` 分两种口径：

    - 封面/章节：**主图**，竖构图，点题用；
    - 要点/对照/矩阵：**配图**，横构图，落在右栏（版式本身是「左文右图」）。

    全部 `optional`。没图就走无图版式——这是「素材缺失不崩」的第一道保险。

    正文页原来一个槽都不给，可渲染层早就支持右栏图位了：能力有、提案不写，
    结果就是**库里存着图，页面上永远配不出来**。
    """

    slots: list[DesignSlot] = [
        DesignSlot(slot="ground", role="底纹", subject="通用", style="motif", priority="optional", aspect="landscape"),
    ]
    if archetype in ("cover", "section", "summary"):
        slots.append(DesignSlot(slot="ornament", role="角饰", subject="通用", style="motif", priority="optional", aspect="square"))
    if archetype in ("cover", "section"):
        slots.insert(
            1,
            DesignSlot(
                slot="figure",
                role="主图",
                subject=topic[:40] or "通用",
                style="plate" if theme_id != "ink" else "line",
                priority="optional",
                aspect="portrait",
            ),
        )
    elif archetype in ("points", "compare", "matrix", "warning"):
        slots.insert(
            1,
            DesignSlot(
                slot="figure",
                role="配图",
                subject=topic[:40] or "通用",
                style="photo" if theme_id != "ink" else "line",
                priority="optional",
                aspect="landscape",
            ),
        )
    return slots[: DECOR_BUDGET + 1]


def _density_for(detail: str, purpose: str) -> Density:
    """密度跟内容量走，不跟页面位置走。"""

    count = len([part for part in re.split(r"[；;\n]", detail or "") if part.strip()])
    if purpose in ("cover", "section", "data"):
        return "airy"
    if count >= 5:
        return "dense"
    if count <= 2:
        return "airy"
    return "normal"


def _paper_for(theme_id: str) -> str:
    return {
        "paper": "xuan-plain",
        "ink": "ink-night",
        "celadon": "celadon-mist",
        "cinnabar": "cinnabar-warm",
    }.get(theme_id, "xuan-plain")


def _ornament_for(theme_id: str) -> str:
    # 角饰是「疏」的艺术：讲稿风给折枝，深色底给缠枝，其余给卷草。
    return {"paper": "vine-a", "ink": "scroll-c", "celadon": "branch-b", "cinnabar": "vine-a"}.get(theme_id, "vine-a")


def _rationale(topic: str, pages: list[PageDesign], theme_id: str, *, look: Any = None) -> list[str]:
    """给用户看的 3–5 条理由。只解释，不参与渲染与校验。"""

    counts: dict[str, int] = {}
    for page in pages:
        counts[page.archetype] = counts.get(page.archetype, 0) + 1
    top = "、".join(f"{ARCHETYPES[key].label}{value} 页" for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:4])
    # 「排版单一」是用户的直接感受，所以把「几种版式」写在最显眼的一句里。
    variety = len(counts)
    wanted = [slot.subject for page in pages for slot in page.slots if slot.role in ("主图", "配图", "证据图") and slot.subject and slot.subject != "通用"]
    compositions = len({page.composition for page in pages})
    mood = str(getattr(look, "mood", "") or "") if look is not None else ""
    lines = [
        (
            f"这份稿子的气质：{mood}。配色、底纹、角饰、字体按这套走，不单独改某一个色值。"
            if mood
            else f"整份按「{topic or '本讲'}」取 {theme_id} 纸面，配色、纸底、角饰成套换。"
        ),
        f"整份用了 {variety} 种版式、{compositions} 种构图：{top or '暂无'}。版式由这一页的内容形态决定，不跟页序走。",
        (
            f"要配图的 {len(wanted)} 页各有各的主体（{'、'.join(dict.fromkeys(wanted))[:60]}），不是拿整句话去搜图。"
            if wanted
            else "这份稿子暂时没有配图需求：讲清内容比配一张「差不多」的图重要。"
        ),
        "每页装饰不超过两处（底纹 + 角饰），字号只在七级阶梯里取值；两者都会被校验拦下，不靠自觉。",
    ]
    return lines


def _assumptions(params: PptParams, unit_count: int, *, audience: str = "") -> list[str]:
    who = (audience or "").strip() or params.audience
    lines = [
        f"受众按「{who}」理解（你没有指定时取这个默认值，可在上方改）。",
        f"篇幅按 {params.page_count} 页设计；实际页数以方案里的单元为准（{unit_count or '—'} 页）。",
    ]
    if not unit_count:
        lines.append("方案里没有可用单元，设计单为空；先补方案再确认。")
    return lines


def _plan_units(plan: Any) -> list[dict[str, Any]]:
    """宽松读方案：既接受 pydantic 模型，也接受落库后的 dict。"""

    if plan is None:
        return []
    units = getattr(plan, "units", None)
    if units is None and isinstance(plan, dict):
        units = plan.get("units") or []
    result: list[dict[str, Any]] = []
    for index, unit in enumerate(units or []):
        if isinstance(unit, dict):
            result.append(
                {
                    "id": str(unit.get("id") or f"u{index}"),
                    "label": str(unit.get("label") or ""),
                    "intent": str(unit.get("intent") or ""),
                    "detail": str(unit.get("detail") or ""),
                }
            )
        else:
            result.append(
                {
                    "id": str(getattr(unit, "id", "") or f"u{index}"),
                    "label": str(getattr(unit, "label", "") or ""),
                    "intent": str(getattr(unit, "intent", "") or ""),
                    "detail": str(getattr(unit, "detail", "") or ""),
                }
            )
    return [item for item in result if item["label"]]


# ── 收敛：前端改过的设计单、模型给的设计单都在这里合流 ──────────────────────


def normalize_design(raw: dict[str, Any] | None, plan: Any, *, params: PptParams | dict[str, Any] | None = None) -> DeckDesign:
    """把回传的设计单收敛成合法契约。

    两条兼容策略：
      - 页数对不上时**以方案为准**：方案是用户签过字的，设计只是它的呈现；
      - 版式不认识时退 ``points``，不抛错——确认阶段抛错等于把用户的手改给丢了。
    """

    units = _plan_units(plan)
    base = propose_design(
        _topic_of(plan),
        params or PptParams(),
        plan,
    )
    if not isinstance(raw, dict):
        return base
    model = DeckDesign.model_validate({**base.model_dump(), **{key: value for key, value in raw.items() if key != "pages"}})
    raw_pages = raw.get("pages") or []
    by_unit = {str(page.get("unit_id") or ""): page for page in raw_pages if isinstance(page, dict)}
    pages: list[PageDesign] = []
    for index, unit in enumerate(units):
        fallback = base.pages[index] if index < len(base.pages) else PageDesign(unit_id=str(unit.get("id") or f"u{index}"))
        page = by_unit.get(str(unit.get("id") or "")) or (raw_pages[index] if index < len(raw_pages) and isinstance(raw_pages[index], dict) else None)
        if not page:
            pages.append(fallback)
            continue
        # 槽位是派生物，不是用户填的字段：它由版式 + 构图 + 视觉方案算出来。
        # 回传的槽位是**提案那一刻**的（那时角花还没改成 none），收下就等于
        # 用旧决定覆盖新决定——「方案里选了不挂角花，成品四角还在开花」。
        page = {key: value for key, value in page.items() if key != "slots"}
        pages.append(PageDesign.model_validate({**fallback.model_dump(), **page, "unit_id": fallback.unit_id}))
    model.pages = pages or base.pages
    return model


def _topic_of(plan: Any) -> str:
    if isinstance(plan, dict):
        return str(plan.get("title") or "").replace("·讲稿", "")
    return str(getattr(plan, "title", "") or "").replace("·讲稿", "")


# ── 容量估算：check 用，渲染不用 ────────────────────────────────────────────


def check_page_capacity(page: PageDesign, blocks: list[dict[str, Any]]) -> list[str]:
    """一页的容量问题。返回空列表表示装得下。

    估算方式是有意的**保守**：宁可让一页少写两句，也不要导出时压到地脚。
    任何一条命中都会触发逐页回路里的一次定向重写。
    """

    spec = page.spec
    issues: list[str] = []
    allowed = set(spec.block_types)
    kinds = [str(block.get("type") or "") for block in blocks]
    if len(blocks) > spec.max_blocks:
        issues.append(f"内容块超限（{len(blocks)}/{spec.max_blocks}）")
    if len(blocks) > DENSITY_CAPS.get(page.density, 5):
        issues.append(f"超出 {page.density} 密度上限")
    if any(kind not in allowed for kind in kinds):
        issues.append(f"版式 {spec.id} 不接受 {sorted(set(kinds) - allowed)}")
    for block in blocks:
        text = str(block.get("text") or "")
        if len(text) > spec.item_chars * (2 if block.get("type") in ("point", "alert") else 1):
            issues.append("单块字数超限")
            break
    return list(dict.fromkeys(issues))


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 对比度。设计校验用：宣纸风也不能为了「像纸」把小字变淡到看不清。"""

    def luminance(value: str) -> float:
        text = value.lstrip("#")
        if len(text) != 6:
            return 0.0
        channels = []
        for offset in (0, 2, 4):
            raw = int(text[offset : offset + 2], 16) / 255
            channels.append(raw / 12.92 if raw <= 0.03928 else ((raw + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return round((lighter + 0.05) / (darker + 0.05), 2)
