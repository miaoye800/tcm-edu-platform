"""现代简约：商务汇报的浅色无饰母版。brief 的默认选择。

气质：浅灰白底、炭黑大字、一道蓝色顶栏；零装饰零角花，靠「顶栏 + 大字 +
hairline 页脚」三件套撑住版面。这是「做精」的两套之二——它的对手不是
中式母版的花，而是 Keynote 商务模板的克制。
"""

from __future__ import annotations

from ..scene import CANVAS_H, CANVAS_W, Scene
from .base import (
    Master,
    MasterSpec,
    MasterStyle,
    PageContext,
    content_painters,
    est_lines,
    figure_el,
    fit_title,
    rect,
    rule,
    scene_background,
    text,
)

SPEC = MasterSpec(
    id="modern-sans",
    label="现代简约",
    tagline="浅灰白底、炭黑大字、一道蓝；零装饰的商务克制",
    genres=("brief", "training"),
    theme="paper",
    type_pair="sans-full",
    palette_variants=(
        {"label": "石墨蓝", "bg": "#F6F7F9", "fg": "#1F2530", "muted": "#5C6572", "accent": "#2F6BB4", "line": "#D8DDE4"},
        {"label": "暖灰橙", "bg": "#FAFAF8", "fg": "#23262B", "muted": "#6A6F76", "accent": "#B4552F", "line": "#E0DFDA"},
    ),
    background={"kind": "solid"},
    decoration_sets=(
        {"id": "plain", "label": "素面", "ornament": {"motif": "none"}, "ground": {"motif": ""}},
        {"id": "paper", "label": "细纸纹", "ornament": {"motif": "none"}, "ground": {"motif": "paper-plain"}},
        {
            "id": "real",
            "label": "几何纹样",
            "ornament": {"subject": "卷草角花", "keywords": ["角花", "卷草"]},
            "ground": {"subject": "中式纹样", "keywords": ["纹样", "几何"]},
        },
        {"id": "none", "label": "不装饰", "ornament": {"motif": "none"}, "ground": {"motif": ""}},
    ),
)

STYLE = MasterStyle(
    kicker=True,
    kicker_color="muted",
    kicker_tracking=0.24,
    title_sizes=(3.3, 2.7, 2.2),
    rule_color="accent",
    rule_weight=0.4,
    rule_len=5.0,
    marker="dash",
    figure_shadow=0.3,
    footer_rule=True,
    note="现代简约",
)


def _cover(ctx: PageContext) -> Scene:
    els = []
    figure = ctx.figure
    # 顶栏：一道实色蓝，宽度吃满出血。整页唯一的色块，克制全靠它。
    els.append(rect((0.0, 0.0, CANVAS_W, 1.8), fill="accent", z=6))
    kicker = (ctx.deck_title or "")[:12]
    if kicker:
        els.append(
            text(
                kicker,
                (9.0, 6.2, 40.0, 2.0),
                role="kicker",
                size=1.3,
                color="muted",
                tracking=0.24,
                z=21,
            )
        )
    text_w = 56.0 if figure else 74.0
    size = fit_title(ctx.title, (6.8, 5.6, 4.4))
    lines = est_lines(ctx.title, text_w, size)
    # 大字沉到下三分之一：现代排版的经典重心。
    title_y = 27.0
    els.append(
        text(
            ctx.title,
            (9.0, title_y, text_w, size * 1.22 * lines + 0.5),
            role="title",
            size=size,
            font="display",
            line_height=1.22,
            max_lines=lines,
            z=22,
        )
    )
    if ctx.subtitle:
        els.append(
            text(
                ctx.subtitle,
                (9.0, title_y + size * 1.22 * lines + 1.6, text_w, 5.4),
                role="subtitle",
                color="muted",
                size=2.2,
                line_height=1.55,
            )
        )
    els.append(rule((9.0, 22.5, 5.0, 0.4), color="accent", weight=0.4))
    if figure:
        fig = figure_el(figure, (CANVAS_W - 9.0 - 30.0, 9.0, 30.0, 34.0), radius=0.8)
        if fig is not None:
            els.append(fig)
    return Scene(note="现代简约·封面", background=scene_background(ctx, SPEC.background), elements=els)


def _section(ctx: PageContext) -> Scene:
    els = []
    els.append(rect((0.0, 0.0, CANVAS_W, 1.8), fill="accent", z=6))
    # 巨号序数压灰，标题旁边竖线提色。
    numeral = f"{ctx.index + 1:02d}"
    els.append(
        text(
            numeral,
            (9.0, 13.0, 18.0, 12.0),
            role="numeral",
            size=7.6,
            color="muted",
            font="display",
            line_height=1.1,
            z=18,
        )
    )
    els.append(rule((26.0, 14.5, 0.14, 9.6), color="accent", weight=0.14, z=10))
    size = fit_title(ctx.title, STYLE.section_sizes)
    lines = est_lines(ctx.title, 54.0, size)
    els.append(
        text(
            ctx.title,
            (30.0, 16.0, 54.0, size * 1.3 * lines + 0.5),
            role="title",
            size=size,
            font="display",
            line_height=1.3,
            max_lines=lines,
            z=22,
        )
    )
    if ctx.subtitle:
        els.append(
            text(
                ctx.subtitle,
                (30.0, 16.0 + size * 1.3 * lines + 1.6, 52.0, 4.4),
                role="subtitle",
                color="muted",
            )
        )
    return Scene(note="现代简约·章节", background=scene_background(ctx, SPEC.background), elements=els)


MASTER = Master(
    spec=SPEC,
    style=STYLE,
    painters={
        **content_painters(STYLE),
        "cover": _cover,
        "section": _section,
    },
)
