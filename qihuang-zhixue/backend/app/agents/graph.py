"""基于 LangGraph 的中药学学习智能体最小闭环。"""

from __future__ import annotations

import asyncio
import re
import warnings
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)

from langgraph.graph import END, StateGraph

from app.kb.ingest import load_herbs
from app.kb.retriever import SearchHit, search
from app.llm.clients import ChatMessage, MockClient
from app.llm.router import ModelRouter
from app.profile.agent import run_cold_start
from app.profile.update import ProfileUpdateEvent, get_latest_profile, profile_to_response, update_profile_from_event
from app.path.service import create_l1_plan

Intent = Literal["profile_setup", "knowledge_qa", "resource_generation", "learning_path", "other"]
GRAPH_DIR = Path(__file__).resolve().parent
MAIN_GRAPH_PATH = GRAPH_DIR / "graph_main.mmd"
LEGACY_GRAPH_PATH = GRAPH_DIR / "graph.mmd"
NO_KB_HIT_ANSWER = "知识库暂未覆盖该内容，暂不能基于当前中药知识库给出可靠讲解。\n\nsources: []"


class AgentTraceItem(dict[str, str]):
    """结构化 trace 条目，同时兼容旧测试里的 startswith 断言。"""

    def __init__(self, node: str, summary: str) -> None:
        super().__init__(node=node, summary=summary)

    def startswith(self, prefix: str) -> bool:
        return str(self).startswith(prefix)

    def __str__(self) -> str:
        return f"{self['node']}: {self['summary']}"


class State(TypedDict):
    user_id: str
    session_id: str | None
    profile_cold_start: bool
    messages: list[ChatMessage]
    intent: Intent | None
    profile: dict[str, Any]
    retrieved_docs: list[dict[str, Any]]
    skill_tree: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
    reviews: dict[str, Any]
    answer: str
    trace: list[AgentTraceItem]


class AgentStreamEvent(TypedDict):
    event: Literal["node_start", "token", "done"]
    data: dict[str, Any]


async def router_node(state: State) -> State:
    router = ModelRouter()
    intent = await router.classify(state["messages"])
    next_state = _copy_state(state)
    next_state["intent"] = intent
    _append_trace(next_state, "router", f"识别意图为 {intent}")
    return next_state


async def profile_agent(state: State) -> State:
    question = _last_user_content(state["messages"])
    next_state = _copy_state(state)

    if state["profile_cold_start"] or _is_active_profile_build(question):
        profile_state = await run_cold_start(user_id=state["user_id"], first_message=question)
        next_state["profile"] = dict(profile_state["profile"])
        next_state["answer"] = "已根据首轮对话和诊断小测生成 7 维学生画像。"
        _append_trace(next_state, "profile_agent", "触发首登/主动构建冷启动")
        for item in profile_state["trace"]:
            node = item.get("node", "profile_subgraph")
            summary = item.get("summary", "")
            _append_trace(next_state, f"profile_subgraph.{node}", summary)
        return next_state

    if _is_profile_read(question):
        profile = await get_latest_profile(state["user_id"])
        next_state["profile"] = profile_to_response(profile)
        next_state["answer"] = "已读取最新学生画像，并在主动查看时完成必要复评。"
        _append_trace(next_state, "profile_agent", "读取最新画像")
        return next_state

    result = await update_profile_from_event(
        state["user_id"],
        ProfileUpdateEvent(type="interaction", payload=_profile_interaction_payload(question)),
    )
    next_state["profile"] = profile_to_response(result.profile)
    next_state["answer"] = "已把本次交互作为画像线索增量回写，未达到阈值的维度只走规则融合。"
    _append_trace(next_state, "profile_agent", f"passive 增量更新：{','.join(result.affected_dimensions) or '无'}")
    return next_state


async def rag_agent(state: State) -> State:
    question = _last_user_content(state["messages"])
    hits = await search(question, top_k=3)

    hits = _prioritize_exact_herb(question, hits)
    docs = _hits_to_docs(hits)
    next_state = _copy_state(state)
    next_state["retrieved_docs"] = docs
    names = "、".join(doc["name"] for doc in docs) or "无结果"
    _append_trace(next_state, "rag_agent", f"检索到 {names}")
    return next_state


