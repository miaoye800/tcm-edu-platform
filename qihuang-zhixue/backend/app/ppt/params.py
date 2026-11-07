"""幻灯任务的入参：把前端的任意 dict 收敛成一个不会越界的值对象。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: one_shot = 一键出整稿；outline = 谋篇后停下等确认。
PptMode = Literal["one_shot", "outline"]

THEME_IDS = ("paper", "ink", "celadon", "cinnabar")
PAGE_CHOICES = (8, 12, 16)


class PptParams(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: PptMode = "one_shot"
    theme: str = "paper"
    page_count: int = Field(default=12, ge=6, le=16)
    audience: str = Field(default="中医药专业本科生", max_length=40)
    topic: str = Field(default="", max_length=60)

    @classmethod
    def parse(cls, raw: dict[str, Any] | None) -> "PptParams":
        raw = dict(raw or {})
        mode = "outline" if str(raw.get("mode") or "").strip() == "outline" else "one_shot"
        theme = str(raw.get("theme") or "").strip()
        if theme not in THEME_IDS:
            theme = "paper"
        try:
            page_count = int(raw.get("page_count"))
        except (TypeError, ValueError):
            page_count = 12
        page_count = min(max(page_count, min(PAGE_CHOICES)), max(PAGE_CHOICES))
        audience = str(raw.get("audience") or "").strip() or "中医药专业本科生"
        topic = str(raw.get("topic") or "").strip()
        return cls(mode=mode, theme=theme, page_count=page_count, audience=audience[:40], topic=topic[:60])

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "theme": self.theme, "page_count": self.page_count, "audience": self.audience, "topic": self.topic}
