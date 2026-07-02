"""ProfileAgent 编排入口。"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, cast

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)

from langgraph.graph import END, StateGraph

from app.profile.models import ProfileEvent, StudentProfile
from app.profile.nodes import (
    ProfileState,
    append_trace,
    copy_state,
    diagnostic_eval_node,
    diagnostic_question_node,
    dialogue_extract_node,
    initial_state,
    profile_synthesis_node,
    route_by_intent,
    router_node,
)
from app.profile.rules import update_from_event as apply_update_from_event
from app.profile.storage import DB_PATH, get_profile, save_profile, save_session_event


async def noop_node(state: ProfileState) -> ProfileState:
    """非画像意图的安全终点，保持图拓扑完整。"""

    next_state = copy_state(state)
    append_trace(next_state, "profile_noop_agent", "当前输入不触发画像冷启动")
    return next_state


async def persist_profile_node(state: ProfileState) -> ProfileState:
    """保存冷启动画像。"""

    next_state = copy_state(state)
    if next_state["profile"]:
        profile = StudentProfile.model_validate(next_state["profile"])
        db_path = Path(next_state["db_path"]) if next_state["db_path"] else DB_PATH
        save_profile(profile, db_path=db_path)
        append_trace(next_state, "profile_persist_agent", "画像已写入 SQLite")
    return next_state


def build_profile_graph() -> Any:
    """构建 ProfileAgent 的 LangGraph Router 图。"""

    workflow = StateGraph(ProfileState)
    workflow.add_node("profile_router", router_node)
    workflow.add_node("dialogue_extract_agent", dialogue_extract_node)
    workflow.add_node("diagnostic_question_agent", diagnostic_question_node)
    workflow.add_node("diagnostic_eval_agent", diagnostic_eval_node)
    workflow.add_node("profile_synthesis_agent", profile_synthesis_node)
    workflow.add_node("profile_persist_agent", persist_profile_node)
    workflow.add_node("profile_noop_agent", noop_node)

    workflow.set_entry_point("profile_router")
    workflow.add_conditional_edges(
        "profile_router",
        route_by_intent,
        {
            "profile_setup": "dialogue_extract_agent",
            "knowledge_qa": "profile_noop_agent",
            "other": "profile_noop_agent",
        },
    )
    workflow.add_edge("dialogue_extract_agent", "diagnostic_question_agent")
    workflow.add_edge("diagnostic_question_agent", "diagnostic_eval_agent")
    workflow.add_edge("diagnostic_eval_agent", "profile_synthesis_agent")
    workflow.add_edge("profile_synthesis_agent", "profile_persist_agent")
    workflow.add_edge("profile_persist_agent", END)
    workflow.add_edge("profile_noop_agent", END)
    return workflow.compile()


async def cold_start_profile(
    user_id: str,
    first_message: str,
    diagnostic_answers: dict[str, str] | None = None,
    db_path: Path | None = None,
) -> StudentProfile:
    """冷启动构建画像。"""

    graph = build_profile_graph()
    result = await graph.ainvoke(initial_state(user_id, first_message, diagnostic_answers, _db_path_text(db_path)))
    state = cast(ProfileState, result)
    if not state["profile"]:
        return get_profile(user_id, db_path=db_path or DB_PATH)
    return StudentProfile.model_validate(state["profile"])


async def run_cold_start(
    user_id: str,
    first_message: str,
    diagnostic_answers: dict[str, str] | None = None,
    db_path: Path | None = None,
) -> ProfileState:
    """运行冷启动图并返回完整 state，便于测试 trace。"""

    graph = build_profile_graph()
    result = await graph.ainvoke(initial_state(user_id, first_message, diagnostic_answers, _db_path_text(db_path)))
    return cast(ProfileState, result)


def update_from_event(
    profile: StudentProfile,
    event: ProfileEvent | dict[str, object],
    db_path: Path | None = None,
) -> StudentProfile:
    """动态更新画像并记录会话事件。"""

    parsed = event if isinstance(event, ProfileEvent) else ProfileEvent.model_validate(event)
    updated = apply_update_from_event(profile, parsed)
    target_db = db_path or DB_PATH
    save_profile(updated, db_path=target_db)
    save_session_event(parsed, db_path=target_db)
    return updated


def export_mermaid(path: Path | None = None) -> Path:
    """导出 ProfileAgent LangGraph 拓扑。"""

    output_path = path or Path(__file__).with_name("graph_profile.mmd")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_profile_graph().get_graph().draw_mermaid(), encoding="utf-8")
    return output_path


def _db_path_text(db_path: Path | None) -> str | None:
    return str(db_path) if db_path is not None else None


__all__ = [
    "DB_PATH",
    "build_profile_graph",
    "cold_start_profile",
    "export_mermaid",
    "get_profile",
    "run_cold_start",
    "save_profile",
    "update_from_event",
]
