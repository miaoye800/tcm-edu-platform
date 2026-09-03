"""Guarded three-level PathAgent planning over the shared knowledge DAG."""

from __future__ import annotations

import inspect
import json
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Literal

from app.config import get_settings
from app.llm.clients import ChatMessage
from app.llm.router import ModelRouter
from app.path.constants import (
    BASE_NODE_MINUTES,
    CALIBRATION_NODE_COUNT,
    CONFIDENCE_THRESHOLD,
    DEFAULT_CURRENT_MASTERY,
    DEFAULT_RESOURCE_HINT,
    DEFAULT_TARGET_MASTERY,
    DURATION_GAP_SCALE,
    EXAM_HOT_THRESHOLD,
    FUN_RESOURCE_THRESHOLD,
    MASTERY_THRESHOLD,
    MAX_TARGET_MASTERY,
    MAX_VERIFY_RETRIES,
    MIN_DURATION_GAP_FACTOR,
    MIN_NODE_MINUTES,
    MIN_TARGET_MASTERY,
    PACE_DURATION_FACTOR,
    REINFORCE_THRESHOLD,
    STRICTNESS_ADJUSTMENT,
    TARGET_BY_DEPTH,
)
from app.path.schema import (
    AdjustmentDiff,
    KnowledgeNodeInput,
    L1PlanResult,
    L2PlanResult,
    NodePlan,
    NodeUpdate,
    PathPlan,
    PlanningContext,
    PrerequisiteEdgeInput,
    StagePlan,
    VerificationIssue,
    VerifiedPlan,
)
from app.path.store import topo_sort

LLMCall = Callable[[list[ChatMessage]], Awaitable[str]]
ReplanHook = Callable[[PathPlan, list[VerificationIssue], int], PathPlan | Awaitable[PathPlan]]