async def explain_agent(state: State) -> State:
    question = _last_user_content(state["messages"])
    docs = state["retrieved_docs"]
    llm_draft = "" if not docs else await _safe_generate(question, docs)
    answer = _compose_answer(question=question, docs=docs, llm_draft=llm_draft)

    next_state = _copy_state(state)
    next_state["answer"] = answer
    _append_trace(next_state, "explain_agent", "生成讲解并附 sources 引用")
    return next_state


async def path_agent(state: State) -> State:
    path_result = await create_l1_plan(user_id=state["user_id"])
    next_state = _copy_state(state)
    next_state["skill_tree"] = [stage.model_dump(mode="json") for stage in path_result.stages]
    next_state["recommendations"] = []
    next_state["reviews"] = {}
    next_state["answer"] = path_result.suggestion
    _append_trace(next_state, "path_agent", f"调用新 PathAgent L1，生成版本 v{path_result.version}")
    return next_state


def build_graph() -> Any:
    workflow = StateGraph(State)
    workflow.add_node("router", router_node)
    workflow.add_node("profile_agent", profile_agent)
    workflow.add_node("rag_agent", rag_agent)
    workflow.add_node("explain_agent", explain_agent)
    workflow.add_node("path_agent", path_agent)

    workflow.set_entry_point("router")
    workflow.add_conditional_edges(
        "router",
        _route_by_intent,
        {
            "profile_setup": "profile_agent",
            "knowledge_qa": "rag_agent",
            "resource_generation": "rag_agent",
            "learning_path": "path_agent",
            "other": "explain_agent",
        },
    )
    workflow.add_edge("profile_agent", END)
    workflow.add_edge("rag_agent", "explain_agent")
    workflow.add_edge("explain_agent", END)
    workflow.add_edge("path_agent", END)
    return workflow.compile()


async def run_agent(
    question: str,
    user_id: str = "anonymous",
    session_id: str | None = None,
    history_messages: list[ChatMessage] | None = None,
    profile_cold_start: bool = False,
) -> State:
    app = build_graph()
    result = await app.ainvoke(
        _initial_state(
            question,
            user_id=user_id,
            session_id=session_id,
            history_messages=history_messages,
            profile_cold_start=profile_cold_start,
        )
    )
    return cast(State, result)


async def stream_agent_events(
    question: str,
    user_id: str = "anonymous",
    session_id: str | None = None,
    history_messages: list[ChatMessage] | None = None,
    profile_cold_start: bool = False,
) -> AsyncIterator[AgentStreamEvent]:
    """以可映射到 SSE 的事件流运行 LangGraph 问答链路。"""

    app = build_graph()
    state = _initial_state(
        question,
        user_id=user_id,
        session_id=session_id,
        history_messages=history_messages,
        profile_cold_start=profile_cold_start,
    )
    yielded_starts: set[str] = set()

    yield _node_start_event("router")
    yielded_starts.add("router")

    try:
        async for update in app.astream(state, stream_mode="updates"):
            if not isinstance(update, dict):
                continue

            node_name, node_state = _first_node_update(update)
            if node_name == "router" and isinstance(node_state, dict):
                next_node = _next_node_after_router(cast(State, node_state))
                if next_node and next_node not in yielded_starts:
                    yield _node_start_event(next_node)
                    yielded_starts.add(next_node)
            elif node_name == "rag_agent" and "explain_agent" not in yielded_starts:
                yield _node_start_event("explain_agent")
                yielded_starts.add("explain_agent")

            if isinstance(node_state, dict):
                state = cast(State, node_state)
    except Exception as exc:
        state["answer"] = f"检索知识库时遇到异常：{exc.__class__.__name__}。请确认本地知识库可写，或稍后重试。"
        state["retrieved_docs"] = []

    token_delay = _mock_token_delay("generate")
    for token in state["answer"]:
        yield {"event": "token", "data": {"text": token}}
        if token_delay > 0:
            await asyncio.sleep(token_delay)

    yield {"event": "done", "data": {"sources": _extract_sources(state["retrieved_docs"])}}


