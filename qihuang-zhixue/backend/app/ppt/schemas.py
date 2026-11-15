"""幻灯的成品契约：严格出、可落库、可推流、可渲染。

与 ``app/mindmap/schemas.py`` 同一套分工：这里只放**严格**模型（超长直接 raise），
模型的宽松产出先落到 ``draft.py`` 的草案上，再由 ``normalize_deck`` 收敛到这里。

字段上限不是随手定的，全部源自 16:9 舞台的排版实测：
标题 40 字在 3.4cqw 下正好两行不压正文；要点 60 字超过就会挤出可视区。

**``blocks`` 是本版新增的内容载体**：升级前一页只能存一句话（标题 + 至多 6 条
要点），对照、流程、矩阵、数据卡、警示、引文六类内容无处存放，只能降维成一条
bullet——这就是「十二页里八九页长得一样」的直接原因。``bullets``/``columns``
保留，是为了让已有资源与旧代码继续可读可渲染。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .scene import Scene

#: 渲染层认识的六种版式。前端 SlideSurface 与 pptx 导出共用这一份枚举。
SlideKind = Literal["cover", "agenda", "section", "bullets", "compare", "summary"]
#: 内容块七类型，与 ``design.BLOCK_TYPES`` 同源。
BlockType = Literal["point", "steps", "matrix", "figures", "alert", "quote", "def"]
#: 十种版式目的，与 ``design.PURPOSES`` 同源。
SlidePurpose = Literal["cover", "agenda", "section", "points", "compare", "matrix", "process", "data", "warning", "summary"]
#: 素材来源四级。``parametric`` 表示这一处是代码算出来的，不是图像资产。
Provenance = Literal["parametric", "library", "generated", "none"]

MAX_TITLE = 40
MAX_SUBTITLE = 80
MAX_BULLET = 60
MAX_NOTE = 200
MAX_BLOCKS = 6


class SlideColumn(BaseModel):
    """对比版式的一栏。只有 compare 用到。"""

    model_config = ConfigDict(extra="forbid")

    heading: str = Field(min_length=1, max_length=24)
    items: list[str] = Field(default_factory=list, max_length=5)


class SlideBlock(BaseModel):
    """一页里的一个内容块。

    七种类型共用一组字段，而不是七个类：渲染层要的是「给我块，我按 type 画」，
    七个类只会让序列化多一层判别联合，收益为零。字段语义按类型约定：

    - ``point`` / ``alert`` / ``quote`` / ``def``：``text`` 是正文，``heading`` 可选
    - ``steps``：``items`` 是步骤
    - ``matrix``：``columns`` 是栏（两栏=对照，三栏=矩阵）
    - ``figures``：``value`` 是数值，``heading`` 是标签
    """

    model_config = ConfigDict(extra="forbid")

    type: BlockType
    text: str = Field(default="", max_length=200)
    heading: str = Field(default="", max_length=24)
    items: list[str] = Field(default_factory=list, max_length=6)
    columns: list[SlideColumn] = Field(default_factory=list, max_length=3)
    value: str = Field(default="", max_length=12)
    source: str = Field(default="", max_length=80)


class AssetRef(BaseModel):
    """一页用到的素材。**由 select 产出**，不是模型产出。

    模型只写需求（槽位），资产 id 由选型器决定——让模型编 ``asset_id`` 会重演
    ``XiaoqiMascot`` 式坏引用：引用一个不存在的文件，页面静默兜底成空白。
    """

    model_config = ConfigDict(extra="forbid")

    slot: str = Field(default="figure", max_length=16)
    asset_id: str = Field(default="", max_length=64)
    #: 阶梯走到第几级。``substitute`` 要标「示意」，``generated`` 要标「AI 生成示意」。
    result: Literal["parametric", "hit", "substitute", "generated", "none"] = "none"
    provenance: Provenance = "none"
    license: str = Field(default="", max_length=32)
    attribution: str = Field(default="", max_length=120)
    file: str = Field(default="", max_length=200)
    #: 远程直链。**素材不落盘**，稿子里只钉 URL：本地库为相对路径（空串表示走 `file`），
    #: 远程源为已合成的 wsrv 代理地址（见 `materials.py` 的 `proxy_url`）。
    url: str = Field(default="", max_length=400)
    #: 400px 缩略图，确认卡与候选列表用。与 `url` 同一个源，只是尺寸档不同。
    thumb: str = Field(default="", max_length=400)
    #: 来源渠道 id（`cleveland` / `iconify` / `wanx` / `local.herbs`…）。只用于标注与审计。
    source: str = Field(default="", max_length=40)
    #: 检索固化时间。稿子冻结后这条 URL 不再重检索；打开时 404 才重来一次。
    pinned_at: str = Field(default="", max_length=32)
    note: str = Field(default="", max_length=60)


class SlideNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    kind: SlideKind
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    subtitle: str = Field(default="", max_length=MAX_SUBTITLE)
    bullets: list[str] = Field(default_factory=list, max_length=6)
    columns: list[SlideColumn] = Field(default_factory=list, max_length=2)
    note: str = Field(default="", max_length=MAX_NOTE)
    source_indexes: list[int] = Field(default_factory=list, max_length=6)
    #: 这一页要达成什么版式。十值，与设计单的 archetype 对应。
    purpose: SlidePurpose = "points"
    #: 结构化内容。为空时渲染层回落到 bullets/columns（旧稿兼容）。
    blocks: list[SlideBlock] = Field(default_factory=list, max_length=MAX_BLOCKS)
    asset_refs: list[AssetRef] = Field(default_factory=list, max_length=3)
    #: 视觉方案。有它渲染层走 `SceneSurface`，没有就回落旧渲染器。
    #:
    #: 为什么挂在页上而不是设计单上：设计单是「整份的方向」，场景是「这一页怎么画」。
    #: 一份稿十二页各有各的几何，只有页知道自己的。落库后随页冻结。
    scene: Scene | None = None

    @field_validator("title")
    @classmethod
    def concise_title(cls, value: str) -> str:
        text = " ".join(value.split())
        if len(text) > MAX_TITLE:
            raise ValueError("slide title must be concise")
        return text

    @field_validator("bullets")
    @classmethod
    def concise_bullets(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(str(value).split()) for value in values]
        if any(not item for item in cleaned):
            raise ValueError("slide bullet must not be blank")
        if any(len(item) > MAX_BULLET for item in cleaned):
            raise ValueError("slide bullet must be concise")
        return cleaned


class DeckContent(BaseModel):
    """一份幻灯。``sources`` 是生成期固化的溯源证据，编辑不覆盖它。"""

    model_config = ConfigDict(extra="forbid")

    theme_id: str = Field(default="paper", max_length=32)
    slides: list[SlideNode] = Field(min_length=1, max_length=20)
    sources: list[dict[str, str]] = Field(default_factory=list)
    #: 逐页排版（scene_art）的记账：排了几页、哪些退回兜底、为什么。
    #: 它回答的是「这页为什么长这样」里最常被问的一半，所以跟着稿子走。
    scene_stats: dict[str, Any] = Field(default_factory=dict)


class OutlineSlide(BaseModel):
    """大纲页：只有标题与要点骨架，是「先定大纲」模式的交付物与回执。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    points: list[str] = Field(default_factory=list, max_length=5)
    #: 这一页要达成什么。来自用户确认过的方案；旧数据为空。
    intent: str = Field(default="", max_length=60)
    source_indexes: list[int] = Field(default_factory=list, max_length=6)


class FramePlan(BaseModel):
    """立骨的产物：这一课叫什么、讲给谁、按什么顺序讲。"""

    model_config = ConfigDict(extra="forbid")

    deck_title: str = Field(min_length=1, max_length=MAX_TITLE)
    audience: str = Field(default="中医药专业本科生", max_length=40)
    arc: list[str] = Field(default_factory=list, max_length=8)
