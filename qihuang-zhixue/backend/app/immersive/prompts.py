"""Centralized copy, prompts, and deterministic recommendation fallback rules."""

from __future__ import annotations

import json
from typing import Any, Final

OPENING_TEMPLATES: Final[tuple[str, ...]] = (
    "这一关是{node_name}，归在{subtype}、{domain}这条脉络里{exam_hint}。按你的口味，想怎么学？",
    "轮到{node_name}了。它属于{subtype}，往上连着{domain}{exam_hint}。先选一种舒服的切入方式吧。",
    "{node_name}这味药已经备好：{subtype}方向，{domain}大类{exam_hint}。这一关你想从哪里起炉？",
)

CHIP_RULES: Final[tuple[tuple[str, str], ...]] = (
    ("high_fun", "轻松点讲"),
    ("exam", "直奔考点"),
    ("visual", "先看图谱"),
    ("default", "先讲核心"),
)

# Rule order is intentional: a direct dislike of dry material takes priority
# over inferred preferences, then trusted profile evidence refines the mix.
FALLBACK_RULES: Final[tuple[dict[str, Any], ...]] = (
    {"name": "anti_boring", "when": "user_dislikes_boring", "types": ("graph", "story")},
    {"name": "high_fun", "when": "trusted_fun_over_0_7", "types": ("graph", "story")},
    {"name": "exam_goal", "when": "trusted_exam_goal", "types": ("quiz", "doc")},
    {"name": "graph_preference", "when": "trusted_graph_preference", "types": ("graph", "mindmap")},
    {"name": "visual_style", "when": "trusted_visual_style", "types": ("graph", "mindmap")},
    {"name": "default", "when": "conservative_default", "types": ("doc", "graph")},
)

RECOMMENDATION_JSON_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reply", "recommendation"],
    "properties": {
        "reply": {"type": "string", "minLength": 1},
        "recommendation": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "reason", "params"],
                "properties": {
                    "type": {"type": "string", "enum": ["doc", "graph", "mindmap", "quiz", "story"]},
                    "reason": {"type": "string", "minLength": 1},
                    "params": {"type": "object"},
                },
            },
        },
    },
}


def build_chat_messages(*, node: dict[str, Any], profile: dict[str, Any], history: list[dict[str, str]], message: str) -> list[dict[str, str]]:
    """Build the only LLM prompt used by this module."""

    system = (
        "You are a concise Chinese medicine learning companion. Return JSON only. "
        "Recommend only doc, graph, mindmap, quiz, story. story is an existing apprentice-story entry, never a new generator. "
        "Every recommendation reason must identify the profile dimension or planning parameter it uses. "
        "Never cite a profile dimension whose confidence is below 0.5. If the user dislikes boring material, prioritize graph and story. "
        f"JSON schema: {json.dumps(RECOMMENDATION_JSON_SCHEMA, ensure_ascii=False)}"
    )
    context = json.dumps({"node": node, "profile": profile}, ensure_ascii=False)
    return [
        {"role": "system", "content": system},
        {"role": "system", "content": f"Context: {context}"},
        *history,
        {"role": "user", "content": message},
    ]


def repair_message() -> dict[str, str]:
    return {
        "role": "user",
        "content": "Previous JSON failed strict validation. Return only one JSON object matching the schema; type must be doc|graph|mindmap|quiz|story.",
    }

