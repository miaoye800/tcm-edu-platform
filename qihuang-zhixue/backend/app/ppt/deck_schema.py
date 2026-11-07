"""整份存盘与大纲确认的出入参校验。

``user_resource`` 只有一个 ``content_json`` 列，存盘就是整份覆盖，所以这里把
「前端能提交什么」卡死：多一个字段就 422，id 必须可逆地唯一（前端拿它做 key）。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import OutlineSlide, SlideNode

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class SaveDeckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    theme_id: str = Field(default="paper", max_length=32)
    slides: list[SlideNode] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_slide_ids(self) -> "SaveDeckRequest":
        seen: set[str] = set()
        for slide in self.slides:
            if not _ID_PATTERN.match(slide.id):
                raise ValueError(f"非法的页 id：{slide.id}")
            if slide.id in seen:
                raise ValueError(f"页 id 重复：{slide.id}")
            seen.add(slide.id)
        return self


class SaveDeckResponse(BaseModel):
    version: int
    slide_count: int


class ConfirmOutlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slides: list[OutlineSlide] = Field(min_length=1, max_length=20)


class ConfirmOutlineResponse(BaseModel):
    ok: bool
    current_step: int


def deck_content(payload: SaveDeckRequest) -> dict[str, Any]:
    """请求体 → content_json 的存储形状。``sources`` 不在这里，由存储层保留原值。"""

    return {
        "theme_id": payload.theme_id,
        "slides": [slide.model_dump(mode="json") for slide in payload.slides],
    }
