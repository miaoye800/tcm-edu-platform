"""Deprecated skill-tree/review compatibility adapter.

The only runtime route planner lives in :mod:`app.path.planner`.
"""

from __future__ import annotations

import warnings
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)

from langgraph.graph import END, StateGraph

from app.agents.resource_schemas import ResourceType
from app.kb.ingest import load_herbs
from app.kb.models import Herb
from app.mastery.bkt import DEFAULT_BKT_PARAMS, MASTERY_THRESHOLD, get_mastery
from app.mastery.review import due_reviews
from app.mastery.storage import DB_PATH, list_mastery_events, list_review_states
from app.profile.store import get_profile as get_confidence_profile

SkillStatus = Literal["locked", "unlocked", "in_progress", "mastered"]
NodeKind = Literal["category", "subcategory", "herb", "differentiation"]

MASTERY_AGENT_PROMPT = "读取 BKT 掌握度快照，只输出 kc_id 到掌握度状态的结构化映射。"
SKILL_TREE_AGENT_PROMPT = "根据中药知识库层级构建技能树 DAG，并用掌握度判定关卡状态。"
REVIEW_AGENT_PROMPT = "读取 FSRS 到期复习项，输出今日复习队列与建议题量。"
RECOMMENDATION_AGENT_PROMPT = "融合薄弱强度、复习紧迫度与前置就绪度，输出 Top3 精准推荐。"
SUMMARY_AGENT_PROMPT = "将技能树、推荐与复习队列压缩成 CLI 与前端可展示摘要。"
NODE_CONTRACTS: dict[str, dict[str, str]] = {
    "mastery_context_agent": {
        "system_prompt": MASTERY_AGENT_PROMPT,
        "input": "user_id, db_path",
        "output": "mastery",
    },
    "skill_tree_agent": {
        "system_prompt": SKILL_TREE_AGENT_PROMPT,
        "input": "mastery",
        "output": "skill_tree",
    },
    "review_queue_agent": {
        "system_prompt": REVIEW_AGENT_PROMPT,
        "input": "user_id, db_path",
        "output": "reviews",
    },
    "recommendation_agent": {
        "system_prompt": RECOMMENDATION_AGENT_PROMPT,
        "input": "skill_tree, mastery, reviews, profile_confidence",
        "output": "recommendations",
    },
    "path_summary_agent": {
        "system_prompt": SUMMARY_AGENT_PROMPT,
        "input": "skill_tree, recommendations, reviews",
        "output": "answer",
    },
}

PATH_GRAPH_PATH = Path(__file__).resolve().parent / "graph_path.mmd"


class TraceDict(TypedDict):
    node: str
    summary: str


class SkillNode(TypedDict):
    id: str
    title: str
    kind: NodeKind
    status: SkillStatus
    prerequisites: list[str]
    recommended_resources: list[ResourceType]
    reason: str
    mastery: float
    confidence: float


class Recommendation(TypedDict):
    kc_id: str
    title: str
    status: SkillStatus
    score: float
    reason: str
    mastery: float
    confidence: float
    recommended_resources: list[ResourceType]


class PathState(TypedDict):
    user_id: str
    db_path: str | None
    mastery: dict[str, dict[str, Any]]
    skill_tree: list[SkillNode]
    reviews: dict[str, Any]
    recommendations: list[Recommendation]
    profile_confidence: float
    answer: str
    trace: list[TraceDict]


@dataclass(frozen=True)
class RawSkillNode:
    """未计算状态前的技能树节点。"""

    id: str
    title: str
    kind: NodeKind
    prerequisites: tuple[str, ...]
    recommended_resources: tuple[ResourceType, ...]


async def mastery_context_agent(state: PathState) -> dict[str, Any]:
    """输入 user_id/db_path，输出 BKT 掌握度快照。"""

    db_path = _db_path_from_state(state)
    mastery = get_mastery(state["user_id"], db_path=db_path)
    return {
        "mastery": mastery,
        "trace": _next_trace(state, "mastery_context_agent", f"读取 {len(mastery)} 个知识点掌握度"),
    }


