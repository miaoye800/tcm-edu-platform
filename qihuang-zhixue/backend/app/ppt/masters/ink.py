"""深墨金线：庄重现代的深色母版。defense / pitch 的默认选择。

气质：深墨底、米白字、金色 hairline；无角花，靠细线与居中构图撑住仪式感。
封面是「双细线画框 + 居中大字 + 上下金线」，答辩与路演的场合天然合身。
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
    id="ink-gold",
    label="深墨金线",
    tagline="深墨底、米白字、一道金线；庄重的仪式感",
    genres=("defense", "pitch"),
    theme="ink",
    type_pair="serif-full",
    palette_variants=(
        {"label": "墨底金线", "bg": "#151719", "fg": "#F2EFE8", "muted": "#9A958A", "accent": "#C9A961", "line": "#3A3D42"},
        {"label": "玄青银线", "bg": "#10151C", "fg": "#EDEFF2", "muted": "#8B93A1", "accent": "#9DB4C8", "line": "#2E3641"},
    ),
    background={"kind": "solid"},
    decoration_sets=(
        {"id": "plain", "label": "素面金线", "ornament": {"motif": "none"}, "ground": {"motif": ""}},
        {
            "id": "scroll",
            "label": "缠枝角花",
            "ornament": {"motif": "corner-scroll"},
            "ground": {"motif": "paper-mottle"},
        },
        {
            "id": "real",
            "label": "真纹样角花",
            "ornament": {"subject": "卷草角花", "keywords": ["角花", "卷草"]},
            "ground": {"subject": "冰裂底纹", "keywords": ["冰裂", "底纹"]},
        },
        {"id": "none", "label": "不装饰", "ornament": {"motif": "none"}, "ground": {"motif": ""}},
    ),
)

STYLE = MasterStyle(
    kicker=True,
    kicker_color="accent",
    kicker_tracking=0.5,
    title_sizes=(3.4, 2.8, 2.3),
    rule_color="accent",
    rule_weight=0.22,
    rule_len=6.0,
    marker="dash",
    figure_frame=True,
    figure_shadow=0.4,
    figure_treatment="toned",
    note="深墨金线",
)


def _cover(ctx: PageContext) -> Scene:
    els = []
    figure = ctx.figure
    # 双细线画框：外圈粗一点、内圈更细——装裱的做法。
    els.append(rect((3.2, 3.2, CANVAS_W - 6.4, CANVAS_H - 6.4), stroke="line", stroke_width=0.2, z=6))
    els.append(rect((4.6, 4.6, CANVAS_W - 9.2, CANVAS_H - 9.2), stroke="line", stroke_width=0.1, z=6))
    kicker = (ctx.deck_title or "")[:12]
    if kicker:
        els.append(
            text(
                kicker,
                (12.0, 12.5, 76.0, 2.2),
                role="kicker",
                size=1.4,
                color="accent",
                align="center",
                tracking=0.5,
                z=21,
            )
        )
    title_w = 60.0 if figure else 78.0
    size = fit_title(ctx.title, (6.2, 5.2, 4.2))
    lines = est_lines(ctx.title, title_w, size)
    title_h = size * 1.3 * lines + 0.5
    title_y = 20.0
    els.append(rule((CANVAS_W / 2 - 9.0, title_y - 1.6, 18.0, 0.22), color="accent", weight=0.22))
    els.append(
        text(
            ctx.title,
            ((CANVAS_W - title_w) / 2, title_y, title_w, title_h),
            role="title",
            size=size,
            font="display",
            align="center",
            line_height=1.3,
            max_lines=lines,
            z=22,
        )
    )
    els.append(rule((CANVAS_W / 2 - 9.0, title_y + title_h + 1.2, 18.0, 0.22), color="accent", weight=0.22))
    if ctx.subtitle:
        els.append(
            text(
                ctx.subtitle,
                (16.0, title_y + title_h + 3.4, CANVAS_W - 32.0, 5.0),
                role="subtitle",
                color="muted",
                align="center",
                tracking=0.18,
                line_height=1.6,
            )
        )
    if figure:
        # 图不抢题：画框内右下角一小幅，装裱位。
        fig = figure_el(figure, (CANVAS_W - 34.0, 30.0, 22.0, 18.0), radius=0.6)
        if fig is not None:
            els.append(fig)
            els.append(rect((CANVAS_W - 34.0, 30.0, 22.0, 18.0), stroke="line", stroke_width=0.14, z=14))
    return Scene(note="深墨金线·封面", background=scene_background(ctx, SPEC.background), elements=els)


def _section(ctx: PageContext) -> Scene:
    els = []
    els.append(rect((0.0, 0.0, CANVAS_W, 0.6), fill="accent", z=6))
    size = fit_title(ctx.title, STYLE.section_sizes)
    lines = est_lines(ctx.title, 56.0, size)
    title_h = size * 1.3 * lines + 0.5
    title_y = 21.0
    # 金色竖线在标题左侧：一道就把「换了章节」说清了。
    els.append(rule((34.0, title_y - 0.4, 0.14, title_h + 0.8), color="accent", weight=0.14, z=10))
    els.append(
        text(
            ctx.title,
            (38.0, title_y, 52.0, title_h),
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
                (38.0, title_y + title_h + 1.6, 50.0, 4.4),
                role="subtitle",
                color="muted",
            )
        )
    return Scene(note="深墨金线·章节", background=scene_background(ctx, SPEC.background), elements=els)


MASTER = Master(
    spec=SPEC,
    style=STYLE,
    painters={
        **content_painters(STYLE),
        "cover": _cover,
        "section": _section,
    },
)
