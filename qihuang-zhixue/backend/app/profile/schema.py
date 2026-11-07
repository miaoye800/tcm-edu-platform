"""Learning profile schema and evidence card primitives."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.profile.evidence import EvidenceCard, EvidenceTier, StructuredEvidence

DimensionKey = Literal[
    "knowledge_base",
    "learning_goal",
    "learning_style",
    "weak_points",
    "pace",
    "motivation",
    "resource_preference",
]

NUMERIC_DIMENSIONS: tuple[DimensionKey, ...] = ("knowledge_base", "pace")
CATEGORY_DIMENSIONS: tuple[DimensionKey, ...] = (
    "learning_goal",
    "learning_style",
    "resource_preference",
)
MIXED_DIMENSIONS: tuple[DimensionKey, ...] = ("weak_points", "motivation")

DIMENSION_LABELS: dict[DimensionKey, str] = {
    "knowledge_base": "知识基础",
    "learning_goal": "学习目标",
    "learning_style": "认知/学习风格",
    "weak_points": "易错点与薄弱知识点",
    "pace": "学习进度与节奏",
    "motivation": "兴趣与动机偏好",
    "resource_preference": "资源类型偏好",
}

LearningGoalLabel = Literal["应试提分", "临床实用", "兴趣科普", "学术深造", "基础巩固", "能力提升", "其他"]
LearningStyleLabel = Literal["视觉型", "文字型", "记忆(口诀)型"]
MotivationLabel = Literal["内驱", "外驱", "内外混合", "被动"]
ResourceTypeLabel = Literal["知识图谱", "拟人化短剧", "汤头歌诀", "医案文选"]

# 旧维度名 → 规范键。模型仍会把知识基础写成 ``base_level``（那是 legacy 字段名）、
# 把学习目标写成 ``goal``，而下游只认七个规范键；一个别名就让整条扫描记录作废。
# 这是唯一允许出现旧键名的地方（``test_no_legacy_stack`` 豁免本文件），
# 其它模块一律用 ``canonical_dimension_name`` 转换后再用。
LEGACY_DIMENSION_ALIASES: dict[str, str] = {
    "base_level": "knowledge_base",
    "knowledge": "knowledge_base",
    "knowledge_mastery": "knowledge_base",
    "goal": "learning_goal",
    "learning_goals": "learning_goal",
    "style": "learning_style",
    "learning_habit": "learning_style",
    "weak_point": "weak_points",
    "weakness": "weak_points",
    "rhythm": "pace",
    "schedule": "pace",
    "learning_pace": "pace",
    "motive": "motivation",
    "resource": "resource_preference",
    "resources": "resource_preference",
}


def canonical_dimension_name(value: Any) -> Any:
    """Map a legacy dimension name onto its canonical key, else pass it through."""

    if not isinstance(value, str):
        return value
    key = value.strip()
    return LEGACY_DIMENSION_ALIASES.get(key.lower(), key)


class ProfileDimensionBase(BaseModel):
    """Shared fields required by every profile dimension."""

    model_config = ConfigDict(validate_assignment=True)

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # The radar is a bounded estimate, deliberately distinct from both raw
    # event values and categorical card ratios. ``None`` means no evidence.
    radar_score: float | None = Field(default=None, ge=5.0, le=95.0)
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    unknown_reasons: list[str] = Field(default_factory=list)
    confidence_breakdown: dict[str, float] = Field(default_factory=dict)
    evidence: list[EvidenceCard] = Field(default_factory=list)
    narrative: str = ""
    # Unified component collection state. A dimension is not scoreable until
    # every required component has a scorable evidence set and agent score.
    component_matrix: dict[str, dict[str, Any]] = Field(default_factory=dict)
    component_scores: dict[str, float] = Field(default_factory=dict)
    scoring_phase: Literal["collecting", "scored"] = "collecting"
    pending_components: list[str] = Field(default_factory=list)
    # Honest error bar for the drawn value. ``None`` when nothing is scored.
    score_interval: dict[str, float] | None = None
    score_summary: str = ""


class NumericDimension(ProfileDimensionBase):
    """0-100 numeric dimension. None means not scored yet."""

    score: float | None = Field(default=None, ge=0.0, le=100.0)


class CategoricalDimension(ProfileDimensionBase):
    """Single label dimension. Distribution keeps source-vote proportions."""

    label: str | None = None
    distribution: dict[str, float] = Field(default_factory=dict)
    distribution_scope: Literal["observed_evidence", "complete_profile"] = "observed_evidence"

    @property
    def derived_score(self) -> float | None:
        if self.radar_score is not None:
            return self.radar_score
        if not self.distribution:
            return None
        # Compatibility view for legacy records without an agent-authored
        # radar estimate. The radar serializer bounds evidence-bearing values
        # away from absolute edges before display.
        return round(max(self.distribution.values()) * 100, 2)

    @field_validator("distribution")
    @classmethod
    def validate_distribution(cls, value: dict[str, float]) -> dict[str, float]:
        for key, ratio in value.items():
            if not key:
                raise ValueError("distribution keys must be non-empty")
            if ratio < 0 or ratio > 1:
                raise ValueError("distribution values must be between 0 and 1")
        return value


class WeakPoint(BaseModel):
    """Weak point tag with objective occurrence strength."""

    label: str
    strength: int = Field(default=1, ge=1)


class IdentityCardView(BaseModel):
    """Stable, traceable view model for one dialogue identity card.

    ``cards`` is retained as a legacy alias for clients that adopted the first
    dialogue implementation. New consumers should use ``items``.
    """

    key: DimensionKey
    label: str
    kind: Literal["distribution", "level", "tags"]
    revealed: bool
    revealable: bool
    revealed_now: bool
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_status: Literal["待取证", "已取证"]
    score: float | None = None
    radar_score: float | None = Field(default=None, ge=5.0, le=95.0)
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    unknown_reasons: list[str] = Field(default_factory=list)
    confidence_breakdown: dict[str, float] = Field(default_factory=dict)
    display_value: str
    distribution: dict[str, float] = Field(default_factory=dict)
    distribution_scope: Literal["observed_evidence", "complete_profile"] = "observed_evidence"
    items: list[dict[str, Any]] = Field(default_factory=list)
    cards: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)
    explanation: str
    provenance: str
    pending_reason: str | None = None


class WeakPointsDimension(ProfileDimensionBase):
    """Hotspot/list dimension: weak labels plus evidence-backed strength."""

    label: str | None = None
    items: list[WeakPoint] = Field(default_factory=list)


class MotivationDimension(ProfileDimensionBase):
    """Mixed dimension: motivation strength plus internal/external label."""

    score: float | None = Field(default=None, ge=0.0, le=100.0)
    label: MotivationLabel | None = None


class ResourcePreferenceDimension(CategoricalDimension):
    """Resource preference distribution across allowed resource types."""

    label: ResourceTypeLabel | None = None
    distribution: dict[ResourceTypeLabel, float] = Field(default_factory=dict)

    @field_validator("distribution")
    @classmethod
    def validate_resource_distribution(cls, value: dict[ResourceTypeLabel, float]) -> dict[ResourceTypeLabel, float]:
        for ratio in value.values():
            if ratio < 0 or ratio > 1:
                raise ValueError("distribution values must be between 0 and 1")
        return value


PlanningDepth = Literal["浅", "中", "深"]
PlanningBreadth = Literal["考点导向", "全面", "兴趣优先"]
PlanningPace = Literal["松", "中", "紧"]
AssessmentStrictness = Literal["宽松", "标准", "严格"]


class TimeBudget(BaseModel):
    total_days: int = Field(ge=1)
    daily_minutes: int = Field(ge=1)


class PlanningParams(BaseModel):
    """Continuous planning controls used by the route-planning agent."""

    depth: PlanningDepth = "中"
    breadth: PlanningBreadth = "全面"
    pace: PlanningPace = "中"
    fun_preference: float = Field(default=0.5, ge=0.0, le=1.0)
    assessment_strictness: AssessmentStrictness = "标准"
    time_budget: TimeBudget | None = None


class Goal(BaseModel):
    raw_text: str = ""
    normalized: LearningGoalLabel | None = None
    confirmed_by_user: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


def derive_planning_params(profile: "Profile") -> PlanningParams:
    """Derive a conservative planning baseline from the existing seven axes."""

    breadth: PlanningBreadth = "考点导向" if profile.learning_goal.label == "应试提分" else "全面"
    pace_score = profile.pace.score
    plan_pace: PlanningPace = "紧" if pace_score is not None and pace_score >= 70 else "松" if pace_score is not None and pace_score <= 30 else "中"
    motivation_score = profile.motivation.score
    strictness: AssessmentStrictness = "宽松" if motivation_score is not None and motivation_score < 40 else "标准"
    engaging_resources = {"拟人化短剧", "汤头歌诀"}
    fun_preference = 0.7 if profile.resource_preference.label in engaging_resources or profile.learning_style.label == "视觉型" else 0.5
    return PlanningParams(
        breadth=breadth,
        pace=plan_pace,
        fun_preference=fun_preference,
        assessment_strictness=strictness,
    )


class Profile(BaseModel):
    """Seven-dimension learning profile snapshot."""

    model_config = ConfigDict(validate_assignment=True)

    user_id: str
    knowledge_base: NumericDimension = Field(default_factory=NumericDimension)
    learning_goal: CategoricalDimension = Field(default_factory=CategoricalDimension)
    learning_style: CategoricalDimension = Field(default_factory=CategoricalDimension)
    weak_points: WeakPointsDimension = Field(default_factory=WeakPointsDimension)
    pace: NumericDimension = Field(default_factory=NumericDimension)
    motivation: MotivationDimension = Field(default_factory=MotivationDimension)
    resource_preference: ResourcePreferenceDimension = Field(default_factory=ResourcePreferenceDimension)
    planning_params: PlanningParams = Field(default_factory=PlanningParams)
    goal: Goal = Field(default_factory=Goal)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def derive_unconfirmed_planning_params(self) -> "Profile":
        if not self.goal.confirmed_by_user:
            object.__setattr__(self, "planning_params", derive_planning_params(self))
        return self


def create_blank_profile(user_id: str) -> Profile:
    """Create an unscored, low-confidence initial learning profile."""

    return Profile(user_id=user_id)


def evidence_card_to_json_dict(card: EvidenceCard) -> dict[str, object]:
    """Serialize an evidence card with JSON-compatible datetime output."""

    return card.model_dump(mode="json")


def evidence_card_from_dict(payload: dict[str, object]) -> EvidenceCard:
    """Deserialize a stored evidence card payload."""

    return EvidenceCard.model_validate(payload)


def evidence_card_asdict(card: EvidenceCard) -> dict[str, object]:
    """Return a plain dict for callers that do not need JSON mode."""

    return card.model_dump()


__all__ = [
    "CATEGORY_DIMENSIONS",
    "DIMENSION_LABELS",
    "DimensionKey",
    "EvidenceCard",
    "EvidenceTier",
    "IdentityCardView",
    "StructuredEvidence",
    "MIXED_DIMENSIONS",
    "NUMERIC_DIMENSIONS",
    "Goal",
    "PlanningParams",
    "TimeBudget",
    "Profile",
    "create_blank_profile",
    "derive_planning_params",
    "evidence_card_asdict",
    "evidence_card_from_dict",
    "evidence_card_to_json_dict",
]