async def skill_tree_agent(state: PathState) -> dict[str, Any]:
    """输入掌握度快照，输出经 DAG 校验的技能树。"""

    herbs = load_herbs()
    skill_tree = build_skill_tree(herbs=herbs, mastery=state["mastery"])
    validate_skill_tree(skill_tree)
    mastered_count = sum(1 for node in skill_tree if node["status"] == "mastered")
    return {
        "skill_tree": skill_tree,
        "trace": _next_trace(
            state,
            "skill_tree_agent",
            f"构建 {len(skill_tree)} 个关卡，已掌握 {mastered_count} 个",
        ),
    }


async def review_queue_agent(state: PathState) -> dict[str, Any]:
    """输入 user_id/db_path，输出今日 FSRS 到期复习项。"""

    reviews = due_reviews(state["user_id"], db_path=_db_path_from_state(state))
    return {
        "reviews": reviews,
        "trace": _next_trace(
            state,
            "review_queue_agent",
            f"今日到期 {len(reviews['items'])} 项，建议 {reviews['suggested_count']} 题",
        ),
    }


async def recommendation_agent(state: PathState) -> dict[str, Any]:
    """输入技能树、掌握度与复习项，输出 Top3 学习推荐。"""

    db_path = _db_path_from_state(state)
    profile_confidence = load_profile_confidence(state["user_id"], db_path=db_path)
    recommendations = next_recommendations(
        state["user_id"],
        skill_tree=state["skill_tree"],
        mastery=state["mastery"],
        reviews=state["reviews"],
        profile_confidence=profile_confidence,
        db_path=db_path,
    )
    return {
        "profile_confidence": profile_confidence,
        "recommendations": recommendations,
        "trace": _next_trace(
            state,
            "recommendation_agent",
            f"生成 Top{len(recommendations)} 精准推荐",
        ),
    }


async def path_summary_agent(state: PathState) -> dict[str, Any]:
    """输入路径规划结构化结果，输出人类可读摘要。"""

    answer = compose_path_answer(
        skill_tree=state["skill_tree"],
        recommendations=state["recommendations"],
        reviews=state["reviews"],
    )
    return {
        "answer": answer,
        "trace": _next_trace(state, "path_summary_agent", "生成学习路径摘要"),
    }


def build_path_graph() -> Any:
    """构建 PathAgent 的 LangGraph 拓扑。"""

    workflow = StateGraph(PathState)
    workflow.add_node("mastery_context_agent", mastery_context_agent)
    workflow.add_node("skill_tree_agent", skill_tree_agent)
    workflow.add_node("review_queue_agent", review_queue_agent)
    workflow.add_node("recommendation_agent", recommendation_agent)
    workflow.add_node("path_summary_agent", path_summary_agent)

    workflow.set_entry_point("mastery_context_agent")
    workflow.add_edge("mastery_context_agent", "skill_tree_agent")
    workflow.add_edge("skill_tree_agent", "review_queue_agent")
    workflow.add_edge("review_queue_agent", "recommendation_agent")
    workflow.add_edge("recommendation_agent", "path_summary_agent")
    workflow.add_edge("path_summary_agent", END)
    return workflow.compile()


async def run_path_planning(
    user_id: str,
    db_path: Path | None = None,
) -> PathState:
    """Deprecated legacy skill-tree/review adapter.

    Runtime route planning moved to :mod:`app.path.planner`. This function is
    kept only for the deprecated GET /api/path compatibility response.
    """

    warnings.warn(
        "run_path_planning is deprecated; use app.path.service.create_l1_plan",
        DeprecationWarning,
        stacklevel=2,
    )

    graph = build_path_graph()
    result = await graph.ainvoke(initial_path_state(user_id=user_id, db_path=db_path))
    return cast(PathState, result)


def initial_path_state(user_id: str, db_path: Path | None = None) -> PathState:
    """生成 PathAgent 初始状态。"""

    return {
        "user_id": user_id,
        "db_path": str(db_path) if db_path is not None else None,
        "mastery": {},
        "skill_tree": [],
        "reviews": {"items": [], "suggested_count": 0},
        "recommendations": [],
        "profile_confidence": 0.0,
        "answer": "",
        "trace": [],
    }


