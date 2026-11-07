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

from .schemas import MAX_NOTE, MAX_SUBTITLE, MAX_TITLE, DeckContent, OutlineSlide, SlideColumn, SlideNode

#: 生成期上限：比存盘校验更紧，留出手工编辑的余量。
MAX_SLIDES = 16
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
) -> DeckContent:
    """把草案收敛成一份可落库、可渲染、可导出的幻灯。"""

    limit = _int_param(params, "page_count", DEFAULT_PAGES, MIN_PAGES, MAX_PAGES)
    source_count = len(sources)

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
        kind = _resolve_kind(index, total, item)
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
            )
        )
    return DeckContent(theme_id=_theme_id(params), slides=slides, sources=sources)


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


def _theme_id(params: dict[str, Any] | None) -> str:
    value = str((params or {}).get("theme") or "").strip()
    return value or "paper"


def _int_param(params: dict[str, Any] | None, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int((params or {}).get(key))
    except (TypeError, ValueError):
        return default
    return min(max(value, low), high)
