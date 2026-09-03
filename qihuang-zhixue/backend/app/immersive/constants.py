"""Stable immersive-dialogue limits and preference thresholds."""

from typing import Final

IMMERSIVE_SESSION_PREFIX: Final = "immersive:"
MIN_PROFILE_CONFIDENCE: Final = 0.5
HIGH_FUN_PREFERENCE: Final = 0.7
MAX_RECOMMENDATIONS: Final = 3
MAX_HISTORY_MESSAGES: Final = 12

# The profile fusion layer records the learner-facing category labels rather
# than resource_task's transport enum. Keep this adapter at the boundary.
RESOURCE_PROFILE_LABELS: Final[dict[str, str]] = {
    "doc": "医案文选",
    "graph": "知识图谱",
    "mindmap": "知识图谱",
    "quiz": "汤头歌诀",
    "story": "拟人化短剧",
}