def build_skill_tree(
    *,
    herbs: list[Herb] | None = None,
    mastery: dict[str, dict[str, Any]] | None = None,
    mastery_threshold: float = MASTERY_THRESHOLD,
) -> list[SkillNode]:
    """从 KB 层级生成技能树，并根据 BKT 掌握度计算状态。"""

    source_herbs = herbs or load_herbs()
    raw_nodes = _raw_skill_nodes(source_herbs)
    node_by_id = {node.id: node for node in raw_nodes}
    mastery_map = mastery or {}
    status_cache: dict[str, SkillStatus] = {}

    def status_for(node_id: str) -> SkillStatus:
        if node_id in status_cache:
            return status_cache[node_id]
        raw = node_by_id[node_id]
        snapshot = mastery_map.get(node_id, {})
        p_mastery = _mastery_value(snapshot)
        attempts = int(snapshot.get("attempts") or 0)
        if p_mastery >= mastery_threshold:
            status: SkillStatus = "mastered"
        elif not raw.prerequisites:
            status = "in_progress" if attempts > 0 else "unlocked"
        elif all(status_for(prerequisite) == "mastered" for prerequisite in raw.prerequisites):
            status = "in_progress" if attempts > 0 else "unlocked"
        else:
            status = "locked"
        status_cache[node_id] = status
        return status

    skill_tree: list[SkillNode] = []
    for raw in raw_nodes:
        snapshot = mastery_map.get(raw.id, {})
        p_mastery = _mastery_value(snapshot)
        confidence = _mastery_confidence(snapshot)
        status = status_for(raw.id)
        skill_tree.append(
            {
                "id": raw.id,
                "title": raw.title,
                "kind": raw.kind,
                "status": status,
                "prerequisites": list(raw.prerequisites),
                "recommended_resources": list(raw.recommended_resources),
                "reason": _status_reason(raw, status, p_mastery),
                "mastery": round(p_mastery, 4),
                "confidence": confidence,
            }
        )
    return skill_tree


