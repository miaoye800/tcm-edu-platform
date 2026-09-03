"""Runtime composition for loading read-only planning inputs and persisting outputs."""

from __future__ import annotations

from pathlib import Path
import json
from collections import defaultdict
from uuid import uuid4

from app.mastery.bkt import get_mastery
from app.path.plan_store import save_plan_version
from app.path.planner import LLMCall, PathAgent, ReplanHook
from app.path.schema import (
    AdjustResponse,
    ExpandResponse,
    MasteryState,
    PlanResponse,
    PlanningContext,
)
from app.path.store import DB_PATH, list_edges, list_nodes
from app.path.plan_store import list_plan_versions
from app.path.state import list_states, set_state
from app.profile.update import ProfileUpdateEvent, update_profile_from_event
from app.llm.router import ModelRouter
from app.profile.schema import TimeBudget
from app.profile.store import get_profile


def load_planning_context(
    user_id: str,
    *,
    db_path: Path = DB_PATH,
    time_budget: TimeBudget | None = None,
) -> PlanningContext:
    """Load one immutable, fully structured snapshot from the shared SQLite DB."""

    profile = get_profile(user_id, db_path=db_path)
    return PlanningContext(
        user_id=user_id,
        profile=profile,
        planning_params=profile.planning_params,
        knowledge_nodes=list_nodes(db_path=db_path),
        prerequisite_edges=list_edges(db_path=db_path, rel_type="prerequisite"),
        mastery=get_mastery(user_id, db_path=db_path),
        time_budget=time_budget or profile.planning_params.time_budget,
    )


async def create_l1_plan(
    user_id: str,
    *,
    db_path: Path = DB_PATH,
    time_budget: TimeBudget | None = None,
    llm_call: LLMCall | None = None,
    replan_hook: ReplanHook | None = None,
) -> PlanResponse:
    context = load_planning_context(user_id, db_path=db_path, time_budget=time_budget)
    result = await PathAgent(context, llm_call=llm_call, replan_hook=replan_hook).plan_l1()
    payload = {
        "fallback": result.fallback,
        "suggestion": result.suggestion,
        **result.plan.model_dump(mode="json"),
        "verify_attempts": result.verify_attempts,
        "verification_issues": [issue.model_dump(mode="json") for issue in result.verification_issues],
    }
    version = save_plan_version(
        user_id=user_id,
        plan_kind="l1",
        payload=payload,
        fallback=result.fallback,
        db_path=db_path,
    )
    _seed_plan_states(user_id, result.plan.model_dump(mode="json"), db_path=db_path)
    return PlanResponse(version=version, **payload)


async def create_l2_plan(
    user_id: str,
    domain_id: str,
    *,
    db_path: Path = DB_PATH,
    time_budget: TimeBudget | None = None,
    llm_call: LLMCall | None = None,
    replan_hook: ReplanHook | None = None,
) -> ExpandResponse:
    context = load_planning_context(user_id, db_path=db_path, time_budget=time_budget)
    result = await PathAgent(context, llm_call=llm_call, replan_hook=replan_hook).expand_domain(domain_id)
    payload = {
        "domain_id": domain_id,
        "fallback": result.fallback,
        **result.plan.model_dump(mode="json"),
        "verify_attempts": result.verify_attempts,
        "verification_issues": [issue.model_dump(mode="json") for issue in result.verification_issues],
    }
    version = save_plan_version(
        user_id=user_id,
        plan_kind="l2",
        domain_id=domain_id,
        payload=payload,
        fallback=result.fallback,
        db_path=db_path,
    )
    _seed_plan_states(user_id, result.plan.model_dump(mode="json"), db_path=db_path)
    return ExpandResponse(version=version, **payload)


def create_l3_adjustment(
    user_id: str,
    node_id: str,
    bkt_snapshot: dict[str, MasteryState],
    *,
    db_path: Path = DB_PATH,
) -> AdjustResponse:
    context = load_planning_context(user_id, db_path=db_path)
    diff = PathAgent(context).adjust_after_node(node_id, bkt_snapshot)
    payload = {"diff": diff.model_dump(mode="json")}
    version = save_plan_version(
        user_id=user_id,
        plan_kind="l3_adjust",
        payload=payload,
        db_path=db_path,
    )
    return AdjustResponse(version=version, diff=diff)


