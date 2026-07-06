"""Learning profile schema and evidence card primitives."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
from pydantic.dataclasses import dataclass

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

LearningGoalLabel = Literal["应试提分", "临床实用", "兴趣科普", "学术深造"]
LearningStyleLabel = Literal["视觉型", "文字型", "记忆(口诀)型"]
MotivationLabel = Literal["内驱", "外驱", "内外混合", "被动"]
ResourceTypeLabel = Literal["知识图谱", "拟人化短剧", "汤头歌诀", "医案文选"]


@dataclass(frozen=True)
class EvidenceCard:
    """Objective evidence emitted by rules, without scoring judgment."""

    dimension: DimensionKey
    source: str
    fact: str
    objective_signal: dict[str, Any]
    weight: float
    timestamp: datetime

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, value: float) -> float:
        if value < 0:
            raise ValueError("weight must be non-negative")
        return value

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> object:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value


EvidenceCardAdapter = TypeAdapter(EvidenceCard)


class ProfileDimensionBase(BaseModel):
    """Shared fields required by every profile dimension."""

    model_config = ConfigDict(validate_assignment=True)

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[EvidenceCard] = Field(default_factory=list)
    narrative: str = ""


class NumericDimension(ProfileDimensionBase):
    """0-100 numeric dimension. None means not scored yet."""

    score: float | None = Field(default=None, ge=0.0, le=100.0)


class CategoricalDimension(ProfileDimensionBase):
    """Single label dimension. Distribution keeps source-vote proportions."""

    label: str | None = None
    distribution: dict[str, float] = Field(default_factory=dict)

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


class WeakPointsDimension(ProfileDimensionBase):
    """Hotspot/list dimension: weak labels plus evidence-backed strength."""

    label: str | None = None
    items: list[WeakPoint] = Field(default_factory=list)


class MotivationDimension(ProfileDimensionBase):
    """Mixed dimension: motivation strength plus internal/external label."""

    score: float | None = Field(default=None, ge=0.0, le=100.0)
    label: MotivationLabel | None = None


class ResourcePreferenceDimension(ProfileDimensionBase):
    """Resource preference distribution across allowed resource types."""

    label: ResourceTypeLabel | None = None
    distribution: dict[ResourceTypeLabel, float] = Field(default_factory=dict)

    @field_validator("distribution")
    @classmethod
    def validate_distribution(cls, value: dict[ResourceTypeLabel, float]) -> dict[ResourceTypeLabel, float]:
        for ratio in value.values():
            if ratio < 0 or ratio > 1:
                raise ValueError("distribution values must be between 0 and 1")
        return value


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
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def create_blank_profile(user_id: str) -> Profile:
    """Create an unscored, low-confidence initial learning profile."""

    return Profile(user_id=user_id)


def evidence_card_to_json_dict(card: EvidenceCard) -> dict[str, Any]:
    """Serialize an evidence dataclass with JSON-compatible datetime output."""

    return EvidenceCardAdapter.dump_python(card, mode="json")


def evidence_card_from_dict(payload: dict[str, Any]) -> EvidenceCard:
    """Deserialize a stored evidence card payload."""

    return EvidenceCardAdapter.validate_python(payload)


def evidence_card_asdict(card: EvidenceCard) -> dict[str, Any]:
    """Return a plain dataclass dict for callers that do not need JSON mode."""

    return asdict(card)


__all__ = [
    "CATEGORY_DIMENSIONS",
    "DIMENSION_LABELS",
    "DimensionKey",
    "EvidenceCard",
    "MIXED_DIMENSIONS",
    "NUMERIC_DIMENSIONS",
    "Profile",
    "create_blank_profile",
    "evidence_card_asdict",
    "evidence_card_from_dict",
    "evidence_card_to_json_dict",
]
