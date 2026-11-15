"""青瓷文楷：清新教学的浅色母版。training 的默认选择。

气质：青瓷浅绿、深绿字、竹叶母题轻轻压角；通篇文楷，行距放宽，
适合「演示一遍 → 分步练」的带教节奏。
"""

from __future__ import annotations

from ..scene import CANVAS_H, CANVAS_W, Scene
from .base import (
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
    text,
)

SPEC = MasterSpec(
    id="celadon-wenkai",
    label="青瓷文楷",
    tagline="青瓷浅绿、深绿字、竹叶一点；清楚又不端着",
    genres=("training", "orientation"),
    theme="celadon",
    type_pair="kai-song",
    palette_variants=(
        {"label": "青瓷竹色", "bg": "#F2F6F3", "fg": "#173B30", "muted": "#5E7A6E", "accent": "#2E7D5B", "line": "#C9D8CF"},
        {"label": "雨过天青", "bg": "#EEF3F6", "fg": "#1E3440", "muted": "#5F7480", "accent": "#3A7CA5", "line": "#CBD8DF"},
    ),
    background={"kind": "motif", "motif": "paper-plain"},
    decoration_sets=(
        # 真纹样默认在前（素材包优先），程序母题作备选轮换——空库时 select
        # 的阶梯会回落母题，不会空窗。理由同 xuan：库里有 CC0 真纹样就该用真的。
        {
            "id": "real",
            "label": "真纹样角花",
            # 同 xuan：角花走素材包，纸面留程序纸纹（素材包的 ground 是横带类图案）。
            "ornament": {"subject": "卷草角花", "keywords": ["角花", "卷草"]},
            "ground": {"motif": "paper-plain"},
        },
        {
            "id": "bamboo",
            "label": "竹叶角花",
            "ornament": {"motif": "corner-bamboo"},
            "ground": {"motif": "paper-plain"},
        },
        {
            "id": "branch",
            "label": "折枝角花",
            "ornament": {"motif": "corner-branch"},
            "ground": {"motif": "paper-chain"},
        },
        {"id": "none", "label": "素面无饰", "ornament": {"motif": "none"}, "ground": {"motif": "paper-plain"}},
    ),
)

STYLE = MasterStyle(
    kicker=True,
    kicker_color="accent",
    kicker_tracking=0.3,
    title_sizes=(3.2, 2.7, 2.2),
    rule_color="accent",
    rule_weight=0.26,
    marker="ring",
    body_line=1.62,
    note="青瓷文楷",
)


def _cover(ctx: PageContext) -> Scene:
    els = []
    figure = ctx.figure
    source, kind = ornament_source(ctx)
    kicker = (ctx.deck_title or "")[:12]
    if kicker:
        els.append(
            text(
                kicker,
                (9.0, 10.0, 40.0, 2.0),
                role="kicker",
                size=1.3,
                color="accent",
                tracking=0.3,
                z=21,
            )
        )
    text_w = 52.0 if figure else 72.0
    size = fit_title(ctx.title, (6.6, 5.4, 4.2) if figure else (7.0, 5.8, 4.5))
    lines = est_lines(ctx.title, text_w, size)
    els.append(
        text(
            ctx.title,
            (9.0, 15.0, text_w, size * 1.28 * lines + 0.5),
            role="title",
            size=size,
            font="display",
            line_height=1.28,
            max_lines=lines,
            z=22,
        )
    )
    title_bottom = 15.0 + size * 1.28 * lines + 1.4
    if ctx.subtitle:
        els.append(
            text(
                ctx.subtitle,
                (9.0, title_bottom, text_w, 6.0),
                role="subtitle",
                color="muted",
                size=2.2,
                line_height=1.65,
            )
        )
    els.append(rule((9.0, 43.5, 9.0, 0.26), color="accent", weight=0.26))
    if figure:
        fig = figure_el(figure, (CANVAS_W - 9.0 - 28.0, 8.4, 28.0, CANVAS_H - 8.4 - 7.2), radius=1.4)
        if fig is not None:
            els.append(fig)
    # 竹叶只压一只角：清新感的来源是「少」。
    els.extend(corner_set(source, 12.0, corners=("bl",), kind=kind if kind else "motif"))
    return Scene(note="青瓷文楷·封面", background=scene_background(ctx, SPEC.background), elements=els)


def _section(ctx: PageContext) -> Scene:
    els = []
    source, kind = ornament_source(ctx)
    size = fit_title(ctx.title, STYLE.section_sizes)
    lines = est_lines(ctx.title, 48.0, size)
    title_h = size * 1.3 * lines + 0.5
    title_y = 20.0
    els.append(
        text(
            ctx.title,
            (26.0, title_y, 48.0, title_h),
            role="title",
            size=size,
            font="display",
            align="center",
            line_height=1.3,
            max_lines=lines,
            z=22,
        )
    )
    # 居中标题两侧各一段短绿线：对称但不封死。
    mid_y = title_y + title_h / 2
    els.append(rule((14.0, mid_y - 0.1, 8.0, 0.2), color="accent", weight=0.2))
    els.append(rule((CANVAS_W - 22.0, mid_y - 0.1, 8.0, 0.2), color="accent", weight=0.2))
    if ctx.subtitle:
        els.append(
            text(
                ctx.subtitle,
                (24.0, title_y + title_h + 1.8, 52.0, 4.4),
                role="subtitle",
                color="muted",
                align="center",
            )
        )
    els.extend(corner_set(source, 11.0, corners=("bl",), kind=kind if kind else "motif"))
    return Scene(note="青瓷文楷·章节", background=scene_background(ctx, SPEC.background), elements=els)


MASTER = Master(
    spec=SPEC,
    style=STYLE,
    painters={
        **content_painters(STYLE),
        "cover": _cover,
        "section": _section,
    },
)
