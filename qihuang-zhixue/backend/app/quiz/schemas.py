"""Strict public and internal contracts for QuizAgent."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

QuestionType = Literal["single", "multi", "case", "short"]
SourceLevel = Literal["real", "adapted", "original"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuizQuestion(StrictModel):
    """Internal question contract. Never return this model directly to learners."""

    id: str
    stem: str = Field(min_length=1)
    options: list[dict[str, str]] = Field(default_factory=list)
    answer: list[str] = Field(min_length=1)
    analysis: str = Field(min_length=1)
    question_type: QuestionType
    exam_point_id: str
    source_level: SourceLevel
    source_detail: dict[str, Any]

    @model_validator(mode="after")
    def validate_source_claim(self) -> "QuizQuestion":
        if self.source_level == "real":
            if not self.source_detail.get("year") or not self.source_detail.get("source"):
                raise ValueError("real questions require a verified year and source")
        elif self.source_level == "adapted":
            if not self.source_detail.get("adapted_from_question_id") or not self.source_detail.get("changes"):
                raise ValueError("adapted questions require origin question and declared changes")
        elif not self.source_detail.get("knowledge_node_id"):
            raise ValueError("original questions require knowledge_node provenance")
        return self


class QuizQuestionPublic(StrictModel):
    """Learner-facing projection. Answer and analysis are intentionally absent."""

    id: str
    stem: str
    options: list[dict[str, str]] = Field(default_factory=list)
    question_type: QuestionType
    exam_point_id: str
    source_level: SourceLevel
    source_detail: dict[str, Any]


class QuizGroup(StrictModel):
    scenario: Literal["practice", "assessment"]
    questions: list[QuizQuestion]


class GradeRequest(StrictModel):
    question_id: str
    answer: str | list[str]
    elapsed_seconds: float | None = Field(default=None, ge=0)


class GradeResult(StrictModel):
    question_id: str
    correct: bool
    score: int = Field(ge=0, le=100)
    missing_points: list[str] = Field(default_factory=list)
    comment: str
    analysis: str
    remediation: dict[str, Any] | None = None


class AssessmentStartRequest(StrictModel):
    node_id: str


class AssessmentAnswerRequest(StrictModel):
    assessment_id: str
    question_id: str
    answer: str | list[str]
    elapsed_seconds: float | None = Field(default=None, ge=0)


class AssessmentFinishRequest(StrictModel):
    assessment_id: str

