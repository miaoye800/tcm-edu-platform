"""Profile-aware immersive dialogue with validated LLM output and safe fallback."""

from __future__ import annotations

import json
import random
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.immersive.constants import (
    HIGH_FUN_PREFERENCE,
    IMMERSIVE_SESSION_PREFIX,
    MAX_HISTORY_MESSAGES,
    MAX_RECOMMENDATIONS,
    MIN_PROFILE_CONFIDENCE,
    RESOURCE_PROFILE_LABELS,
)
from app.immersive.prompts import CHIP_RULES, FALLBACK_RULES, OPENING_TEMPLATES, build_chat_messages, repair_message
from app.immersive.schemas import Recommendation, RecommendationOutput
from app.immersive import storage
from app.llm.clients import ChatMessage
from app.llm.router import ModelRouter
from app.path.store import list_nodes
from app.profile import store as profile_storage
from app.profile.update import ProfileUpdateEvent, update_profile_from_event
from app.resources import storage as resource_storage

LLMCall = Callable[[list[ChatMessage]], Awaitable[str]]
_BORING_WORDS = ("枯燥", "无聊", "不喜欢枯燥", "太干", "没意思")


def create_session(
    user_id: str,
    node_id: str,
    *,
    db_path: Path,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Create a node-bound session and return an adaptive opening prompt."""

    if not resource_storage.node_exists(node_id, db_path):
        raise LookupError("知识节点不存在")
    profile = profile_storage.get_profile(user_id, db_path=db_path)
    node = load_node_context(node_id, db_path)
    previous = storage.last_opening_template(user_id, db_path)
    candidates = [index for index in range(len(OPENING_TEMPLATES)) if index != previous] or [0]
    selected = (rng or random.SystemRandom()).choice(candidates)
    session_id = f"{IMMERSIVE_SESSION_PREFIX}{uuid4()}"
    storage.create_session(session_id, user_id, node_id, selected, db_path)
    return {
        "session_id": session_id,
        "greeting": OPENING_TEMPLATES[selected].format(**node),
        "chips": opening_chips(profile),
        "node": node,
    }


async def chat_turn(
    user_id: str,
    session_id: str,
    message: str,
    *,
    db_path: Path,
    settings: Settings | None = None,
    llm_call: LLMCall | None = None,
) -> RecommendationOutput:
    """Persist a turn, feed the existing profile event hook, and recommend resources."""

    session = storage.get_session(session_id, user_id, db_path)
    if session is None:
        raise LookupError("沉浸式会话不存在")
    clean_message = message.strip()
    node = load_node_context(str(session["node_id"]), db_path)
    profile = profile_storage.get_profile(user_id, db_path=db_path)
    history = profile_storage.list_chat_messages(user_id, session_id, db_path=db_path)[-MAX_HISTORY_MESSAGES:]
    await record_profile_event(
        user_id,
        ProfileUpdateEvent(type="interaction", payload={"subtype": "chat_message", "topic": node["node_name"], "message": clean_message}),
        db_path=db_path,
        settings=settings,
    )
    result = await recommend(
        message=clean_message,
        node=node,
        profile=profile,
        history=history,
        settings=settings,
        llm_call=llm_call,
    )
    profile_storage.append_chat_messages(
        user_id=user_id,
        session_id=session_id,
        messages=[{"role": "user", "content": clean_message}, {"role": "assistant", "content": result.reply}],
        db_path=db_path,
    )
    return result


async def confirm_resources(
    user_id: str,
    session_id: str,
    resources: list[Recommendation],
    *,
    db_path: Path,
    settings: Settings | None = None,
) -> list[str]:
    """Create existing resource tasks after validating every browser-returned item again."""

    session = storage.get_session(session_id, user_id, db_path)
    if session is None:
        raise LookupError("沉浸式会话不存在")
    if not resource_storage.node_exists(str(session["node_id"]), db_path):
        raise LookupError("知识节点不存在")
    validated = [Recommendation.model_validate(item.model_dump(mode="json")) for item in resources]
    task_ids: list[str] = []
    for item in validated:
        task_id = str(uuid4())
        resource_storage.create_task(task_id, user_id, str(session["node_id"]), item.type, db_path)
        task_ids.append(task_id)
        await record_profile_event(
            user_id,
            ProfileUpdateEvent(
                type="interaction",
                payload={
                    "subtype": "resource_choice",
                    "selected_type": item.type,
                    "resource_preference": RESOURCE_PROFILE_LABELS[item.type],
                    "topic": str(session["node_id"]),
                },
            ),
            db_path=db_path,
            settings=settings,
        )
    return task_ids


async def recommend(
    *,
    message: str,
    node: dict[str, Any],
    profile: Any,
    history: list[dict[str, str]],
    settings: Settings | None = None,
    llm_call: LLMCall | None = None,
) -> RecommendationOutput:
    """Run the LLM once plus one strict repair, then always return the rule fallback."""

    active_settings = settings or get_settings()
    if llm_call is not None or _has_llm_key(active_settings):
        messages = build_chat_messages(
            node=node,
            profile=profile.model_dump(mode="json"),
            history=history,
            message=message,
        )
        call = llm_call or _router_call(active_settings)
        for attempt in range(2):
            try:
                raw = await call(messages if attempt == 0 else [*messages, repair_message()])
                result = RecommendationOutput.model_validate(_json_object(raw))
                _validate_reason_citations(result, profile)
                return result
            except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
                continue
    return fallback_recommendation(message, profile)


def fallback_recommendation(message: str, profile: Any) -> RecommendationOutput:
    """Apply the centralized rule table without citing untrusted dimensions."""

    rule_name, types = _match_fallback_rule(message, profile)
    reasons = _reasons_for_rule(rule_name, profile, message)
    recommendations = [
        Recommendation(type=resource_type, reason=reasons[resource_type], params=_recommendation_params(resource_type))
        for resource_type in types[:MAX_RECOMMENDATIONS]
    ]
    return RecommendationOutput(reply="我按你现在的学习状态配了这组资源，先挑一个开炼。", recommendation=recommendations)


def load_node_context(node_id: str, db_path: Path) -> dict[str, Any]:
    nodes = {str(item["id"]): item for item in list_nodes(db_path)}
    node = nodes.get(node_id)
    if node is None:
        raise LookupError("知识节点不存在")
    parent = nodes.get(str(node.get("parent_id"))) if node.get("parent_id") else None
    grandparent = nodes.get(str(parent.get("parent_id"))) if parent and parent.get("parent_id") else None
    subtype = str(parent["name"]) if parent else (str(node["name"]) if node.get("level") == "subtype" else "本草要点")
    domain = str(grandparent["name"]) if grandparent else (str(parent["name"]) if parent and parent.get("level") == "domain" else "中药学")
    frequency = float(node.get("exam_frequency") or 0)
    exam_hint = "，也是高频考点" if frequency >= 0.7 else ("，考试中偶有出现" if frequency >= 0.3 else "")
    return {
        "node_id": node_id,
        "node_name": str(node["name"]),
        "subtype": subtype,
        "domain": domain,
        "exam_frequency": frequency,
        "exam_hint": exam_hint,
    }


def opening_chips(profile: Any) -> list[str]:
    trusted_goal = _trusted_label(profile, "learning_goal") == "应试提分"
    trusted_visual = _trusted_label(profile, "learning_style") == "视觉型"
    high_fun = _trusted_fun(profile) is not None and _trusted_fun(profile) > HIGH_FUN_PREFERENCE
    selected: list[str] = []
    for condition, label in CHIP_RULES:
        if condition == "high_fun" and high_fun:
            selected.append(label)
        elif condition == "exam" and trusted_goal:
            selected.append(label)
        elif condition == "visual" and trusted_visual:
            selected.append(label)
        elif condition == "default":
            selected.append(label)
    return selected[:3] or ["先讲核心", "直奔考点"]


def _match_fallback_rule(message: str, profile: Any) -> tuple[str, tuple[str, ...]]:
    boring = any(word in message for word in _BORING_WORDS)
    goal = _trusted_label(profile, "learning_goal")
    resource = _trusted_label(profile, "resource_preference")
    style = _trusted_label(profile, "learning_style")
    fun = _trusted_fun(profile)
    checks = {
        "user_dislikes_boring": boring,
        "trusted_fun_over_0_7": fun is not None and fun > HIGH_FUN_PREFERENCE,
        "trusted_exam_goal": goal == "应试提分",
        "trusted_graph_preference": resource == "知识图谱",
        "trusted_visual_style": style == "视觉型",
        "conservative_default": True,
    }
    for rule in FALLBACK_RULES:
        if checks[rule["when"]]:
            return str(rule["name"]), tuple(rule["types"])
    raise AssertionError("fallback rules must include a default")


def _reasons_for_rule(rule_name: str, profile: Any, message: str) -> dict[str, str]:
    fun = _trusted_fun(profile)
    if rule_name == "anti_boring":
        suffix = f"趣味偏好{fun:.2f}（资源偏好维度）" if fun is not None else "画像维度置信度不足0.5，采用保守默认"
        return {"graph": f"你提到不喜欢枯燥；{suffix}→先用图谱建立脉络", "story": f"你提到不喜欢枯燥；{suffix}→提供收徒剧情入口"}
    if rule_name == "high_fun":
        return {"graph": f"趣味偏好{fun:.2f}（资源偏好维度）→图谱更容易进入状态", "story": f"趣味偏好{fun:.2f}（资源偏好维度）→优先收徒剧情入口"}
    if rule_name == "exam_goal":
        return {"quiz": "学习目标=应试提分（learning_goal 维度）→先用题目定位考点", "doc": "学习目标=应试提分（learning_goal 维度）→配套精炼讲义"}
    if rule_name == "graph_preference":
        return {"graph": "资源偏好=知识图谱（resource_preference 维度）→先看关系网络", "mindmap": "资源偏好=知识图谱（resource_preference 维度）→再用导图收束层级"}
    if rule_name == "visual_style":
        return {"graph": "学习风格=视觉型（learning_style 维度）→先用图谱定位关联", "mindmap": "学习风格=视觉型（learning_style 维度）→再以导图整理记忆"}
    return {"doc": "画像维度置信度不足0.5，采用保守默认讲义", "graph": "画像维度置信度不足0.5，采用保守默认图谱"}


def _trusted_label(profile: Any, dimension: str) -> str | None:
    value = getattr(profile, dimension)
    return str(value.label) if value.confidence >= MIN_PROFILE_CONFIDENCE and value.label else None


def _recommendation_params(resource_type: str) -> dict[str, str]:
    return {"entry": "story_stage" if resource_type == "story" else "immersive"}


def _trusted_fun(profile: Any) -> float | None:
    # fun_preference is a planning parameter; resource-preference confidence is
    # the evidence gate that allows it to appear in user-facing reasons.
    if profile.resource_preference.confidence < MIN_PROFILE_CONFIDENCE:
        return None
    return float(profile.planning_params.fun_preference)


def _has_llm_key(settings: Settings) -> bool:
    return bool((settings.deepseek_api_key or "").strip() or (settings.zhipu_api_key or "").strip())


def _validate_reason_citations(result: RecommendationOutput, profile: Any) -> None:
    """Reject LLM reasons that do not identify a trusted profile input."""

    markers = {
        "learning_goal": ("learning_goal", "学习目标"),
        "learning_style": ("learning_style", "学习风格"),
        "resource_preference": ("resource_preference", "资源偏好", "趣味偏好"),
    }
    for item in result.recommendation:
        cited = [dimension for dimension, values in markers.items() if any(value in item.reason for value in values)]
        if not cited and "考核严格度" not in item.reason and "画像维度置信度不足" not in item.reason:
            raise ValueError("recommendation reason must cite a profile dimension")
        if any(getattr(profile, dimension).confidence < MIN_PROFILE_CONFIDENCE for dimension in cited):
            raise ValueError("recommendation reason cites a low-confidence profile dimension")


def _router_call(settings: Settings) -> LLMCall:
    async def call(messages: list[ChatMessage]) -> str:
        return await ModelRouter(settings=settings).chat("generate", messages, response_format={"type": "json_object"}, temperature=0.2)

    return call


def _json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("LLM response must contain a JSON object")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object")
    return parsed


async def record_profile_event(user_id: str, event: ProfileUpdateEvent, *, db_path: Path, settings: Settings | None) -> None:
    try:
        await update_profile_from_event(user_id, event, db_path=db_path, settings=settings or get_settings())
    except Exception:
        return
