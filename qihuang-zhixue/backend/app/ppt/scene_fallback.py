"""兜底场景：模型没给 ``Scene`` 时，用这里拼一个。

**这不是模板库。** 区别在于：模板是「用户可以选的东西」，兜底是「模型没产出时的
安全网」。前者把设计权收走，后者只在失败时接管，且永远产出同一份可预测的结果。

十个 ``archetype`` 共用四个构造器（``_head`` / ``_stack`` / ``_columns`` / ``_figure``），
而不是十套坐标——写十套几何就是把 ``frames.ts`` 的错误再犯一次。

风格上只保留一条底线：**纸 + 墨 + 一条朱砂**。不加角花、不加远山——兜底不该比模型
设计的更花，否则用户会以为「降级版更好看」。
"""

from __future__ import annotations

from typing import Any, Iterable

from .scene import (
    CANVAS_H,
    CANVAS_W,
    Scene,
    SceneBackground,
    SceneElement,
    SceneText,
)

MARGIN = {"top": 8.4, "right": 9.0, "bottom": 7.2, "left": 9.0}
CONTENT_W = CANVAS_W - MARGIN["left"] - MARGIN["right"]
CONTENT_TOP = MARGIN["top"]
FOOT_Y = CANVAS_H - MARGIN["bottom"] + 1.2

#: 密度 → (块间距, 正文字号)。与前端 ``rowHeight`` 同一档意思。
DENSITY = {
    "airy": (3.2, 2.2),
    "normal": (2.0, 2.2),
    "dense": (1.2, 1.8),
}

#: 纸面处方 → 底纹母题。**与 ``frontend/src/render/skin.ts`` 的 `PAPER_MOTIFS` 逐条对应**，
#: 由 ``tests/test_scene.py`` 读那个文件校验。两层分开的理由不变：设计单存处方（语义），
#: 渲染层要母题（实现）。但两份表必须同源——处方改了母题不跟着改，就是一张白纸。
PAPER_MOTIFS: dict[str, str] = {
    "xuan-plain": "paper-plain",
    "xuan-chain": "paper-chain",
    "xuan-mottle": "paper-mottle",
    "ink-night": "paper-mottle",
    "celadon-mist": "paper-chain",
    "cinnabar-warm": "paper-mottle",
}

#: 角饰处方 → 角饰母题。同上，与 `ORNAMENT_MOTIFS` 对应。
ORNAMENT_MOTIFS: dict[str, str] = {
    "vine-a": "corner-vine",
    "branch-b": "corner-branch",
    "bamboo-d": "corner-bamboo",
    "fret-e": "corner-fret",
    "scroll-c": "corner-scroll",
    "none": "",
}


def paper_motif_for(paper: str | None) -> str:
    return PAPER_MOTIFS.get(paper or "", "paper-plain")


def ornament_motif_for(ornament_set: str | None) -> str:
    return ORNAMENT_MOTIFS.get(ornament_set or "", "")


def _text(
    content: str,
    box: tuple[float, float, float, float],
    *,
    role: str = "body",
    size: float | None = None,
    color: str = "fg",
    font: str = "body",
    write: str = "h",
    align: str = "left",
    line_height: float = 1.6,
    tracking: float = 0.0,
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
            line_height=line_height,
            tracking=tracking,
        ),
    )


def _rule(
    box: tuple[float, float, float, float],
    *,
    color: str = "accent",
    weight: float = 0.3,
    z: int = 10,
) -> SceneElement:
    orient = "v" if box[3] > box[2] else "h"
    return SceneElement(
        type="rule",
        box=box,
        z=z,
        orient=orient,  # type: ignore[arg-type]
        weight=weight,
        fill=color,  # type: ignore[arg-type]
    )