class PathAgent:
    """Structured planner. Profile and planning parameters are immutable inputs."""

    def __init__(
        self,
        context: PlanningContext,
        *,
        llm_call: LLMCall | None = None,
        replan_hook: ReplanHook | None = None,
    ) -> None:
        self.context = context
        self._llm_call = llm_call
        self._replan_hook = replan_hook
        self._nodes = {node.id: node for node in context.knowledge_nodes}
        self._children = _children_by_parent(context.knowledge_nodes)
        self._personalization_enabled = llm_call is not None or _configured_llm_available()

    async def plan_l1(self) -> L1PlanResult:
        """Build every domain stage, allowing LLM ordering only inside topo free layers."""

        base_plan, layers = self._l1_topological_plan()
        plan = base_plan
        suggestion = self._default_suggestion()
        if self._personalization_enabled:
            try:
                ordered_ids, llm_suggestion = await self._request_l1_preference(base_plan)
                if self._valid_l1_order(ordered_ids, layers):
                    by_id = {stage.domain_id: stage for stage in base_plan.stages}
                    stages = []
                    for order, domain_id in enumerate(ordered_ids, 1):
                        original = by_id[domain_id]
                        stages.append(
                            original.model_copy(
                                update={
                                    "order": order,
                                    "reason": f"{original.reason}；LLM 仅在 free 组内排序，依据：{llm_suggestion}",
                                }
                            )
                        )
                    plan = PathPlan(stages=stages, nodes=[])
                    suggestion = llm_suggestion
            except Exception:
                # No key, malformed JSON and provider failures all stay deterministic.
                self._personalization_enabled = False
                plan = base_plan

        verified = await self.verify_plan(plan)
        return L1PlanResult(
            plan=verified.plan,
            suggestion=suggestion,
            fallback=verified.fallback,
            verify_attempts=verified.attempts,
            verification_issues=verified.issues,
        )

    async def expand_domain(self, domain_id: str) -> L2PlanResult:
        """Lazily expand exactly one domain into ordered point-level learning nodes."""

        domain = self._nodes.get(domain_id)
        if domain is None or domain.level != "domain":
            raise ValueError(f"unknown domain_id: {domain_id}")
        point_ids = self._descendant_point_ids(domain_id)
        ordered_ids = self._ordered_points(point_ids)
        low_confidence = self._low_confidence_dimensions()
        target = self._target_mastery()
        resource_hint, resource_reason = self._resource_hint()
        nodes: list[NodePlan] = []
        for seq, node_id in enumerate(ordered_ids, 1):
            source = self._nodes[node_id]
            mastery = self.context.mastery.get(node_id)
            current = mastery.p_mastery if mastery else DEFAULT_CURRENT_MASTERY
            attempts = mastery.attempts if mastery else 0
            highlight: Literal["normal", "exam_hot", "remedial"] = "normal"
            if attempts > 0 and current < REINFORCE_THRESHOLD:
                highlight = "remedial"
            elif source.exam_frequency >= EXAM_HOT_THRESHOLD:
                highlight = "exam_hot"
            calibration = bool(low_confidence and seq <= CALIBRATION_NODE_COUNT)
            calibration_reason = (
                f"；{','.join(low_confidence)}置信度<0.5→前{CALIBRATION_NODE_COUNT}关标记 calibration"
                if calibration
                else ""
            )
            nodes.append(
                NodePlan(
                    node_id=node_id,
                    stage=1,
                    seq=seq,
                    target_mastery=target,
                    resource_hint=resource_hint,
                    highlight=highlight,
                    calibration=calibration,
                    reason=(
                        f"{self._target_reason(target)}；{resource_reason}；"
                        f"知识基础掌握度{current:.2f}、考点频率{source.exam_frequency:.2f}→{highlight}"
                        f"{calibration_reason}"
                    ),
                )
            )
        plan = PathPlan(
            stages=[
                StagePlan(
                    domain_id=domain_id,
                    order=1,
                    order_locked=True,
                    reason=f"仅懒加载{domain.name}；学习目标{self._dimension_value('learning_goal')}→不展开其他大方向",
                )
            ],
            nodes=nodes,
        )
        verified = await self.verify_plan(plan, domain_id=domain_id)
        return L2PlanResult(
            domain_id=domain_id,
            plan=verified.plan,
            fallback=verified.fallback,
            verify_attempts=verified.attempts,
            verification_issues=verified.issues,
        )

    def adjust_after_node(
        self,
        node_id: str,
        bkt_snapshot: dict[str, Any],
    ) -> AdjustmentDiff:
        """Return a local L3 diff; it never rewrites the complete route."""

        node = self._nodes.get(node_id)
        if node is None or node.level != "point":
            raise ValueError(f"unknown point node_id: {node_id}")
        current = _snapshot_mastery(bkt_snapshot.get(node_id), default=DEFAULT_CURRENT_MASTERY)
        siblings = [
            sibling_id
            for sibling_id in self._ordered_points(set(self._children.get(node.parent_id, [])))
            if self._nodes[sibling_id].level == "point"
        ]
        current_index = siblings.index(node_id) if node_id in siblings else -1
        following = siblings[current_index + 1 :] if current_index >= 0 else []
        profile_basis = self._profile_basis("knowledge_base")

        if current < REINFORCE_THRESHOLD:
            remedial = NodePlan(
                node_id=f"remedial:{node_id}",
                stage=1,
                seq=1,
                target_mastery=DEFAULT_TARGET_MASTERY,
                resource_hint="错因讲解+变式测验",
                highlight="remedial",
                calibration=False,
                reason=f"BKT掌握度{current:.2f}<补救阈值{REINFORCE_THRESHOLD:.2f}；{profile_basis}",
            )
            return AdjustmentDiff(
                node_id=node_id,
                action="remedial",
                inserted_nodes=[remedial],
                reason=f"未过关：BKT掌握度{current:.2f}→插入补救关；{profile_basis}",
            )

        if current < MASTERY_THRESHOLD:
            updates: list[NodeUpdate] = []
            if following:
                updates.append(
                    NodeUpdate(
                        node_id=following[0],
                        target_mastery=min(MAX_TARGET_MASTERY, self._target_mastery() + 0.05),
                        resource_hint="前置回顾+加固测验",
                        highlight="remedial",
                    )
                )
            return AdjustmentDiff(
                node_id=node_id,
                action="reinforce",
                updates=updates,
                reason=(
                    f"勉强过关：BKT掌握度{current:.2f}介于{REINFORCE_THRESHOLD:.2f}和"
                    f"{MASTERY_THRESHOLD:.2f}→下一节点加固；{profile_basis}"
                ),
            )

        mastered_following = [
            sibling_id
            for sibling_id in following
            if _snapshot_mastery(bkt_snapshot.get(sibling_id), default=0.0) >= MASTERY_THRESHOLD
        ]
        if mastered_following:
            return AdjustmentDiff(
                node_id=node_id,
                action="skip",
                skip_node_ids=mastered_following,
                reason=(
                    f"已过关且同类后续节点BKT掌握度达到{MASTERY_THRESHOLD:.2f}→跳级"
                    f"{','.join(mastered_following)}；{profile_basis}"
                ),
            )
        return AdjustmentDiff(
            node_id=node_id,
            action="normal",
            reason=f"BKT掌握度{current:.2f}已过关，后续同类尚未达标→按原路线继续；{profile_basis}",
        )

    async def verify_plan(self, plan: PathPlan, *, domain_id: str | None = None) -> VerifiedPlan:
        """Validate prerequisites, exam coverage and budget; repair twice then fall back."""

        candidate = PathPlan.model_validate(plan)
        issues = self._verification_issues(candidate, domain_id=domain_id)
        if not issues:
            return VerifiedPlan(plan=candidate)
        last_issues = issues
        for attempt in range(1, MAX_VERIFY_RETRIES + 1):
            if self._replan_hook is not None:
                repaired = self._replan_hook(candidate, last_issues, attempt)
                candidate = await repaired if inspect.isawaitable(repaired) else repaired
                candidate = PathPlan.model_validate(candidate)
            else:
                candidate = self._deterministic_repair(candidate, last_issues, domain_id=domain_id, attempt=attempt)
            last_issues = self._verification_issues(candidate, domain_id=domain_id)
            if not last_issues:
                return VerifiedPlan(plan=candidate, attempts=attempt)
        fallback = self._conservative_plan(domain_id=domain_id)
        return VerifiedPlan(
            plan=fallback,
            fallback=True,
            attempts=MAX_VERIFY_RETRIES,
            issues=last_issues,
        )

    def _l1_topological_plan(self) -> tuple[PathPlan, list[list[str]]]:
        domains = [node for node in self.context.knowledge_nodes if node.level == "domain"]
        projected = self._project_domain_edges()
        layers = topo_sort([node.id for node in domains], [edge.model_dump() for edge in projected])
        layers = [sorted(layer, key=self._node_sort_key) for layer in layers]
        stages: list[StagePlan] = []
        for layer in layers:
            for domain_id in layer:
                stages.append(
                    StagePlan(
                        domain_id=domain_id,
                        order=len(stages) + 1,
                        order_locked=len(layer) == 1,
                        reason=(
                            f"先修图topo_sort第{len(stages) + 1}位，"
                            f"{'strict边锁定' if len(layer) == 1 else 'free组可偏好排序'}；"
                            f"{self._profile_basis('learning_goal')}"
                        ),
                    )
                )
        return PathPlan(stages=stages, nodes=[]), layers

    async def _request_l1_preference(self, base_plan: PathPlan) -> tuple[list[str], str]:
        safe_profile = self._safe_profile_for_llm()
        messages: list[ChatMessage] = [
            {
                "role": "system",
                "content": (
                    "你是小岐路线排序器。只输出JSON对象："
                    '{"ordered_domain_ids":["..."],"suggestion":"..."}。'
                    "只能在free组内排序，不得违反strict先修边；suggestion必须点名所依据的画像维度和数值/等级。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "topological_order": [stage.domain_id for stage in base_plan.stages],
                        "strict_edges": [
                            edge.model_dump(mode="json")
                            for edge in self._project_domain_edges()
                            if edge.order_sensitivity == "strict"
                        ],
                        "profile": safe_profile,
                        "planning_params": self.context.planning_params.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        raw = await (self._llm_call(messages) if self._llm_call is not None else _router_call(messages))
        payload = _json_object(raw)
        ordered = payload.get("ordered_domain_ids")
        suggestion = str(payload.get("suggestion") or "").strip()
        if not isinstance(ordered, list) or not suggestion:
            raise ValueError("L1 preference output is incomplete")
        return [str(item) for item in ordered], suggestion

    def _valid_l1_order(self, ordered_ids: list[str], layers: list[list[str]]) -> bool:
        expected = [stage_id for layer in layers for stage_id in layer]
        if len(ordered_ids) != len(expected) or set(ordered_ids) != set(expected):
            return False
        positions = {node_id: index for index, node_id in enumerate(ordered_ids)}
        for edge in self._project_domain_edges():
            if edge.order_sensitivity == "strict" and positions[edge.from_node_id] >= positions[edge.to_node_id]:
                return False
        layer_by_id = {node_id: index for index, layer in enumerate(layers) for node_id in layer}
        return all(
            layer_by_id[ordered_ids[index]] <= layer_by_id[ordered_ids[index + 1]]
            for index in range(len(ordered_ids) - 1)
        )

    def _verification_issues(self, plan: PathPlan, *, domain_id: str | None) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        conflicts: set[str] = set()
        if domain_id is None:
            stage_positions = {stage.domain_id: stage.order for stage in plan.stages}
            for edge in self._project_domain_edges():
                if edge.order_sensitivity != "strict":
                    continue
                if edge.to_node_id in stage_positions and (
                    edge.from_node_id not in stage_positions
                    or stage_positions[edge.from_node_id] >= stage_positions[edge.to_node_id]
                ):
                    conflicts.update((edge.from_node_id, edge.to_node_id))

        if plan.nodes:
            node_positions = {node.node_id: node.seq for node in plan.nodes}
            scope_points = set(self._descendant_point_ids(domain_id)) if domain_id else set(node_positions)
            for source, target in self._effective_point_edges(scope_points, sensitivity="strict"):
                if target in node_positions and (source not in node_positions or node_positions[source] >= node_positions[target]):
                    conflicts.update((source, target))
        if conflicts:
            issues.append(
                VerificationIssue(
                    code="prerequisite_conflict",
                    message="路线违反 strict 先修依赖",
                    node_ids=sorted(conflicts),
                )
            )

        if plan.nodes and self.context.planning_params.breadth == "考点导向":
            scope = self._descendant_point_ids(domain_id) if domain_id else [node.node_id for node in plan.nodes]
            required = {
                node_id
                for node_id in scope
                if self._nodes[node_id].exam_frequency >= EXAM_HOT_THRESHOLD
            }
            missing = sorted(required - {node.node_id for node in plan.nodes})
            if missing:
                issues.append(
                    VerificationIssue(
                        code="exam_coverage",
                        message=f"缺少考点频率>={EXAM_HOT_THRESHOLD:.2f}的必学节点",
                        node_ids=missing,
                    )
                )

        budget = self.context.time_budget or self.context.planning_params.time_budget
        if plan.nodes and budget is not None:
            estimated = sum(self._estimated_minutes(node) for node in plan.nodes)
            available = budget.total_days * budget.daily_minutes
            if estimated > available:
                issues.append(
                    VerificationIssue(
                        code="time_budget",
                        message=f"预估{estimated}分钟超过预算{available}分钟",
                        node_ids=[node.node_id for node in plan.nodes],
                    )
                )
        return issues

    def _deterministic_repair(
        self,
        plan: PathPlan,
        issues: list[VerificationIssue],
        *,
        domain_id: str | None,
        attempt: int,
    ) -> PathPlan:
        codes = {issue.code for issue in issues}
        if "prerequisite_conflict" in codes or "exam_coverage" in codes:
            repaired = self._conservative_plan(domain_id=domain_id)
            if domain_id and attempt == 1:
                original = {node.node_id: node for node in plan.nodes}
                repaired = repaired.model_copy(
                    update={
                        "nodes": [
                            node.model_copy(
                                update={
                                    "target_mastery": original.get(node.node_id, node).target_mastery,
                                    "resource_hint": original.get(node.node_id, node).resource_hint,
                                    "reason": f"自动重排补齐考点/先修；{node.reason}",
                                }
                            )
                            for node in repaired.nodes
                        ]
                    }
                )
            return repaired
        if "time_budget" in codes:
            if attempt == 1:
                return plan.model_copy(
                    update={
                        "nodes": [
                            node.model_copy(
                                update={
                                    "target_mastery": max(MIN_TARGET_MASTERY, min(DEFAULT_TARGET_MASTERY, node.target_mastery)),
                                    "reason": f"时间预算约束→目标掌握度收敛到保守值；{node.reason}",
                                }
                            )
                            for node in plan.nodes
                        ]
                    }
                )
            required = self._required_node_closure(domain_id, plan)
            return plan.model_copy(update={"nodes": self._resequence([node for node in plan.nodes if node.node_id in required])})
        return plan

    def _conservative_plan(self, *, domain_id: str | None) -> PathPlan:
        if domain_id is None:
            return self._l1_topological_plan()[0]
        point_ids = self._descendant_point_ids(domain_id)
        ordered = self._ordered_points(point_ids)
        domain = self._nodes[domain_id]
        nodes = [
            NodePlan(
                node_id=node_id,
                stage=1,
                seq=index,
                target_mastery=DEFAULT_TARGET_MASTERY,
                resource_hint=DEFAULT_RESOURCE_HINT,
                highlight="exam_hot" if self._nodes[node_id].exam_frequency >= EXAM_HOT_THRESHOLD else "normal",
                calibration=index <= CALIBRATION_NODE_COUNT and bool(self._low_confidence_dimensions()),
                reason=(
                    f"fallback保守模板：纯拓扑序+默认掌握度{DEFAULT_TARGET_MASTERY:.2f}；"
                    f"{self._profile_basis('knowledge_base')}"
                ),
            )
            for index, node_id in enumerate(ordered, 1)
        ]
        return PathPlan(
            stages=[StagePlan(domain_id=domain_id, order=1, order_locked=True, reason=f"fallback保守展开{domain.name}；{self._profile_basis('learning_goal')}")],
            nodes=nodes,
        )

    def _ordered_points(self, point_ids: Iterable[str]) -> list[str]:
        selected = {node_id for node_id in point_ids if node_id in self._nodes and self._nodes[node_id].level == "point"}
        if not selected:
            return []
        projected: list[dict[str, Any]] = []
        for sensitivity in ("strict", "free"):
            for source, target in self._effective_point_edges(selected, sensitivity=sensitivity):
                projected.append(
                    {
                        "from_node_id": source,
                        "to_node_id": target,
                        "rel_type": "prerequisite",
                        "order_sensitivity": sensitivity,
                        "reason": "projected point prerequisite",
                    }
                )
        layers = topo_sort(selected, projected)
        return [node_id for layer in layers for node_id in sorted(layer, key=self._node_sort_key)]

    def _project_domain_edges(self) -> list[PrerequisiteEdgeInput]:
        projected: dict[tuple[str, str], PrerequisiteEdgeInput] = {}
        for edge in self.context.prerequisite_edges:
            source = self._domain_ancestor(edge.from_node_id)
            target = self._domain_ancestor(edge.to_node_id)
            if source is None or target is None or source == target:
                continue
            key = (source, target)
            existing = projected.get(key)
            sensitivity: Literal["strict", "free"] = edge.order_sensitivity
            if existing is not None and existing.order_sensitivity == "strict":
                continue
            projected[key] = PrerequisiteEdgeInput(
                from_node_id=source,
                to_node_id=target,
                order_sensitivity=sensitivity,
                reason=edge.reason,
            )
        return list(projected.values())

    def _effective_point_edges(
        self,
        selected: set[str],
        *,
        sensitivity: Literal["strict", "free"],
    ) -> set[tuple[str, str]]:
        result: set[tuple[str, str]] = set()
        for edge in self.context.prerequisite_edges:
            if edge.order_sensitivity != sensitivity:
                continue
            sources = self._points_below(edge.from_node_id) & selected
            targets = self._points_below(edge.to_node_id) & selected
            for source in sources:
                for target in targets:
                    if source != target:
                        result.add((source, target))
        return result

    def _required_node_closure(self, domain_id: str | None, plan: PathPlan) -> set[str]:
        present = {node.node_id for node in plan.nodes}
        required = {
            node_id
            for node_id in present
            if self._nodes[node_id].exam_frequency >= EXAM_HOT_THRESHOLD
        }
        changed = True
        strict_edges = self._effective_point_edges(present, sensitivity="strict")
        while changed:
            changed = False
            for source, target in strict_edges:
                if target in required and source not in required:
                    required.add(source)
                    changed = True
        return required or set(list(present)[:1])

    def _estimated_minutes(self, node: NodePlan) -> int:
        current = self.context.mastery.get(node.node_id)
        p_mastery = current.p_mastery if current else DEFAULT_CURRENT_MASTERY
        gap_factor = max(MIN_DURATION_GAP_FACTOR, (node.target_mastery - p_mastery) / DURATION_GAP_SCALE)
        pace_factor = PACE_DURATION_FACTOR[self.context.planning_params.pace]
        return max(MIN_NODE_MINUTES, round(BASE_NODE_MINUTES * gap_factor * pace_factor))

    def _target_mastery(self) -> float:
        if not self._personalization_enabled:
            return DEFAULT_TARGET_MASTERY
        depth_base = TARGET_BY_DEPTH[self.context.planning_params.depth]
        strict_adjust = STRICTNESS_ADJUSTMENT[self.context.planning_params.assessment_strictness]
        return round(min(MAX_TARGET_MASTERY, max(MIN_TARGET_MASTERY, depth_base + strict_adjust)), 2)

    def _target_reason(self, target: float) -> str:
        params = self.context.planning_params
        return f"学习深度{params.depth}+考核严格度{params.assessment_strictness}→target_mastery={target:.2f}"

    def _resource_hint(self) -> tuple[str, str]:
        if not self._personalization_enabled:
            return DEFAULT_RESOURCE_HINT, f"无可用LLM→学习风格采用保守默认资源{DEFAULT_RESOURCE_HINT}"
        style = self.context.profile.learning_style
        if style.confidence < CONFIDENCE_THRESHOLD or not style.label:
            return (
                DEFAULT_RESOURCE_HINT,
                f"学习风格置信度{style.confidence:.2f}<0.5→保守默认资源{DEFAULT_RESOURCE_HINT}",
            )
        fun = self.context.planning_params.fun_preference
        if fun >= FUN_RESOURCE_THRESHOLD:
            if style.label == "视觉型":
                hint = "知识图谱+拟人化短剧+测验"
            elif style.label == "记忆(口诀)型":
                hint = "汤头歌诀+节奏记忆+测验"
            else:
                hint = "趣味案例+短篇精读+测验"
        else:
            hint = {
                "视觉型": "知识图谱+对比图+测验",
                "文字型": "教材精读+要点笔记+测验",
                "记忆(口诀)型": "口诀记忆+抽认测验",
            }.get(style.label, DEFAULT_RESOURCE_HINT)
        return hint, f"趣味偏好{fun:.2f}+学习风格{style.label}(置信度{style.confidence:.2f})→{hint}"

    def _safe_profile_for_llm(self) -> dict[str, dict[str, Any]]:
        safe: dict[str, dict[str, Any]] = {}
        for key in (
            "knowledge_base",
            "learning_goal",
            "learning_style",
            "weak_points",
            "pace",
            "motivation",
            "resource_preference",
        ):
            dimension = getattr(self.context.profile, key)
            value = self._dimension_value(key)
            if dimension.confidence < CONFIDENCE_THRESHOLD:
                safe[key] = {
                    "value": "保守默认",
                    "confidence": dimension.confidence,
                    "constraint": "不得据此做激进决策",
                }
            else:
                safe[key] = {"value": value, "confidence": dimension.confidence}
        return safe

    def _dimension_value(self, key: str) -> Any:
        dimension = getattr(self.context.profile, key)
        if getattr(dimension, "score", None) is not None:
            return dimension.score
        if getattr(dimension, "label", None) is not None:
            return dimension.label
        items = getattr(dimension, "items", None)
        if items:
            return [item.label for item in items]
        return "未确定"

    def _profile_basis(self, key: str) -> str:
        dimension = getattr(self.context.profile, key)
        label = {
            "knowledge_base": "知识基础",
            "learning_goal": "学习目标",
            "learning_style": "学习风格",
            "weak_points": "薄弱点",
            "pace": "节奏",
            "motivation": "动机",
            "resource_preference": "资源偏好",
        }[key]
        if dimension.confidence < CONFIDENCE_THRESHOLD:
            return f"{label}置信度{dimension.confidence:.2f}<0.5→采用保守默认"
        return f"{label}{self._dimension_value(key)}(置信度{dimension.confidence:.2f})"

    def _low_confidence_dimensions(self) -> list[str]:
        return [
            key
            for key in (
                "knowledge_base",
                "learning_goal",
                "learning_style",
                "weak_points",
                "pace",
                "motivation",
                "resource_preference",
            )
            if getattr(self.context.profile, key).confidence < CONFIDENCE_THRESHOLD
        ]

    def _default_suggestion(self) -> str:
        return f"小岐建议：当前按先修拓扑稳妥推进；{self._profile_basis('learning_goal')}。"

    def _domain_ancestor(self, node_id: str) -> str | None:
        current = self._nodes.get(node_id)
        seen: set[str] = set()
        while current is not None and current.id not in seen:
            if current.level == "domain":
                return current.id
            seen.add(current.id)
            current = self._nodes.get(current.parent_id or "")
        return None

    def _points_below(self, node_id: str) -> set[str]:
        node = self._nodes.get(node_id)
        if node is None:
            return set()
        if node.level == "point":
            return {node_id}
        return set(self._descendant_point_ids(node_id))

    def _descendant_point_ids(self, root_id: str | None) -> list[str]:
        if root_id is None:
            return []
        result: list[str] = []
        pending = list(self._children.get(root_id, []))
        while pending:
            node_id = pending.pop()
            node = self._nodes[node_id]
            if node.level == "point":
                result.append(node_id)
            else:
                pending.extend(self._children.get(node_id, []))
        return sorted(result, key=self._node_sort_key)

    def _node_sort_key(self, node_id: str) -> tuple[int, int, str]:
        node = self._nodes[node_id]
        parent_sort = self._nodes[node.parent_id].sort_hint if node.parent_id in self._nodes else 0
        return parent_sort, node.sort_hint, node.id

    @staticmethod
    def _resequence(nodes: list[NodePlan]) -> list[NodePlan]:
        return [node.model_copy(update={"seq": index}) for index, node in enumerate(nodes, 1)]


def _children_by_parent(nodes: Iterable[KnowledgeNodeInput]) -> dict[str | None, list[str]]:
    children: dict[str | None, list[str]] = defaultdict(list)
    for node in nodes:
        children[node.parent_id].append(node.id)
    return children


def _snapshot_mastery(value: Any, *, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, dict):
        value = value.get("p_mastery", default)
    else:
        value = getattr(value, "p_mastery", value)
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return default


async def _router_call(messages: list[ChatMessage]) -> str:
    return await ModelRouter().chat("reason", messages, temperature=0, response_format={"type": "json_object"})


def _configured_llm_available() -> bool:
    settings = get_settings()
    return bool((settings.deepseek_api_key or "").strip() or (settings.zhipu_api_key or "").strip())


def _json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("[MOCK:"):
        raise ValueError("mock response is not planner JSON")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("planner response must be a JSON object")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("planner response must be a JSON object")
    return parsed


__all__ = ["LLMCall", "PathAgent", "ReplanHook"]
