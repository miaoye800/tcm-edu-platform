"""朱砂编辑部：杂志感的暖色母版。lecture / pitch 的备选。

气质：暖朱砂、深褐字、通栏题头带；像一本中医药刊物的开扉页。
装饰主打「横带」（云纹 / 回纹边带素材），不是角花——这是它与宣纸母版
最直观的分野。
"""

from __future__ import annotations

from ..scene import CANVAS_H, CANVAS_W, Scene
from .base import (
    Master,
    MasterSpec,
    MasterStyle,
    PageContext,
    band_el,
    content_painters,
    corner_set,
    est_lines,
    figure_el,
    fit_title,
    ornament_source,
    rect,
    rule,
    scene_background,
    text,
)

SPEC = MasterSpec(
    id="cinnabar-editorial",
    label="朱砂编辑部",
    tagline="暖朱砂题头带、大号宋体标题；杂志开扉页的编辑感",
    genres=("lecture", "pitch"),
    theme="cinnabar",
    type_pair="serif-full",
    palette_variants=(
        {"label": "朱砂深褐", "bg": "#FBF2ED", "fg": "#2A1815", "muted": "#7A6A60", "accent": "#A83B2E", "line": "#E3D2C6"},
        {"label": "墨赭暖纸", "bg": "#F7EFE8", "fg": "#33211B", "muted": "#7C6A5F", "accent": "#8C3B2E", "line": "#DFCDBE"},
    ),
    background={"kind": "motif", "motif": "paper-mottle"},
    decoration_sets=(
        {
            "id": "band",
            "label": "云纹横带",
            "ornament": {"subject": "回纹横带", "keywords": ["回纹", "横带"]},
            "ground": {"motif": "paper-mottle"},
        },
        {
            "id": "scroll",
            "label": "卷草角花",
            "ornament": {"motif": "corner-scroll"},
            "ground": {"motif": "paper-mottle"},
        },
        {
            "id": "real",
            "label": "花边横带",
            "ornament": {"subject": "花边横带", "keywords": ["花边", "横带"]},
            "ground": {"subject": "民间纹样", "keywords": ["纹样", "底纹"]},
        },
        {"id": "none", "label": "素面无饰", "ornament": {"motif": "none"}, "ground": {"motif": "paper-plain"}},
    ),
)

STYLE = MasterStyle(
    kicker=True,
    kicker_color="accent",
    kicker_tracking=0.45,
    title_sizes=(3.4, 2.8, 2.3),
    rule_color="accent",
    rule_weight=0.4,
    rule_len=12.0,
    marker="dash",
    title_bar=True,
    note="朱砂编辑部",
)


def _band(ctx: PageContext, y: float, height: float = 5.2) -> list:
    """底部的装饰横带。真纹样资产铺通栏；只有程序母题时退化为底部双角。"""
    els = []
    if ctx.ornament_ids:
        band = band_el(ctx.ornament_ids[0], (1.5, y, CANVAS_W - 3.0, height), kind="asset")
        if band is not None:
            els.append(band)
            return els
    if ctx.ornament_motif:
        # 程序母题是角花不是横带：压底双角（顶部有题头带，别再叠角）。
        els.extend(corner_set(ctx.ornament_motif, 9.5, corners=("bl", "br")))
        return els
    return els


def _cover(ctx: PageContext) -> Scene:
    els = []
    figure = ctx.figure
    # 通栏题头带：朱砂实色，报头写在带里。
    els.append(rect((0.0, 0.0, CANVAS_W, 6.0), fill="accent", z=6))
    kicker = (ctx.deck_title or "")[:12]
    if kicker:
        els.append(
            text(
                kicker,
                (9.0, 1.5, 60.0, 3.0),
                role="kicker",
                size=1.6,
                color="bg",
                tracking=0.45,
                z=21,
            )
        )
    text_w = 58.0 if figure else 74.0
    size = fit_title(ctx.title, (7.0, 5.8, 4.5))
    lines = est_lines(ctx.title, text_w, size)
    els.append(
        text(
            ctx.title,
            (9.0, 12.5, text_w, size * 1.26 * lines + 0.5),
            role="title",
            size=size,
            font="display",
            line_height=1.26,
            max_lines=lines,
            z=22,
        )
    )
    title_bottom = 12.5 + size * 1.26 * lines + 1.4
    if ctx.subtitle:
        els.append(
            text(
                ctx.subtitle,
                (9.0, title_bottom, text_w, 5.6),
                role="subtitle",
                color="muted",
                size=2.2,
                line_height=1.6,
            )
        )
    els.append(rule((9.0, 41.5, 14.0, 0.4), color="accent", weight=0.4))
    if figure:
        fig = figure_el(figure, (CANVAS_W - 9.0 - 28.0, 10.0, 28.0, 32.0), radius=1.0)
        if fig is not None:
            els.append(fig)
    els.extend(_band(ctx, CANVAS_H - 8.2))
    if not ctx.ornament_ids and not ctx.ornament_motif:
        # 没装饰也留一道通栏细线，题头带的呼应。
        els.append(rule((0.0, CANVAS_H - 4.4, CANVAS_W, 0.35), color="accent", weight=0.35, z=8))
    return Scene(note="朱砂编辑部·封面", background=scene_background(ctx, SPEC.background), elements=els)


def _section(ctx: PageContext) -> Scene:
    els = []
    els.append(rect((0.0, 0.0, CANVAS_W, 1.4), fill="accent", z=6))
    size = fit_title(ctx.title, STYLE.section_sizes)
    lines = est_lines(ctx.title, 56.0, size)
    els.append(
        text(
            ctx.title,
            (9.0, 17.0, 56.0, size * 1.3 * lines + 0.5),
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
                (9.0, 17.0 + size * 1.3 * lines + 1.6, 52.0, 4.4),
                role="subtitle",
                color="muted",
            )
        )
    els.append(rule((9.0, 38.5, 12.0, 0.35), color="accent", weight=0.35))
    els.extend(_band(ctx, CANVAS_H - 7.4, height=4.6))
    return Scene(note="朱砂编辑部·章节", background=scene_background(ctx, SPEC.background), elements=els)


MASTER = Master(
    spec=SPEC,
    style=STYLE,
    painters={
        **content_painters(STYLE),
        "cover": _cover,
        "section": _section,
    },
)