def _head(title: str, kicker: str, rule_w: float = 8.0) -> list[SceneElement]:
    """眉标 + 标题 + 一条短朱砂。所有内页共用这一个头，位置固定才有一本册子的感觉。"""
    out: list[SceneElement] = []
    if kicker:
        out.append(
            _text(
                kicker,
                (MARGIN["left"], CONTENT_TOP, 44.0, 2.4),
                role="kicker",
                size=1.3,
                color="accent",
                tracking=0.42,
                z=21,
            )
        )
    out.append(
        _text(
            title,
            (MARGIN["left"], CONTENT_TOP + 3.4, CONTENT_W, 7.6),
            role="title",
            size=3.2,
            font="display",
            line_height=1.32,
            z=22,
        )
    )
    out.append(_rule((MARGIN["left"], CONTENT_TOP + 11.6, rule_w, 0.3)))
    return out


def _corners(motif: str, size: float) -> list[SceneElement]:
    """四角角花：画一份，镜像三份。

    传统角饰本来就是四面对称的，存四张图是浪费，存四个 `flip` 才是排版该有的写法。
    尺寸分两档是因为两档的**可用余地**根本不同：正文页角落只剩 9×8.4，角花超过
    7 就会压到眉标；封面整张纸都是留白，给 14 才有分量。
    """
    if not motif:
        return []
    return [
        SceneElement(type="motif", box=(0.0, 0.0, size, size), z=8, motif=motif, flip="none"),
        SceneElement(type="motif", box=(CANVAS_W - size, 0.0, size, size), z=8, motif=motif, flip="x"),
        SceneElement(type="motif", box=(0.0, CANVAS_H - size, size, size), z=8, motif=motif, flip="y"),
        SceneElement(type="motif", box=(CANVAS_W - size, CANVAS_H - size, size, size), z=8, motif=motif, flip="xy"),
    ]


def _figure(
    item_id: str,
    box: tuple[float, float, float, float],
    *,
    caption: str = "",
    fit: str = "cover",
    kind: str = "asset",
) -> SceneElement:
    """图位两种：库内素材走 ``asset``，参数化母题走 ``motif``。

    这两者不能混：``select`` 的阶梯第 1 级是 parametric，此时 ``asset_id`` 是一个
    母题 id（``paper-plain``），把它塞进 ``asset`` 会被命名规则拒掉——而拒绝的结果
    是整份稿子失败，不是「少一张图」。
    """
    if kind == "motif":
        return SceneElement(type="motif", box=box, z=15, motif=item_id)
    return SceneElement(
        type="asset",
        box=box,
        z=15,
        asset=item_id,
        fit=fit,  # type: ignore[arg-type]
        caption=caption,
    )


def _stack(
    items: Iterable[str],
    box: tuple[float, float, float, float],
    *,
    size: float = 2.2,
    gap: float = 2.0,
    numbered: bool = False,
) -> list[SceneElement]:
    """纵向堆叠。条目数决定行高，不做等分行——等分会在条目少时把字拉得很开。

    编号用独立文字块而不是前缀，因为「01」要用强调色、字号也要大一档。
    """
    rows = [item for item in items if item]
    if not rows:
        return []
    out: list[SceneElement] = []
    x, y, w, _ = box
    # 行高 = min(给的高度 / 条目数, 字号 × 2.4)。后者防止两三条被摊到整页。
    row_h = min((box[3] - gap * (len(rows) - 1)) / len(rows), size * 2.6)
    for index, row in enumerate(rows):
        top = y + index * (row_h + gap)
        if numbered:
            out.append(
                _text(
                    f"{index + 1:02d}",
                    (x, top, 5.0, row_h),
                    role="numeral",
                    size=min(2.6, row_h * 0.8),
                    color="accent",
                    font="display",
                    line_height=1.2,
                )
            )
            out.append(_text(row, (x + 6.2, top, w - 6.2, row_h), size=size, line_height=1.5))
        else:
            out.append(_text(row, (x + 1.6, top, w - 1.6, row_h), size=size, line_height=1.5))
            # 菱形点：0.55cqw 的小方块旋转 45°，用 rect 的 rotate 表达。
            out.append(
                SceneElement(
                    type="rect",
                    box=(x + 0.1, top + size * 0.62, 0.55, 0.55),
                    z=19,
                    fill="accent",  # type: ignore[arg-type]
                    rotate=45.0,
                )
            )
    return out


