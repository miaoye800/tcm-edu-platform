"""校验之后的单页重写。

``_deck_verify`` 的立场是「只报问题，不改内容」，这条不能破：校验完偷偷重写，
用户签过字的方案就成了一张废纸，成品也没法再解释「为什么这页是这样」。

但「只报不改」不等于「看着错误交付」。校验报出来的问题里，有三类不是整份稿子
错了，而是**这一页没写对**：

- **容量超限**：这页塞了七块，版心装不下，导出会压到地脚。
- **页面没有内容**：这页只剩标题。
- **引用越界**：页脚标的依据是第 12 条，而这次只取了 5 条材料。

整份重跑一次要几十秒，而且四页内容全变——用户刚看完的方案等于重来。所以这里
只重写有问题的页（一次最多三页），其余原样不动；重写完只重排这几页的版面，
别的页连场景都不碰。

不归这里管的两类，各有各的去处：

- **资产问题**（许可、署名、生成图进证据位）：素材是 ``select`` 步定下的，
  改一页正文改不了它。硬改只会把「为什么用了这张图」的理由抹掉。
- **不在确认方案内**：标题是用户定的，重写正文改不了标题；真跑偏了要重排方案，
  不是重写一页。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.structured_output import structured_create
from app.llm.router import ModelRouter

from .design import DENSITY_CAPS, PageDesign, check_page_capacity
from .scene_art import paint_scenes
from .schemas import MAX_BLOCKS, DeckContent, SlideBlock

logger = logging.getLogger(__name__)

#: 一次最多重写几页。三页是成本上限，也是「还看得出是同一份稿子」的上限：
#: 半数以上的页都重写了，那本来就是该整份重跑。
MAX_REVISE = 3

#: 能靠重写正文解决的说法。字符串比对的是校验器自己吐的话，两边必须同步改。
FIXABLE_PREFIXES = (
    "内容块超限",
    "超出 ",  # 「超出 normal 密度上限」
    "版式 ",  # 「版式 points 不接受 ['steps']」
    "单块字数超限",
    "页面没有内容",
    "引用越界",
)

SYSTEM_PROMPT = """你在修一份已经成稿的中文幻灯里的**一页**。

给你的这一页有两个毛病，不是猜的，是校验器逐条算出来的。请只改这一页的正文，
把毛病去掉。

硬性要求：
1. **标题一个字都不许改**。标题来自用户确认过的方案，改标题等于换了议题。
2. 内容块只能从 `capacity.block_types` 里选；块数不超过 `capacity.max_blocks`
   （同时不超过密度上限 `capacity.density_cap`）。
3. 单块 `text` 不超过 `capacity.item_chars` 字；`point` / `alert` 可以到两倍。
   宁可写短句，不要为凑字数把一句话拆成两块。
4. `source_indexes` 只能从 `sources` 里给的下标中选；没有依据就留空，不许编。
5. 原来的信息不要丢：修的是**装不下**和**引用越界**，不是把内容删空。
   删到只剩一句「本法需辨证使用」是最常见的错误答案。

