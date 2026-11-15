"""模型直出的收稿草案 → 可校验的严格成品。

与 ``app/mindmap/draft.py`` 同一套「宽进严出」：

1. 草案对长度几乎不设防（标题 120 字、要点不限条），因为一 raise 整份幻灯就
   退化成模板；
2. ``normalize_deck`` 负责截断、去重、剪枝、补封面、重排 id，最后才构造
   ``SlideNode``——保证构造阶段不会失败；
3. **``kind`` 由后端按字段重算**，不采信模型自报的版式。模型经常把只有两行字的
   页标成 compare，渲染层就会画出两个空栏。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .design import ARCHETYPES, KIND_TO_PURPOSE, PURPOSE_TO_KIND, purpose_for_blocks
from .scene_fallback import fallback_scene, ornament_motif_for, paper_motif_for
from .schemas import (
    MAX_NOTE,
    MAX_SUBTITLE,
    MAX_TITLE,
    AssetRef,
    DeckContent,
    OutlineSlide,
    SlideBlock,
    SlideColumn,
    SlideNode,
)

#: 生成期上限：比存盘校验更紧，留出手工编辑的余量。
MAX_SLIDES = 16
#: 用户确认过方案的路径用这个上限：页数由方案定，不该被生成前的建议页数截断。
#: 与 ``DeckContent.slides.max_length``、前端 ``SLIDE_LIMITS.slides`` 同一数字。
MAX_PLAN_SLIDES = 20
MAX_BULLETS = 5
MAX_COLUMNS = 2
MAX_COLUMN_ITEMS = 5
MAX_SOURCE_INDEXES = 3
#: 单条要点与栏内条目的生成期字数。40 / 30 是 16:9 舞台「5 条不溢出」的实测上限，
#: 存盘校验放宽到 60 是为了让用户手改时可以多写几个字。
GEN_BULLET = 40
GEN_COLUMN_ITEM = 30

DEFAULT_PAGES = 12
MIN_PAGES = 6
MAX_PAGES = 16

_CUT_CHARS = "，。；、：（）,.;:()!?！？"
_PUNCT_ONLY = re.compile(r"[\W_]+", re.UNICODE)
_FALLBACK_TITLE = "未命名讲稿"
_COVER_SUBTITLE = "中医药学讲稿"
_AGENDA_HINTS = ("目录", "纲要", "导览", "路线", "全课地图", "本讲")
#: 模型爱在标题前自报版式，剥掉这些前缀。只收版式词，避免误伤「配伍：麻黄与桂枝」。
_LAYOUT_LABELS = ("封面", "标题页", "首页", "导览", "目录", "纲要", "议程", "小结", "总结", "封底", "结尾")


class DraftColumn(BaseModel):
    """模型给的一栏。``extra="ignore"``：模型常顺手塞 width / tone。"""

    model_config = ConfigDict(extra="ignore")

    heading: str = Field(default="", max_length=60)
    items: list[str] = Field(default_factory=list, max_length=10)


class DraftSlide(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str = Field(default="", max_length=24)
    title: str = Field(default="", max_length=120)
    subtitle: str = Field(default="", max_length=240)
    bullets: list[str] = Field(default_factory=list, max_length=12)
    columns: list[DraftColumn] = Field(default_factory=list, max_length=4)
    note: str = Field(default="", max_length=600)
    source_indexes: list[int] = Field(default_factory=list, max_length=10)


class DeckDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    deck_title: str = Field(default="", max_length=120)
    slides: list[DraftSlide] = Field(default_factory=list, max_length=24)


class OutlineDraftSlide(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(default="", max_length=120)
    points: list[str] = Field(default_factory=list, max_length=10)
    source_indexes: list[int] = Field(default_factory=list, max_length=10)


class OutlineDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    deck_title: str = Field(default="", max_length=120)
    slides: list[OutlineDraftSlide] = Field(default_factory=list, max_length=24)


def normalize_deck(
    draft: DeckDraft,
    *,
    topic: str,
    sources: list[dict[str, str]],
    params: dict[str, Any] | None = None,
    limit_override: int | None = None,
    design: Any = None,
    resolutions: list[Any] | None = None,
) -> DeckContent:
    """把草案收敛成一份可落库、可渲染、可导出的幻灯。

    ``limit_override`` 只给「用户确认过方案」的路径用：那时页数是方案定的，
    左栏那个「几页」是生成前的建议值，不该把方案最后一页截掉。

    ``design`` 是用户确认过的设计单：它决定每一页的版式（``purpose``），
    从而决定 ``blocks`` 的类型。**版式不跟页序走**——这是「第 8 页想放对照表」
    在数据层得以表达的关键。没有设计单（v1/v2 老路径）时退回按结构推断，
    行为与升级前逐字节一致。
    """

    if limit_override is None:
        limit = _int_param(params, "page_count", DEFAULT_PAGES, MIN_PAGES, MAX_PAGES)
    else:
        limit = min(max(int(limit_override), 1), MAX_PLAN_SLIDES)
    source_count = len(sources)
    pages = _design_pages(design)

    built: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for raw in draft.slides:
        if len(built) >= limit:
            break
        item = _collect(raw, source_count)
        if item is None:
            continue
        # 相邻同标题多半是模型复读，保留第一个即可。
        if item["title"] in seen_titles:
            continue
        seen_titles.add(item["title"])
        built.append(item)

    if not built:
        built = [_cover_item(_cover_title(topic, draft.deck_title), source_count)]
    elif built[0]["bullets"] or built[0]["columns"]:
        # 首屏是内容页时补一张封面。首屏只是标题页（模型常这么写）就不补，
        # 否则会得到「封面 + 标题页」两张几乎一样的页。
        built.insert(0, _cover_item(_cover_title(topic, draft.deck_title), source_count))
    built = built[:limit]

    total = len(built)
    slides: list[SlideNode] = []
    for index, item in enumerate(built):
        page = pages[index] if index < len(pages) else None
        if page:
            purpose = str(page.get("archetype") or "points")
            purpose = ARCHETYPES[purpose].purpose if purpose in ARCHETYPES else "points"
            kind = PURPOSE_TO_KIND.get(purpose, "bullets")
        else:
            kind = _resolve_kind(index, total, item)
            purpose = KIND_TO_PURPOSE.get(kind, "points")  # type: ignore[assignment]
        blocks = _blocks_for(purpose, item)
        if not page and blocks and kind in ("bullets", "compare"):
            # 无设计单时，内容页的版式以块结构为准：模型把两行字标成 compare 是常态。
            # 封面/章节/末页收束由位置定性，不参与重算——否则「最后一页」会被
            # 重算成普通要点页，收尾就没了。
            purpose = purpose_for_blocks([block.model_dump() for block in blocks])  # type: ignore[assignment]
            kind = PURPOSE_TO_KIND.get(purpose, kind)
        refs = _asset_refs(resolutions, page)
        # 图位取阶梯走得最远的那个：`hit` 优先于 `substitute`，`none` 不算有图。
        figure = next((ref for ref in refs if ref.result != "none" and ref.asset_id), None)
        # 密度：设计单给了就用它的；没有就按块数推——块数就是密度的结果。
        density = str(page.get("density") or "") if page else ""
        if density not in {"airy", "normal", "dense"}:
            density = "dense" if len(blocks) >= 5 else "airy" if len(blocks) <= 2 else "normal"
        slides.append(
            SlideNode(
                id=f"s{index}",
                kind=kind,  # type: ignore[arg-type]
                title=item["title"],
                subtitle=item["subtitle"],
                bullets=item["bullets"],
                columns=[SlideColumn(**column) for column in item["columns"]],
                note=item["note"],
                source_indexes=item["source_indexes"],
                purpose=purpose,  # type: ignore[arg-type]
                blocks=blocks,
                asset_refs=refs,
                # 场景由兜底生成器铺底：模型还没接手写场景（工具循环在 M3），
                # 但渲染路径必须先通——否则「模型自己排」这件事永远只是计划里的字。
                scene=fallback_scene(
                    archetype=purpose,
                    title=item["title"],
                    subtitle=item["subtitle"],
                    blocks=[block.model_dump() for block in blocks],
                    bullets=item["bullets"],
                    density=density,
                    figure_id=figure.asset_id if figure else "",
                    figure_caption=figure.note if figure else "",
                    # 阶梯第 1 级是参数化母题，它的 id 不能当 asset 用。
                    figure_kind="motif" if figure and figure.result == "parametric" else "asset",
                    page_no=index + 1,
                    total=total,
                    deck_title=getattr(draft, "deck_title", ""),
                    paper_motif=paper_motif_for(getattr(design, "paper", None)),
                    ornament_motif=ornament_motif_for(getattr(design, "ornament_set", None)),
                ),
            )
        )
    return DeckContent(theme_id=_theme_id(params, design), slides=slides, sources=sources)


#: 每页允许的内容块上限。与 ``design.MAX_BLOCKS`` 同口径，这里卡在生成端。
MAX_BLOCKS_PER_PAGE = 6


def _design_pages(design: Any) -> list[dict[str, Any]]:
    """设计单 → 逐页 dict。宽容接受模型 / dict / None。"""

    if design is None:
        return []
    pages = design.get("pages") if isinstance(design, dict) else getattr(design, "pages", None)
    result: list[dict[str, Any]] = []
    for page in pages or []:
        if isinstance(page, dict):
            result.append(page)
        else:
            result.append({"archetype": getattr(page, "archetype", "points"), "unit_id": getattr(page, "unit_id", "")})
    return result


def _blocks_for(purpose: str, item: dict[str, Any]) -> list[SlideBlock]:
    """按版式把扁平字段拆成内容块。

    这是「一页只能存一句话」的解法：同一批 bullets，在 ``points`` 下是五条要点，
    在 ``process`` 下是一条四步流程，在 ``warning`` 下是两条朱砂警示。内容没变，
    结构变了，版面才可能不一样。
    """

    bullets: list[str] = list(item["bullets"])
    columns: list[dict[str, Any]] = list(item["columns"])
    if purpose in ("cover", "section"):
        return []
    if purpose in ("compare", "matrix"):
        if len(columns) >= 2:
            return [SlideBlock(type="matrix", columns=[SlideColumn(**column) for column in columns[:3]])]
        # 只有要点没有栏时，按两栏对切：总比渲染出两个空栏强。
        if len(bullets) >= 2:
            half = (len(bullets) + 1) // 2
            return [
                SlideBlock(
                    type="matrix",
                    columns=[
                        SlideColumn(heading="其一", items=bullets[:half]),
                        SlideColumn(heading="其二", items=bullets[half:]),
                    ],
                )
            ]
        return [SlideBlock(type="point", text=bullets[0])] if bullets else []
    if purpose in ("agenda", "process"):
        if not bullets:
            return []
        return [SlideBlock(type="steps", items=bullets[:6])]
    if purpose == "data":
        return [_figure_block(text) for text in bullets[:3]]
    if purpose == "warning":
        return [SlideBlock(type="alert", text=text) for text in bullets[:2] or [item["title"]]]
    if purpose == "summary":
        blocks = [SlideBlock(type="point", text=text) for text in bullets[:3]]
        if item["subtitle"]:
            blocks.append(SlideBlock(type="def", text=item["subtitle"]))
        return blocks[:MAX_BLOCKS_PER_PAGE]
    return [SlideBlock(type="point", text=text) for text in bullets[:5]]


_NUMBER = re.compile(r"^([0-9][0-9.\-~～—%％]*\s*[a-zA-Z克gG毫克mlML升天日周年月]*)\s*[，,：:\s]*(.*)$")


def _figure_block(text: str) -> SlideBlock:
    """把「3~9g，常用剂量」拆成数值卡。拆不出数值时整句当标签，不编数字。"""

    match = _NUMBER.match(text.strip())
    if match and match.group(1).strip():
        return SlideBlock(type="figures", value=match.group(1).strip()[:12], heading=match.group(2).strip()[:24] or text[:24])
    return SlideBlock(type="figures", value="", heading=text[:24])


def _asset_refs(resolutions: list[Any] | None, page: dict[str, Any] | None) -> list[AssetRef]:
    """把选型结果挂到这一页。没有设计单时为空——旧稿子本来就没有图。"""

    if not resolutions or page is None:
        return []
    unit_id = str(page.get("unit_id") or "")
    refs: list[AssetRef] = []
    for item in resolutions:
        data = item if isinstance(item, dict) else item.model_dump()
        if unit_id and str(data.get("unit_id") or "") != unit_id:
            continue
        # 「降级」的槽位不进 asset_refs：它没有资产，硬塞一个空引用只会让渲染层
        # 画一个灰块。降级的信息在 verify 与 meta.slot_resolution 里，看得见。
        if str(data.get("result") or "") == "none":
            continue
        # 角标规则（assets.md §7.3）：平替标「示意」，生成图标「AI 生成示意」，
        # 参数化不标。写在数据里，渲染层不必自己推断该不该标。
        result = str(data.get("result") or "none")
        note = "AI 生成示意" if result == "generated" else ("示意" if result == "substitute" else "")
        refs.append(
            AssetRef(
                slot=str(data.get("slot") or "figure"),
                asset_id=str(data.get("asset_id") or ""),
                result=result,  # type: ignore[arg-type]
                provenance=str(data.get("provenance") or "none"),  # type: ignore[arg-type]
                license=str(data.get("license") or "")[:32],
                attribution=str(data.get("attribution") or "")[:120],
                file=str(data.get("file") or "")[:200],
                url=str(data.get("url") or "")[:400],
                thumb=str(data.get("thumb") or "")[:400],
                source=str(data.get("source") or "")[:40],
                pinned_at=str(data.get("pinned_at") or "")[:32],
                note=note,
            )
        )
    return refs[:3]


_ORDINAL_PREFIX = re.compile(r"^[\s　]*[一二三四五六七八九十百零〇\d]+[、.．)）:：]\s*")


def _outline_key(text: str) -> str:
    """标题归一：去章号、去标点空白、忽略大小写。

    方案里的「二、栽培演变」与模型写回来的「栽培演变」是同一页，
    按全等匹配会把它们当成两页，然后凭空补出一张空页。
    """

    cleaned = _ORDINAL_PREFIX.sub("", str(text or "").strip())
    return _PUNCT_ONLY.sub("", cleaned).lower()


def align_deck_to_outline(
    draft: DeckDraft,
    outline: list[OutlineSlide],
    fallback: DeckDraft | None = None,
) -> DeckDraft:
    """把草案按确认过的大纲对齐：补缺页、去掉大纲外的页、按大纲排页序。

    模型偶尔少写一页（尤其最后那页「回顾」）。少一页不是文案瑕疵，是
    「用户确认的东西没做到」——所以按方案补齐，而不是留给校验去报缺页。
    ``fallback`` 是同一份大纲铺出来的模板页，缺页时优先用它，至少有真实要点，
    比凭空造一页「待补充」强。
    """

    if not outline:
        return draft
    pool = list(draft.slides)
    keys = [_outline_key(slide.title) for slide in pool]
    used: set[int] = set()
    aligned: list[DraftSlide] = []
    for index, entry in enumerate(outline):
        key = _outline_key(entry.title)
        hit: int | None = None
        # 先全等，再退一步用包含：模型常给标题加「上/下」「其」这类前后缀。
        for position, candidate in enumerate(keys):
            if position not in used and candidate and candidate == key:
                hit = position
                break
        if hit is None:
            for position, candidate in enumerate(keys):
                if position in used or not candidate:
                    continue
                if candidate in key or key in candidate:
                    hit = position
                    break
        if hit is not None:
            used.add(hit)
            # 标题以方案为准：用户改过的字不能被模型写回来的标题盖掉。
            aligned.append(pool[hit].model_copy(update={"title": entry.title}))
            continue
        fill = None
        if fallback is not None:
            for candidate in fallback.slides:
                if _outline_key(candidate.title) == key:
                    fill = candidate.model_copy(deep=True)
                    break
        if fill is not None:
            aligned.append(fill.model_copy(update={"title": entry.title}))
            continue
        points = [str(point) for point in (entry.points or []) if str(point).strip()]
        aligned.append(
            DraftSlide(
                # 首页按约定就是封面；封面只放标题，要点搬上去会压住标题。
                kind="cover" if index == 0 else "",
                title=entry.title,
                bullets=[] if index == 0 else points[:MAX_BULLETS],
                source_indexes=[value for value in entry.source_indexes if value >= 0][:MAX_SOURCE_INDEXES],
            )
        )
    return DeckDraft(deck_title=draft.deck_title, slides=aligned)


def normalize_outline(
    draft: OutlineDraft,
    *,
    topic: str,
    sources: list[dict[str, str]],
    params: dict[str, Any] | None = None,
) -> list[OutlineSlide]:
    """大纲只需标题 + 要点骨架，因此比成品更宽松，但仍要保证「能往下生成」。"""

    limit = _int_param(params, "page_count", DEFAULT_PAGES, MIN_PAGES, MAX_PAGES)
    source_count = len(sources)
    slides: list[OutlineSlide] = []
    seen: set[str] = set()
    for raw in draft.slides:
        if len(slides) >= limit:
            break
        title = clean_text(_strip_label(raw.title), MAX_TITLE)
        if not title or title in seen:
            continue
        seen.add(title)
        # 大纲模型偶尔会照抄成品结构、把要点写在 bullets 里；两种都收。
        raw_points = getattr(raw, "points", None) or getattr(raw, "bullets", None) or []
        points = _clean_list(list(raw_points), GEN_BULLET, MAX_BULLETS)
        slides.append(
            OutlineSlide(
                id=f"s{len(slides)}",
                title=title,
                points=points,
                source_indexes=_source_indexes(raw.source_indexes, source_count),
            )
        )
    if not slides:
        slides = [OutlineSlide(id="s0", title=clean_text(topic, MAX_TITLE) or _FALLBACK_TITLE)]
    return slides


def _collect(raw: DraftSlide, source_count: int) -> dict[str, Any] | None:
    title = clean_text(_strip_label(raw.title), MAX_TITLE)
    subtitle = clean_text(raw.subtitle, MAX_SUBTITLE)
    bullets = _clean_list(raw.bullets, GEN_BULLET, MAX_BULLETS)
    columns = _clean_columns(raw.columns)
    if not title and not bullets and not columns:
        return None
    if not title:
        # 标题是版式的唯一锚点，缺了就只能拿第一条要点顶上，再不行丢弃。
        title = bullets[0] if bullets else ""
        if not title:
            return None
        bullets = bullets[1:]
    return {
        "kind": str(raw.kind or "").strip().lower(),
        "title": title,
        "subtitle": subtitle,
        "bullets": bullets,
        "columns": columns,
        "note": clean_text(raw.note, MAX_NOTE),
        "source_indexes": _source_indexes(raw.source_indexes, source_count),
    }


def _resolve_kind(index: int, total: int, item: dict[str, Any]) -> str:
    """版式判定顺序即优先级：先看位置，再看结构，最后看标题语义。"""

    bullets = item["bullets"]
    columns = item["columns"]
    if index == 0:
        return "cover"
    if len(columns) >= 2:
        return "compare"
    # 末页一律收束成总结版式：哪怕模型写成「课后延伸」，放映时它就是收尾页。
    if total >= 3 and index == total - 1:
        return "summary"
    if any(hint in item["title"] for hint in _AGENDA_HINTS) and len(bullets) >= 3:
        return "agenda"
    if not bullets and not columns:
        return "section"
    return "bullets"


def _cover_title(topic: str, deck_title: str) -> str:
    """封面上放整份讲稿的标题，而不是光秃秃的药名。

    ``deck_title`` 一般写成「麻黄：发汗解表，宣肺平喘」，比题目（常常只有两个字）
    更适合当封面主标题；太短（模型偷懒只回药名）时才退回题目。
    """

    title = clean_text(_strip_label(deck_title), MAX_TITLE)
    if len(title) >= 3:
        return title
    return clean_text(_strip_label(topic), MAX_TITLE) or title or _FALLBACK_TITLE


def _strip_label(text: str) -> str:
    """去掉模型自报的版式标签：「封面：麻黄——发汗解表第一药」→「麻黄——发汗解表第一药」。

    版式已由 :func:`_resolve_kind` 按结构重算，标题里再写一次「封面：」只会在
    封面上多出两个字的噪音；提示词已要求不写，这里是模型不听话时的兜底。
    """

    value = (text or "").strip()
    for label in _LAYOUT_LABELS:
        for sep in ("：", ":"):
            prefix = label + sep
            if value.startswith(prefix):
                rest = value[len(prefix) :].strip()
                # 只剩标签本身（「封面：」）就原样返回，剥完没标题更糟。
                if rest:
                    return rest
    return value


def _cover_item(title: str, source_count: int) -> dict[str, Any]:
    text = clean_text(title, MAX_TITLE) or _FALLBACK_TITLE
    return {
        "kind": "cover",
        "title": text,
        "subtitle": _COVER_SUBTITLE,
        "bullets": [],
        "columns": [],
        "note": "",
        "source_indexes": list(range(min(source_count, MAX_SOURCE_INDEXES))),
    }


def _clean_columns(values: list[DraftColumn]) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    for raw in values:
        if len(columns) >= MAX_COLUMNS:
            break
        heading = clean_text(raw.heading, 20)
        items = _clean_list(raw.items, GEN_COLUMN_ITEM, MAX_COLUMN_ITEMS)
        # 空栏会渲染成一个孤零零的标题，比直接不画更难看。
        if not heading or not items:
            continue
        columns.append({"heading": heading, "items": items})
    return columns


def _clean_list(values: list[str], limit: int, max_items: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value, limit)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    return cleaned


def _source_indexes(values: list[int], source_count: int) -> list[int]:
    if source_count <= 0:
        return []
    kept = {int(value) for value in values if isinstance(value, int) and 0 <= int(value) < source_count}
    return sorted(kept)[:MAX_SOURCE_INDEXES]


def clean_text(value: str, limit: int) -> str:
    """清洗空白与首尾标点，超长时在标点处断开，避免切出半句话。"""

    text = str(value or "").replace("\u3000", " ").replace("\n", " ")
    text = " ".join(text.split())
    text = text.strip(" \t-—·•、，,。.；;：:")
    if not text:
        return ""
    if not _PUNCT_ONLY.sub("", text):
        return ""
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = max((window.rfind(char) for char in _CUT_CHARS), default=-1)
    candidate = window[:cut] if cut >= max(6, limit // 3) else window
    return candidate.strip(" \t-—·•、，,。.；;：:") or window


def _theme_id(params: dict[str, Any] | None, design: Any = None) -> str:
    """主题以设计单为准：用户在设计单里换过主题，生成期不能又按左栏的旧值来。"""

    if design is not None:
        theme = design.get("theme_id") if isinstance(design, dict) else getattr(design, "theme_id", "")
        if str(theme or "").strip():
            return str(theme).strip()
    value = str((params or {}).get("theme") or "").strip()
    return value or "paper"


def _int_param(params: dict[str, Any] | None, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int((params or {}).get(key))
    except (TypeError, ValueError):
        return default
    return min(max(value, low), high)
