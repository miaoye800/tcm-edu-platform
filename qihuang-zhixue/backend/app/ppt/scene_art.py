"""版面设计：**让模型自己把这页排出来**。

`compose` 产出的是「这页说什么」，`scene_fallback` 给的是「安全版式」。这一层夹在
中间：拿逐页内容 + 素材 + 装帧参数，请模型写一份 ``Scene``——位置、大小、字号、
竖排、角花、印章全由它定。它写不出来或写坏了，就退回兜底，**不修、不猜**。

## 三条边界

1. **一页一次调用**。一页写坏只影响那一页，并记下原因（``scene_art`` 会落进稿件）。
2. **校验不过就退回**。半坏的场景画到纸上比没画更糟——用户看不出哪句是对的。
3. **标题是硬条件**。一页没有标题就不成立。模型常把标题漏在正文里，所以这里
   检查"标题原文是否出现在场景文字中"，不通过就整页退回。

## 与 `archetype` 的关系

`archetype` 只在提示词里作**语义提示**（「这页是对照，两栏更自然」），不参与几何。
模型想给对照页排成上下两段也是它的自由——校验只检查"对照页必须有两组内容"。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence
from typing import Any

from app.agents.structured_output import structured_create
from app.llm.router import ModelRouter

from .scene import CANVAS_H, TEXT_ROLE_SPEC, Scene, empty_scene, validate_scene
from .scene_fallback import ornament_motif_for, paper_motif_for

logger = logging.getLogger(__name__)

#: 一次设计最多几页。与 ``DeckContent.slides`` 的上限一致，超过只排前面的。
MAX_PAGES = 16
#: 并发几页。三路够快，又不会把网关的限流打满。
CONCURRENCY = 3
#: 单页元素下限。少于此数基本是空页（纸 + 标题），不如退回兜底。
MIN_ELEMENTS = 4
#: 提示词里给的母题样例。只作示例，真正的清单在 payload 里。
CORNERS = ("corner-scroll", "corner-branch", "corner-bamboo", "corner-fret", "corner-vine")
#: 竖排文字的最小字号（cqw）。再小就不是标题而是注脚了，宁可让它改用横排。
MIN_VERTICAL_SIZE = 3.2

SYSTEM_PROMPT = """\
你是这份中医药讲稿的**版面设计师**。用户已经确认过一份视觉方案（brief.look），
你的任务不是另起一套风格，而是**把那份方案在这一页上执行到位**：
每样东西放哪里、多大、什么字号由你定，但气质、配色、背景、角饰、图在左还是右
必须服从 look。

## 画布
100 × 56.25（16:9），单位 cqw，原点左上角，y 向下。box 是 [x, y, w, h]。
版心：左右各 9，天头 8.4，地脚 7.2。内容放版心内；装饰可以出血（允许负坐标，最远 -25）。

## 九种元素（按 type 取用，其余字段省略）
```jsonc
{"type": "text",   "box": [9, 8.4, 50, 8],  "text": {"content": "人参", "role": "title", "size": 4.6, "write": "h", "align": "left", "color": "fg"}}
{"type": "asset",  "box": [50, 8, 41, 41],  "asset": "plate-445da94b", "fit": "cover", "caption": "《本草品汇精要》"}
{"type": "motif",  "box": [0, 0, 13, 13],   "motif": "corner-scroll", "flip": "xy"}
{"type": "rect",   "box": [9, 30, 12, 1.2], "fill": "accent"}
{"type": "rule",   "box": [9, 8.4, 82, 0],  "orient": "h", "weight": 0.1, "fill": "line"}
{"type": "circle", "box": [6, 12, 1.2, 1.2],"fill": "accent"}
{"type": "seal",   "box": [80, 44, 5, 5],   "content": "岐黄", "shape": "square"}
{"type": "group",  "box": [9, 12, 36, 20],  "items": [子元素坐标 0..1 相对本组]}
```
- 文字角色与默认字号：title 4.6 / subtitle 2.2 / body 2.2 / caption 1.3 / note 1.3 /
  kicker 1.3 / numeral 7.6。可用 `size` 覆盖（0.8–20）。**一页不超过三档字号**。
- 颜色只写角色名：`fg` / `muted` / `accent` / `line` / `bg`。颜色字段叫 `fill`（块与线）
  或 `stroke`（描边），文字颜色在 `text.color`。**不许写 hex**——hex 在 look.palette 里，
  渲染层已经替你落到角色名上了。
