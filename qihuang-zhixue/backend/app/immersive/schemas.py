"""Strict contracts for immersive dialogue and resource recommendations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ResourceType = Literal["doc", "graph", "mindmap", "quiz", "story"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Recommendation(StrictModel):
    type: ResourceType
    reason: str = Field(min_length=1, max_length=300)
    params: dict[str, Any] = Field(default_factory=dict)


class RecommendationOutput(StrictModel):
    reply: str = Field(min_length=1, max_length=1200)
    recommendation: list[Recommendation] = Field(min_length=1, max_length=3)


class SessionRequest(StrictModel):
    node_id: str = Field(min_length=1, max_length=200)


class ChatRequest(StrictModel):
    session_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=2000)


class ConfirmRequest(StrictModel):
    session_id: str = Field(min_length=1, max_length=200)
    resources: list[Recommendation] = Field(min_length=1, max_length=3)