def export_mermaid(path: Path | None = None) -> Path:
    mermaid = build_graph().get_graph().draw_mermaid()
    if path is not None:
        path.write_text(mermaid, encoding="utf-8")
        return path

    MAIN_GRAPH_PATH.write_text(mermaid, encoding="utf-8")
    LEGACY_GRAPH_PATH.write_text(mermaid, encoding="utf-8")
    return MAIN_GRAPH_PATH


def _initial_state(
    question: str,
    user_id: str = "anonymous",
    session_id: str | None = None,
    history_messages: list[ChatMessage] | None = None,
    profile_cold_start: bool = False,
) -> State:
    messages = [dict(message) for message in history_messages or []]
    messages.append({"role": "user", "content": question})
    return {
        "user_id": user_id,
        "session_id": session_id,
        "profile_cold_start": profile_cold_start,
        "messages": messages,
        "intent": None,
        "profile": {},
        "retrieved_docs": [],
        "skill_tree": [],
        "recommendations": [],
        "reviews": {"items": [], "suggested_count": 0},
        "answer": "",
        "trace": [],
    }


def _node_start_event(node_name: str) -> AgentStreamEvent:
    return {"event": "node_start", "data": {"node": node_name}}


def _first_node_update(update: dict[str, Any]) -> tuple[str, Any]:
    for node_name, node_state in update.items():
        return str(node_name), node_state
    return "", {}


def _next_node_after_router(state: State) -> str:
    if state["intent"] == "profile_setup":
        return "profile_agent"
    if state["intent"] in {"knowledge_qa", "resource_generation"}:
        return "rag_agent"
    if state["intent"] == "learning_path":
        return "path_agent"
    return "explain_agent"


