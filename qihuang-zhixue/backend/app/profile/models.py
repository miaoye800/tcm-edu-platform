"""学生画像领域模型。"""

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


class ProfileDimension(BaseModel):
    """单个画像维度，score 用于雷达图，evidence 保留依据摘要。"""

    model_config = ConfigDict(validate_assignment=True)

    score: int = Field(default=0, ge=0, le=100)
    evidence: str = ""

    @field_validator("score", mode="before")
    @classmethod
    def clamp_score(cls, value: object) -> object:
        """兼容外部直接赋值，保证持久化分数始终落在 0~100。"""

        try:
            score = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return value
        return max(0, min(100, score))


class WeakPointDimension(ProfileDimension):
    """薄弱点维度额外维护标签列表。"""

    items: list[str] = Field(default_factory=list)

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


class StudentProfile(BaseModel):
    """7 维学生画像。"""

    model_config = ConfigDict(validate_assignment=True)

    user_id: str
    knowledge_base: ProfileDimension = Field(default_factory=ProfileDimension)
    learning_style: ProfileDimension = Field(default_factory=ProfileDimension)
    weak_points: WeakPointDimension = Field(default_factory=WeakPointDimension)
    goal: ProfileDimension = Field(default_factory=ProfileDimension)
    pace: ProfileDimension = Field(default_factory=ProfileDimension)
    motivation: ProfileDimension = Field(default_factory=ProfileDimension)
    resource_preference: ProfileDimension = Field(default_factory=ProfileDimension)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("weak_points", mode="before")
    @classmethod
    def normalize_weak_points(cls, value: object) -> object:
        """兼容直接赋值 list，以及早期版本写入的 list 形态 JSON。"""

        if isinstance(value, list):
            return WeakPointDimension(items=[str(item) for item in value])
        return value


EventType = Literal["quiz_answer", "qa", "game"]


class ProfileEvent(BaseModel):
    """画像动态更新事件。"""

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
    """冷启动诊断小测题。"""

    id: str
    question: str
    answer: str
    topic: str
    skill: str


def default_profile(user_id: str) -> StudentProfile:
    """生成默认空画像。"""

    return StudentProfile(user_id=user_id)