- 线的粗细写 `weight`（cqw）；横线的 h 给 0 或 0.2 都行，代码会补。
- 竖排标题用 `"write": "v"`，中文版面里很有效，但**不是每页都要**：它适合 ≤6 字的
  封面/章节标题，正文页的标题用横排更好读。竖排框窄而高：`w ≈ 字号 × 1.6`，
  `h ≈ 字数 × 字号 × 行距`，占住的那一列必须留空。

## 这一页怎么排：先看 brief.composition
- `text-only`：文字占满版心，不要图。靠字号对比、短横线、留白做层次。
- `text-image-right` / `text-image-left`：图占一侧约 34–42 宽，另一侧放标题与正文。
  图在哪一侧由 composition 说了算，**不要每页都放同一边**。
- `full-bleed`：背景用整张图（background 里给的那张），文字压在留白处，
  必须有一条 veil 或半透明 rect 保证可读。
- `centered`：少而居中，常用于章节、金句、收束页；可以只有一个标题 + 一条短横。
- `split`：上下或左右两段各自成组，不要混排成一大块。
- `auto`：按这一页的内容自己判断，但**同一份稿子里不要连续三页用同一种骨架**。

## 视觉方案（brief.look）怎么执行
- `look.mood` 是气质，不是形容词：它决定用多少留白、线条粗细、装饰多少。
- `look.ornament.motif == "none"` 或 `corners` 为空时：**一个角花都不要画**。
  有值时只画在 look.ornament.corners 列出的角上（tl/tr/bl/br），
  用同一份 motif 镜像；**四角全挂只允许在封面**。
- `look.background.kind == "asset"` 时：背景用 brief.background_asset 那张图
  （`{"kind":"asset","asset":"<id>","fit":"cover","veil":{...}}`），不要再铺纸纹。
- 素材清单（brief.assets）里的 id 是**这一页真实可用的图**，用哪张、放多大由你定；
  清单外一律不许写。没有合适的图时，宁可不放——空白比错图好。

## 硬规矩（会校验，不过就退回安全版式）
1. **标题必须在场景里**：把标题原文放进 role=title 的 text（可以拆成两个 title 元素，
   但一个字不能少、不能改写）。
2. **正文逐字照抄**：brief 的 `bullets`、`blocks[].items`、`blocks[].text` 是这一页
   要讲的话，写进 text.content 时必须一字不改——可以断行、可以分块，但不能缩写、
   不能补一个冒号就了事、不能把后半句省掉（「先净制：」这种半句标签是坏稿）。
   框放不下就把框加高、字号降一档，**不许删字**。
3. **素材与母题只能用 brief 里给出的 id**。写了清单外的 id 就是断图。
4. **元素总数 6–14**。少于 6 多半是下半页整片空着。
5. **不要画页码**，渲染层会盖。
6. 不要写 CSS、不要写百分比。版面挤时先减装饰（短横、印章、多余 rect），
   再把正文字号降一档；**正文句子不许为了排版删掉**。