def current_path(user_id: str, *, db_path: Path = DB_PATH) -> dict[str, object]:
    versions = list_plan_versions(user_id, db_path)
    plan: dict[str, object] = {"version": versions[-1]["version"] if versions else None, "stages": [], "nodes": [], "adjustments": []}
    domains: dict[str, dict[str, object]] = {}
    for version in versions:
        payload = version["payload"]
        if version["plan_kind"] == "l1":
            plan["stages"] = payload.get("stages", [])
        elif version["plan_kind"] == "l2":
            domain_id = str(version["domain_id"] or payload.get("domain_id") or "")
            domains[domain_id] = payload
        elif version["plan_kind"] in {"reorder", "custom_node"}:
            if payload.get("stages") is not None:
                plan["stages"] = payload.get("stages", [])
            if payload.get("nodes") is not None:
                domain_id = str(payload.get("domain_id") or "")
                domains[domain_id] = payload
        elif version["plan_kind"] == "l3_adjust":
            plan["adjustments"].append({"version": version["version"], **payload})
    plan["domains"] = domains
    return {"plan": plan, "states": list_states(user_id, db_path), "suggestions": _suggestions(plan)}


def reorder_path(user_id: str, node_ids: list[str], *, db_path: Path = DB_PATH) -> dict[str, object]:
    view = current_path(user_id, db_path=db_path)
    nodes = [node for payload in view["plan"]["domains"].values() for node in payload.get("nodes", [])]
    stages = list(view["plan"].get("stages", []))
    by_id = {node["node_id"]: node for node in nodes}
    by_id.update({stage["domain_id"]: stage for stage in stages})
    if any(node_id not in by_id for node_id in node_ids) or len(set(node_ids)) != len(node_ids):
        raise PathConflict("reorder 只允许已展开的 free 组内节点", edges=[], reason="节点不存在或重复")
    edges = list_edges(db_path=db_path)
    positions = {node_id: index for index, node_id in enumerate(node_ids)}
    violations = []
    for edge in edges:
        if edge["order_sensitivity"] == "strict" and edge["from_node_id"] in positions and edge["to_node_id"] in positions:
            if positions[edge["from_node_id"]] > positions[edge["to_node_id"]]:
                violations.append(edge)
    if violations:
        raise PathConflict("违反 strict 前置依赖", edges=violations, reason="需先学完总论")
    free_group = _free_component(node_ids[0], edges)
    if any(node_id not in free_group for node_id in node_ids):
        raise PathConflict("reorder 只允许调整 free 组内顺序", edges=[], reason="请求必须覆盖同一个 free 组")
    is_stage_reorder = all(node_id in {stage["domain_id"] for stage in stages} for node_id in node_ids)
    if is_stage_reorder:
        reordered = [dict(by_id[node_id], order=index) for index, node_id in enumerate(node_ids, 1)]
        payload = {"stages": reordered, "nodes": [], "ordered_node_ids": node_ids}
    else:
        reordered_nodes = [dict(by_id[node_id], seq=index) for index, node_id in enumerate(node_ids, 1)]
        payload = {"nodes": reordered_nodes, "ordered_node_ids": node_ids}
    version = save_plan_version(user_id=user_id, plan_kind="reorder", payload=payload, domain_id=_domain_for(by_id[node_ids[0]], nodes), db_path=db_path)
    return {"version": version, "ordered_node_ids": node_ids, "states": list_states(user_id, db_path)}


class PathConflict(ValueError):
    def __init__(self, message: str, *, edges: list[dict[str, object]], reason: str) -> None:
        super().__init__(message)
        self.edges, self.reason = edges, reason


async def add_custom_node(user_id: str, text: str, *, db_path: Path = DB_PATH, router: ModelRouter | None = None) -> dict[str, object]:
    nodes = list_nodes(db_path=db_path)
    placement = await _classify_custom_node(text, nodes, router=router or ModelRouter())
    if placement is None:
        return {"accepted": False, "suggestion": "这个内容超出本图范围，建议先从知识树中的相关方向开始学习，或换一个更具体的中医基础主题。"}
    node_id = f"custom:{uuid4().hex[:12]}"
    plan = current_path(user_id, db_path=db_path)["plan"]
    domain_id = placement["domain_id"]
    payload = dict(plan["domains"].get(domain_id, {"domain_id": domain_id, "stages": [], "nodes": []}))
    custom = {"node_id": node_id, "name": text.strip(), "stage": 1, "seq": len(payload.get("nodes", [])) + 1,
              "target_mastery": 0.8, "resource_hint": "自定义主题学习", "highlight": "normal", "calibration": False,
              "reason": "用户自定义节点", "custom": True, "parent_id": placement.get("parent_id"), "domain_id": domain_id}
    payload.setdefault("nodes", []).append(custom)
    version = save_plan_version(user_id=user_id, plan_kind="custom_node", domain_id=domain_id, payload=payload, db_path=db_path)
    state = set_state(user_id, node_id, "custom", meta={"text": text.strip(), **placement}, db_path=db_path)
    return {"accepted": True, "version": version, "node": custom, "state": state}


