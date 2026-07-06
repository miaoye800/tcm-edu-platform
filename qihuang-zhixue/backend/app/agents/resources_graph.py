"""资源生成组 LangGraph 编排。"""

from __future__ import annotations

import operator
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict, cast

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from app.agents.explain_agent import generate_explain_resource
from app.agents.graph_agent import generate_graph_resource
from app.agents.mindmap_agent import generate_mindmap_resource
from app.agents.quiz_agent import generate_quiz_resource
from app.agents.reading_agent import generate_reading_resource
from app.agents.resource_context import ResourceDoc, ResourceWhitelist, build_whitelist, retrieve_resource_docs
from app.agents.resource_schemas import ResourceEnvelope, ResourceType, TraceItem
from app.agents.verify_agent import verify_resource_agent
from app.llm.router import ModelRouter

ResourceIntent = Literal["resource_generation", "other"]
DEFAULT_RESOURCE_TYPES: list[ResourceType] = ["explain", "graph", "mindmap", "quiz", "reading"]
RESOURCE_GRAPH_PATH = Path(__file__).resolve().parent / "graph.mmd"


def merge_resources(
    left: dict[ResourceType, ResourceEnvelope] | None,
    right: dict[ResourceType, ResourceEnvelope] | None,
) -> dict[ResourceType, ResourceEnvelope]:
    """按资源 type 合并并发分支产物。"""

    merged: dict[ResourceType, ResourceEnvelope] = {}
    if left:
        merged.update(left)
    if right:
        merged.update(right)
    return merged


class ResourceState(TypedDict):
    topic: str
    profile: dict[str, Any]
    resource_types: list[ResourceType]
    intent: ResourceIntent | None
    retrieved_docs: list[ResourceDoc]
    whitelist: ResourceWhitelist
    resources: Annotated[dict[ResourceType, ResourceEnvelope], merge_resources]
    trace: Annotated[list[TraceItem], operator.add]


async def router_node(state: ResourceState) -> dict[str, Any]:
    router = ModelRouter()
    intent = await router.classify(f"生成资源：{state['topic']}")
    resource_intent: ResourceIntent = "resource_generation" if intent == "resource_generation" else "other"
    return {
        "intent": resource_intent,
        "trace": [TraceItem(node="router", summary=f"意图分类为 {resource_intent}")],
    }


async def retrieve_node(state: ResourceState) -> dict[str, Any]:
    docs = await retrieve_resource_docs(state["topic"], top_k=5)
    whitelist = build_whitelist(docs)
    names = "、".join(doc["name"] for doc in docs) or "无命中"
    return {
        "retrieved_docs": docs,
        "whitelist": whitelist,
        "trace": [TraceItem(node="retrieve_agent", summary=f"检索命中 {names}")],
    }


async def dispatch_node(state: ResourceState) -> dict[str, Any]:
    types = _normalize_resource_types(state.get("resource_types", []))
    return {
        "resource_types": types,
        "trace": [TraceItem(node="dispatch", summary=f"fan-out 到 {','.join(types)}")],
    }


async def explain_node(state: ResourceState) -> dict[str, Any]:
    resource = await generate_explain_resource(
        topic=state["topic"],
        retrieved_docs=state["retrieved_docs"],
        profile=state["profile"],
    )
    return {
        "resources": {"explain": resource},
        "trace": [TraceItem(node="explain_agent", summary="生成结构化讲解")],
    }


async def graph_node(state: ResourceState) -> dict[str, Any]:
    resource = await generate_graph_resource(
        topic=state["topic"],
        retrieved_docs=state["retrieved_docs"],
        profile=state["profile"],
    )
    return {
        "resources": {"graph": resource},
        "trace": [TraceItem(node="graph_agent", summary="生成 ECharts Graph JSON")],
    }


async def mindmap_node(state: ResourceState) -> dict[str, Any]:
    resource = await generate_mindmap_resource(
        topic=state["topic"],
        retrieved_docs=state["retrieved_docs"],
        profile=state["profile"],
    )
    return {
        "resources": {"mindmap": resource},
        "trace": [TraceItem(node="mindmap_agent", summary="生成 markmap Markdown")],
    }


async def quiz_node(state: ResourceState) -> dict[str, Any]:
    resource = await generate_quiz_resource(
        topic=state["topic"],
        retrieved_docs=state["retrieved_docs"],
        profile=state["profile"],
    )
    return {
        "resources": {"quiz": resource},
        "trace": [TraceItem(node="quiz_agent", summary="生成测验题数组")],
    }


async def reading_node(state: ResourceState) -> dict[str, Any]:
    resource = await generate_reading_resource(
        topic=state["topic"],
        retrieved_docs=state["retrieved_docs"],
        profile=state["profile"],
    )
    return {
        "resources": {"reading": resource},
        "trace": [TraceItem(node="reading_agent", summary="生成延伸阅读")],
    }


