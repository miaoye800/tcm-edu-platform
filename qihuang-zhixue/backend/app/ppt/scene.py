"""场景 DSL：一页幻灯的**视觉方案**，由模型自己写。

## 为什么不是模板库

升级前渲染层有十种 ``archetype``，每种在 ``frames.ts`` 里有一套写死的几何。模型只能
「从十个里挑一个」，于是十二页里有八九页长得一样——不是模型不会设计，是它没有设计
的自由度。

这里的做法相反：**给九个基元，不给任何版式**。模型读需求、读素材、自己决定这页是
「左文右图带角花」还是「整幅照片铺底压字」，然后写出一份 ``Scene``。编译器只负责把它
画出来，我不知道也不需要知道「这是第几号版式」。

``archetype`` 没有废除，但它降级成**语义标签**：只用于降级（图版页无图时改成什么）
与校验（对照页必须有两栏），**不参与几何**。标签与模板是两个角色，混用就是把设计权
收回去了。

## 坐标系

沿用渲染层的 cqw：画布 100 × 56.25（16:9）。所有 ``box`` 是 ``(x, y, w, h)``，
**允许负值与越界**——出血是排版手法，不是错误。越界上限见 ``BLEED_LIMIT``。

## 颜色

只认**角色名**（``fg`` / ``muted`` / ``accent`` / ``line`` / ``bg``），不认 hex。
理由是同一份场景要能在 12 套配色下都成立：模型写 ``accent``，宣纸上是朱砂、墨夜
里是赭石；写 ``B5443A`` 就只在那套配色下对。契约测试守着这一条。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .motifs import MOTIFS

#: 画布尺寸（cqw）。16:9 固定，不开放改比例：牌面比例变了，字号阶梯全部失效。
CANVAS_W = 100.0
CANVAS_H = 56.25

#: 出血允许的最大外扩（cqw）。超过这个数就不是「出血」，是画到纸外面去了。
BLEED_LIMIT = 25.0

#: 一页最多几个元素（group 展开后计数）。24 是「装饰预算 ≤2 + 正文块 ≤8 + 结构线」
#: 的宽松上限；再多一定是在堆装饰。
MAX_ELEMENTS = 24

#: 硬上限。超过这个数不是「排得密」，是模型把整页内容摊成了碎块——几乎必然溢出，
#: 直接拒绝而不是警告。与 `MAX_ELEMENTS` 分开：一个是预算，一个是合法性。
ELEMENT_HARD_LIMIT = 48

#: 颜色只认角色名。``soft`` 后缀表示同名角色的低透明度版本，由渲染层算。
ColorRole = Literal["fg", "muted", "accent", "line", "bg"]

#: 九个基元。没有「标题块」「要点块」「图片位」这类复合元素——复合即模板。
ElementType = Literal["asset", "motif", "rect", "rule", "circle", "text", "seal", "group"]

#: 文字的角色决定默认字号与字重，也决定长度上限。模型可以覆盖字号，但角色必须有。
TextRole = Literal["title", "subtitle", "kicker", "body", "caption", "note", "numeral"]

#: 字号档位（cqw）。模型写数字也行，但给了这七档它就不必猜。
TYPE_SCALE = (7.6, 4.6, 3.2, 2.2, 1.8, 1.5, 1.3)

#: 角色 → (默认字号档位, 默认字重, 字数上限)。与前端 ``SCENE_TEXT_ROLES`` 同源。
TEXT_ROLE_SPEC: dict[str, tuple[float, int, int]] = {
    "title": (4.6, 600, 40),
    "subtitle": (2.2, 400, 80),
    "kicker": (1.3, 400, 16),
    "body": (2.2, 400, 200),
    "caption": (1.3, 400, 60),
    "note": (1.3, 400, 80),
    "numeral": (7.6, 600, 8),
}

#: 素材 id 的形状：``{kind}-{subject}-{seq}``（见 docs/studios/assets.md §2）。
#: ornament- / ground- 是 2026-11 装饰素材包（真纹样资产）新增的前缀。
ASSET_ID_PREFIXES = ("plate-", "photo-", "motif-", "generated-", "ornament-", "ground-")


class Veil(BaseModel):
    """压在图片上的一层色，用来给文字让出低对比区域。

    满版照片压白字，不铺这层十有八九读不清；铺实色又变成色块。所以是「半透明色 +
    可选渐变方向」。``from_bottom`` 这类方向比角度好写，模型不容易算错。
    """

    model_config = ConfigDict(extra="forbid")

    color: ColorRole = "fg"
    opacity: float = Field(default=0.45, ge=0.0, le=0.9)
    direction: Literal["flat", "from_left", "from_right", "from_bottom", "from_top"] = "flat"


class SceneBackground(BaseModel):
    """背景三选一：母题（纸）、素材（照片铺底）、纯色。

    这是「角花版」与「整幅大图版」的唯一分野——两张参考图的差别只在这一个字段。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["motif", "asset", "solid"] = "motif"
    #: kind=motif 时的母题 id，必须来自 ``motifs.MOTIFS``。
    motif: str = "paper-plain"
    params: dict[str, Any] = Field(default_factory=dict)
    #: kind=asset 时的素材 id。
    asset: str = ""
    fit: Literal["cover", "contain"] = "cover"
    #: 铺在背景图上的一层色。kind != asset 时无效。
    veil: Veil | None = None
    #: kind=solid 时的填充角色。
    tone: ColorRole = "bg"