"""


def _normalise(text: str) -> str:
    """去掉空白与标点，只留字。比较标题用。"""

    return re.sub(r"[\s\W_]+", "", text or "")


def _carries_title(scene: Scene, title: str) -> bool:
    """标题原文是否出现在场景的文字里。

    允许拆成多个 text（竖排标题常常两段），所以先看单块、再看 ``title`` 角色的
    拼接；不允许改写——比的是子串而不是相似度，模型写错一个字，读者看到的就是错标题。
    """

    want = _normalise(title)
    if not want:
        return True
    heading: list[str] = []
    for element in scene.elements:
        if element.type != "text" or element.text is None:
            continue
        text = _normalise(element.text.content)
        if want in text:
            return True
        if element.text.role in ("title", "numeral"):
            heading.append(text)
    return want in "".join(heading)


def _allowed_assets(assets: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("id") or "") for item in assets if item.get("id")}


def _unknown_assets(scene: Scene, allowed: set[str]) -> list[str]:
    return [element.asset for element in scene.elements if element.type == "asset" and element.asset not in allowed]


def _fit_vertical_text(scene: Scene) -> list[str]:
    """竖排文字的框**机械修正**成"一列"。

    竖排的框语义与横排相反：宽是列宽（≈字号），高是字数 × 字号 × 行距。模型常按
    横排的习惯给 ``w=20, h=4.6``——那是四行横字的形状，竖排会变成一条压在正文上的
    长列。这不是设计判断，是坐标系约定，所以这里直接改正，并把改动记下来。

    放不下就缩字号（下限 1.3cqw），不让文字溢出到纸外——溢出比字小难看得多。
    """

    notes: list[str] = []
    for index, element in enumerate(scene.elements):
        text = element.text
        if element.type != "text" or text is None or text.write != "v" or not text.content:
            continue
        size = text.size or TEXT_ROLE_SPEC[text.role][0]
        chars = max(1, len(text.content))
        # 竖排的列距不需要横排那么松：1.5 是「行与行」，竖排里那一行是字距。收到 1.15
        # 之后，八个字的封面主标仍能按 4.6 号排进版心，而不是被硬缩成 3.4 号。
        line_height = min(text.line_height, 1.15)
        text.line_height = line_height
        needed = chars * size * line_height
        x, y, _w, _h = element.box
        room = CANVAS_H - 7.2 - y
        if needed > room > 0:
            fitted = room / (chars * line_height)
            if fitted < MIN_VERTICAL_SIZE:
                # 缩到这个字号就不是标题了。不缩，交给 ``_inspect`` 判「这行字放不下」，
                # 让模型自己改成横排或拆两段——那才是它该做的决定。
                notes.append(f"element[{index}] vertical text needs {needed:.0f} > {room:.0f}")
                continue
            size = round(fitted, 2)
            text.size = size
            needed = chars * size * line_height
            notes.append(f"element[{index}] vertical text shrunk to {size}")
        element.box = (x, y, round(max(size * 1.6, 2.0), 2), round(needed, 2))
    return notes


def _page_brief(
    slide: Any,
    *,
    index: int,
    total: int,
    deck_title: str,
    design: Any = None,
    design_page: Any = None,
) -> dict[str, Any]:
    """给模型的一页说明。视觉方案是**约束**，不是建议——所以整块 look 都给出去。"""

    assets = []
    background_asset = ""
    ornament_asset = ""
    for ref in getattr(slide, "asset_refs", []) or []:
        item = ref.model_dump() if hasattr(ref, "model_dump") else dict(ref)
        if not item.get("asset_id") or item.get("result") == "none":
            continue
        assets.append(
            {
                "id": item["asset_id"],
                "caption": item.get("note") or "",
                "provenance": item.get("provenance") or "",
                "license": item.get("license") or "",
                "slot": item.get("slot") or "",
            }
        )
        if item.get("slot") == "ground" and item.get("result") in ("hit", "substitute"):
            background_asset = str(item["asset_id"])
        if item.get("slot") == "ornament" and item.get("result") in ("hit", "substitute"):
            ornament_asset = str(item["asset_id"])
    blocks = []
    for block in getattr(slide, "blocks", []) or []:
        item = block.model_dump() if hasattr(block, "model_dump") else dict(block)
        blocks.append(item)
    look = _look_payload(design)
    composition = str(getattr(design_page, "composition", "") or "auto")
    return {
        "page": f"{index + 1}/{total}",
        "deck_title": deck_title,
        "archetype_hint": getattr(slide, "purpose", ""),
        "composition": composition,
        "title": getattr(slide, "title", ""),
        "subtitle": getattr(slide, "subtitle", ""),
        "bullets": list(getattr(slide, "bullets", []) or []),
        "blocks": blocks,
        "assets": assets,
        "look": look,
        "background_asset": background_asset,
        "ornament_asset": ornament_asset,
        "paper_motif": look.get("paper_motif") or "paper-plain",
        "ornament_motif": look.get("ornament_motif") or "none",
        "canvas": {"w": 100, "h": 56.25, "margin": {"left": 9, "right": 9, "top": 8.4, "bottom": 7.2}},
    }


def _look_payload(design: Any) -> dict[str, Any]:
    """设计单上的视觉方案 → 提示词里的 look。没有设计单时给一份最保守的。"""

    if design is None:
        return {
            "mood": "",
            "palette": {},
            "background": {"kind": "motif"},
            "ornament_motif": "none",
            "ornament_corners": [],
            "type_pair": "song-kai",
            "image_treatment": "plate",
            "rhythm": "",
            "paper_motif": "paper-plain",
        }
    data = design.model_dump() if hasattr(design, "model_dump") else dict(design)
    ornament = data.get("ornament") or {}
    background = data.get("background") or {}
    return {
        "mood": data.get("mood") or "",
        "palette": data.get("palette") or {},
        "background": background,
        "ornament_motif": data.get("ornament_motif") or ornament.get("motif") or "none",
        "ornament_corners": list(ornament.get("corners") or []),
        "ornament_ask": ornament.get("ask") or "",
        "type_pair": data.get("type_pair") or "song-kai",
        "image_treatment": data.get("image_treatment") or "plate",
        "rhythm": data.get("rhythm") or "",
        "paper_motif": data.get("paper_motif") or "paper-plain",
    }


REPAIR_HINT = (
    "这一版有一个问题：{detail}。请只改出问题的地方，其余元素的原样保持不变"
    "（坐标、字号、颜色都别改），重新输出完整的一页 JSON。"
)


def _text_collisions(scene: Scene) -> list[str]:
    """两个文字框压在了一起。

    这是模型排版最常见的硬伤：竖排标题一路写下来，撞上手边那几行正文。文字压字
    比版式普通难看得多，所以它算「问题」，不算「风格」。
    """

    boxes: list[tuple[int, tuple[float, float, float, float]]] = []
    for index, element in enumerate(scene.elements):
        if element.type == "text" and element.text is not None and element.text.content.strip():
            boxes.append((index, element.box))
    problems: list[str] = []
    for left in range(len(boxes)):
        for right in range(left + 1, len(boxes)):
            i, a = boxes[left]
            j, b = boxes[right]
            overlap_x = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
            overlap_y = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
            smaller = min(a[2] * a[3], b[2] * b[3])
            if smaller <= 0:
                continue
            ratio = overlap_x * overlap_y / smaller
            # 细长条（竖排一列）压住横排文字的角落时，面积比不到 25% 却已经很难看，
            # 所以再加一条"两个方向都压进去了"的判据：过半字宽 × 过半字高即算撞。
            if ratio > 0.10 and overlap_x > 0.6 and overlap_y > 0.6:
                problems.append(f"元素{i}与元素{j}的文字框重叠了 {ratio:.0%}")
    return problems


def _vertical_overflow(scene: Scene) -> list[str]:
    """竖排放不下的那几行。放不下就该改横排，而不是缩成 2cqw 的小字。"""

    problems: list[str] = []
    for index, element in enumerate(scene.elements):
        text = element.text
        if element.type != "text" or text is None or text.write != "v" or not text.content:
            continue
        size = text.size or TEXT_ROLE_SPEC[text.role][0]
        needed = len(text.content) * size * text.line_height
        if element.box[3] < needed * 0.95:
            problems.append(f"元素{index}的竖排文字要 {needed:.0f} 高，框只有 {element.box[3]:.0f}")
    return problems


def _inspect(scene: Scene, brief: dict[str, Any]) -> tuple[str, str]:
    """一页的硬伤。返回 ``(原因码, 给人看的一句话)``，没有问题返回两个空串。"""

    if not _carries_title(scene, brief["title"]):
        return "title_missing", f"标题「{brief['title']}」没有出现在任何角色为 title 的文字里"
    unknown = _unknown_assets(scene, _allowed_assets(brief["assets"]))
    if unknown:
        return f"asset_unknown: {','.join(unknown[:2])}", f"素材 {', '.join(unknown[:2])} 不在给你的清单里"
    # 角花是方案里的可选项：「不要角花」的稿子画出四角花，等于没执行方案。
    allowed_motifs = {brief.get("paper_motif") or "", brief.get("ornament_motif") or ""} - {"", "none"}
    stray = [element.motif for element in scene.elements if element.type == "motif" and element.motif not in allowed_motifs]
    if stray:
        return (
            f"motif_not_allowed: {','.join(stray[:2])}",
            f"母题 {', '.join(stray[:2])} 不在这一页的视觉方案里"
            + ("（方案明确不要角花）" if (brief.get("ornament_motif") or "none") == "none" else ""),
        )
    if scene.element_count < MIN_ELEMENTS:
        return f"scene_sparse: {scene.element_count}", "元素太少，这一页几乎是空白的"
    overflow = _vertical_overflow(scene)
    if overflow:
        return "vertical_overflow", f"{overflow[0]}；竖排放不下就改用横排，或拆成主标 + 副标"
    collisions = _text_collisions(scene)
    if collisions:
        return "text_collision", (
            f"{'；'.join(collisions[:3])}。把撞上的元素挪开——竖排标题占住的那一列"
            "（宽度约字号 × 1.6）要留空，横排文字放到它右边"
        )
    return "", ""


async def _paint_one(
    slide: Any,
    *,
    index: int,
    brief: dict[str, Any],
    settings: Any,
    task_type: str,
    semaphore: asyncio.Semaphore,
) -> tuple[int, Scene | None, str, list[str]]:
    """排一页。失败返回 ``(index, None, reason, warnings)``，调用方保留兜底场景。

    有问题只回灌一次：把违反的那一条告诉模型，让它自己改。再不过就退回兜底——
    一页排两遍已经是上限，再多就是在替它做设计。
    """

    fallback = getattr(slide, "scene", None) or empty_scene()
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(brief, ensure_ascii=False)},
    ]
    warnings: list[str] = []
    async with semaphore:
        for attempt in range(2):
            try:
                scene = await structured_create(
                    task_type=task_type,
                    messages=messages,
                    response_model=Scene,
                    mock_factory=lambda: fallback,
                    router=ModelRouter(settings=settings),
                )
            except Exception as error:  # noqa: BLE001 - 模型产出，任何错都退回兜底
                logger.info("scene art failed on page %s: %s", index + 1, error)
                return index, None, f"scene_error: {str(error).splitlines()[0][:120]}", warnings

            scene, warnings = validate_scene(scene)
            if scene is None:
                return index, None, warnings[0] if warnings else "scene_invalid", warnings
            # 竖排框修正后一律重验：修正改了框与行距，第一次那份警告说的是修正前的
            # 尺寸，留着只会误导（比如报"要 34.5 高"，而现在的框已经够了）。
            fits = _fit_vertical_text(scene)
            scene, after = validate_scene(scene)
            if scene is None:
                return index, None, after[0] if after else "scene_invalid", after
            warnings = list(dict.fromkeys(after)) + fits

            reason, detail = _inspect(scene, brief)
            # 文字装不下也要回灌一次：模型给的框只有一行高、正文要两行时，
            # 用户看到的是半句话被切断（「道地药材：特定产区出产、品质与疗效公认更优」
            # 后面没了）。这属于版面问题，让模型自己把框加高/加宽，比渲染层
            # 死缩字号好看。第二轮仍装不下就不再折腾——版面留着，前端框内缩字。
            overflow = [item for item in warnings if item.startswith("text_overflow")]
            if not reason and not overflow:
                return index, scene, "", warnings
            if attempt == 1:
                if not reason:
                    return index, scene, "", warnings
                return index, None, reason, warnings
            problem = reason or "text_overflow"
            if not reason:
                detail = "；".join(overflow[:3]) + "。把这些框按 brief 的字数加高或加宽，必要时收紧正文；不要靠缩小字号硬塞。"
            logger.info("scene art page %s: %s, asking for a fix", index + 1, problem)
            messages = [
                *messages,
                {"role": "assistant", "content": json.dumps(scene.model_dump(), ensure_ascii=False)},
                {"role": "user", "content": REPAIR_HINT.format(detail=detail)},
            ]

    return index, None, "scene_unreachable", warnings


async def paint_scenes(
    deck: Any,
    *,
    settings: Any,
    design: Any = None,
    deck_title: str = "",
    task_type: str = "generate",
    only: Sequence[int] | None = None,
) -> dict[str, Any]:
    """逐页请模型排版。返回一份记账，写进稿件 ``scene_stats``。

    没有 key 时直接返回 ``{"skipped": "no_key"}``——不是错误，是离线链路。

    ``only`` 只排指定的页（0 起算）。单页重写后重排被改过的那几页就够了：
    没改的页连场景都不碰，它们的版面是用户已经看过、也已经认可的那一版。
    """

    slides = list(getattr(deck, "slides", []) or [])
    if not slides:
        return {"skipped": "no_slides"}
    if not (getattr(settings, "deepseek_api_key", None) or getattr(settings, "zhipu_api_key", None)):
        return {"skipped": "no_key", "pages": len(slides)}

    semaphore = asyncio.Semaphore(CONCURRENCY)
    total = len(slides)
    wanted = set(only) if only is not None else None
    targets = [index for index in range(min(total, MAX_PAGES)) if wanted is None or index in wanted]
    if not targets:
        return {"pages": total, "painted": 0, "fallback": [], "warnings": [], "model": "scene-art"}

    design_pages = list(getattr(design, "pages", []) or []) if design is not None else []
    briefs = {
        index: _page_brief(
            slides[index],
            index=index,
            total=total,
            deck_title=deck_title,
            design=design,
            design_page=design_pages[index] if index < len(design_pages) else None,
        )
        for index in targets
    }
    results = await asyncio.gather(
        *(
            _paint_one(
                slides[index],
                index=index,
                brief=briefs[index],
                settings=settings,
                task_type=task_type,
                semaphore=semaphore,
            )
            for index in targets
        )
    )

    painted = 0
    fallback: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, scene, reason, warns in results:
        warnings.extend(f"page{index + 1}: {item}" for item in warns)
        if scene is None:
            fallback.append({"page": index + 1, "reason": reason})
            continue
        slides[index].scene = scene
        painted += 1

    return {
        "pages": total,
        "painted": painted,
        "fallback": fallback,
        "warnings": warnings[:24],
        "model": "scene-art",
    }