async def defer_node(user_id: str, node_id: str, reason: str, reason_text: str | None, *, db_path: Path = DB_PATH) -> dict[str, object]:
    detail = reason_text.strip() if reason_text else reason
    state = set_state(user_id, node_id, "paused", meta={"reason": reason, "reason_text": detail}, db_path=db_path)
    event = ProfileUpdateEvent(type="interaction", payload={"subtype": "skip_or_retry", "topic": node_id, "skip": True, "reason": reason, "reason_text": detail})
    await update_profile_from_event(user_id, event, db_path=db_path)
    return {"state": state, "event": event.model_dump(mode="json")}


def complete_node(user_id: str, node_id: str, bkt_snapshot: dict[str, MasteryState], *, db_path: Path = DB_PATH) -> dict[str, object]:
    response = create_l3_adjustment(user_id, node_id, bkt_snapshot, db_path=db_path)
    diff = response.diff
    changes = [set_state(user_id, node_id, "completed", meta={"adjustment_action": diff.action}, db_path=db_path)]
    for update in diff.updates:
        changes.append(set_state(user_id, update.node_id, "available", meta=update.model_dump(mode="json"), db_path=db_path))
    for skipped in diff.skip_node_ids:
        changes.append(set_state(user_id, skipped, "completed", meta={"skipped_by_adjustment": node_id}, db_path=db_path))
    for inserted in diff.inserted_nodes:
        changes.append(set_state(user_id, inserted.node_id, "current", meta={"inserted_by": node_id, "reason": diff.reason}, db_path=db_path))
    if not diff.inserted_nodes:
        next_id = _next_node_id(current_path(user_id, db_path=db_path)["plan"], node_id)
        if next_id:
            changes.append(set_state(user_id, next_id, "current", db_path=db_path))
    return {"version": response.version, "next_node": diff.inserted_nodes[0].node_id if diff.inserted_nodes else _next_node_id(current_path(user_id, db_path=db_path)["plan"], node_id), "changes": changes, "diff": diff.model_dump(mode="json")}


def _seed_plan_states(user_id: str, plan: dict[str, object], *, db_path: Path) -> None:
    existing = {item["node_id"] for item in list_states(user_id, db_path)}
    nodes = plan.get("nodes", [])
    for index, node in enumerate(nodes):
        if node["node_id"] not in existing:
            set_state(user_id, node["node_id"], "current" if index == 0 else "available", db_path=db_path)


def _free_component(start: str, edges: list[dict[str, object]]) -> set[str]:
    group = {start}
    changed = True
    while changed:
        changed = False
        for edge in edges:
            if edge["order_sensitivity"] == "free" and ({edge["from_node_id"], edge["to_node_id"]} & group) and not ({edge["from_node_id"], edge["to_node_id"]} <= group):
                group.update((edge["from_node_id"], edge["to_node_id"]))
                changed = True
    return group


def _domain_for(node: dict[str, object], nodes: list[dict[str, object]]) -> str | None:
    current = node
    by_id = {item["id"]: item for item in nodes}
    while current.get("parent_id"):
        current = by_id.get(current["parent_id"], current)
    return current.get("id") if current.get("level") == "domain" else None


async def _classify_custom_node(text: str, nodes: list[dict[str, object]], *, router: ModelRouter | None) -> dict[str, str] | None:
    by_name = sorted(nodes, key=lambda item: len(str(item["name"])), reverse=True)
    match = next((item for item in by_name if str(item["name"]) in text or str(item["id"]) in text), None)
    if match is None and router is not None:
        try:
            raw = await router.chat("learning_path", [{"role": "system", "content": "Return JSON only: {domain_id, parent_id, accepted}. accepted is false when outside the graph."}, {"role": "user", "content": text}])
            data = json.loads(raw)
            if data.get("accepted") and data.get("domain_id"):
                return {"domain_id": str(data["domain_id"]), "parent_id": str(data.get("parent_id") or data["domain_id"]), "source": "llm"}
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    if match is None:
        return None
    return {"domain_id": str(_domain_for(match, nodes) or match["id"]), "parent_id": str(match["id"]), "source": "catalog_match"}


def _next_node_id(plan: dict[str, object], node_id: str) -> str | None:
    nodes = [node for payload in plan.get("domains", {}).values() for node in payload.get("nodes", [])]
    ordered = [node["node_id"] for node in sorted(nodes, key=lambda item: (item.get("stage", 0), item.get("seq", 0)))]
    try:
        return ordered[ordered.index(node_id) + 1]
    except (ValueError, IndexError):
        return None


def _suggestions(plan: dict[str, object]) -> list[str]:
    adjustments = plan.get("adjustments", [])
    return [str(item.get("diff", {}).get("reason")) for item in adjustments if item.get("diff", {}).get("reason")]


__all__ = ["PathConflict", "add_custom_node", "complete_node", "create_l1_plan", "create_l2_plan", "create_l3_adjustment", "current_path", "defer_node", "load_planning_context", "reorder_path"]
