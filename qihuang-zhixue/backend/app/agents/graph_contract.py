"""Stable persisted contract for the GraphAgent content layer."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RelationType(StrEnum):
    """The only vocabulary accepted in herb_relation and graph resources."""

    EFFECT_SIMILAR = "功效相通"
    XIANG_XU = "相须"
    XIANG_SHI = "相使"
    XIANG_WEI = "相畏"
    XIANG_SHA = "相杀"
    XIANG_WU = "相恶"
    XIANG_FAN = "相反"
    NATURE_FLAVOR_SIMILAR = "性味相通"
    NATURE_FLAVOR_OPPOSITE = "性味相反"
    MERIDIAN_CLUSTER = "归经聚类"
    HERB_SYNDROME = "药证链"
    SYNDROME_FORMULA = "证方链"
    COMPOSITION = "组成"
    DIFFERENTIATION = "同类鉴别"


class GraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphSource(GraphModel):
    出处: str = Field(min_length=1)
    原文摘录: str = Field(min_length=1, max_length=180)
    定位: str = Field(min_length=1)


class GraphRelationDraft(GraphModel):
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    relation_type: RelationType
    label: str = Field(min_length=1, max_length=80)
    sources: list[GraphSource] = Field(min_length=1)
    confidence: float = Field(default=0.8, ge=0, le=1)


class GraphNodeContent(GraphModel):
    id: str
    name: str
    type: Literal["herb", "syndrome", "formula", "category"]
    brief: str


class GraphEdgeContent(GraphModel):
    source: str
    target: str
    relation_type: RelationType
    label: str
    source_ref: int = Field(ge=0)


class GraphContent(GraphModel):
    """Do not rename these fields: this is the session-2 frontend contract."""

    center_id: str
    nodes: list[GraphNodeContent]
    edges: list[GraphEdgeContent]
    sources: list[GraphSource]

    @model_validator(mode="after")
    def ensure_edges_are_sourced(self) -> "GraphContent":
        if any(edge.source_ref >= len(self.sources) for edge in self.edges):
            raise ValueError("每条图谱边必须引用 sources 中的溯源项")
        return self
