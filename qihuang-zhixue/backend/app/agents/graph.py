"""基于 LangGraph 的中药学学习智能体最小闭环。"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import warnings
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)

from langgraph.graph import END, StateGraph

from app.config import get_settings
from app.kb.ingest import load_herbs
from app.kb.retriever import SearchHit, search
from app.llm.clients import ChatMessage, MockClient
from app.llm.router import ModelRouter
from app.profile.agent import run_cold_start
from app.profile.update import ProfileUpdateEvent, get_latest_profile, profile_to_response, update_profile_from_event
from app.path.service import create_l1_plan

logger = logging.getLogger(__name__)

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

    # 图谱那一步在检索之后：检索负责「查得到这味药」，图谱负责「说得出为什么」。
    # 反过来说，检索没查到时图谱也不该硬撑——所以扩展失败一律静默跳过。
    cards, skip_reason = await _kg_expand(question, hits)

    next_state = _copy_state(state)
    next_state["retrieved_docs"] = [*docs, *cards]
    names = "、".join(doc["name"] for doc in docs) or "无结果"
    if cards:
        count = _card_item_count(cards[0])
        summary = f"检索到 {names}；图谱补 {count} 条教材依据"
    else:
        summary = f"检索到 {names}；{skip_reason}"
    _append_trace(next_state, "rag_agent", summary)
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
    # 先按 id 查，查不到再退回按名字：id 唯一，名字可能撞。
    by_id = {herb.id: herb for herb in load_herbs()}
    by_name = {herb.name: herb for herb in load_herbs()}
    docs: list[dict[str, Any]] = []
    for hit in hits:
        herb = by_id.get(hit.id) if hit.id else None
        if herb is None:
            herb = by_name.get(hit.name)
        docs.append(
            {
                "kind": "herb",
                # ``SearchHit.id`` 就是图谱实体 id（``herb_mahuang``）。以前这里
                # 只留名字，于是「检索结果 → 沿图扩展」必须回头再解析一遍问题文本，
                # 检索等于白做；措辞一不一样就扩不出去。
                "id": hit.id,
                "name": hit.name,
                "score": hit.score,
                "effects": list(herb.effects) if herb else [],
                "indications": [item.syndrome for item in herb.indications] if herb else [],
                "sources": hit.sources,
                # 资源链路的 ``build_whitelist`` 会读这两个键。主链路的 doc 现在
                # 不会流进资源链路，但两边形状保持一致，将来谁把 doc 传过去都不会
                # 因为缺键炸掉（缺键是 KeyError，不是默认空表）。
                "combinations": [],
                "aliases": [],
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
                id=herb.id,
            )
        prioritized.append(hit)

    prioritized_names = {hit.name for hit in prioritized}
    prioritized.extend(hit for hit in hits if hit.name not in prioritized_names)
    return prioritized[:3]


# --------------------------------------------------------------------------- #
# GraphRAG：检索负责「查得到」，图谱负责「说得出为什么」
# --------------------------------------------------------------------------- #

#: 只有这三类问题值得在主问答链路里走图。
#:
#: 另外三个能力刻意不接：``lookup`` 问的是收录与别名，检索本身就在做这件事；
#: ``review`` 要一张处方表，是独立接口；``attribute`` 要错题 id，问答里根本没有。
#: 把它们硬塞进这里，等于同一个问题答两遍，还更容易互相矛盾。
_KG_ROUTE_CAPABILITIES = frozenset({"ask", "compare", "path"})


def _card_item_count(card: dict[str, Any]) -> int:
    """一张关系卡里有几条依据。trace 里报的是这个数，不是 token 数。"""

    payload = card.get("payload") or {}
    for key in ("relations", "steps", "differences"):
        items = payload.get(key)
        if items:
            return len(items)
    return 1 if payload.get("text_span") else 0


def _relation_card(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    """投影后的载荷 → 一张「关系卡」doc。

    形状刻意与药材 doc 保持兼容（``name``/``score``/``sources``/``effects``），
    渲染侧就不用为两种 doc 写两套外壳。``payload`` 里才是真内容——**渲染必须
    从结构化数据来，不能念 ``AskResult.answer``**：那句摘要的七情只挑「相须/
    相使/相畏/相杀/相恶」五类，把占多数的「配伍」全漏了，正好是用户最常问的
    那一类。

    ``combinations`` / ``aliases`` 补空表是硬要求，理由见 ``_hits_to_docs``。
    """

    payload = {**payload, "capability": capability}
    return {
        "kind": "relation",
        "id": "",
        "name": _card_title(payload),
        "score": 1.0,
        "effects": [],
        "indications": [],
        "sources": [],
        "combinations": [],
        "aliases": [],
        "payload": payload,
    }


def _card_title(payload: dict[str, Any]) -> str:
    capability = payload.get("capability")
    if capability == "compare":
        left = _name_of(payload.get("left"))
        right = _name_of(payload.get("right"))
        return f"{left}与{right}的对比"
    if capability == "path":
        return f"{payload.get('from') or '起点'}到{payload.get('to') or '终点'}的关系链"
    seeds = [str(item.get("name") or "") for item in payload.get("seeds") or []]
    names = "、".join(name for name in seeds if name)
    return f"{names}的关系依据" if names else "教材里的关系依据"


def _name_of(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or "")
    return str(item or "")


def _kg_expand_sync(
    question: str,
    seed_ids: list[str],
    capability: str,
    settings: Any,
) -> dict[str, Any] | None:
    """同步执行图查询。``asyncio.to_thread`` 里跑的就是这一段。

    SQLite 是同步驱动的，直接在事件循环里查会阻塞所有并发请求；查询本身有界
    （每个种子最多 ``kg_agent_limit`` 条边），所以放在线程里最省事，不需要连接池。
    """

    from app.kg import contract as kg_contract
    from app.kg import projection as kg_projection

    db_path = Path(settings.qihuang_db_path)

    if capability == "compare":
        from app.kg.compare import compare as kg_compare

        if len(seed_ids) < 2:
            return None
        payload = kg_contract.normalize_compare(
            kg_compare(seed_ids[0], seed_ids[1], db_path=db_path)
        )
        if not payload.get("ok"):
            return None
        projection = kg_projection.project_compare(payload)
    elif capability == "path":
        from app.kg.compare import find_path as kg_find_path

        if len(seed_ids) < 2:
            return None
        payload = kg_contract.normalize_path(
            kg_find_path(seed_ids[0], seed_ids[1], db_path=db_path)
        )
        if not payload.get("found"):
            return None
        projection = kg_projection.project_path(payload)
    else:
        from app.kg.ask import ask as kg_ask

        result = kg_ask(
            question,
            db_path=db_path,
            seed_ids=seed_ids or None,
            limit=settings.kg_agent_limit,
        )
        projection = kg_projection.project_ask(result)

    payload = dict(projection.payload)
    relations = payload.get("relations")
    if isinstance(relations, list):
        # 排序与「邻域最多留 2 条」的策略在 ``projection._collect_relations`` 里，
        # 这里只按配置收口数量——同一条规则写在两处，迟早改成不一致。
        payload["relations"] = relations[: max(0, int(settings.kg_agent_max_relations))]

    if not _card_item_count({"payload": payload}):
        return None
    return _relation_card(capability, payload)


async def _kg_expand(question: str, hits: list[SearchHit]) -> tuple[list[dict[str, Any]], str]:
    """图谱扩展。返回 ``(关系卡, 跳过原因)``，两个值永远互斥。

    三个决定，都不是随手写的：

    1. **种子先取检索结果**（``SearchHit.id`` 就是实体 id），不够才回头解析问题
       文本。向量检索已经把「这题在说哪味药」算出来了，再解析一遍是重复劳动。
    2. **超时与异常一律降级为「不扩展」**，不抛给调用方。图谱是加分项：它挂了
       用户还应该能查到「麻黄的功效」，只是少了一段「为什么」。
    3. **不吞 ``BaseException``**。``asyncio.CancelledError`` 在 3.11 继承它，
       吞掉会让请求取消失效，客户端断开后这个协程还会继续跑完。
    """

    settings = get_settings()
    if not settings.kg_agent_enabled:
        return [], "图谱扩展已关闭"

    try:
        from app.kg import contract as kg_contract

        route = kg_contract.plan(question, db_path=Path(settings.qihuang_db_path))
    except Exception:  # noqa: BLE001 - 图未建好/表缺失时降级为基础检索
        logger.warning("kg route failed", exc_info=True)
        return [], "图谱尚未就绪，已跳过"

    if route.capability not in _KG_ROUTE_CAPABILITIES:
        return [], f"无需图谱扩展（{route.rule}）"

    seed_ids = [hit.id for hit in hits if hit.id]
    if len(set(seed_ids)) < 2:
        seed_ids = [seed.entity_id for seed in route.seeds if seed.entity_id]

    try:
        card = await asyncio.wait_for(
            asyncio.to_thread(_kg_expand_sync, question, seed_ids, route.capability, settings),
            timeout=settings.kg_agent_timeout_seconds,
        )
    except asyncio.TimeoutError:
        return [], "图谱扩展超时，已跳过"
    except (sqlite3.Error, FileNotFoundError, OSError):
        # ``TimeoutError`` 是 ``OSError`` 的子类，所以上面那个 except 必须在前面；
        # 顺序反了会把超时误报成「尚未就绪」，排查时方向全错。
        return [], "图谱尚未就绪，已跳过"
    except Exception:  # noqa: BLE001 - 扩展失败不该影响基础检索
        logger.warning("kg expand failed", exc_info=True)
        return [], "图谱扩展异常，已跳过"

    if not card:
        return [], "图上没有可引用的关系"
    return [card], ""


# --------------------------------------------------------------------------- #
# 讲解：doc → 答案
# --------------------------------------------------------------------------- #


def _cite(item: dict[str, Any]) -> str:
    """一条依据的出处。``ref`` 里通常已经带页码，别再重复追加一遍。"""

    ref = str(item.get("ref") or "").strip()
    page = str(item.get("page") or "").strip()
    if not page or (page and page in ref):
        return ref or ("教材" if not page else f"教材 {page}")
    return f"{ref or '教材'} {page}"


def _values(values: Any, *, cap: int = 3) -> str:
    items = [str(value) for value in values or [] if str(value).strip()]
    if not items:
        return "未标注"
    head = "、".join(items[:cap])
    return f"{head}等" if len(items) > cap else head


def _relation_lines(payload: dict[str, Any], limit: int) -> list[str]:
    """关系卡 → 人读的几行。每行都必须带出处与原文。

    没有原文的关系在投影阶段就被丢掉了（``_relation_out`` 直接返回 None），
    所以这里不用担心渲染出「A 和 B 有关系」却拿不出依据的句子。
    """

    lines: list[str] = []

    # 禁忌排最前，和路由里的优先级是同一条理由：安全结论不能输给辨析需求。
    # 「人参和藜芦有什么区别」如果只报四气五味，用户很可能就去配着用了。
    for item in (payload.get("constraints") or [])[:limit]:
        lines.append(f"- 禁忌：{item.get('relation_type')}（{_cite(item)}：{item.get('text_span')}）")

    for item in (payload.get("relations") or [])[: max(0, limit - len(lines))]:
        lines.append(
            f"- {item.get('from')} —{item.get('relation_type')}→ {item.get('to')}"
            f"（{_cite(item)}：{item.get('text_span')}）"
        )

    for item in (payload.get("steps") or [])[: max(0, limit - len(lines))]:
        lines.append(
            f"- {item.get('from')} —{item.get('relation_type')}→ {item.get('to')}"
            f"（{_cite(item)}：{item.get('text_span')}）"
        )

    left = _name_of(payload.get("left"))
    right = _name_of(payload.get("right"))
    for item in (payload.get("differences") or [])[: max(0, limit - len(lines))]:
        # ``project_compare`` 只留 label/left/right；原始差异里的 only_left/
        # only_right 在这一层已被裁掉。两者都认，取得到哪层就说哪层的话。
        only_left = item.get("only_left") or []
        only_right = item.get("only_right") or []
        if only_left or only_right:
            lines.append(
                f"- {item.get('label')}：{left}独有 {_values(only_left)}；"
                f"{right}独有 {_values(only_right)}"
            )
        else:
            lines.append(
                f"- {item.get('label')}：{left} {_values(item.get('left'))}；"
                f"{right} {_values(item.get('right'))}"
            )

    textbook = payload.get("textbook") or payload.get("textbook_differentiation")
    if isinstance(textbook, dict) and textbook.get("text_span") and len(lines) < limit:
        lines.append(f"- 教材鉴别要点（{_cite(textbook)}：{textbook['text_span']}）")

    return lines


def _relation_block(cards: list[dict[str, Any]], limit: int) -> list[str]:
    if not cards or limit <= 0:
        return []
    payload = cards[0].get("payload") or {}
    lines = _relation_lines(payload, limit)
    if not lines:
        return []
    sentence = str(payload.get("sentence") or "").strip()
    header = "教材依据：" if not sentence else f"教材依据（关系链：{sentence}）："
    return [header, *lines]


async def _safe_generate(question: str, docs: list[dict[str, Any]]) -> str:
    router = ModelRouter()
    herb_docs = [doc for doc in docs if doc.get("kind", "herb") == "herb"]
    context_parts = [
        f"{doc['name']}：功效={doc['effects']}；主治={doc['indications']}" for doc in herb_docs
    ]
    for card in docs:
        if card.get("kind") != "relation":
            continue
        lines = _relation_lines(card.get("payload") or {}, 6)
        if lines:
            context_parts.append("教材原文（关系）：\n" + "\n".join(lines))
    context = "\n".join(context_parts)
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


#: 讲解总长硬上限。修「只用第一本」这个缺陷时最容易顺手把回答写长，
#: 1500 字是「还能看完」与「已经像教材目录」的分界。
ANSWER_MAX_CHARS = 1500


def _clip_text(text: str, cap: int) -> str:
    """按句读截断。宁可少说一句，也不要在半句话中间断开。"""

    value = text.strip()
    if len(value) <= cap:
        return value
    head = value[:cap]
    for sep in ("。", "；", "\n", "，", " "):
        index = head.rfind(sep)
        if index >= cap * 0.6:
            return head[: index + 1]
    return head.rstrip() + "……"


def _compose_answer(question: str, docs: list[dict[str, Any]], llm_draft: str) -> str:
    if not docs:
        return NO_KB_HIT_ANSWER

    herb_docs = [doc for doc in docs if doc.get("kind", "herb") == "herb"]
    cards = [doc for doc in docs if doc.get("kind") == "relation"]

    # 先拼「有出处的那半」：药材字段 + 教材依据。模型文采再好，也不如页码可靠；
    # 谁先占版面，谁就在长度超限时活下来。
    structured: list[str] = []
    for doc in herb_docs[:2]:
        # 以前只念 docs[0]。两味药一起问时第二味药彻底消失，用户问「区别」，
        # 看到的却是一味药的功效。
        effects = "、".join(str(item) for item in doc["effects"]) or "资料中未标注功效"
        indications = "、".join(str(item) for item in doc["indications"][:3]) or "资料中未标注主治"
        structured.append(
            f"{doc['name']}的核心功效是：{effects}。\n常见主治包括：{indications}。"
        )
    limit = max(0, int(get_settings().kg_agent_max_relations))
    structured.extend(_relation_block(cards, limit))
    structured_text = "\n".join(part for part in structured if part).strip()

    draft = llm_draft.strip() if llm_draft and not llm_draft.startswith("[MOCK:") else ""
    if draft:
        # 给模型留的版面 = 总上限 - 已占用的结构化内容。留不够 200 字就整段不贴，
        # 贴一句被腰斩的开场白比不贴更糟。
        room = ANSWER_MAX_CHARS - len(structured_text) - 2
        draft = _clip_text(draft, room) if room >= 200 else ""

    body = "\n".join(part for part in (draft, structured_text) if part).strip()
    if not body:
        return NO_KB_HIT_ANSWER
    if len(body) > ANSWER_MAX_CHARS:
        body = _clip_text(body, ANSWER_MAX_CHARS)

    source_lines = _format_sources(herb_docs[:2], cards)
    return f"{body}\n\nsources:\n{source_lines}"


def _format_sources(
    docs: list[dict[str, Any]], cards: list[dict[str, Any]] | None = None
) -> str:
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for doc in docs:
        # 同一味药的 ``sources`` 常常是同一章重复五条，去重后才看得清出处。
        refs = list(
            dict.fromkeys(
                str(source.get("ref", "")).strip()
                for source in doc.get("sources", [])
                if isinstance(source, dict) and source.get("ref")
            )
        )
        ref_text = "；".join(refs) if refs else "本地中药学知识库"
        key = (str(doc["name"]), ref_text)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"[{len(lines) + 1}] {doc['name']}：{ref_text}")

    # 关系卡的出处是边上的教材原文，不是药材条目的 sources——两者页码经常不同，
    # 混着报会让用户翻到一页找不到那句话。最多补 4 条，sources 不该比正文还长。
    for card in cards or []:
        payload = card.get("payload") or {}
        citations: list[dict[str, Any]] = []
        for item in (payload.get("relations") or []) + (payload.get("steps") or []):
            citations.append(item)
        for key in ("textbook", "textbook_differentiation"):
            value = payload.get(key)
            if isinstance(value, dict):
                citations.append(value)
        citations.extend(payload.get("constraints") or [])
        for item in citations:
            if len(lines) >= 4:
                break
            quote = str(item.get("text_span") or "").strip()
            if not quote:
                continue
            cite = _cite(item)
            key = (str(card.get("name")), cite)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"[{len(lines) + 1}] {card.get('name')}：{cite}")

    return "\n".join(lines) if lines else "本地中药学知识库"
