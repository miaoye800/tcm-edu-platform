"""Central, product-level rules for the stage 3 assessment domain."""

from typing import Final

# This is deliberately separate from app.mastery.bkt.MASTERY_THRESHOLD (0.8).
ASSESSMENT_PASS_THRESHOLDS: Final[dict[str, int]] = {"宽松": 60, "标准": 70, "严格": 80}
ASSESSMENT_GATE_WEIGHTS: Final[dict[str, float]] = {"quick": 0.50, "case": 0.35, "optional": 0.15}
QUICK_QUESTION_COUNT: Final[int] = 3
QUICK_RECOMMENDED_SECONDS: Final[int] = 45
CASE_RECOMMENDED_SECONDS: Final[int] = 180
OPTIONAL_RECOMMENDED_SECONDS: Final[int] = 120
RECENT_QUESTION_LIMIT: Final[int] = 20
RECENT_POINT_LIMIT: Final[int] = 8
HIGH_FUN_PREFERENCE: Final[float] = 0.70

