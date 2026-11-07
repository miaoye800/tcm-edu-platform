"""幻灯的成品契约：严格出、可落库、可推流、可渲染。

与 ``app/mindmap/schemas.py`` 同一套分工：这里只放**严格**模型（超长直接 raise），
模型的宽松产出先落到 ``draft.py`` 的草案上，再由 ``normalize_deck`` 收敛到这里。

字段上限不是随手定的，全部源自 16:9 舞台的排版实测：
标题 40 字在 3.4cqw 下正好两行不压正文；要点 60 字超过就会挤出可视区。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 渲染层认识的六种版式。前端 SlideSurface 与 pptx 导出共用这一份枚举。
SlideKind = Literal["cover", "agenda", "section", "bullets", "compare", "summary"]

MAX_TITLE = 40
MAX_SUBTITLE = 80
MAX_BULLET = 60
MAX_NOTE = 200


class SlideColumn(BaseModel):
    """对比版式的一栏。只有 compare 用到。"""

    model_config = ConfigDict(extra="forbid")

    heading: str = Field(min_length=1, max_length=24)
    items: list[str] = Field(default_factory=list, max_length=5)


class SlideNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    kind: SlideKind
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    subtitle: str = Field(default="", max_length=MAX_SUBTITLE)
    bullets: list[str] = Field(default_factory=list, max_length=6)
    columns: list[SlideColumn] = Field(default_factory=list, max_length=2)
    note: str = Field(default="", max_length=MAX_NOTE)
    source_indexes: list[int] = Field(default_factory=list, max_length=6)

    @field_validator("title")
    @classmethod
    def concise_title(cls, value: str) -> str:
        text = " ".join(value.split())
        if len(text) > MAX_TITLE:
            raise ValueError("slide title must be concise")
        return text

    @field_validator("bullets")
    @classmethod
    def concise_bullets(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(str(value).split()) for value in values]
        if any(not item for item in cleaned):
            raise ValueError("slide bullet must not be blank")
        if any(len(item) > MAX_BULLET for item in cleaned):
            raise ValueError("slide bullet must be concise")
        return cleaned


class DeckContent(BaseModel):
    """一份幻灯。``sources`` 是生成期固化的溯源证据，编辑不覆盖它。"""

    model_config = ConfigDict(extra="forbid")

    theme_id: str = Field(default="paper", max_length=32)
    slides: list[SlideNode] = Field(min_length=1, max_length=20)
    sources: list[dict[str, str]] = Field(default_factory=list)


class OutlineSlide(BaseModel):
    """大纲页：只有标题与要点骨架，是「先定大纲」模式的交付物与回执。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    points: list[str] = Field(default_factory=list, max_length=5)
    source_indexes: list[int] = Field(default_factory=list, max_length=6)


class FramePlan(BaseModel):
    """立骨的产物：这一课叫什么、讲给谁、按什么顺序讲。"""

    model_config = ConfigDict(extra="forbid")

    deck_title: str = Field(min_length=1, max_length=MAX_TITLE)
    audience: str = Field(default="中医药专业本科生", max_length=40)
    arc: list[str] = Field(default_factory=list, max_length=8)
