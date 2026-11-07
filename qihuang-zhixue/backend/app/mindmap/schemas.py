from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 依据等级。三档的口径与前端底栏一致：教材实证 / 图谱关系 / 模型推断。
#: 只在 ``model`` 上打角标——满屏徽标等于没有徽标。
Evidence = Literal["textbook", "graph", "model"]


class MindmapMeta(BaseModel):
    # ``extra="forbid"`` 保持不变：证据等级是唯一新增的字段，
    # 其余排版字段（颜色/字体/uid）仍不允许混进存储结构。
    model_config = ConfigDict(extra="forbid")
    source_indexes: list[int] = Field(default_factory=list)
    #: 这个节点的内容从哪来。None = 老数据，按「教材实证」处理。
    evidence: Evidence | None = None


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
    #: 问法判定结果。前端据此在对话里说明「按什么组织」，也方便排查
    #: 「为什么这张图长成这样」——不用再翻日志。
    intent: str = ""
    archetype: str = ""
    structure_hint: str = ""
    #: True = 语料外，整棵树都是模型推断。前端必须明说，不能装作教材。
    degraded: bool = False


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