class SceneText(BaseModel):
    """一个文字块。竖排、避头尾、字号、字距都在这里，不靠 CSS 猜。"""

    model_config = ConfigDict(extra="forbid")

    content: str = ""
    role: TextRole = "body"
    #: 字号（cqw）。留空则用角色的档位。
    size: float | None = Field(default=None, ge=0.8, le=20.0)
    #: ``display`` 走标题字体、``body`` 走正文字体；实际字族由 deck 的 type_pair 决定。
    font: Literal["display", "body"] = "body"
    color: ColorRole = "fg"
    #: 竖排是中文版面的基本手法（参考图 1 右侧那一列）。
    write: Literal["h", "v"] = "h"
    align: Literal["left", "center", "right", "justify"] = "left"
    weight: int | None = Field(default=None, ge=300, le=800)
    line_height: float = Field(default=1.5, ge=1.0, le=2.4)
    tracking: float = Field(default=0.0, ge=0.0, le=2.0)
    #: 场景级断言：超过这个行数就是排不下，校验会报 warning 而不是硬截断。
    max_lines: int = Field(default=4, ge=1, le=12)

    @model_validator(mode="after")
    def fit_role_capacity(self) -> "SceneText":
        limit = TEXT_ROLE_SPEC.get(self.role, TEXT_ROLE_SPEC["body"])[2]
        if len(self.content) > limit:
            raise ValueError(f"text for role '{self.role}' exceeds {limit} chars")
        return self