async def verify_node(state: ResourceState) -> dict[str, Any]:
    verified: dict[ResourceType, ResourceEnvelope] = {}
    for resource_type, resource in state["resources"].items():
        verified[resource_type] = await verify_resource_agent(
            resource=resource,
            docs=state["retrieved_docs"],
            whitelist=state["whitelist"],
        )
    return {
        "resources": verified,
        "trace": [TraceItem(node="verify_agent", summary=f"完成 {len(verified)} 类资源证据校验")],
    }


async def other_node(state: ResourceState) -> dict[str, Any]:
    return {
        "trace": [TraceItem(node="other", summary="非资源生成意图，未生成资源")],
    }


def build_resource_graph() -> Any:
    workflow = StateGraph(ResourceState)
    workflow.add_node("router", router_node)
    workflow.add_node("retrieve_agent", retrieve_node)
    workflow.add_node("dispatch", dispatch_node)
    workflow.add_node("explain_agent", explain_node)
    workflow.add_node("graph_agent", graph_node)
    workflow.add_node("mindmap_agent", mindmap_node)
    workflow.add_node("quiz_agent", quiz_node)
    workflow.add_node("reading_agent", reading_node)
    workflow.add_node("verify_agent", verify_node)
    workflow.add_node("other", other_node)

    workflow.set_entry_point("router")
    workflow.add_conditional_edges(
        "router",
        _route_by_intent,
        {
            "resource_generation": "retrieve_agent",
            "other": "other",
        },
    )
    workflow.add_edge("retrieve_agent", "dispatch")
    workflow.add_conditional_edges(
        "dispatch",
        _dispatch_resource_agents,
        ["explain_agent", "graph_agent", "mindmap_agent", "quiz_agent", "reading_agent"],
    )
    workflow.add_edge("explain_agent", "verify_agent")
    workflow.add_edge("graph_agent", "verify_agent")
    workflow.add_edge("mindmap_agent", "verify_agent")
    workflow.add_edge("quiz_agent", "verify_agent")
    workflow.add_edge("reading_agent", "verify_agent")
    workflow.add_edge("verify_agent", END)
    workflow.add_edge("other", END)
    return workflow.compile()


async def run_resource_generation(
    topic: str,
    resource_types: list[ResourceType] | None = None,
    profile: dict[str, Any] | None = None,
    max_concurrency: int = 5,
) -> ResourceState:
    app = build_resource_graph()
    result = await app.ainvoke(
        _initial_state(topic=topic, resource_types=resource_types, profile=profile),
        config={"max_concurrency": max_concurrency},
    )
    return cast(ResourceState, result)


def export_resource_mermaid(path: Path | None = None) -> Path:
    output_path = path or RESOURCE_GRAPH_PATH
    mermaid = build_resource_graph().get_graph().draw_mermaid()
    try:
        output_path.write_text(mermaid, encoding="utf-8")
    except PermissionError:
        if not output_path.exists():
            raise
    return output_path


def _initial_state(
    *,
    topic: str,
    resource_types: list[ResourceType] | None,
    profile: dict[str, Any] | None,
) -> ResourceState:
    return {
        "topic": topic,
        "profile": dict(profile or {}),
        "resource_types": _normalize_resource_types(resource_types or DEFAULT_RESOURCE_TYPES),
        "intent": None,
        "retrieved_docs": [],
        "whitelist": {"herbs": set(), "herb_ids": set(), "syndromes": set(), "formulas": set()},
        "resources": {},
        "trace": [],
    }


def _route_by_intent(state: ResourceState) -> ResourceIntent:
    return state["intent"] or "other"


def _dispatch_resource_agents(state: ResourceState) -> list[Send]:
    node_by_type: dict[ResourceType, str] = {
        "explain": "explain_agent",
        "graph": "graph_agent",
        "mindmap": "mindmap_agent",
        "quiz": "quiz_agent",
        "reading": "reading_agent",
    }
    return [
        Send(
            node_by_type[resource_type],
            {
                "topic": state["topic"],
                "profile": state["profile"],
                "resource_types": state["resource_types"],
                "intent": state["intent"],
                "retrieved_docs": state["retrieved_docs"],
                "whitelist": state["whitelist"],
                "resources": {},
                "trace": [],
            },
        )
        for resource_type in _normalize_resource_types(state["resource_types"])
    ]


def _normalize_resource_types(resource_types: list[ResourceType]) -> list[ResourceType]:
    seen: set[ResourceType] = set()
    normalized: list[ResourceType] = []
    for resource_type in resource_types:
        if resource_type in DEFAULT_RESOURCE_TYPES and resource_type not in seen:
            seen.add(resource_type)
            normalized.append(resource_type)
    return normalized or list(DEFAULT_RESOURCE_TYPES)
