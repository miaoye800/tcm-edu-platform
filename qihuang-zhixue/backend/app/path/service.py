"""Runtime composition for loading read-only planning inputs and persisting outputs."""

from __future__ import annotations

from pathlib import Path
import json
from collections import defaultdict
from uuid import uuid4

from app.mastery.alias import alias_mastery
from app.mastery.bkt import get_mastery
from app.path.clarify import planning_from_goal, subject_hints
from app.path.plan_store import find_reusable_l1, latest_l1_overrides, save_plan_version
from app.path.planner import LLMCall, PathAgent, ReplanHook, build_input_snapshot, input_hash_of
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
    request_text: str = "",
) -> PlanningContext:
    """Load one immutable, fully structured snapshot from the shared SQLite DB.

    ``request_text`` 是用户在向导第一步写的那句话。它**不参与任何规则计算**，
    只往上游传：过去的版本把这句话交给一张关键词表翻成 breadth/pace/depth 就丢了，
    模型从头到尾没看见过"我已经掌握中药学"——于是它当然会照排中药学。
    """

    profile = get_profile(user_id, db_path=db_path)
    nodes = list_nodes(db_path=db_path)
    # 测评事件用的是图谱实体 id（``herb_mahuang``），规划器查的是知识点 id
    # （``d01.s1.p1``）。不对齐的话每个点都取默认 0.35，"补救关"永远不触发。
    mastery = alias_mastery(
        get_mastery(user_id, db_path=db_path),
        known_ids={str(node["id"]) for node in nodes},
        db_path=db_path,
    )
    # 已有路线进度：模型要"结合已有路线的节点"判断哪些学过可以跳、哪些续着学。
    # 不带这一项，它只能把这份路线当成从零开始排的新计划。
    route_state = {
        str(item["node_id"]): str(item["status"]) for item in list_states(user_id, db_path)
    }
    return PlanningContext(
        user_id=user_id,
        profile=profile,
        planning_params=profile.planning_params,
        knowledge_nodes=nodes,
        prerequisite_edges=list_edges(db_path=db_path, rel_type="prerequisite"),
        mastery=mastery,
        time_budget=time_budget or profile.planning_params.time_budget,
        request_text=request_text,
        route_state=route_state,
    )


