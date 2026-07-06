"""ProfileAgent orchestration entry points."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, cast

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)

from langgraph.graph import END, StateGraph

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
from app.profile.schema import Profile
from app.profile.store import DB_PATH, add_evidence, get_profile, upsert_profile


async def noop_node(state: ProfileState) -> ProfileState:
    """Safe terminal node for non-profile intents."""

    next_state = copy_state(state)
    append_trace(next_state, "profile_noop_agent", "current input does not trigger profile cold start")
    return next_state


async def persist_profile_node(state: ProfileState) -> ProfileState:
    """Persist the cold-start profile and evidence cards to the new profile store."""

    next_state = copy_state(state)
    if next_state["profile"]:
        profile = Profile.model_validate(next_state["profile"])
        db_path = Path(next_state["db_path"]) if next_state["db_path"] else DB_PATH
        for card in next_state["evidence_cards"]:
            add_evidence(profile.user_id, card, db_path=db_path)
        upsert_profile(profile, db_path=db_path)
        append_trace(
            next_state,
            "profile_persist_agent",
            f"profile and {len(next_state['evidence_cards'])} evidence cards persisted to SQLite",
        )
    return next_state


def build_profile_graph() -> Any:
    """Build the ProfileAgent LangGraph router graph."""

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
) -> Profile:
    """Run a cold-start build and return the new-stack Profile snapshot."""

    graph = build_profile_graph()
    result = await graph.ainvoke(initial_state(user_id, first_message, diagnostic_answers, _db_path_text(db_path)))
    state = cast(ProfileState, result)
    if not state["profile"]:
        return get_profile(user_id, db_path=db_path or DB_PATH)
    return Profile.model_validate(state["profile"])


async def run_cold_start(
    user_id: str,
    first_message: str,
    diagnostic_answers: dict[str, str] | None = None,
    db_path: Path | None = None,
) -> ProfileState:
    """Run the cold-start graph and return the full state for tests/API callers."""

    graph = build_profile_graph()
    result = await graph.ainvoke(initial_state(user_id, first_message, diagnostic_answers, _db_path_text(db_path)))
    return cast(ProfileState, result)


def export_mermaid(path: Path | None = None) -> Path:
    """Export the ProfileAgent LangGraph topology."""

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
]
