from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MindmapMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_indexes: list[int] = Field(default_factory=list)


class MindmapNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    topic: str = Field(min_length=1, max_length=30)
    direction: int | None = Field(default=None, ge=0, le=1)
    meta: MindmapMeta = Field(default_factory=MindmapMeta)
    children: list["MindmapNode"] = Field(default_factory=list, max_length=12)

    @field_validator("topic")
    @classmethod
    def concise_topic(cls, value: str) -> str:
        value = " ".join(value.strip().split())
        if len(value) > 30:
            raise ValueError("mindmap node topic must be concise")
        return value


class MindElixirData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodeData: MindmapNode


class MindmapContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str
    choice_reason: str = Field(min_length=2, max_length=100)
    mindmap_data: MindElixirData
    sources: list[dict[str, str]] = Field(default_factory=list)


class ExpandRequest(BaseModel):
    node_path: list[str] = Field(min_length=1, max_length=12)


class ExpandResponse(BaseModel):
    node_path: list[str]
    added_nodes: list[MindmapNode]
    mindmap_data: MindElixirData
    idempotent: bool = False


def merge_branch(data: MindElixirData, node_path: list[str], added_nodes: list[MindmapNode]) -> tuple[MindElixirData, bool]:
    current = data.nodeData
    if current.id != node_path[0]:
        raise ValueError("node_path root does not match mindmap")
    for node_id in node_path[1:]:
        current = next((child for child in current.children if child.id == node_id), None)
        if current is None:
            raise ValueError("node_path does not exist")
    existing = {child.id for child in current.children}
    fresh = [node for node in added_nodes if node.id not in existing]
    if not fresh:
        return data, True
    current.children.extend(fresh)
    return data, False
