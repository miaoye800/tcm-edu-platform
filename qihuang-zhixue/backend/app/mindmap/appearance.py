"""导图的外观枚举：结构与配色。

两者都是**视图属性**，不参与内容生成，也不影响树的形状。
放在独立模块里，是因为要被三处同时引用：整树保存、外观保存、以及
生成任务把偏好写进 ``meta`` 的那一步。三处各写一份枚举迟早对不上。
"""

from __future__ import annotations

from typing import Final

# 与前端 config/mindmapStructures.ts、config/mindmapThemes.ts 一一对应。
ALLOWED_STRUCTURE_IDS: Final[frozenset[str]] = frozenset(
    {"logic-right", "mind-both", "org-down", "fishbone"}
)
ALLOWED_THEME_IDS: Final[frozenset[str]] = frozenset(
    {"indigo", "verdant", "violet", "inkCream"}
)

DEFAULT_STRUCTURE_ID: Final[str] = "logic-right"
DEFAULT_THEME_ID: Final[str] = "indigo"

# 旧版 8 套主题 id：存量资源仍要能原样读出来，读到就映射成默认配色，
# 不拒绝——拒绝等于让老用户的导图打不开。
LEGACY_THEME_IDS: Final[frozenset[str]] = frozenset(
    {
        "ink-scroll",
        "warm-gold",
        "neon-night",
        "herb-garden",
        "fishbone-contrast",
        "timeline-process",
        "star-chart",
        "hand-scroll",
    }
)


def normalize_structure(value: str | None) -> str:
    return value if value in ALLOWED_STRUCTURE_IDS else DEFAULT_STRUCTURE_ID


def normalize_theme(value: str | None) -> str:
    """旧 id 一律落到默认配色：宁可少一套配色，也不能打不开。"""

    if value in ALLOWED_THEME_IDS:
        return str(value)
    return DEFAULT_THEME_ID
