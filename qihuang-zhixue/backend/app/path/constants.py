"""路径知识图的固定枚举值，集中维护以避免数据库出现自由文本变体。"""

from typing import Final, Literal

from app.mastery.bkt import DEFAULT_BKT_PARAMS, MASTERY_THRESHOLD as BKT_MASTERY_THRESHOLD

KnowledgeLevel = Literal["domain", "subtype", "point"]
OrderSensitivity = Literal["strict", "free"]
PathState = Literal["locked", "available", "current", "completed", "paused", "custom"]
DeferReason = Literal["太难", "暂时不感兴趣", "以后再学", "其他"]
PATH_STATES: Final[tuple[str, ...]] = ("locked", "available", "current", "completed", "paused", "custom")
DEFER_REASONS: Final[tuple[str, ...]] = ("太难", "暂时不感兴趣", "以后再学", "其他")

KNOWLEDGE_LEVELS: Final[tuple[KnowledgeLevel, ...]] = ("domain", "subtype", "point")
ORDER_SENSITIVITIES: Final[tuple[OrderSensitivity, ...]] = ("strict", "free")
DEFAULT_REL_TYPE: Final[str] = "prerequisite"
HIERARCHY_REL_TYPE: Final[str] = "hierarchy"

# Planning guardrails. Keep all policy thresholds here so planner behavior is
# auditable and tests do not depend on scattered magic numbers.
CONFIDENCE_THRESHOLD: Final[float] = 0.5
MASTERY_THRESHOLD: Final[float] = BKT_MASTERY_THRESHOLD
REINFORCE_THRESHOLD: Final[float] = 0.65
EXAM_HOT_THRESHOLD: Final[float] = 0.8
DEFAULT_TARGET_MASTERY: Final[float] = 0.8
MIN_TARGET_MASTERY: Final[float] = 0.7
MAX_TARGET_MASTERY: Final[float] = 0.95
DEFAULT_CURRENT_MASTERY: Final[float] = DEFAULT_BKT_PARAMS.p_known_initial
FUN_RESOURCE_THRESHOLD: Final[float] = 0.7
CALIBRATION_NODE_COUNT: Final[int] = 3
MAX_VERIFY_RETRIES: Final[int] = 2
BASE_NODE_MINUTES: Final[int] = 30
MIN_NODE_MINUTES: Final[int] = 5
MIN_DURATION_GAP_FACTOR: Final[float] = 0.35
DURATION_GAP_SCALE: Final[float] = 0.45
TARGET_BY_DEPTH: Final[dict[str, float]] = {"浅": 0.75, "中": 0.82, "深": 0.9}
STRICTNESS_ADJUSTMENT: Final[dict[str, float]] = {"宽松": -0.05, "标准": 0.0, "严格": 0.05}
PACE_DURATION_FACTOR: Final[dict[str, float]] = {"松": 1.15, "中": 1.0, "紧": 0.85}
DEFAULT_RESOURCE_HINT: Final[str] = "图文讲解+随堂测验"
