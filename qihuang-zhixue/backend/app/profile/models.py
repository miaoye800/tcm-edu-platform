"""Student profile domain models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DIMENSION_LABELS: dict[str, str] = {
    "knowledge_base": "知识基础",
    "learning_style": "认知/学习风格",
    "weak_points": "易错/薄弱点",
    "goal": "学习目标",
    "pace": "进度节奏",
    "motivation": "兴趣动机",
    "resource_preference": "资源类型偏好",
}

DIMENSION_KEYS: tuple[str, ...] = tuple(DIMENSION_LABELS.keys())

ProfileSource = Literal["rule", "llm", "jury"]


class ProfileDimension(BaseModel):
    """Numeric 0-100 profile dimension with confidence and provenance."""

    model_config = ConfigDict(validate_assignment=True)

    score: int | None = Field(default=None, ge=0, le=100)
    evidence: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: ProfileSource = "rule"

    @field_validator("score", mode="before")
    @classmethod
    def clamp_score(cls, value: object) -> object:
        if value is None:
            return None
        try:
            score = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return value
        return max(0, min(100, score))


class CategoricalDimension(BaseModel):
    """Categorical dimension with source distribution and confidence."""

    model_config = ConfigDict(validate_assignment=True)

    label: str | None = None
    distribution: dict[str, float] = Field(default_factory=dict)
    evidence: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: ProfileSource = "rule"

    @property
    def derived_score(self) -> float | None:
        """Clarity/concentration helper for radar display."""

        if not self.distribution:
            return None
        return round(max(self.distribution.values()) * 100, 2)

    @property
    def score(self) -> int | None:
        """Backward-compatible read-only radar value; not persisted as a score."""

        if self.derived_score is None:
            return None
        return int(round(self.derived_score))

    @field_validator("distribution", mode="before")
    @classmethod
    def normalize_distribution(cls, value: object) -> object:
        if value is None:
            return {}
        return value

    @field_validator("distribution")
    @classmethod
    def validate_distribution(cls, value: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for key, ratio in value.items():
            label = str(key).strip()
            if not label:
                raise ValueError("distribution keys must be non-empty")
            numeric = float(ratio)
            if numeric < 0 or numeric > 1:
                raise ValueError("distribution values must be between 0 and 1")
            normalized[label] = numeric
        return normalized


class WeakPointDimension(ProfileDimension):
    """Weak point list with objective strength per tag."""

    items: list[str] = Field(default_factory=list)
    strengths: dict[str, float] = Field(default_factory=dict)

    def __str__(self) -> str:
        return str(self.items)

    @field_validator("items")
    @classmethod
    def dedupe_items(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in value:
            normalized = item.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    @field_validator("strengths")
    @classmethod
    def validate_strengths(cls, value: dict[str, float]) -> dict[str, float]:
        return {str(key).strip(): max(0.0, float(strength)) for key, strength in value.items() if str(key).strip()}


class StudentProfile(BaseModel):
    """Seven-dimension student profile snapshot."""

    model_config = ConfigDict(validate_assignment=True)

    user_id: str
    knowledge_base: ProfileDimension = Field(default_factory=ProfileDimension)
    learning_style: CategoricalDimension = Field(default_factory=CategoricalDimension)
    weak_points: WeakPointDimension = Field(default_factory=WeakPointDimension)
    goal: CategoricalDimension = Field(default_factory=CategoricalDimension)
    pace: ProfileDimension = Field(default_factory=ProfileDimension)
    motivation: ProfileDimension = Field(default_factory=ProfileDimension)
    resource_preference: CategoricalDimension = Field(default_factory=CategoricalDimension)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("weak_points", mode="before")
    @classmethod
    def normalize_weak_points(cls, value: object) -> object:
        if isinstance(value, list):
            return WeakPointDimension(items=[str(item) for item in value])
        return value

    @field_validator("goal", "learning_style", "resource_preference", mode="before")
    @classmethod
    def migrate_categorical_dimension(cls, value: object) -> object:
        """Accept old score/evidence-shaped category dimensions."""

        if isinstance(value, dict):
            payload = dict(value)
            if "label" not in payload:
                evidence = payload.get("evidence")
                payload["label"] = str(evidence) if evidence else None
            payload.pop("score", None)
            return payload
        return value


EventType = Literal["quiz_answer", "qa", "game"]


class ProfileEvent(BaseModel):
    """Profile update event used by the legacy cold-start flow."""

    user_id: str
    event_type: EventType
    is_correct: bool | None = None
    topic: str = ""
    skill: str = ""
    detail: str = ""
    score_delta: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DiagnosticQuestion(BaseModel):
    """Cold-start diagnostic question."""

    id: str
    question: str
    answer: str
    topic: str
    skill: str


def default_profile(user_id: str) -> StudentProfile:
    """Create a blank initial profile with no score/label and zero confidence."""

    return StudentProfile(user_id=user_id)