class SceneElement(BaseModel):
    """一个基元。九种类型共用一组字段，按 ``type`` 取用——与 ``SlideBlock`` 同一套思路。

    为什么不做判别联合：渲染层要的是「给我元素，我按 type 画」，九个类只会让序列化
    多一层 union，而字段总数并没有减少。校验放在 ``model_validator`` 里按类型做。
    """

    model_config = ConfigDict(extra="forbid")

    type: ElementType
    #: (x, y, w, h)，单位 cqw。允许负值与越界（出血）。
    box: tuple[float, float, float, float] = (0.0, 0.0, 10.0, 10.0)
    #: 层序。同值按数组顺序，后画的压前画的。
    z: int = Field(default=0, ge=-10, le=30)
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    rotate: float = Field(default=0.0, ge=-180.0, le=180.0)
    #: 镜像。角花本来就四面对称，画一份镜像四处比存四张图合理——传统角饰就是这么做的。
    flip: Literal["none", "x", "y", "xy"] = "none"

    # asset
    asset: str = ""
    fit: Literal["cover", "contain"] = "contain"
    radius: float = Field(default=0.0, ge=0.0, le=20.0)
    caption: str = ""

    # ── 渲染润色（母版路线 Phase 4；additive，旧场景不写 = 原样）───────────
    # 这些字段**不进 scene_art 提示词**：模型不排阴影，母版代码才排。
    #: 阴影强度 0~1。asset/rect/circle/seal 有效；0 = 无阴影（默认，老数据零改动）。
    shadow: float = Field(default=0.0, ge=0.0, le=1.0)
    #: 装裱：none 无框 / hairline 细线框 / mat 画框留边（衬纸一圈再嵌图）。
    frame: Literal["none", "hairline", "mat"] = "none"
    #: 图片调性：none 原样 / toned 染纸色（深色底母版常用）/ duotone 双色调。
    #: 屏幕端走 CSS filter 近似；pptx 端不支持滤镜，原样导出（导出回执已注明）。
    img_treatment: Literal["none", "toned", "duotone"] = "none"

    # motif
    motif: str = ""
    params: dict[str, Any] = Field(default_factory=dict)

    # rect / circle / rule
    fill: ColorRole | None = None
    stroke: ColorRole | None = None
    stroke_width: float = Field(default=0.0, ge=0.0, le=4.0)
    dash: Literal["solid", "dash", "dot"] = "solid"
    # rule 专用
    orient: Literal["h", "v"] = "h"
    weight: float = Field(default=0.18, ge=0.02, le=4.0)
    # rect 专用：双色渐变（fill → fill2，方向 h/v）。fill2 为空 = 纯色，老数据零改动。
    fill2: ColorRole | None = None
    gradient_dir: Literal["h", "v", "none"] = "none"

    # text
    text: SceneText | None = None

    # seal
    content: str = Field(default="", max_length=4)
    shape: Literal["square", "round"] = "square"

    # group：子元素坐标相对本组 box（0..1 归一）。
    items: list["SceneElement"] = Field(default_factory=list, max_length=12)

    # ---------------------------------------------------------------- 校验

    @field_validator("box")
    @classmethod
    def box_in_canvas(cls, value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        x, y, w, h = value
        # 线允许零粗细：``rule`` 写 ``h=0`` 是最自然的写法，模型与人都会这么写。
        # 真正的"必须有尺寸"由 ``require_by_type`` 按类型判——那里知道这是不是一条线。
        if w < 0 or h < 0:
            raise ValueError("box w/h must not be negative")
        if w == 0 and h == 0:
            raise ValueError("box must have a length")
        if x < -BLEED_LIMIT or y < -BLEED_LIMIT:
            raise ValueError(f"box origin beyond bleed limit ({BLEED_LIMIT})")
        if x + w > CANVAS_W + BLEED_LIMIT or y + h > CANVAS_H + BLEED_LIMIT:
            raise ValueError(f"box exceeds bleed limit ({BLEED_LIMIT})")
        return value

    @field_validator("asset")
    @classmethod
    def asset_id_shape(cls, value: str) -> str:
        if value and not value.startswith(ASSET_ID_PREFIXES):
            raise ValueError("asset id must look like {plate|photo|motif|generated}-{subject}-{seq}")
        return value

    @model_validator(mode="after")
    def require_by_type(self) -> "SceneElement":
        x, y, w, h = self.box
        if self.type == "rule":
            # 一条线只需要一个长度。零粗细补成 ``weight``（默认 0.18），
            # 否则渲染出来是"看不见但占位"的元素——那比报错更难查。
            if self.orient == "h" and h <= 0:
                self.box = (x, y, w, max(self.weight, 0.2))
            if self.orient == "v" and w <= 0:
                self.box = (x, y, max(self.weight, 0.2), h)
        elif w <= 0 or h <= 0:
            raise ValueError(f"{self.type} box must have positive w/h")
        if self.type == "text" and self.text is None:
            raise ValueError("text element requires 'text'")
        if self.type == "asset" and not self.asset:
            raise ValueError("asset element requires 'asset'")
        if self.type == "motif":
            if not self.motif:
                raise ValueError("motif element requires 'motif'")
            if self.motif not in {m.id for m in MOTIFS}:
                raise ValueError(f"unknown motif '{self.motif}'")
        if self.type == "seal" and not self.content:
            raise ValueError("seal element requires 'content'")
        if self.type in {"rect", "circle"} and self.fill is None and self.stroke is None:
            raise ValueError(f"{self.type} requires fill or stroke")
        if self.type == "group" and not self.items:
            raise ValueError("group element requires 'items'")
        return self


class Scene(BaseModel):
    """一页的完整视觉方案。落库后 ``frozen``，编辑期不许再改形状。"""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    #: 一句话：为什么这页这样排。只给人看，不参与渲染——但它是「设计理由」的唯一载体。
    note: str = Field(default="", max_length=200)
    background: SceneBackground = Field(default_factory=SceneBackground)
    #: 元素数不设 pydantic 上限：预算由 ``validate_scene`` 判，超预算只警告。
    #: 在这里卡死会让「排得密一点」变成一个 schema 错误，而那本是手艺问题。
    elements: list[SceneElement] = Field(default_factory=list)

    @property
    def element_count(self) -> int:
        """展开 group 后的元素数。装饰预算按这个数算。

        容忍 dict 元素：这份场景可能在别处被拼过（不经 pydantic），而计数是校验
        的第一步——它自己先抛 TypeError 会让「模型产出不抛异常」这条规矩落空。
        """

        total = 0
        for element in self.elements:
            items = element.get("items") if isinstance(element, dict) else getattr(element, "items", None)
            total += 1 + len(items or [])
        return total


#: 元素类型清单，供前端镜像与提示词生成共用。
ELEMENT_TYPES: tuple[str, ...] = ("asset", "motif", "rect", "rule", "circle", "text", "seal", "group")

#: 母题 id 清单（从 ``motifs.MOTIFS`` 取，不另立一份）。
MOTIF_IDS: tuple[str, ...] = tuple(m.id for m in MOTIFS)


# ------------------------------------------------------------------ 校验与降级


def validate_scene(raw: dict[str, Any] | Scene) -> tuple[Scene | None, list[str]]:
    """把模型的产出收敛成 ``Scene``。返回 ``(scene, warnings)``。

    不合法就返回 ``(None, errors)``——调用方应当降级到兜底场景，而不是把半坏的场景
    画到纸上。**错误不抛异常**，因为这是模型产出，抛异常会让整份稿子失败。
    """
    warnings: list[str] = []
    try:
        # 一律走一遍 ``model_validate``：即使调用方给的是 ``Scene`` 实例，也可能在
        # 外部被拼进过原始 dict（pydantic 不对 list 赋值做校验）。重验一次是幂等的。
        payload = raw if isinstance(raw, dict) else raw.model_dump()
        scene = Scene.model_validate(payload)
    except Exception as error:  # noqa: BLE001 - 模型产出，任何错都要收敛
        return None, [f"scene_invalid: {str(error).splitlines()[0]}"]

    if scene.element_count > ELEMENT_HARD_LIMIT:
        # 超过硬上限不是「排得密」，是把一页内容拆成了碎块——必然溢出，不画。
        return None, [f"scene_bloated: {scene.element_count} elements > {ELEMENT_HARD_LIMIT}"]

    if scene.element_count > MAX_ELEMENTS:
        warnings.append(f"scene_dense: {scene.element_count} elements > {MAX_ELEMENTS}")

    # 文字越界：只报 warning。真截断会让内容消失，缩字号才是解法，而那要人来看。
    for index, element in enumerate(scene.elements):
        if element.type != "text" or element.text is None:
            continue
        text = element.text
        size = text.size or TEXT_ROLE_SPEC[text.role][0]
        # 中文字宽约等于字号，行数 = 面积 / (字号² × 行距)。近似够用，不做字形测量。
        per_line = max(1.0, element.box[2] / max(size, 0.1))
        lines = max(1.0, len(text.content) / per_line)
        if text.write == "h" and lines > text.max_lines:
            warnings.append(f"text_overflow: element[{index}] needs {lines:.1f} lines > {text.max_lines}")
        if text.write == "h" and element.box[3] < lines * size * text.line_height * 0.9:
            # 留 10% 容差：字高按字号估，实际行盒会比字号略高，差一点点不算溢出。
            warnings.append(f"text_overflow: element[{index}] box too short for {lines:.1f} lines")
        if text.write == "v":
            # 竖排的"行"是字：一列要装下所有字，装不下就是溢出到纸外。
            needed = len(text.content) * size * text.line_height
            if element.box[3] < needed * 0.9:
                warnings.append(
                    f"text_overflow: element[{index}] column too short for {len(text.content)} chars ({needed:.1f} needed)"
                )

    return scene, warnings


def empty_scene() -> Scene:
    """一张只有纸的单页。任何降级的终点。"""
    return Scene(note="blank", background=SceneBackground(kind="motif", motif="paper-plain"), elements=[])
