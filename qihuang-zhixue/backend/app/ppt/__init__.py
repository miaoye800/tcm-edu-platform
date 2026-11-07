"""幻灯工坊：中医药讲稿的四道工序（立骨 / 谋篇 / 敷演 / 校色）。"""

from __future__ import annotations

from .agent import PptAgent
from .schemas import DeckContent, OutlineSlide, SlideNode

__all__ = ["PptAgent", "DeckContent", "OutlineSlide", "SlideNode"]
