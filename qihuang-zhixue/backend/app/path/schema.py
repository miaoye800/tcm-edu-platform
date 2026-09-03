"""Strict structured contracts for the three-level learning-path planner."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.profile.schema import PlanningParams, Profile, TimeBudget


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeNodeInput(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    level: Literal["domain", "subtype", "point"]
    parent_id: str | None = None
    description: str = ""
    exam_frequency: float = Field(default=0.0, ge=0.0, le=1.0)
    sort_hint: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)


class PrerequisiteEdgeInput(StrictModel):
    from_node_id: str = Field(min_length=1)
    to_node_id: str = Field(min_length=1)
    rel_type: Literal["prerequisite"] = "prerequisite"
    order_sensitivity: Literal["strict", "free"]
    reason: str = ""


class MasteryState(StrictModel):
    p_mastery: float = Field(ge=0.0, le=1.0)
    attempts: int = Field(default=0, ge=0)
    last_seen: str | None = None


class PlanningContext(StrictModel):
    """Complete read-only input snapshot; the planner never persists profile changes."""

    user_id: str = Field(min_length=1)
    profile: Profile
    planning_params: PlanningParams
    knowledge_nodes: list[KnowledgeNodeInput]
    prerequisite_edges: list[PrerequisiteEdgeInput]
    mastery: dict[str, MasteryState] = Field(default_factory=dict)
    time_budget: TimeBudget | None = None

    @model_validator(mode="after")
    def validate_graph_references(self) -> "PlanningContext":
        node_ids = {node.id for node in self.knowledge_nodes}
        for edge in self.prerequisite_edges:
            if edge.from_node_id not in node_ids or edge.to_node_id not in node_ids:
                raise ValueError(f"prerequisite edge references an unknown node: {edge.from_node_id}->{edge.to_node_id}")
        return self


class StagePlan(StrictModel):
    domain_id: str = Field(min_length=1)
    order: int = Field(ge=1)
    order_locked: bool
    reason: str = Field(min_length=1)


class NodePlan(StrictModel):
    node_id: str = Field(min_length=1)
    stage: int = Field(ge=1)
    seq: int = Field(ge=1)
    target_mastery: float = Field(ge=0.0, le=1.0)
    resource_hint: str = Field(min_length=1)
    highlight: Literal["normal", "exam_hot", "remedial"] = "normal"
    calibration: bool = False
    reason: str = Field(min_length=1)


class PathPlan(StrictModel):
    """Core JSON contract returned by L1/L2 before transport metadata is added."""

    stages: list[StagePlan]
    nodes: list[NodePlan]


class VerificationIssue(StrictModel):
    code: Literal["prerequisite_conflict", "exam_coverage", "time_budget"]
    message: str
    node_ids: list[str] = Field(default_factory=list)


class VerifiedPlan(StrictModel):
    plan: PathPlan
    fallback: bool = False
    attempts: int = Field(default=0, ge=0)
    issues: list[VerificationIssue] = Field(default_factory=list)


class L1PlanResult(StrictModel):
    plan: PathPlan
    suggestion: str
    fallback: bool = False
    verify_attempts: int = 0
    verification_issues: list[VerificationIssue] = Field(default_factory=list)


class L2PlanResult(StrictModel):
    domain_id: str
    plan: PathPlan
    fallback: bool = False
    verify_attempts: int = 0
    verification_issues: list[VerificationIssue] = Field(default_factory=list)


class NodeUpdate(StrictModel):
    node_id: str
    target_mastery: float | None = Field(default=None, ge=0.0, le=1.0)
    resource_hint: str | None = None
    highlight: Literal["normal", "exam_hot", "remedial"] | None = None


class AdjustmentDiff(StrictModel):
    node_id: str
    action: Literal["skip", "normal", "reinforce", "remedial"]
    skip_node_ids: list[str] = Field(default_factory=list)
    updates: list[NodeUpdate] = Field(default_factory=list)
    inserted_nodes: list[NodePlan] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class PlanRequest(StrictModel):
    time_budget: TimeBudget | None = None


class ExpandRequest(StrictModel):
    domain_id: str = Field(min_length=1)
    time_budget: TimeBudget | None = None


class AdjustRequest(StrictModel):
    node_id: str = Field(min_length=1)
    bkt_snapshot: dict[str, MasteryState]


class PlanResponse(StrictModel):
    version: int = Field(ge=1)
    fallback: bool
    suggestion: str
    stages: list[StagePlan]
    nodes: list[NodePlan]
    verify_attempts: int = 0
    verification_issues: list[VerificationIssue] = Field(default_factory=list)


class ExpandResponse(StrictModel):
    version: int = Field(ge=1)
    domain_id: str
    fallback: bool
    stages: list[StagePlan]
    nodes: list[NodePlan]
    verify_attempts: int = 0
    verification_issues: list[VerificationIssue] = Field(default_factory=list)


class AdjustResponse(StrictModel):
    version: int = Field(ge=1)
    diff: AdjustmentDiff


class CurrentResponse(StrictModel):
    plan: dict[str, Any]
    states: list[dict[str, Any]]
    suggestions: list[str] = Field(default_factory=list)


class ReorderRequest(StrictModel):
    node_ids: list[str] = Field(min_length=1)


class CustomNodeRequest(StrictModel):
    text: str = Field(min_length=1, max_length=500)


class DeferRequest(StrictModel):
    node_id: str = Field(min_length=1)
    reason: Literal["太难", "暂时不感兴趣", "以后再学", "其他"]
    reason_text: str | None = None

    @model_validator(mode="after")
    def require_other_text(self) -> "DeferRequest":
        if self.reason == "其他" and not (self.reason_text or "").strip():
            raise ValueError("reason_text is required when reason is 其他")
        return self


class CompleteNodeRequest(StrictModel):
    node_id: str = Field(min_length=1)
    bkt_snapshot: dict[str, MasteryState] = Field(default_factory=dict)