只输出 JSON：{"blocks": [...], "bullets": [...], "note": "", "source_indexes": [0]}。
`blocks` 用原来的块结构（type / text / heading / items / columns / value），
`bullets` 与 `blocks` 二选一即可，有 `blocks` 时可以留空。
"""


class PagePatch(BaseModel):
    """模型交回来的一页正文。标题不含在内——它不在可改范围里。"""

    model_config = ConfigDict(extra="ignore")

    blocks: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_BLOCKS)
    bullets: list[str] = Field(default_factory=list, max_length=6)
    note: str = ""
    source_indexes: list[int] = Field(default_factory=list, max_length=6)


def fixable(issues: list[str]) -> list[str]:
    """一页的问题里，哪些能靠重写正文解决。"""

    return [item for item in issues if str(item).startswith(FIXABLE_PREFIXES)]


def _design_page(design: Any, index: int) -> PageDesign | None:
    if not isinstance(design, dict):
        return None
    pages = design.get("pages") or []
    if index >= len(pages) or not isinstance(pages[index], dict):
        return None
    try:
        return PageDesign.model_validate(pages[index])
    except Exception:  # noqa: BLE001 - 一条坏页不该让重写整个失效
        return None


def _capacity_of(page: PageDesign | None, fallback_purpose: str) -> dict[str, Any]:
    spec = page.spec if page is not None else None
    density = page.density if page is not None else "normal"
    return {
        "block_types": list(spec.block_types) if spec else ["point", "steps", "figures", "quote", "def", "alert", "matrix"],
        "max_blocks": spec.max_blocks if spec else MAX_BLOCKS,
        "item_chars": spec.item_chars if spec else 60,
        "density": density,
        "density_cap": DENSITY_CAPS.get(density, 5),
        "archetype": page.archetype if page is not None else (fallback_purpose or "points"),
    }


def _clean_blocks(raw: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """逐块过一遍模型，坏块丢掉并计数。

    块结构一条写错（比如 ``columns`` 给了字符串）会让整页在渲染层炸掉；
    而重写是「救这一页」，不是「让这一页把整份稿子拖下水」。
    """

    kept: list[dict[str, Any]] = []
    dropped = 0
    for item in raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        try:
            kept.append(SlideBlock.model_validate(item).model_dump(mode="json"))
        except Exception:  # noqa: BLE001 - 模型产出，坏块丢
            dropped += 1
    return kept, dropped


def _payload(
    page: dict[str, Any],
    *,
    index: int,
    issues: list[str],
    design_page: PageDesign | None,
    sources: list[dict[str, str]],
) -> dict[str, Any]:
    capacity = _capacity_of(design_page, str(page.get("purpose") or "points"))
    return {
        "page": index + 1,
        "title": str(page.get("title") or ""),
        "kind": str(page.get("kind") or "content"),
        "purpose": str(page.get("purpose") or ""),
        "capacity": capacity,
        "current": {
            "blocks": page.get("blocks") or [],
            "bullets": page.get("bullets") or [],
        },
        "problems": issues,
        # 只给下标与名目：正文由模型自己回头看 current，不必再贴一遍材料。
        "sources": [
            {"index": i, "name": str(item.get("name") or ""), "ref": str(item.get("ref") or "")}
            for i, item in enumerate(sources)
        ],
        "design_note": design_page.note if design_page is not None else "",
    }


def _apply(page: dict[str, Any], patch: PagePatch, source_count: int) -> list[str]:
    """把补丁写进页里，返回这次改动留下的说明。"""

    notes: list[str] = []
    blocks, dropped = _clean_blocks(patch.blocks)
    if dropped:
        notes.append(f"丢弃 {dropped} 个结构不合法的块")
    if blocks:
        page["blocks"] = blocks
    bullets = [text.strip() for text in patch.bullets if text.strip()][:6]
    if not blocks and bullets:
        # 有块就以块为准：两套同时写会让渲染层二选一，页面上出现哪套看谁先到。
        page["bullets"] = bullets
    if patch.note.strip():
        page["note"] = patch.note.strip()[:200]
    page["source_indexes"] = [value for value in patch.source_indexes if 0 <= value < source_count][:6]
    return notes


def _still_broken(page: dict[str, Any], design_page: PageDesign | None, source_count: int) -> list[str]:
    """改完之后还犯不犯规。用校验器自己的判据，不另立一套。"""

    problems: list[str] = []
    blocks = [block for block in (page.get("blocks") or []) if isinstance(block, dict)]
    if design_page is not None:
        problems.extend(check_page_capacity(design_page, blocks))
    if not blocks and not page.get("bullets") and page.get("kind") not in ("cover", "section"):
        problems.append("页面没有内容")
    indexes = [int(value) for value in (page.get("source_indexes") or []) if isinstance(value, int)]
    if any(value < 0 or value >= source_count for value in indexes):
        problems.append("引用越界")
    return problems


async def _rewrite_one(
    page: dict[str, Any],
    *,
    index: int,
    issues: list[str],
    design_page: PageDesign | None,
    sources: list[dict[str, str]],
    settings: Any,
    task_type: str,
) -> tuple[dict[str, Any] | None, str, list[str]]:
    """重写一页。返回 ``(补丁, 失败原因, 说明)``，失败时补丁为 None。"""

    payload = _payload(page, index=index, issues=issues, design_page=design_page, sources=sources)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _json(payload)},
    ]
    try:
        patch = await structured_create(
            task_type=task_type,
            messages=messages,
            response_model=PagePatch,
            mock_factory=lambda: PagePatch(),
            router=ModelRouter(settings=settings),
        )
    except Exception as error:  # noqa: BLE001 - 模型产出，任何错都退回原文
        logger.info("page %s revise failed: %s", index + 1, error)
        return None, f"model_error: {str(error).splitlines()[0][:120]}", []

    if not patch.blocks and not patch.bullets:
        return None, "model_empty", []

    probe = {**page}
    notes = _apply(probe, patch, len(sources))
    remaining = _still_broken(probe, design_page, len(sources))
    if not remaining:
        page.update(probe)
        return patch, "", notes

    # 一次回灌：把「还差什么」原话告诉它。重写是定点修，两次不行就该让人看，
    # 而不是无限重试——那种稿子会越改越薄。
    retry = _json({**payload, "problems": remaining, "previous": {"blocks": probe.get("blocks"), "bullets": probe.get("bullets")}})
    try:
        patch = await structured_create(
            task_type=task_type,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": retry},
            ],
            response_model=PagePatch,
            mock_factory=lambda: PagePatch(),
            router=ModelRouter(settings=settings),
        )
    except Exception as error:  # noqa: BLE001
        return None, f"model_error: {str(error).splitlines()[0][:120]}", notes

    probe = {**page}
    notes += _apply(probe, patch, len(sources))
    remaining = _still_broken(probe, design_page, len(sources))
    if remaining:
        return None, f"still_broken: {'；'.join(remaining[:2])}", notes
    page.update(probe)
    return patch, "", notes


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


async def revise_pages(
    content: dict[str, Any],
    pages: list[dict[str, Any]],
    *,
    settings: Any,
    design: Any = None,
    task_type: str = "generate",
    limit: int = MAX_REVISE,
) -> dict[str, Any]:
    """按校验结果重写有问题的页，并只重排这几页的版面。

    ``content`` 会被就地修改（这是成品本身，不是副本）；返回一份记账，写进
    ``meta.revise``，让「这一页为什么和模型第一次写的不一样」有据可查。
    """

    slides = content.get("slides") or []
    sources = content.get("sources") or []
    if not slides:
        return {"revised": [], "skipped": []}
    if not (getattr(settings, "deepseek_api_key", None) or getattr(settings, "zhipu_api_key", None)):
        return {"revised": [], "skipped": [{"reason": "no_key"}]}

    targets: list[tuple[int, list[str]]] = []
    skipped: list[dict[str, Any]] = []
    for item in pages:
        index = int(item.get("index") or 0)
        issues = fixable(list(item.get("issues") or []))
        if not issues:
            continue
        if not 0 <= index < len(slides):
            continue
        if len(targets) >= limit:
            skipped.append({"page": index + 1, "reason": "over_budget", "issues": issues})
            continue
        targets.append((index, issues))
    if not targets:
        return {"revised": [], "skipped": skipped}

    revised: list[dict[str, Any]] = []
    for index, issues in targets:
        page = slides[index]
        if not isinstance(page, dict):
            continue
        _patch, reason, notes = await _rewrite_one(
            page,
            index=index,
            issues=issues,
            design_page=_design_page(design, index),
            sources=sources,
            settings=settings,
            task_type=task_type,
        )
        if _patch is None:
            skipped.append({"page": index + 1, "reason": reason, "issues": issues})
            continue
        # 内容变了，旧版面就作废。不置空的话会看到「新正文 + 按旧正文排的框」，
        # 那是比不改更糟的一种结果。
        page["scene"] = None
        revised.append({"page": index + 1, "issues": issues, "notes": notes})

    if revised:
        await _repaint(content, [item["page"] - 1 for item in revised], settings=settings, design=design, task_type=task_type)

    return {"revised": revised, "skipped": skipped}


async def _repaint(
    content: dict[str, Any],
    indexes: list[int],
    *,
    settings: Any,
    design: Any,
    task_type: str,
) -> None:
    """只重排被改过的页。构造不出一份合法稿子就放弃——退回兜底场景仍能看。"""

    try:
        deck = DeckContent.model_validate(content)
    except Exception as error:  # noqa: BLE001 - 重写过的页理应合法，但别赌
        logger.info("repaint skipped, deck invalid after revise: %s", error)
        return

    # 母版命中的稿子重排也走母版（确定性、零调用）；未命中才请模型。
    from .masters import by_id as master_by_id
    from .masters.painter import master_id_of, paint_master_scenes

    stats: dict[str, Any] = {}
    master = master_by_id(master_id_of(design))
    if master is not None:
        stats = paint_master_scenes(deck, design=design, only=indexes)
    # 没命中母版（老稿子/非母版路径）或母版 painter 明确跳过，都得请模型重排；
    # 只看 stats["skipped"] 会把「本来就没母版」的稿子整份跳过重排。
    if master is None or stats.get("skipped"):
        stats = await paint_scenes(
            deck,
            settings=settings,
            design=design,
            task_type=task_type,
            only=indexes,
        )
    slides = content.get("slides") or []
    for index in indexes:
        if index < len(slides) and deck.slides[index].scene is not None:
            slides[index]["scene"] = deck.slides[index].scene.model_dump(mode="json")
    if not stats.get("painted"):
        return
    # 记账要**累加**不是覆盖：重排只排了两页，不能因此把「整份稿子排了几页」
    # 改写成 2。落库后 meta.scene_art 是回答「这页为什么长这样」的证据。
    merged = dict(content.get("scene_stats") or {})
    merged["pages"] = int(stats.get("pages") or merged.get("pages") or 0)
    merged["painted"] = int(merged.get("painted") or 0) + int(stats.get("painted") or 0)
    merged["fallback"] = list(merged.get("fallback") or []) + list(stats.get("fallback") or [])
    merged["warnings"] = list(merged.get("warnings") or []) + list(stats.get("warnings") or [])
    merged["model"] = stats.get("model") or merged.get("model") or "scene-art"
    merged["revised_pages"] = sorted({*(merged.get("revised_pages") or []), *(index + 1 for index in indexes)})
    content["scene_stats"] = merged
