"""宣纸宋楷：古典雅致的默认母版。lecture / orientation 的默认选择。

气质：素纸底纹 + 回纹角花 + 印章 + 竖排封面。这是「做精」的两套之一——
封面短标题走竖排（中文册页的传统开法），长标题与带图封面退横排大字；
章节页用中文数字水印压角，正文用菱点要点栈。
"""

from __future__ import annotations

from ..scene import CANVAS_H, CANVAS_W, Scene
from .base import (
    CN_NUMERALS,
    Master,
    MasterSpec,
    MasterStyle,
    PageContext,
    content_painters,
    corner_set,
    est_lines,
    figure_el,
    fit_title,
    ornament_source,
    rule,
    scene_background,
    seal_el,
    text,
)

SPEC = MasterSpec(
    id="xuan-songkai",
    label="宣纸宋楷",
    tagline="素纸、墨字、朱砂一点；古籍册页的静气",
    genres=("lecture", "orientation"),
    theme="paper",
    type_pair="song-kai",
    palette_variants=(
        {"label": "宣纸朱砂", "bg": "#FAF7F0", "fg": "#26221C", "muted": "#6B6459", "accent": "#A83B2E", "line": "#DDD6C8"},
        {"label": "素笺赭石", "bg": "#F5F1E8", "fg": "#2B2620", "muted": "#75705F", "accent": "#8C6A3F", "line": "#D9D2C0"},
    ),
    background={"kind": "motif", "motif": "paper-chain"},
    decoration_sets=(
        # 真纹样排第 0 套：素材包里已经有 CC0 卷草角花/回纹底纹，默认就该用它。
        # 「程序母题」降为备选轮换——库里搜不到时 select 的阶梯照样回落母题，
        # 所以把真纹样放前面不会出现空窗（tests/test_ppt_masters.py 有断言）。
        {
            "id": "real",
            "label": "真纹样角花",
            # 角花走素材包（CC0 卷草角花）；纸面仍用程序纸纹——素材包里的 ground
            # 都是「横带/剪纸」类图案，当满版底纹会变成一条条花带，不如素纸。
            "ornament": {"subject": "卷草角花", "keywords": ["角花", "卷草"]},
            "ground": {"motif": "paper-chain"},
        },
        {
            "id": "fret",
            "label": "回纹角花",
            "ornament": {"motif": "corner-fret"},
            "ground": {"motif": "paper-chain"},
        },
        {
            "id": "branch",
            "label": "折枝角花",
            "ornament": {"motif": "corner-branch"},
            "ground": {"motif": "paper-plain"},
        },
        {"id": "none", "label": "素面无饰", "ornament": {"motif": "none"}, "ground": {"motif": "paper-plain"}},
    ),
)

STYLE = MasterStyle(
    kicker=True,
    kicker_color="accent",
    title_sizes=(3.4, 2.8, 2.3),
    rule_color="accent",
    marker="diamond",
    figure_mat=True,
    figure_shadow=0.18,
    note="宣纸宋楷",
)


def _cover(ctx: PageContext) -> Scene:
    els = []
    title = ctx.title
    figure = ctx.figure
    source, kind = ornament_source(ctx)

    if len(title) <= 8 and not figure:
        # 竖排封面：右侧一列大字，左下副题。短标题的传统开法。
        size = max(3.0, min(5.2, (CANVAS_H - 18.0) / (max(len(title), 1) * 1.15)))
        column_h = len(title) * size * 1.15 + 0.5
        els.append(
            text(
                title,
                (58.0, 9.0, size * 1.7, column_h),
                role="title",
                size=size,
                font="display",
                write="v",
                line_height=1.15,
                z=22,
            )
        )
        seal_y = 9.0 + column_h + 2.2
        if seal_y + 4.8 <= CANVAS_H - 7.0:
            els.append(seal_el("岐黄", (58.3, seal_y, 4.6, 4.6)))
        kicker = (ctx.deck_title or "")[:12]
        if kicker:
            els.append(
                text(
                    kicker,
                    (9.0, 12.0, 40.0, 2.0),
                    role="kicker",
                    size=1.3,
                    color="accent",
                    tracking=0.4,
                    z=21,
                )
            )
        if ctx.subtitle:
            els.append(
                text(
                    ctx.subtitle,
                    (9.0, 28.0, 42.0, 8.0),
                    role="subtitle",
                    color="muted",
                    size=2.2,
                    line_height=1.6,
                )
            )
        els.append(rule((9.0, 42.0, 10.0, 0.32), color="accent", weight=0.32))
    else:
        # 横排封面：大字在左，图在右（有图时），角花四角。
        text_w = 52.0 if figure else 74.0
        sizes = (6.6, 5.4, 4.2) if figure else (7.6, 6.2, 4.8)
        size = fit_title(title, sizes)
        lines = est_lines(title, text_w, size)
        els.append(
            text(
                title,
                (9.0, 14.0, text_w, size * 1.28 * lines + 0.5),
                role="title",
                size=size,
                font="display",
                line_height=1.28,
                max_lines=lines,
                z=22,
            )
        )
        kicker = (ctx.deck_title or "")[:12]
        if kicker:
            els.append(
                text(
                    kicker,
                    (9.0, 9.5, 40.0, 2.0),
                    role="kicker",
                    size=1.3,
                    color="accent",
                    tracking=0.4,
                    z=21,
                )
            )
        title_bottom = 14.0 + size * 1.28 * lines + 1.2
        if ctx.subtitle:
            els.append(
                text(
                    ctx.subtitle,
                    (9.0, title_bottom, text_w, 6.0),
                    role="subtitle",
                    color="muted",
                    size=2.2,
                    line_height=1.6,
                )
            )
        els.append(rule((9.0, 44.0, 12.0, 0.32), color="accent", weight=0.32))
        if figure:
            fig = figure_el(figure, (CANVAS_W - 9.0 - 28.0, 8.4, 28.0, CANVAS_H - 8.4 - 7.2), radius=1.2)
            if fig is not None:
                els.append(fig)
    els.extend(corner_set(source, 13.5, kind=kind if kind else "motif"))
    return Scene(note="宣纸宋楷·封面", background=scene_background(ctx, SPEC.background), elements=els)


def _section(ctx: PageContext) -> Scene:
    els = []
    source, kind = ornament_source(ctx)
    numeral = CN_NUMERALS[ctx.index % len(CN_NUMERALS)]
    els.append(
        text(
            numeral,
            (72.0, 8.0, 16.0, 14.0),
            role="numeral",
            size=7.6,
            color="muted",
            font="display",
            line_height=1.1,
            z=18,
        )
    )
    size = fit_title(ctx.title, STYLE.section_sizes)
    lines = est_lines(ctx.title, 54.0, size)
    els.append(
        text(
            ctx.title,
            (9.0, 18.0, 54.0, size * 1.3 * lines + 0.5),
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
                (9.0, 18.0 + size * 1.3 * lines + 1.6, 50.0, 4.6),
                role="subtitle",
                color="muted",
            )
        )
    els.append(rule((9.0, 38.0, 8.0, 0.3), color="accent", weight=0.3))
    # 章节页留白多，角花只挂对角两只——四角全开是封面的待遇。
    els.extend(corner_set(source, 12.0, corners=("tl", "br"), kind=kind if kind else "motif"))
    return Scene(note="宣纸宋楷·章节", background=scene_background(ctx, SPEC.background), elements=els)


MASTER = Master(
    spec=SPEC,
    style=STYLE,
    painters={
        **content_painters(STYLE),
        "cover": _cover,
        "section": _section,
    },
)