def validate_skill_tree(skill_tree: list[SkillNode]) -> None:
    """校验技能树 DAG：无缺失前置、无环、所有节点从根可达。"""

    node_ids = {node["id"] for node in skill_tree}
    missing: list[tuple[str, str]] = []
    adjacency: dict[str, list[str]] = {node["id"]: [] for node in skill_tree}
    indegree: dict[str, int] = {node["id"]: 0 for node in skill_tree}

    for node in skill_tree:
        for prerequisite in node["prerequisites"]:
            if prerequisite not in node_ids:
                missing.append((node["id"], prerequisite))
                continue
            adjacency[prerequisite].append(node["id"])
            indegree[node["id"]] += 1

    if missing:
        detail = "、".join(f"{node}->{prerequisite}" for node, prerequisite in missing)
        raise ValueError(f"技能树存在缺失前置：{detail}")

    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited: list[str] = []
    while queue:
        current = queue.popleft()
        visited.append(current)
        for child in adjacency[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if len(visited) != len(skill_tree):
        raise ValueError("技能树存在环，无法生成闯关路径")

    roots = [node["id"] for node in skill_tree if not node["prerequisites"]]
    reachable = _reachable_nodes(roots, adjacency)
    orphan_ids = sorted(node_ids - reachable)
    if orphan_ids:
        raise ValueError(f"技能树存在孤儿节点：{','.join(orphan_ids)}")


def next_recommendations(
    user_id: str,
    *,
    skill_tree: list[SkillNode] | None = None,
    mastery: dict[str, dict[str, Any]] | None = None,
    reviews: dict[str, Any] | None = None,
    profile_confidence: float | None = None,
    db_path: Path = DB_PATH,
) -> list[Recommendation]:
    """返回融合薄弱度、FSRS 紧迫度和前置就绪度的 Top3 推荐。"""

    mastery_map = mastery if mastery is not None else get_mastery(user_id, db_path=db_path)
    tree = skill_tree if skill_tree is not None else build_skill_tree(mastery=mastery_map)
    review_payload = reviews if reviews is not None else due_reviews(user_id, db_path=db_path)
    confidence = profile_confidence if profile_confidence is not None else load_profile_confidence(user_id, db_path)
    due_by_kc = {item["kc_id"]: item for item in review_payload.get("items", [])}
    review_state_by_kc = {item["kc_id"]: item for item in list_review_states(user_id, db_path=db_path)}
    evidence_by_kc = _last_evidence_by_kc(user_id, db_path)

    recommendations: list[Recommendation] = []
    for node in tree:
        if node["status"] == "mastered":
            continue
        weak_strength = 1.0 - node["mastery"]
        due_urgency = float(due_by_kc.get(node["id"], {}).get("urgency", 0.0))
        if due_urgency == 0.0 and node["id"] in review_state_by_kc:
            due_urgency = 0.15
        readiness = _readiness_score(node)
        direct_attempts = int(mastery_map.get(node["id"], {}).get("attempts") or 0)
        evidence_boost = 1.0 + min(direct_attempts, 3) * 1.5
        score = weak_strength * (0.55 + due_urgency) * readiness * evidence_boost
        if score <= 0:
            continue
        reason = _recommendation_reason(
            node=node,
            confidence=confidence,
            due_item=due_by_kc.get(node["id"]),
            evidence=evidence_by_kc.get(node["id"]),
        )
        recommendations.append(
            {
                "kc_id": node["id"],
                "title": node["title"],
                "status": node["status"],
                "score": round(score, 4),
                "reason": reason,
                "mastery": node["mastery"],
                "confidence": round(min(node["confidence"], max(confidence, 0.25)), 4),
                "recommended_resources": node["recommended_resources"],
            }
        )

    recommendations.sort(key=lambda item: item["score"], reverse=True)
    return recommendations[:3]


def load_profile_confidence(user_id: str, db_path: Path = DB_PATH) -> float:
    """读取画像关键维度置信度，低置信时推荐只给补画像提示。"""

    profile = get_confidence_profile(user_id, db_path=db_path)
    dimensions = [profile.knowledge_base, profile.weak_points, profile.pace, profile.resource_preference]
    if not dimensions:
        return 0.0
    return round(sum(dimension.confidence for dimension in dimensions) / len(dimensions), 4)


def compose_path_answer(
    *,
    skill_tree: list[SkillNode],
    recommendations: list[Recommendation],
    reviews: dict[str, Any],
) -> str:
    """生成规划结果摘要。"""

    status_count: dict[str, int] = defaultdict(int)
    for node in skill_tree:
        status_count[node["status"]] += 1
    lines = [
        "学习路径已更新：",
        (
            f"- 技能树：mastered={status_count['mastered']}，"
            f"in_progress={status_count['in_progress']}，"
            f"unlocked={status_count['unlocked']}，locked={status_count['locked']}"
        ),
        f"- 今日复习：{len(reviews.get('items', []))} 项，建议 {reviews.get('suggested_count', 0)} 题",
        "- Top3 推荐：",
    ]
    if not recommendations:
        lines.append("  暂无推荐，建议先完成画像或进入任一已解锁关卡。")
    for index, item in enumerate(recommendations, start=1):
        lines.append(
            f"  {index}. {item['title']} | mastery={item['mastery']:.2f} | "
            f"confidence={item['confidence']:.2f} | {item['reason']}"
        )
    return "\n".join(lines)


def export_path_mermaid(path: Path | None = None) -> Path:
    """导出 PathAgent LangGraph 拓扑。"""

    output_path = path or PATH_GRAPH_PATH
    output_path.write_text(build_path_graph().get_graph().draw_mermaid(), encoding="utf-8")
    return output_path


def _raw_skill_nodes(herbs: list[Herb]) -> list[RawSkillNode]:
    nodes: list[RawSkillNode] = []
    seen: set[str] = set()
    herb_ids = {herb.id for herb in herbs}

    for category in _unique(str(herb.category) for herb in herbs):
        category_id = _category_id(category)
        nodes.append(
            RawSkillNode(
                id=category_id,
                title=category,
                kind="category",
                prerequisites=(),
                recommended_resources=("explain", "mindmap", "quiz"),
            )
        )
        seen.add(category_id)

    for category, subcategory in _unique((str(herb.category), herb.subcategory) for herb in herbs):
        subcategory_id = _subcategory_id(category, subcategory)
        if subcategory_id in seen:
            continue
        nodes.append(
            RawSkillNode(
                id=subcategory_id,
                title=subcategory,
                kind="subcategory",
                prerequisites=(_category_id(category),),
                recommended_resources=("explain", "graph", "quiz"),
            )
        )
        seen.add(subcategory_id)

    for herb in herbs:
        nodes.append(
            RawSkillNode(
                id=herb.id,
                title=herb.name,
                kind="herb",
                prerequisites=(_subcategory_id(str(herb.category), herb.subcategory),),
                recommended_resources=("explain", "graph", "quiz", "reading"),
            )
        )
        seen.add(herb.id)

    for herb in herbs:
        for diff in herb.differentiation:
            compare_id = diff.compare_with
            if compare_id not in herb_ids:
                continue
            diff_id = _diff_id(herb.id, compare_id)
            if diff_id in seen:
                continue
            compare_title = next((item.name for item in herbs if item.id == compare_id), compare_id)
            nodes.append(
                RawSkillNode(
                    id=diff_id,
                    title=f"鉴别：{herb.name} vs {compare_title}",
                    kind="differentiation",
                    prerequisites=(herb.id, compare_id),
                    recommended_resources=("graph", "quiz", "reading"),
                )
            )
            seen.add(diff_id)
    return nodes


def _status_reason(node: RawSkillNode, status: SkillStatus, p_mastery: float) -> str:
    if status == "mastered":
        return f"BKT 掌握度 {p_mastery:.2f} 已达到阈值"
    if status == "locked":
        return f"需先完成前置：{', '.join(node.prerequisites)}"
    if status == "in_progress":
        return f"已有练习记录，当前掌握度 {p_mastery:.2f}"
    return "前置已满足，可开始学习"


def _recommendation_reason(
    *,
    node: SkillNode,
    confidence: float,
    due_item: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
) -> str:
    parts: list[str] = []
    if confidence < 0.35:
        parts.append("画像置信度低，建议先补画像")
    if evidence:
        correctness = "答对" if evidence.get("correct") else "答错"
        parts.append(f"薄弱证据：{evidence.get('event_type', '练习')} {correctness}")
    else:
        parts.append(f"薄弱强度来自 BKT 掌握度 {node['mastery']:.2f}")
    if due_item:
        parts.append(f"FSRS 已到期，紧迫度 {float(due_item.get('urgency', 0.0)):.2f}")
    if node["status"] == "locked":
        parts.append("前置未完全掌握，推荐先处理关联前置")
    else:
        parts.append("前置就绪")
    return "；".join(parts)


def _last_evidence_by_kc(user_id: str, db_path: Path) -> dict[str, dict[str, Any]]:
    evidence_by_kc: dict[str, dict[str, Any]] = {}
    for event in list_mastery_events(user_id, db_path=db_path):
        evidence_by_kc[event["kc_id"]] = {
            "correct": event["correct"],
            "event_type": event["event_type"],
            "elapsed_seconds": event["elapsed_seconds"],
            "created_at": event["created_at"].isoformat(),
            "evidence": event["evidence"],
        }
    return evidence_by_kc


def _readiness_score(node: SkillNode) -> float:
    if node["status"] in {"unlocked", "in_progress"}:
        return 1.0
    if node["status"] == "locked":
        return 0.25
    return 0.0


def _mastery_value(snapshot: dict[str, Any]) -> float:
    value = snapshot.get("p_mastery", DEFAULT_BKT_PARAMS.p_known_initial)
    return float(value)


def _mastery_confidence(snapshot: dict[str, Any]) -> float:
    attempts = int(snapshot.get("attempts") or 0)
    if attempts <= 0:
        return 0.3
    return round(min(0.95, 0.45 + attempts * 0.12), 4)


def _reachable_nodes(roots: list[str], adjacency: dict[str, list[str]]) -> set[str]:
    reachable: set[str] = set()
    queue = deque(roots)
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        queue.extend(adjacency[current])
    return reachable


def _category_id(category: str) -> str:
    return f"category:{category}"


def _subcategory_id(category: str, subcategory: str) -> str:
    return f"subcategory:{category}:{subcategory}"


def _diff_id(left: str, right: str) -> str:
    return f"diff:{left}:{right}"


def _unique(items: Any) -> list[Any]:
    seen: set[Any] = set()
    result: list[Any] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _next_trace(state: PathState, node: str, summary: str) -> list[TraceDict]:
    return [*state["trace"], {"node": node, "summary": summary}]


def _db_path_from_state(state: PathState) -> Path:
    if state["db_path"]:
        return Path(state["db_path"])
    return DB_PATH
