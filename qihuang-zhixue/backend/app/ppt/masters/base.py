"""母版系统的地基：规格、上下文、容量规则与共享排版构件。

## 母版是什么

「版式母版路线」的母版 = **后端代码，确定性产出 SceneSpec**。每套母版是一组
构造器：输入（页型, 内容, 已解析素材, 色板）→ ``Scene``。模型的新职责只剩
「选母版 + 逐页选页型 + 填内容槽」——几何、字号、装饰位置由这里的代码定，
于是同一份内容在同一套母版下永远排出同一个版面，可在后端单测容量。

这与 ``scene_fallback`` 的四个构造器（``_head/_stack/_columns/_figure``）是
同一血统：fallback 是「安全网，只保证不坏」，母版是「设计系统，要好看」。
共享构件都收在这里，各母版模块只写自己的气质差异。

## 容量规则（确定性，不调模型）

- bullets 上限 6，超出截断并让 painter 记 warning；
- ``fit_bullets``：3 条以内 2.2 档，4–5 条 1.8 档，6 条 1.5 档并收紧行距；
- ``fit_title``：≤8 字顶档，9–16 字中档，>16 字降档。

构造器产出一律过 ``validate_scene``（painter 负责），这里只保证「字一个不少」：
标题与正文逐字上纸是母版的硬合同，与 scene_art 的模型合同一致。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

from ..scene import (
    CANVAS_H,
    CANVAS_W,
    Scene,
    SceneBackground,
    SceneElement,
    SceneText,
    Veil,
)

#: 十种页型词汇，与 ``design.PURPOSES`` / ``spec.ROLES`` 同一套 id，
#: 母版不发明新页型——页型是内容侧的语言，母版只是给每种页型一套自己的几何。
PAGE_ROLES: tuple[str, ...] = (
    "cover", "agenda", "section", "points", "compare", "matrix", "process", "data", "warning", "summary",
)

ROLE_LABEL: dict[str, str] = {
    "cover": "封面", "agenda": "导览", "section": "章节", "points": "要点",
    "compare": "对照", "matrix": "矩阵", "process": "流程", "data": "数据",
    "warning": "警示", "summary": "收束",
}

#: 中文数字，章节页的水印字号。index 对不上没关系——它是节奏记号，不是页码。
CN_NUMERALS = "壹贰叁肆伍陆柒捌玖拾"

Painter = Callable[["PageContext"], Scene]


# ── 规格 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MasterSpec:
    """一套母版的静态规格。``masters/__init__.py`` 用它生成模型菜单与前端 pptMasters.ts。"""

    id: str
    label: str
    tagline: str
    #: 适用的场合（``spec.GENRES`` 子集）。模型按场合与气质挑母版。
    genres: tuple[str, ...]
    #: 兼容旧主题体系（select 打分 / 母题筛选仍按 theme 走）。
    theme: str
    #: 字对 id（``fontpairs.TYPE_PAIRS`` 之一）。渲染层只认字对，不认裸族名。
    type_pair: str
    #: 色板变体（≥2 套）。每套含 label + bg/fg/muted/accent/line 五色。
    palette_variants: tuple[dict[str, str], ...]
    #: 默认背景：{"kind": "motif", "motif": "paper-chain"} 或 {"kind": "solid"}。
    background: dict[str, Any]
    #: 装饰组。第 0 组是默认；每组 {id,label,ornament:{...},ground:{...}}，
    #: ornament/ground 要么是程序母题 {"motif": id}，要么是素材检索
    #: {"subject": ..., "keywords": [...]}，要么 {"motif": "none"}。
    decoration_sets: tuple[dict[str, Any], ...]
    #: 这套母版吃得下哪些页型（都是全十种，字段留着做菜单摘要）。
    page_types: tuple[str, ...] = PAGE_ROLES

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "tagline": self.tagline,
            "genres": list(self.genres),
            "theme": self.theme,
            "type_pair": self.type_pair,
            "palettes": [dict(variant) for variant in self.palette_variants],
            "background": dict(self.background),
            "decorations": [dict(item) for item in self.decoration_sets],
            "page_types": list(self.page_types),
        }

    def palette(self, variant: int) -> dict[str, str]:
        """第 N 套色板，越界夹回。宽松读：模型给的序号不许让生成炸掉。"""
        variants = self.palette_variants
        if not variants:
            return {}
        return variants[max(0, min(variant, len(variants) - 1))]

    def decoration(self) -> dict[str, Any]:
        """默认装饰组（第 0 组）。"""
        return self.decoration_sets[0] if self.decoration_sets else {}

    def menu_entry(self) -> dict[str, Any]:
        """给模型的菜单项：id / label / tagline / 场合 / 色板摘要。"""
        return {
            "master_id": self.id,
            "label": self.label,
            "tagline": self.tagline,
            "genres": list(self.genres),
            "palettes": [str(variant.get("label") or "") for variant in self.palette_variants],
        }


@dataclass(frozen=True)
class MasterStyle:
    """共享内容排版器的风格旋钮。每套母版一个实例，几何骨架共用、气质各异。"""

    margin: tuple[float, float, float, float] = (9.0, 9.0, 8.4, 7.2)  # l r t b
    kicker: bool = True
    kicker_color: str = "accent"
    kicker_tracking: float = 0.35
    title_sizes: tuple[float, float, float] = (3.4, 2.8, 2.3)
    cover_sizes: tuple[float, float, float] = (7.6, 6.2, 4.8)
    section_sizes: tuple[float, float, float] = (5.2, 4.4, 3.6)
    head_rule: bool = True
    rule_len: float = 8.0
    rule_weight: float = 0.3
    rule_color: str = "accent"
    marker: str = "diamond"  # diamond | number | dash | ring | none
    marker_color: str = "accent"
    body_line: float = 1.55
    column_head_color: str = "accent"
    #: 图位描一圈 hairline（深色系母版给图「装裱」）。
    figure_frame: bool = False
    #: 图位走「画框留边」：衬纸一圈再嵌图，宣纸系母版的古籍图版感。
    figure_mat: bool = False
    #: 图位阴影强度（0~1）。浅底母版给 0.25~0.3，深底给 0.4。
    figure_shadow: float = 0.0
    #: 图位调性：toned 染纸色（深底母版常用）/ none 原样。
    figure_treatment: str = "none"
    #: 内容页吃不吃图位（有的母版正文页一图不留，靠留白）。
    content_figure: bool = True
    #: 标题左侧竖条（编辑部风的题花）。
    title_bar: bool = False
    footer_rule: bool = False
    #: Scene.note 前缀，人看的设计说明。
    note: str = "母版"


@dataclass
class PageContext:
    """一页的全部输入。painter 从 slide + design + resolutions 拼出它，再交母版。"""

    purpose: str = "points"
    title: str = ""
    subtitle: str = ""
    bullets: list[str] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    columns: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""
    #: 主图（figure/evidence 槽解析成功）：{"id", "kind": asset|motif, "caption"}。
    figure: dict[str, Any] | None = None
    #: 真纹样角饰（ornament 槽命中素材库）。
    ornament_ids: list[str] = field(default_factory=list)
    #: 程序母题角饰（ornament 槽走参数化阶梯的产物）。
    ornament_motif: str = ""
    #: 背景：{"kind": "motif"|"asset"|"solid", "id": ...}。空则用母版默认。
    ground: dict[str, Any] = field(default_factory=dict)
    palette: dict[str, str] = field(default_factory=dict)
    type_pair: str = "song-kai"
    composition: str = "auto"
    index: int = 0
    total: int = 1
    deck_title: str = ""
    #: bullets 超 6 被截断时置 True，painter 据此记 warning。
    truncated: bool = False


@dataclass(frozen=True)
class Master:
    """一套母版：规格 + 风格 + 逐页型构造器。"""

    spec: MasterSpec
    style: MasterStyle
    painters: dict[str, Painter]

    def paint(self, ctx: PageContext) -> Scene:
        painter = self.painters.get(ctx.purpose) or self.painters.get("points")
        if painter is None:  # pragma: no cover - painters 恒含 points
            raise ValueError(f"master {self.spec.id} has no painters")
        return painter(ctx)


# ── 容量规则 ────────────────────────────────────────────────────────────────


def fit_bullets(count: int) -> tuple[float, float]:
    """条数 → (正文字号, 行距)。条多就缩字号、收紧行距，不许把字挤出纸外。"""
    if count <= 3:
        return 2.2, 1.6
    if count <= 5:
        return 1.8, 1.5
    return 1.5, 1.42


def fit_title(text_value: str, sizes: tuple[float, float, float]) -> float:
    """标题字数 → 字号档。≤8 字顶档（大字有分量），>16 字降档（两行也放得下）。"""
    length = len(text_value or "")
    if length <= 8:
        return sizes[0]
    if length <= 16:
        return sizes[1]
    return sizes[2]


def est_lines(content: str, width: float, size: float) -> int:
    """按中文字宽≈字号估行数。与 ``validate_scene`` 的口径一致，别两套算法。"""
    per_line = max(1.0, width / max(size, 0.1))
    return max(1, math.ceil(len(content or "") / per_line))


# ── 基元构造（与 scene_fallback 同款，多出 max_lines/weight 等母版需要的旋钮）──


def text(
    content: str,
    box: tuple[float, float, float, float],
    *,
    role: str = "body",
    size: float | None = None,
    color: str = "fg",
    font: str = "body",
    write: str = "h",
    align: str = "left",
    weight: int | None = None,
    line_height: float = 1.55,
    tracking: float = 0.0,
    max_lines: int = 4,
    z: int = 20,
) -> SceneElement:
    return SceneElement(
        type="text",
        box=box,
        z=z,
        text=SceneText(
            content=content,
            role=role,  # type: ignore[arg-type]
            size=size,
            color=color,  # type: ignore[arg-type]
            font=font,  # type: ignore[arg-type]
            write=write,  # type: ignore[arg-type]
            align=align,  # type: ignore[arg-type]
            weight=weight,
            line_height=line_height,
            tracking=tracking,
            max_lines=max_lines,
        ),
    )


def rule(
    box: tuple[float, float, float, float],
    *,
    color: str = "accent",
    weight: float = 0.3,
    z: int = 10,
    dash: str = "solid",
) -> SceneElement:
    orient = "v" if box[3] > box[2] else "h"
    return SceneElement(
        type="rule",
        box=box,
        z=z,
        orient=orient,  # type: ignore[arg-type]
        weight=weight,
        fill=color,  # type: ignore[arg-type]
        dash=dash,  # type: ignore[arg-type]
    )


def rect(
    box: tuple[float, float, float, float],
    *,
    fill: str | None = None,
    fill2: str | None = None,
    gradient_dir: str = "none",
    stroke: str | None = None,
    stroke_width: float = 0.2,
    z: int = 10,
    rotate: float = 0.0,
    opacity: float = 1.0,
) -> SceneElement:
    return SceneElement(
        type="rect",
        box=box,
        z=z,
        fill=fill,  # type: ignore[arg-type]
        fill2=fill2,  # type: ignore[arg-type]
        gradient_dir=gradient_dir,  # type: ignore[arg-type]
        stroke=stroke,  # type: ignore[arg-type]
        stroke_width=stroke_width,
        rotate=rotate,
        opacity=opacity,
    )


def circle(
    box: tuple[float, float, float, float],
    *,
    fill: str | None = None,
    stroke: str | None = None,
    stroke_width: float = 0.18,
    z: int = 10,
) -> SceneElement:
    return SceneElement(
        type="circle",
        box=box,
        z=z,
        fill=fill,  # type: ignore[arg-type]
        stroke=stroke,  # type: ignore[arg-type]
        stroke_width=stroke_width,
    )


def figure_el(
    figure: dict[str, Any] | None,
    box: tuple[float, float, float, float],
    *,
    z: int = 15,
    radius: float = 0.0,
    frame: str = "none",
    shadow: float = 0.0,
    img_treatment: str = "none",
) -> SceneElement | None:
    """图位。库内素材走 asset，参数化母题走 motif——两者不能混（命名规则会拒）。

    ``frame`` / ``shadow`` / ``img_treatment`` 只对 asset 生效：母题是矢量装饰，
    加框加影反而脏。装裱（hairline/mat）与调性是母版的设计决定，不是模型的。
    """
    if not figure or not figure.get("id"):
        return None
    if figure.get("kind") == "motif":
        return SceneElement(type="motif", box=box, z=z, motif=str(figure["id"]))
    return SceneElement(
        type="asset",
        box=box,
        z=z,
        asset=str(figure["id"]),
        fit="cover",
        radius=radius,
        caption=str(figure.get("caption") or "")[:60],
        frame=frame,  # type: ignore[arg-type]
        shadow=shadow,
        img_treatment=img_treatment,  # type: ignore[arg-type]
    )


def seal_el(content: str, box: tuple[float, float, float, float], *, z: int = 12) -> SceneElement:
    return SceneElement(type="seal", box=box, z=z, content=content[:4], shape="square")


_CORNER_BOX: dict[str, tuple[float, float, str]] = {
    # corner → (左上角 x 固定逻辑见下, ...)；直接存偏移与镜像更清楚。
    "tl": (0.0, 0.0, "none"),
    "tr": (1.0, 0.0, "x"),
    "bl": (0.0, 1.0, "y"),
    "br": (1.0, 1.0, "xy"),
}


def corner_set(
    source: str,
    size: float,
    corners: tuple[str, ...] = ("tl", "tr", "bl", "br"),
    *,
    kind: str = "motif",
    z: int = 8,
) -> list[SceneElement]:
    """角饰：母题或真纹样资产各画一份，镜像三份。角花本来就四面对称。"""
    if not source:
        return []
    out: list[SceneElement] = []
    for corner in corners:
        fx, fy, flip = _CORNER_BOX.get(corner, (0.0, 0.0, "none"))
        x = (CANVAS_W - size) if fx else 0.0
        y = (CANVAS_H - size) if fy else 0.0
        if kind == "asset":
            out.append(SceneElement(type="asset", box=(x, y, size, size), z=z, asset=source, fit="cover", flip=flip))  # type: ignore[arg-type]
        else:
            out.append(SceneElement(type="motif", box=(x, y, size, size), z=z, motif=source, flip=flip))  # type: ignore[arg-type]
    return out


def band_el(
    source: str,
    box: tuple[float, float, float, float],
    *,
    kind: str = "asset",
    z: int = 8,
) -> SceneElement | None:
    """横带（云纹/回纹边带）。真纹样资产横向铺开，母题退化为角饰时不用这个。"""
    if not source:
        return None
    if kind == "asset":
        return SceneElement(type="asset", box=box, z=z, asset=source, fit="cover")
    return SceneElement(type="motif", box=box, z=z, motif=source)


def ornament_source(ctx: PageContext) -> tuple[str, str]:
    """这一页的角饰用什么：真纹样优先，程序母题兜底，都没有就空。

    返回 (source, kind)。kind ∈ {"asset", "motif", ""}。
    """
    if ctx.ornament_ids:
        return ctx.ornament_ids[0], "asset"
    if ctx.ornament_motif:
        return ctx.ornament_motif, "motif"
    return "", ""


def scene_background(ctx: PageContext, default: dict[str, Any] | None = None) -> SceneBackground:
    """PageContext.ground → SceneBackground。空则用母版默认。"""
    ground = ctx.ground or {}
    if not ground.get("id") and ground.get("kind") != "solid":
        ground = dict(default or {})
    kind = str(ground.get("kind") or "motif")
    if kind == "asset" and ground.get("id"):
        # 真底纹（剪纸/回纹方连）铺底要压一层淡纱，不然正文读不清。
        return SceneBackground(
            kind="asset",
            asset=str(ground["id"]),
            fit="cover",
            veil=Veil(color="fg", opacity=0.34),
        )
    if kind == "solid":
        return SceneBackground(kind="solid", tone="bg")
    motif = str(ground.get("id") or "")
    if not motif:
        motif = str((default or {}).get("motif") or "paper-plain")
    return SceneBackground(kind="motif", motif=motif)


# ── 复合构件 ────────────────────────────────────────────────────────────────


def stack(
    items: list[str],
    box: tuple[float, float, float, float],
    *,
    size: float = 2.2,
    line_height: float = 1.55,
    marker: str | None = "diamond",
    marker_color: str = "accent",
    font: str = "body",
    text_color: str = "fg",
    gap: float | None = None,
    z: int = 20,
) -> list[SceneElement]:
    """纵向要点栈。行高按估算行数算，装不下整体收缩——**字一个不删**。

    与 scene_fallback._stack 的差别：那里等分行（条目少时把字拉得很开），
    这里按内容估行，行高=内容高，多余的空间留给页脚。marker 四种：
    diamond（菱点）/ number（大号序数）/ dash（短横）/ ring（圈点）/ None。
    """
    rows = [str(item) for item in items if str(item).strip()]
    if not rows:
        return []
    x, y, w, h = box
    if gap is None:
        gap = max(1.0, size * 0.55)
    if marker == "number":
        text_x, text_w = x + 6.2, w - 6.2
    elif marker:
        text_x, text_w = x + 1.9, w - 1.9
    else:
        text_x, text_w = x, w
    est = [est_lines(row, text_w, size) for row in rows]
    heights = [size * line_height * lines + 0.35 for lines in est]
    total = sum(heights) + gap * (len(rows) - 1)
    if total > h > 0 and total > 0:
        scale = max(0.7, h / total)
        heights = [value * scale for value in heights]
        gap *= scale
    out: list[SceneElement] = []
    top = y
    for index, (row, lines) in enumerate(zip(rows, est)):
        row_h = heights[index]
        if marker == "number":
            out.append(
                text(
                    f"{index + 1:02d}",
                    (x, top, 5.2, row_h),
                    role="numeral",
                    size=min(2.6, max(1.6, row_h * 0.7)),
                    color=marker_color,
                    font="display",
                    line_height=1.2,
                    z=z,
                )
            )
        elif marker == "diamond":
            out.append(
                rect(
                    (x + 0.15, top + size * 0.62, 0.55, 0.55),
                    fill=marker_color,
                    z=z - 1,
                    rotate=45.0,
                )
            )
        elif marker == "ring":
            out.append(circle((x + 0.3, top + size * 0.55, 0.8, 0.8), stroke=marker_color, stroke_width=0.16, z=z - 1))
        elif marker == "dash":
            out.append(rule((x, top + size * 0.72, 1.6, 0.16), color=marker_color, weight=0.16, z=z - 1))
        out.append(
            text(
                row,
                (text_x, top, text_w, row_h),
                size=size,
                color=text_color,
                font=font,
                line_height=line_height,
                max_lines=max(lines, 1),
                z=z,
            )
        )
        top += row_h + gap
    return out


def columns_layout(
    cols: list[dict[str, Any]],
    box: tuple[float, float, float, float],
    *,
    size: float = 1.8,
    head_size: float | None = None,
    head_color: str = "accent",
    divider: bool = True,
    gap: float = 2.6,
) -> list[SceneElement]:
    """多栏（对照/矩阵）。栏间竖细线——表格的骨架是线，不是底色。"""
    usable = [col for col in cols if (col.get("items") or col.get("heading"))]
    if not usable:
        return []
    x, y, w, h = box
    n = len(usable)
    col_w = (w - gap * (n - 1)) / n
    head_size = head_size or size * 1.15
    out: list[SceneElement] = []
    for index, col in enumerate(usable):
        left = x + index * (col_w + gap)
        if index and divider:
            out.append(rule((left - gap / 2, y + 0.3, 0.08, h - 0.6), color="muted", weight=0.08, z=12))
        items = [str(item) for item in (col.get("items") or []) if str(item).strip()]
        head = str(col.get("heading") or "")
        if head:
            out.append(
                text(
                    head,
                    (left, y, col_w, head_size * 1.4 + 0.3),
                    role="subtitle",
                    size=head_size,
                    color=head_color,
                    font="display",
                    line_height=1.3,
                    max_lines=1,
                )
            )
        top = y + (head_size * 1.4 + 1.1 if head else 0.0)
        out.extend(
            stack(
                items,
                (left, top, col_w, max(4.0, y + h - top)),
                size=size,
                marker=None,
                line_height=1.5,
                gap=1.1,
            )
        )
    return out


def cards(
    blocks: list[dict[str, Any]],
    box: tuple[float, float, float, float],
    *,
    value_size: float = 4.2,
    accent: str = "accent",
    head_size: float = 2.0,
) -> list[SceneElement]:
    """数据卡：渐变软底板 + 卡顶强调线 + 大号数值 + 标签（+ 出处）。"""
    usable = [block for block in blocks if str(block.get("value") or "").strip()]
    if not usable:
        return []
    x, y, w, h = box
    n = min(len(usable), 4)
    gap = 2.4
    card_w = (w - gap * (n - 1)) / n
    out: list[SceneElement] = []
    for index in range(n):
        block = usable[index]
        left = x + index * (card_w + gap)
        # 软底板：fg→bg 的纵向渐变、6% 透明度。数值卡浮起来一点，
        # 但不用实色块——实色块在宣纸上是补丁，渐变是光。
        out.append(
            rect(
                (left, y + 0.2, card_w, 13.2),
                fill="fg",
                fill2="bg",
                gradient_dir="v",
                opacity=0.06,
                z=5,
            )
        )
        out.append(rule((left, y, card_w, 0.2), color=accent, weight=0.2))
        value = str(block.get("value") or "")
        # numeral 角色限 8 字；「3~9g」没问题，「10.5%±0.2」这种长值退 subtitle 档。
        if len(value) <= 8:
            out.append(
                text(
                    value,
                    (left, y + 1.2, card_w, 5.4),
                    role="numeral",
                    size=value_size,
                    color=accent,
                    font="display",
                    line_height=1.15,
                    max_lines=2,
                )
            )
        else:
            out.append(
                text(
                    value,
                    (left, y + 1.2, card_w, 5.4),
                    role="subtitle",
                    size=min(value_size, 3.0),
                    color=accent,
                    font="display",
                    line_height=1.25,
                    max_lines=2,
                )
            )
        heading = str(block.get("heading") or "")
        if heading:
            out.append(
                text(
                    heading,
                    (left, y + 7.0, card_w, 3.4),
                    size=head_size,
                    line_height=1.4,
                    max_lines=2,
                )
            )
        source = str(block.get("source") or "")
        if source:
            out.append(
                text(
                    source,
                    (left, y + 10.8, card_w, 2.2),
                    role="caption",
                    size=1.3,
                    color="muted",
                    max_lines=1,
                )
            )
    return out


def steps_flow(
    items: list[str],
    box: tuple[float, float, float, float],
    *,
    size: float = 1.9,
    numeral_size: float = 2.4,
    numeral_color: str = "accent",
    line_color: str = "line",
) -> list[SceneElement]:
    """流程：大号序数 + 连接竖线。步骤天然有序，线把它串成一条。"""
    rows = [str(item) for item in items if str(item).strip()]
    if not rows:
        return []
    x, y, w, h = box
    n = len(rows)
    gap = 1.8
    row_h = min((h - gap * (n - 1)) / n, size * 2.5)
    out: list[SceneElement] = []
    if n > 1:
        out.append(rule((x + 1.1, y + row_h * 0.5, 0.12, (row_h + gap) * (n - 1)), color=line_color, weight=0.12, z=8))
    for index, row in enumerate(rows):
        top = y + index * (row_h + gap)
        out.append(
            text(
                f"{index + 1:02d}",
                (x, top, 3.6, row_h),
                role="numeral",
                size=numeral_size,
                color=numeral_color,
                font="display",
                line_height=1.2,
            )
        )
        out.append(
            text(
                row,
                (x + 4.8, top + 0.25, w - 4.8, row_h),
                size=size,
                line_height=1.5,
                max_lines=est_lines(row, w - 4.8, size),
            )
        )
    return out


def alert_bars(
    texts: list[str],
    box: tuple[float, float, float, float],
    *,
    size: float = 2.0,
    bar_color: str = "accent",
    line_height: float = 1.5,
) -> list[SceneElement]:
    """警示：左侧竖条 + 正文。红线就该看起来像红线。"""
    rows = [str(item) for item in texts if str(item).strip()]
    if not rows:
        return []
    x, y, w, h = box
    n = len(rows)
    gap = 2.2
    row_h = min((h - gap * (n - 1)) / n, size * 2.8)
    out: list[SceneElement] = []
    for index, row in enumerate(rows):
        top = y + index * (row_h + gap)
        out.append(rect((x, top + 0.1, 0.5, row_h - 0.2), fill=bar_color, z=18))
        out.append(
            text(
                row,
                (x + 1.9, top, w - 1.9, row_h),
                size=size,
                line_height=line_height,
                max_lines=est_lines(row, w - 1.9, size),
            )
        )
    return out


# ── 共享内容排版器（八种内容页型 + 兜底封面/章节） ─────────────────────────


def paint_content(ctx: PageContext, style: MasterStyle) -> Scene:
    """八种内容页型的共享骨架：眉标 + 标题 + 短线，内容区按页型分派。"""
    left, right, top, bottom = style.margin
    content_w = CANVAS_W - left - right
    foot_y = CANVAS_H - bottom
    els: list[SceneElement] = []

    kicker = (ctx.deck_title or "")[:12]
    if style.kicker and kicker:
        els.append(
            text(
                kicker,
                (left, top, 42.0, 2.0),
                role="kicker",
                size=1.3,
                color=style.kicker_color,
                tracking=style.kicker_tracking,
                z=21,
            )
        )
    size = fit_title(ctx.title, style.title_sizes)
    title_w = content_w * 0.84
    lines = est_lines(ctx.title, title_w, size)
    title_y = top + (2.6 if style.kicker and kicker else 0.0)
    title_h = size * 1.3 * lines + 0.4
    if style.title_bar:
        els.append(rect((left - 1.7, title_y + 0.2, 0.5, title_h - 0.4), fill=style.rule_color, z=19))
    els.append(
        text(
            ctx.title,
            (left, title_y, title_w, title_h),
            role="title",
            size=size,
            font="display",
            line_height=1.3,
            max_lines=lines,
            z=22,
        )
    )
    head_bottom = title_y + title_h
    if style.head_rule:
        els.append(rule((left, head_bottom + 0.7, style.rule_len, 0.3), color=style.rule_color, weight=style.rule_weight))
        head_bottom += 0.7 + max(style.rule_weight, 0.3)
    content_top = head_bottom + 1.9
    content_h = foot_y - content_top
    content_box = (left, content_top, content_w, content_h)

    figure = ctx.figure if style.content_figure else None
    body_box = content_box
    if figure and ctx.purpose in ("points", "summary", "data"):
        fig_w = min(30.0, content_w * 0.34)
        if ctx.composition == "text-image-left":
            _append_figure(els, figure, (left, content_top, fig_w, content_h), style)
            body_box = (left + fig_w + 2.6, content_top, content_w - fig_w - 2.6, content_h)
        else:
            fig_x = left + content_w - fig_w
            _append_figure(els, figure, (fig_x, content_top, fig_w, content_h), style)
            body_box = (left, content_top, content_w - fig_w - 2.6, content_h)

    els.extend(_content_elements(ctx, body_box, style))

    if style.footer_rule:
        els.append(rule((left, foot_y + 0.8, content_w, 0.2), color="line", weight=0.12, z=6))

    return Scene(
        note=f"{style.note}·{ROLE_LABEL.get(ctx.purpose, ctx.purpose)}",
        background=scene_background(ctx),
        elements=els,
    )


def _append_figure(els: list[SceneElement], figure: dict[str, Any], box: tuple[float, float, float, float], style: MasterStyle) -> None:
    """图位上纸；装裱（hairline/mat）与阴影由风格旋钮决定，不再是额外的 rect。"""
    frame = "mat" if style.figure_mat else ("hairline" if style.figure_frame else "none")
    element = figure_el(
        figure,
        box,
        radius=1.0,
        frame=frame,
        shadow=style.figure_shadow,
        img_treatment=style.figure_treatment,
    )
    if element is None:
        return
    els.append(element)


def _content_elements(ctx: PageContext, box: tuple[float, float, float, float], style: MasterStyle) -> list[SceneElement]:
    purpose = ctx.purpose
    blocks = [block for block in ctx.blocks if isinstance(block, dict)]
    columns = [col for col in ctx.columns if isinstance(col, dict) and col.get("items")]
    bullets = [str(item) for item in ctx.bullets if str(item).strip()]
    body_size, body_line = fit_bullets(max(len(bullets), 3))

    if purpose == "agenda":
        return stack(bullets, box, size=body_size, line_height=body_line, marker="number", marker_color=style.marker_color)

    if purpose in ("compare", "matrix"):
        cols = columns
        if not cols:
            for block in blocks:
                if block.get("type") == "matrix" and block.get("columns"):
                    cols = [col for col in block["columns"] if isinstance(col, dict) and col.get("items")]
                    break
        if cols:
            return columns_layout(cols, box, size=body_size * 0.82, head_color=style.column_head_color)
        return stack(bullets, box, size=body_size, line_height=body_line, marker=style.marker, marker_color=style.marker_color)

    if purpose == "process":
        items = bullets
        for block in blocks:
            if block.get("type") == "steps" and block.get("items"):
                items = [str(item) for item in block["items"] if str(item).strip()]
                break
        if items:
            return steps_flow(items, box, size=body_size, numeral_color=style.marker_color)
        return stack(bullets, box, size=body_size, line_height=body_line, marker=style.marker, marker_color=style.marker_color)

    if purpose == "data":
        figure_blocks = [block for block in blocks if block.get("type") == "figures" and str(block.get("value") or "").strip()]
        if figure_blocks:
            return cards(figure_blocks, box, accent=style.rule_color)
        return stack(bullets, box, size=body_size, line_height=body_line, marker=style.marker, marker_color=style.marker_color)

    if purpose == "warning":
        texts = [str(block.get("text") or "") for block in blocks if block.get("type") == "alert" and block.get("text")]
        texts = [item for item in texts if item.strip()] or bullets
        if texts:
            return alert_bars(texts, box, size=body_size, bar_color=style.rule_color, line_height=body_line)
        return []

    # points / summary / 兜底：正文栈；def 块拼「术语：释义」。
    texts: list[str] = []
    for block in blocks:
        kind = str(block.get("type") or "")
        if kind in ("point", "alert", "quote") and str(block.get("text") or "").strip():
            texts.append(str(block.get("text")))
        elif kind == "def":
            heading = str(block.get("heading") or "").strip()
            body = str(block.get("text") or "").strip()
            if heading or body:
                texts.append(f"{heading}：{body}" if heading and body else (heading or body))
    rows = [item for item in texts if item.strip()] or bullets

    out: list[SceneElement] = []
    body = box
    if purpose == "summary" and ctx.subtitle:
        # 底部留一句定论：rule + accent 副题。
        body = (box[0], box[1], box[2], max(6.0, box[3] - 5.0))
    out.extend(
        stack(rows, body, size=body_size, line_height=body_line, marker=style.marker, marker_color=style.marker_color)
    )
    if purpose == "summary" and ctx.subtitle:
        y = body[1] + body[3]
        out.append(rule((body[0], y + 0.7, 6.0, 0.2), color=style.rule_color, weight=0.24))
        out.append(
            text(
                ctx.subtitle,
                (body[0], y + 1.5, body[2], 3.0),
                role="subtitle",
                size=2.0,
                color=style.marker_color,
                font="display",
                line_height=1.4,
                max_lines=2,
            )
        )
    return out


def content_painters(style: MasterStyle) -> dict[str, Painter]:
    """八种内容页型共用 paint_content。封面/章节由各母版自己写。"""

    def _paint(ctx: PageContext) -> Scene:
        return paint_content(ctx, style)

    return {role: _paint for role in ("points", "agenda", "compare", "matrix", "process", "data", "warning", "summary")}


def default_cover(ctx: PageContext, style: MasterStyle) -> Scene:
    """兜底封面：居中大字 + 短线。各母版都会覆写，这里只保证「没有也能出」。"""
    els: list[SceneElement] = []
    kicker = (ctx.deck_title or "")[:12]
    if kicker:
        els.append(text(kicker, (10.0, 12.0, 80.0, 2.0), role="kicker", size=1.4, color=style.kicker_color, align="center", tracking=0.4, z=21))
    size = fit_title(ctx.title, style.cover_sizes)
    lines = est_lines(ctx.title, 72.0, size)
    els.append(
        text(
            ctx.title,
            (14.0, 18.0, 72.0, size * 1.3 * lines + 0.5),
            role="title",
            size=size,
            font="display",
            align="center",
            line_height=1.3,
            max_lines=lines,
            z=22,
        )
    )
    if ctx.subtitle:
        els.append(text(ctx.subtitle, (18.0, 40.0, 64.0, 5.0), role="subtitle", color="muted", align="center", line_height=1.5))
    els.append(rule((44.0, 47.5, 12.0, 0.3), color=style.rule_color, weight=style.rule_weight))
    element = figure_el(ctx.figure, (64.0, 8.4, 27.0, 30.0))
    if element is not None:
        els.append(element)
    return Scene(note=f"{style.note}·封面", background=scene_background(ctx), elements=els)


def default_section(ctx: PageContext, style: MasterStyle) -> Scene:
    """兜底章节页：居中标题 + 章节序号。"""
    els: list[SceneElement] = []
    numeral = CN_NUMERALS[ctx.index % len(CN_NUMERALS)]
    els.append(
        text(
            numeral,
            (72.0, 10.0, 14.0, 12.0),
            role="numeral",
            size=7.6,
            color="muted",
            font="display",
            line_height=1.1,
            z=18,
        )
    )
    size = fit_title(ctx.title, style.section_sizes)
    lines = est_lines(ctx.title, 56.0, size)
    els.append(
        text(
            ctx.title,
            (9.0, 20.0, 56.0, size * 1.3 * lines + 0.5),
            role="title",
            size=size,
            font="display",
            line_height=1.3,
            max_lines=lines,
            z=22,
        )
    )
    if ctx.subtitle:
        els.append(text(ctx.subtitle, (9.0, 34.0, 52.0, 4.5), role="subtitle", color="muted"))
    els.append(rule((9.0, 32.0, 8.0, 0.3), color=style.rule_color, weight=style.rule_weight))
    return Scene(note=f"{style.note}·章节", background=scene_background(ctx), elements=els)