async def create_l1_plan(
    user_id: str,
    *,
    db_path: Path = DB_PATH,
    time_budget: TimeBudget | None = None,
    llm_call: LLMCall | None = None,
    replan_hook: ReplanHook | None = None,
    reuse: bool = True,
    request_text: str = "",
) -> PlanResponse:
    context = load_planning_context(
        user_id, db_path=db_path, time_budget=time_budget, request_text=request_text
    )
    # 「备期末考、只要中药学」必须真的改变取舍，而不是只写进摘要。
    # 这两步少一步，用户说完等于没说：
    #   1. 目标翻成 breadth/pace/depth（赶考试就抓考点）；
    #   2. 点名的学科交给 ``PathAgent(subjects=)`` 当**硬边界**裁域。
    # 以前只把 ``request_text`` 塞进 context，规划器从头到尾拿不到范围，
    # 于是"只考中药学"的人拿到的是从「中医学的哲学基础」起步的全图 85 个阶段。
    if request_text:
        context = context.model_copy(
            update={"planning_params": planning_from_goal(request_text, context.planning_params)}
        )
    snapshot = build_input_snapshot(context)
    digest = input_hash_of(snapshot)
    if reuse:
        # 同画像同图 = 同一份取舍；再问一遍只会得到漂移，不会得到更好的路线。
        # "重新规划"入口（P5）要显式传 reuse=False，因为它要的是新草稿版本。
        cached = find_reusable_l1(user_id, digest, db_path=db_path)
        if cached is not None:
            return PlanResponse(version=cached["version"], **cached["payload"])
    agent = PathAgent(
        context,
        subjects=subject_hints(request_text),
        llm_call=llm_call,
        replan_hook=replan_hook,
    )
    result = await agent.plan_l1()
    payload = {
        "fallback": result.fallback,
        "suggestion": result.suggestion,
        **result.plan.model_dump(mode="json"),
        "verify_attempts": result.verify_attempts,
        "verification_issues": [issue.model_dump(mode="json") for issue in result.verification_issues],
        # 三样落库：解释、复现、A/B 全靠它们，少一样就只能靠嘴解释"凭什么砍三章"。
        "proposal": result.proposal,
        "model_meta": result.model_meta,
        "input_snapshot": snapshot,
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
    result = await PathAgent(context, llm_call=llm_call, replan_hook=replan_hook).expand_domain(
        domain_id,
        overrides=latest_l1_overrides(user_id, db_path=db_path),
    )
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
    plan: dict[str, object] = {
        "version": versions[-1]["version"] if versions else None,
        "stages": [],
        "nodes": [],
        "adjustments": [],
        "totals": {},
    }
    domains: dict[str, dict[str, object]] = {}
    for version in versions:
        payload = version["payload"]
        if version["plan_kind"] == "l1":
            plan["stages"] = payload.get("stages", [])
            plan["totals"] = payload.get("totals") or {}
            plan["suggestion"] = payload.get("suggestion", "")
            plan["goal"] = payload.get("goal", "")
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
    states = list_states(user_id, db_path)
    # 路线条要能回答"我现在第几步、下一步是什么"。这不是前端算得出来的：
    # 单位进度要按**单元的点集**去数 states，而前端只有一张扁平的状态表。
    plan["progress"] = build_progress(plan, states)
    return {"plan": plan, "states": states, "suggestions": _suggestions(plan)}


def build_progress(plan: dict[str, object], states: list[dict[str, object]]) -> dict[str, object]:
    """把「已确认的单元序列」翻成一条可展示的学习步骤。

    三步都必要：

    1. **当前第一步**——用户确认完最想知道的是"今天从哪开始"。以前地图只有一片
       孤立穴位与一个按章排列的芯片条，他得自己从 291 行里找第一个没学的。
    2. **顺序**——`seq` 即确认过的先后，不重新排。
    3. **下一步**——学完这一节会去哪。没有它，用户点完"标记已学"只看到穴位变色，
       不知道接下来该动哪一个。

    单位完成度按**该单元的点集**统计，不按穴位统计：一个穴位可能挂着半节，
    拿穴位当刻度会让"学完 3/16 味药"显示成"这一节完成"。
    """

    stage_rows = [row for row in (plan.get("stages") or []) if isinstance(row, dict)]
    status_of = {str(item.get("node_id") or ""): str(item.get("status") or "") for item in states}
    active_point = next((node_id for node_id, status in status_of.items() if status == "current"), "")

    # 老计划（按章成行、没有 `point_ids`）也要能算进度：这一章的点在 `plan["domains"]`
    # 里按域分装。少了这层回退，升级前定的计划会显示"第 1 步 / 0%"——用户明明学过一半。
    domain_points = {
        str(domain_id): [
            str(node.get("node_id") or "")
            for node in (payload.get("nodes") or [])
            if isinstance(node, dict) and not node.get("custom")
        ]
        for domain_id, payload in (plan.get("domains") or {}).items()
        if isinstance(payload, dict)
    }

    steps: list[dict[str, object]] = []
    for index, row in enumerate(stage_rows, 1):
        point_ids = [str(item) for item in (row.get("point_ids") or []) if item]
        if not point_ids:
            point_ids = [item for item in domain_points.get(str(row.get("domain_id") or ""), []) if item]
        total = len(point_ids) or int(row.get("point_count") or 0)
        done = sum(1 for point_id in point_ids if status_of.get(point_id) == "completed")
        steps.append(
            {
                "seq": index,
                "stage_id": str(row.get("stage_id") or row.get("unit_id") or row.get("domain_id") or ""),
                "unit_id": str(row.get("unit_id") or row.get("domain_id") or ""),
                "title": str(row.get("unit_title") or row.get("name") or ""),
                "chapter_title": str(row.get("chapter_title") or ""),
                "subject_id": str(row.get("subject_id") or ""),
                "subject_name": str(row.get("subject_name") or ""),
                "unit_kind": str(row.get("unit_kind") or "section"),
                "point_count": total,
                "done": done,
                "minutes": int(row.get("estimated_minutes") or 0),
                "point_ids": point_ids,
                "state": "pending",
            }
        )

    # 当前步：优先看有没有状态为 current 的点（用户/测评推进过），否则取第一个没学完的。
    current_index = 0
    if active_point:
        for index, step in enumerate(steps):
            if active_point in step["point_ids"]:
                current_index = index
                break
    if not active_point or steps[current_index]["done"] >= steps[current_index]["point_count"]:
        unfinished = next(
            (index for index, step in enumerate(steps) if step["done"] < step["point_count"]),
            None,
        )
        current_index = unfinished if unfinished is not None else max(0, len(steps) - 1)

    for index, step in enumerate(steps):
        if step["done"] >= step["point_count"] and step["point_count"]:
            step["state"] = "done"
        elif index == current_index:
            step["state"] = "current"
        elif index == current_index + 1:
            step["state"] = "next"
        elif index < current_index:
            # 当前步之前还没学完的（跳着学的），标成"补"而不是装成已完成
            step["state"] = "pending"
        else:
            step["state"] = "later"

    total_points = sum(int(step["point_count"]) for step in steps)
    done_points = sum(int(step["done"]) for step in steps)
    return {
        "steps": steps,
        "current": steps[current_index] if steps else None,
        "next": steps[current_index + 1] if current_index + 1 < len(steps) else None,
        "total": len(steps),
        "total_points": total_points,
        "done_points": done_points,
        "percent": round(done_points / total_points, 4) if total_points else 0.0,
    }


def _row_key(stage: dict[str, object]) -> str:
    """一行的键。

    一章现在会拆成 1–7 个单元（小节 > 节 > 章），`domain_id` 因此不再是唯一标识；
    旧版本存下来的行没有 `stage_id` / `unit_id`，退回 `domain_id` 仍然可用。
    """

    return str(stage.get("stage_id") or stage.get("unit_id") or stage.get("domain_id") or "")


def reorder_path(user_id: str, node_ids: list[str], *, db_path: Path = DB_PATH) -> dict[str, object]:
    view = current_path(user_id, db_path=db_path)
    nodes = [node for payload in view["plan"]["domains"].values() for node in payload.get("nodes", [])]
    stages = [stage for stage in view["plan"].get("stages", []) if isinstance(stage, dict)]
    by_id = {node["node_id"]: node for node in nodes}
    rows = {_row_key(stage): stage for stage in stages}
    per_domain: dict[str, int] = {}
    for stage in stages:
        domain = str(stage.get("domain_id") or "")
        per_domain[domain] = per_domain.get(domain, 0) + 1
    # 按**章**调序在多行单元表上不成立：`d01` 有 3 个单元时，按章调序会把 3 行压成 1 行，
    # 用户看到的是"计划莫名其妙少了十几节"。宁可 409 让调用方按行键（`stage_id`）重来。
    stale = [node_id for node_id in node_ids if node_id not in rows and node_id in per_domain]
    if stale:
        raise PathConflict(
            "请按节调整顺序",
            edges=[],
            reason=f"{stale[0]} 是章不是单元（这一章有 {per_domain[stale[0]]} 个单元），请按 stage_id 调序",
        )
    by_id.update(rows)
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
    is_stage_reorder = all(node_id in rows for node_id in node_ids)
    if is_stage_reorder:
        # 只传一部分行时**不能丢掉其余行**（那等于删课）。没点到的行按原相对顺序排在后面。
        ordered = [dict(by_id[node_id], order=index) for index, node_id in enumerate(node_ids, 1)]
        ordered += [dict(stage, order=len(ordered) + offset) for offset, (key, stage) in enumerate((item for item in rows.items() if item[0] not in node_ids), 1)]
        payload = {"stages": ordered, "nodes": [], "ordered_node_ids": node_ids}
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


def _seed_plan_states(
    user_id: str,
    plan: dict[str, object],
    *,
    db_path: Path,
    skipped: list[dict[str, object]] | None = None,
) -> None:
    existing = {item["node_id"] for item in list_states(user_id, db_path)}
    nodes = plan.get("nodes", [])
    for index, node in enumerate(nodes):
        if node["node_id"] not in existing:
            set_state(user_id, node["node_id"], "current" if index == 0 else "available", db_path=db_path)
    # 免修点标 ``completed``：模型判过"已有基础"，那它在地图上就该是亮的。
    # 只写还没有状态的点——用户自己点过完成 / 暂停的，优先级高于模型的推断。
    for item in skipped or []:
        node_id = str(item.get("node_id") or "")
        if node_id and node_id not in existing:
            set_state(
                user_id,
                node_id,
                "completed",
                meta={"skipped_by_model": True, "reason": item.get("reason") or ""},
                db_path=db_path,
            )


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


__all__ = ["PathConflict", "add_custom_node", "build_progress", "complete_node", "create_l1_plan", "create_l2_plan", "create_l3_adjustment", "current_path", "defer_node", "load_planning_context", "reorder_path"]
