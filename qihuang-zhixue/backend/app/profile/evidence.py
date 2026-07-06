"""Objective evidence card schema for profile collection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EvidenceCard(BaseModel):
    """Objective fact captured by rules before any scoring judgment."""

    model_config = ConfigDict(validate_assignment=True)

    dimension: str
    source: str
    fact: str
    objective_signal: dict[str, Any] = Field(default_factory=dict)
    weight: float = Field(ge=0.0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("dimension", "source", "fact")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be non-empty")
        return normalized

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> object:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value
