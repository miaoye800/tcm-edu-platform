"""GoalAgent LangGraph-compatible node for planning parameter extraction."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from pydantic import BaseModel, Field, ValidationError

from app.llm.clients import ChatMessage
from app.llm.router import ModelRouter
from app.profile.schema import Goal, PlanningParams, Profile, derive_planning_params


class GoalAgentState(TypedDict, total=False):
    goal_raw_text: str
    profile_summary: dict[str, Any]
    goal_result: dict[str, Any]


class GoalAgentResult(BaseModel):
    planning_params: PlanningParams
    goal: Goal
    restatement: str = Field(min_length=1)


LLMCall = Callable[[list[ChatMessage]], Awaitable[str]]


async def goal_agent_node(state: GoalAgentState, *, llm_call: LLMCall | None = None) -> GoalAgentState:
    """LangGraph node entry; it does not persist profile data."""

    profile = Profile.model_validate(state["profile_summary"]) if state.get("profile_summary") else None
    result = await run_goal_agent(state.get("goal_raw_text", ""), profile=profile, llm_call=llm_call)
    updated = dict(state)
    updated["goal_result"] = result.model_dump(mode="json")
    return updated


async def run_goal_agent(
    raw_text: str,
    *,
    profile: Profile | None = None,
    llm_call: LLMCall | None = None,
) -> GoalAgentResult:
    """Normalize one stated goal with one guarded LLM retry and safe fallback."""

    raw_text = raw_text.strip()
    baseline = derive_planning_params(profile) if profile is not None else PlanningParams()
    messages = _messages(raw_text, profile, baseline)
    call = llm_call or _router_call
    for attempt in range(2):
        try:
            response = await call(messages if attempt == 0 else messages + [_repair_message()])
            result = _parse_result(response, raw_text)
            return result
        except (ValueError, ValidationError, json.JSONDecodeError, TypeError):
            continue
    return _fallback_result(raw_text)


async def _router_call(messages: list[ChatMessage]) -> str:
    return await ModelRouter().chat("profile_goal", messages, temperature=0, response_format={"type": "json_object"})


def _parse_result(response: str, raw_text: str) -> GoalAgentResult:
    parsed = json.loads(_json_object(response))
    result = GoalAgentResult.model_validate(parsed)
    if not _mentions_time_budget(raw_text) and result.planning_params.time_budget is not None:
        raise ValueError("time budget was not explicitly stated")
    if result.goal.raw_text != raw_text:
        result.goal.raw_text = raw_text
    result.goal.confirmed_by_user = False
    return result


def _json_object(response: str) -> str:
    text = response.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("goal response must be a JSON object")
    return match.group(0)


def _mentions_time_budget(text: str) -> bool:
    return bool(re.search(r"\d+\s*(?:天|日|周|星期|个月|月|分钟|min|小时)", text, flags=re.I) or "半小时" in text)


def _fallback_result(raw_text: str) -> GoalAgentResult:
    normalized = _normalized_goal(raw_text)
    return GoalAgentResult(
        planning_params=PlanningParams(),
        goal=Goal(raw_text=raw_text, normalized=normalized, confirmed_by_user=False, confidence=0.3),
        restatement=f"我理解你的目标是：{raw_text or '按当前画像继续学习'}。",
    )


def _normalized_goal(text: str) -> str:
    if any(word in text for word in ("考研", "考试", "考证", "期末", "应试")):
        return "应试提分"
    if any(word in text for word in ("临床", "门诊", "能用", "实用")):
        return "临床实用"
    if any(word in text for word in ("玩", "兴趣", "科普", "了解")):
        return "兴趣科普"
    if any(word in text for word in ("科研", "论文", "学术", "深造")):
        return "学术深造"
    return "其他"


def _messages(raw_text: str, profile: Profile | None, baseline: PlanningParams) -> list[ChatMessage]:
    summary = profile.model_dump(mode="json") if profile is not None else {}
    system = """Return only JSON with planning_params, goal, restatement. planning_params has depth(浅|中|深), breadth(考点导向|全面|兴趣优先), pace(松|中|紧), fun_preference(0..1), assessment_strictness(宽松|标准|严格), time_budget({total_days,daily_minutes}|null). goal has raw_text, normalized(应试提分|临床实用|兴趣科普|学术深造|其他), confirmed_by_user, confidence. Never invent a time budget; use null unless explicitly stated. Examples: 就想学着玩玩 => 浅/兴趣优先/松/0.9/宽松; 准备考研 => 深/考点导向/紧/0.3/严格; 临床能用就行 => 中/考点导向/中/0.5/标准."""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps({"goal_raw_text": raw_text, "profile_summary": summary, "baseline": baseline.model_dump(mode="json")}, ensure_ascii=False)},
    ]


def _repair_message() -> ChatMessage:
    return {"role": "user", "content": "Previous output failed validation. Return strict JSON only, use allowed enums, and use null time_budget unless explicitly stated."}


__all__ = ["GoalAgentResult", "GoalAgentState", "goal_agent_node", "run_goal_agent"]