def _columns(
    columns: list[dict[str, Any]],
    box: tuple[float, float, float, float],
    *,
    size: float = 2.2,
) -> list[SceneElement]:
    """多栏。栏间一条竖细线——表格的骨架是线，不是底色（斑马格在纸上像「被选中」）。"""
    cols = [c for c in columns if c.get("items")]
    if not cols:
        return []
    x, y, w, h = box
    gap = 2.4
    col_w = (w - gap * (len(cols) - 1)) / len(cols)
    out: list[SceneElement] = []
    for index, column in enumerate(cols):
        left = x + index * (col_w + gap)
        if index:
            # 用 `muted` 而不是 `line`：`line` 是页脚那种"几乎没有"的分隔线，
            # 用在两栏之间等于没画，读者会把两侧看成一段连续文字。
            out.append(_rule((left - gap / 2, y, 0.06, h), color="muted", weight=0.08, z=12))
        if column.get("heading"):
            out.append(
                _text(
                    column["heading"],
                    (left, y, col_w, 3.0),
                    role="subtitle",
                    size=size * 1.05,
                    color="accent",
                    font="display",
                    line_height=1.3,
                )
            )
        out.extend(
            _stack(
                column["items"],
                (left, y + 4.0, col_w, h - 4.0),
                size=size * 0.94,
                gap=1.2,
            )
        )
    return out


def _figures(blocks: list[dict[str, Any]], box: tuple[float, float, float, float]) -> list[SceneElement]:
    """数据卡：数值一行大号，标签在下，卡顶一道朱砂细线。"""
    cards = [b for b in blocks if b.get("value")]
    if not cards:
        return []
    x, y, w, h = box
    gap = 2.2
    card_w = (w - gap * (len(cards) - 1)) / len(cards)
    out: list[SceneElement] = []
    for index, card in enumerate(cards):
        left = x + index * (card_w + gap)
        out.append(_rule((left, y, card_w, 0.18), weight=0.18))
        out.append(
            _text(
                str(card["value"]),
                (left, y + 1.2, card_w, 7.0),
                role="numeral",
                size=4.6,
                color="accent",
                font="display",
                line_height=1.1,
            )
        )
        out.append(
            _text(
                str(card.get("heading", "")),
                (left, y + 8.8, card_w, 4.0),
                role="body",
                size=2.0,
                line_height=1.4,
            )
        )
        if card.get("source"):
            out.append(
                _text(
                    str(card["source"]),
                    (left, y + 13.2, card_w, 2.4),
                    role="caption",
                    size=1.3,
                    color="muted",
                )
            )
    return out


