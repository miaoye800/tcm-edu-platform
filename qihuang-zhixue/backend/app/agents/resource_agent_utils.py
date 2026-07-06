"""资源 Agent 的公共小工具。"""

from __future__ import annotations

from typing import Any

from app.agents.resource_cache import get_cached_resource, set_cached_resource
from app.agents.resource_context import ResourceDoc, cache_key_for, collect_source_refs
from app.agents.resource_schemas import (
    GroundingReport,
    ResourceEnvelope,
    ResourcePayload,
    ResourceType,
    SourceRef,
)
from app.agents.structured_output import generated_by


def cached_or_none(topic: str, resource_type: ResourceType, profile: dict[str, Any] | None) -> ResourceEnvelope | None:
    return get_cached_resource(cache_key_for(topic, resource_type, profile))


def build_envelope(
    *,
    topic: str,
    resource_type: ResourceType,
    payload: ResourcePayload,
    docs: list[ResourceDoc],
    profile: dict[str, Any] | None,
    trace_node: str,
) -> ResourceEnvelope:
    envelope = ResourceEnvelope(
        type=resource_type,
        topic=topic,
        payload=payload,
        sources=[SourceRef.model_validate(source) for source in collect_source_refs(docs)],
        grounding=GroundingReport(score=1.0),
        trace_node=trace_node,
        generated_by=generated_by("generate"),
        cache_key=cache_key_for(topic, resource_type, profile),
    )
    set_cached_resource(envelope)
    return envelope


def difficulty_from_profile(profile: dict[str, Any] | None, default: int = 3) -> int:
    """根据学生画像粗调难度，缺省保持中等难度。"""

    if not profile:
        return default
    raw_level = (
        profile.get("level")
        or profile.get("ability_level")
        or profile.get("knowledge_level")
        or profile.get("mastery")
        or profile.get("score")
    )
    if isinstance(raw_level, (int, float)):
        if raw_level <= 2:
            return 2
        if raw_level >= 4:
            return 4
        return 3
    if isinstance(raw_level, str):
        if raw_level in {"薄弱", "初学", "beginner", "low"}:
            return 2
        if raw_level in {"优秀", "进阶", "advanced", "high"}:
            return 4
    return default


def list_text(items: list[str], fallback: str = "知识库暂未覆盖") -> str:
    return "、".join(item for item in items if item) or fallback