def _extract_sources(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for doc in docs:
        for source in doc.get("sources", []):
            if not isinstance(source, dict):
                continue
            source_type = str(source.get("type", "")).strip()
            ref = str(source.get("ref", "")).strip()
            key = (source_type, ref)
            if not ref or key in seen:
                continue
            seen.add(key)
            sources.append({"name": doc.get("name", ""), "type": source_type, "ref": ref})
    return sources


def _mock_token_delay(task_type: str) -> float:
    client = ModelRouter().select(task_type)
    if isinstance(client, MockClient):
        return client.stream_delay
    return 0.0


def _route_by_intent(state: State) -> Intent:
    return state["intent"] or "other"


def _copy_state(state: State) -> State:
    return {
        "user_id": state["user_id"],
        "session_id": state["session_id"],
        "profile_cold_start": state["profile_cold_start"],
        "messages": list(state["messages"]),
        "intent": state["intent"],
        "profile": dict(state["profile"]),
        "retrieved_docs": [dict(doc) for doc in state["retrieved_docs"]],
        "skill_tree": [dict(node) for node in state["skill_tree"]],
        "recommendations": [dict(item) for item in state["recommendations"]],
        "reviews": dict(state["reviews"]),
        "answer": state["answer"],
        "trace": list(state["trace"]),
    }


def _append_trace(state: State, node_name: str, summary: str) -> None:
    state["trace"].append(AgentTraceItem(node_name, summary))


def _is_active_profile_build(text: str) -> bool:
    return _contains_any(text, ("建立画像", "构建画像", "冷启动", "首登", "初始化画像", "焕醒画像"))


def _is_profile_read(text: str) -> bool:
    return _contains_any(text, ("查看画像", "我的画像", "当前画像", "画像详情", "读取画像"))


def _profile_interaction_payload(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"topic": text}
    if _contains_any(text, ("考试", "期末", "考证")):
        payload["learning_goal"] = "应试提分"
    elif _contains_any(text, ("临床", "辨证", "坐诊")):
        payload["learning_goal"] = "临床实用"
    elif _contains_any(text, ("兴趣", "喜欢")):
        payload["learning_goal"] = "兴趣科普"

    if _contains_any(text, ("图谱", "图表", "画图")):
        payload["learning_style"] = "视觉型"
        payload["resource_preference"] = "知识图谱"
    elif _contains_any(text, ("口诀", "歌诀")):
        payload["learning_style"] = "记忆(口诀)型"
        payload["resource_preference"] = "汤头歌诀"
    elif _contains_any(text, ("医案", "案例")):
        payload["resource_preference"] = "医案文选"

    minutes = _minutes_from_text(text)
    if minutes is not None:
        payload["daily_minutes"] = minutes
    return payload


def _minutes_from_text(text: str) -> int | None:
    if "半小时" in text:
        return 30
    if "一小时" in text or "1小时" in text:
        return 60
    match = re.search(r"(\d{1,3})\s*(?:分钟|min)", text, flags=re.I)
    return int(match.group(1)) if match else None


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _last_user_content(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    if messages:
        return str(messages[-1].get("content", ""))
    return ""


def _hits_to_docs(hits: list[SearchHit]) -> list[dict[str, Any]]:
    herbs_by_name = {herb.name: herb for herb in load_herbs()}
    docs: list[dict[str, Any]] = []
    for hit in hits:
        herb = herbs_by_name.get(hit.name)
        docs.append(
            {
                "name": hit.name,
                "score": hit.score,
                "effects": list(herb.effects) if herb else [],
                "indications": [item.syndrome for item in herb.indications] if herb else [],
                "sources": hit.sources,
            }
        )
    return docs


def _prioritize_exact_herb(question: str, hits: list[SearchHit]) -> list[SearchHit]:
    herbs_by_name = {herb.name: herb for herb in load_herbs()}
    exact_names = [name for name in herbs_by_name if name and name in question]
    if not exact_names:
        return hits

    hit_by_name = {hit.name: hit for hit in hits}
    prioritized: list[SearchHit] = []
    for name in exact_names:
        hit = hit_by_name.get(name)
        if hit is None:
            herb = herbs_by_name[name]
            hit = SearchHit(
                name=herb.name,
                score=1.0,
                verified=herb.verified,
                sources=[source.model_dump(mode="json") for source in herb.sources],
            )
        prioritized.append(hit)

    prioritized_names = {hit.name for hit in prioritized}
    prioritized.extend(hit for hit in hits if hit.name not in prioritized_names)
    return prioritized[:3]


async def _safe_generate(question: str, docs: list[dict[str, Any]]) -> str:
    router = ModelRouter()
    context = "\n".join(
        f"{doc['name']}：功效={doc['effects']}；主治={doc['indications']}" for doc in docs
    )
    try:
        return await router.chat(
            "generate",
            [
                {"role": "system", "content": "你是中药学助教，请基于检索结果生成简明讲解。"},
                {"role": "user", "content": f"问题：{question}\n检索结果：\n{context}"},
            ],
        )
    except NotImplementedError:
        return ""


def _compose_answer(question: str, docs: list[dict[str, Any]], llm_draft: str) -> str:
    if not docs:
        return NO_KB_HIT_ANSWER

    main_doc = docs[0]
    effects = "、".join(str(item) for item in main_doc["effects"]) or "资料中未标注功效"
    indications = "、".join(str(item) for item in main_doc["indications"][:3]) or "资料中未标注主治"
    body = (
        f"{main_doc['name']}的核心功效是：{effects}。\n"
        f"常见主治包括：{indications}。"
    )
    if llm_draft and not llm_draft.startswith("[MOCK:"):
        body = f"{llm_draft.strip()}\n\n{body}"

    source_lines = _format_sources([main_doc])
    return f"{body}\n\nsources:\n{source_lines}"


def _format_sources(docs: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, doc in enumerate(docs, start=1):
        refs = [
            str(source.get("ref", "")).strip()
            for source in doc.get("sources", [])
            if isinstance(source, dict) and source.get("ref")
        ]
        ref_text = "；".join(refs) if refs else "本地中药学知识库"
        lines.append(f"[{index}] {doc['name']}：{ref_text}")
    return "\n".join(lines)