def fallback_scene(
    *,
    archetype: str,
    title: str,
    subtitle: str = "",
    blocks: list[dict[str, Any]] | None = None,
    bullets: list[str] | None = None,
    columns: list[dict[str, Any]] | None = None,
    density: str = "normal",
    figure_id: str = "",
    figure_caption: str = "",
    figure_kind: str = "asset",
    page_no: int = 1,
    total: int = 1,
    deck_title: str = "",
    paper_motif: str = "paper-plain",
    ornament_motif: str = "",
) -> Scene:
    """十个原型共用的兜底。**只保证不坏，不追求好看**——好看是模型的活。"""
    blocks = blocks or []
    bullets = bullets or []
    columns = columns or []
    gap, body_size = DENSITY.get(density, DENSITY["normal"])
    elements: list[SceneElement] = []
    background = SceneBackground(kind="motif", motif=paper_motif or "paper-plain")

    content_top = CONTENT_TOP + 15.2
    content_h = FOOT_Y - 2.0 - content_top
    content_box = (MARGIN["left"], content_top, CONTENT_W, content_h)

    if archetype == "cover":
        # 有图：左文右图，图出血到右沿（参考图 1 的骨架）。无图：文字居中留白。
        has_figure = bool(figure_id)
        text_w = CONTENT_W * (0.56 if has_figure else 0.86)
        elements.append(
            _text(
                title,
                (MARGIN["left"], 16.0, text_w, 20.0),
                role="title",
                size=7.6,
                font="display",
                line_height=1.22,
                tracking=0.08,
                z=22,
            )
        )
        if subtitle:
            elements.append(
                _text(
                    subtitle,
                    (MARGIN["left"], 38.0, text_w, 6.0),
                    role="subtitle",
                    size=2.2,
                    color="muted",
                    line_height=1.5,
                )
            )
        elements.append(_rule((MARGIN["left"], 46.0, 12.0, 0.32)))
        if has_figure:
            elements.append(
                _figure(
                    figure_id,
                    (CANVAS_W - 34.0, CONTENT_TOP, 34.0, CANVAS_H - CONTENT_TOP - MARGIN["bottom"]),
                    caption=figure_caption,
                    kind=figure_kind,
                )
            )
        elements.extend(_corners(ornament_motif, 14.0))
        return Scene(
            note="兜底封面：大字 + 一条朱砂，有图时左文右图",
            background=background,
            elements=elements,
        )

    if archetype == "section":
        elements.append(
            _text(
                title,
                (MARGIN["left"], 21.0, CONTENT_W * 0.7, 12.0),
                role="title",
                size=4.6,
                font="display",
                line_height=1.3,
            )
        )
        if subtitle:
            elements.append(
                _text(
                    subtitle,
                    (MARGIN["left"], 34.0, CONTENT_W * 0.66, 5.0),
                    role="subtitle",
                    size=2.2,
                    color="muted",
                )
            )
        elements.extend(_corners(ornament_motif, 14.0))
        return Scene(
            note="兜底章节页：居中偏上，右侧留给装饰",
            background=background,
            elements=elements,
        )

    # 其余原型：共用一个头，内容区按块的类型分派。
    elements.extend(_head(title, "" if archetype == "summary" else "", rule_w=8.0))

    out_blocks = [b for b in blocks if b.get("type")]
    matrix_blocks = [b for b in out_blocks if b.get("type") == "matrix" and b.get("columns")]
    if not matrix_blocks and columns:
        # 旧稿只有 `columns` 没有 `blocks`：对照页的两栏在这条路径上会整页丢失，
        # 只剩一个标题——那比没有这一页更糟，因为用户以为内容被删了。
        matrix_blocks = [{"type": "matrix", "columns": columns}]
    figure_blocks = [b for b in out_blocks if b.get("type") == "figures"]
    step_blocks = [b for b in out_blocks if b.get("type") == "steps"]
    plain = [b for b in out_blocks if b.get("type") in {"point", "alert", "quote", "def"}]

    body_box = content_box
    if figure_id and archetype in {"points", "summary", "data"}:
        # 有图时让出右侧三分之一。图宽 = 版心的 32%，与文字留 3cqw 缝。
        fig_w = CONTENT_W * 0.32
        body_box = (MARGIN["left"], content_top, CONTENT_W - fig_w - 3.0, content_h)
        elements.append(
            _figure(
                figure_id,
                (MARGIN["left"] + CONTENT_W - fig_w, content_top, fig_w, content_h),
                caption=figure_caption,
                kind=figure_kind,
            )
        )

    if matrix_blocks:
        elements.extend(_columns(matrix_blocks[0]["columns"], body_box, size=body_size))
    elif figure_blocks:
        elements.extend(_figures(figure_blocks, body_box))
    elif step_blocks:
        elements.extend(
            _stack(
                step_blocks[0].get("items", []),
                body_box,
                size=body_size,
                gap=gap,
                numbered=True,
            )
        )
    elif plain:
        texts: list[str] = []
        for block in plain:
            if block.get("type") == "def" and block.get("heading"):
                texts.append(f"{block['heading']}：{block.get('text', '')}")
            else:
                texts.append(str(block.get("text", "")))
        elements.extend(_stack(texts, body_box, size=body_size, gap=gap))
    elif bullets:
        elements.extend(_stack(bullets, body_box, size=body_size, gap=gap))

    if archetype == "summary":
        # 收束页压一条顶线，与内页区分但不换纸。
        elements.append(_rule((0.0, 0.0, CANVAS_W, 0.5), weight=0.5))

    # 正文页只给内花：角落里放不下界栏，放下去就是一道没画完的短线（与角饰母题的
    # `frame=0` 同一个理由）。
    elements.extend(_corners(ornament_motif, 9.5))
    return Scene(
        note=f"兜底 {archetype}：统一头 + 内容栈",
        background=background,
        elements=elements,
    )
