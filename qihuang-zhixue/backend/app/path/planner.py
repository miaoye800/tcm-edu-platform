"""Guarded three-level PathAgent planning over the shared knowledge DAG."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
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
    MAX_PROPOSAL_RETRIES,
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
from app.path.proposal import (
    NodeOverride,
    PlanProposal,
    ProposalScope,
    ProposalVerdict,
    RejectedItem,
    validate_proposal,
)
from app.path.subjects import (
    CANONICAL_ORDER,
    aliases_of,
    group_domains,
    order_key,
    subject_of,
    title_of,
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
from app.path.units import Curriculum, StudyUnit

#: 取舍能力的版本号。proposal 的字段集 / 校验规则 / prompt 硬规则有实质变化时 +1。
#: 它进 ``input_hash``，所以升级会让旧 L1 缓存失效（否则用户看不到改进）。
#:
#: v3：``service.create_l1_plan`` 开始真的把学科范围交给 ``PathAgent(subjects=)``、
#: 把 goal 翻成 planning_params。升级前缓存下来的 L1 是"全图 85 阶段"那一版，
#: 不失效的话，用户重问同一句仍会拿到旧答案——而代码明明已经改了。
PROPOSAL_VERSION = 3

LLMCall = Callable[[list[ChatMessage]], Awaitable[str]]
ReplanHook = Callable[[PathPlan, list[VerificationIssue], int], PathPlan | Awaitable[PathPlan]]


class PathAgent:
    """Structured planner. Profile and planning parameters are immutable inputs."""

    def __init__(
        self,
        context: PlanningContext,
        *,
        subjects: Sequence[str] | None = None,
        llm_call: LLMCall | None = None,
        replan_hook: ReplanHook | None = None,
    ) -> None:
        self.context = context
        self._llm_call = llm_call
        self._replan_hook = replan_hook
        #: 用户点名要学的学科。**这是硬边界，不是给模型的建议**。
        #:
        #: 以前范围只拼进 ``request_text`` 里一句"重点学中药学"，然后指望模型
        #: 自己遵守；而校验器的下限是 `max(6, 60% 域)`，模型真照做（只留中药学）
        #: 反而被 ``over_pruned`` 整单拒掉、重试两次、最后回退成六门全排。
        #: 于是"想考中药学期末"的人拿到 265 节、456 小时的计划。
        #: 现在把范围提前裁到域清单上：校验器的分母跟着变小，"只留中药学"
        #: 就是 100% 保留，谁也不拦。
        self.subjects = tuple(dict.fromkeys(str(item) for item in (subjects or ()) if item))
        self._nodes = {node.id: node for node in context.knowledge_nodes}
        self._children = _children_by_parent(context.knowledge_nodes)
        # 教材视图：把图的三层翻译成「小节/节/章」。规划单位、排序、估时都走它，
        # 免得三处各写一套"这到底是章还是节"的判断（以前只有章，用户看到的
        # 就是"总论顶掉七章"）。
        self.curriculum = Curriculum(context.knowledge_nodes)
        self._personalization_enabled = llm_call is not None or _configured_llm_available()
        # 输入快照随时可算，且必须**在规划之前**算好：它同时是落库内容与复用键，
        # 不能让"这次跑完才知道 hash"这种顺序问题污染复用判断。
        self.input_snapshot = build_input_snapshot(context)
        self.input_hash = input_hash_of(self.input_snapshot)
        #: 最近一次采纳的逐点覆盖（域级 + 知识点级），供 expand_domain 使用。
        self._overrides: dict[str, NodeOverride] = {}

    async def plan_l1(self) -> L1PlanResult:
        """出拓扑底稿 → 让模型做取舍 → 逐条校验 → 交付合法顺序。"""

        base_plan, layers = self._l1_topological_plan()
        scope = self._proposal_scope(base_plan, layers)
        plan = base_plan
        suggestion = self._default_suggestion()
        fallback = False
        proposal_record: dict[str, Any] | None = None
        model_meta = self._model_meta(prompt_hash="")

        if self._personalization_enabled:
            feedback: list[RejectedItem] = []
            for _ in range(MAX_PROPOSAL_RETRIES + 1):
                try:
                    payload, prompt = await self._request_plan_proposal(base_plan, scope, feedback)
                    # 解析也放在 try 里：模型把 include 写成 [1,2] 之类的形状时，
                    # 应该在规划层退回底稿，而不是让 500 冒到接口上。
                    proposal = PlanProposal.parse(payload)
                except Exception:
                    # 无 key、JSON 坏、provider 挂了：一律退回确定性底稿，绝不抛给调用方。
                    self._personalization_enabled = False
                    break
                verdict = validate_proposal(proposal, scope)
                model_meta = self._model_meta(prompt_hash=hash_text(prompt))
                proposal_record = self._proposal_record(payload, proposal, verdict)
                if verdict.accepted:
                    plan = self._apply_verdict(verdict, base_plan)
                    suggestion = self._proposal_suggestion(verdict, base_plan)
                    self._overrides = dict(verdict.overrides)
                    break
                # 整单拒：把拒收理由原样交回模型，让它自己改，最多两轮。
                feedback = verdict.rejected
            else:
                # 用完重试仍不合法：走规则底稿并标 fallback，不把半成品交给用户。
                plan = base_plan
                fallback = True
                suggestion = f"{self._default_suggestion()}（模型取舍两次未过校验，已回退规则路线）"

        verified = await self.verify_plan(plan)
        return L1PlanResult(
            plan=verified.plan,
            suggestion=suggestion,
            fallback=fallback or verified.fallback,
            verify_attempts=verified.attempts,
            verification_issues=verified.issues,
            proposal=proposal_record,
            model_meta=model_meta,
        )

    async def expand_domain(
        self,
        domain_id: str,
        *,
        overrides: Mapping[str, Any] | None = None,
    ) -> L2PlanResult:
        """把一个大方向展开成逐点节点；``overrides`` 是提案留下的逐点掌握度/资源覆盖。

        覆盖可以挂在域上（``d01``），也可以挂在单个知识点上（``d01.s1.p1``）；
        点级优先于域级。**位置不参与覆盖** —— 两个学生的「解表药」都在肺经同一段，
        只是亮的时间不同。
        """

        domain = self._nodes.get(domain_id)
        if domain is None or domain.level != "domain":
            raise ValueError(f"unknown domain_id: {domain_id}")
        point_ids = self._descendant_point_ids(domain_id)
        ordered_ids = self._ordered_points(point_ids)
        low_confidence = self._low_confidence_dimensions()
        target_rule = self._target_mastery()
        resource_rule, resource_reason = self._resource_hint()
        scoped_overrides = dict(overrides or {})
        domain_override = scoped_overrides.get(domain_id)
        nodes: list[NodePlan] = []
        skipped: list[dict[str, str]] = []
        for node_id in ordered_ids:
            source = self._nodes[node_id]
            override = scoped_overrides.get(node_id)
            # 逐点跳过：模型判过"这一点已有基础"。它**不进路线**，但要在确认时
            # 标成已掌握，否则用户会看到"我明明会中药学，地图上却一片漆黑"。
            # 理由原样带出去，呈现层不要再编一句。
            if _override_field(override, "skip") or (
                override is None and _override_field(domain_override, "skip")
            ):
                skipped.append(
                    {
                        "node_id": node_id,
                        "name": source.name,
                        "reason": str(
                            _override_field(override, "reason")
                            or _override_field(domain_override, "reason")
                            or "已有基础，免修"
                        ),
                    }
                )
                continue
            target, mastery_from_model = self._override_target(override, domain_override, target_rule)
            resource_hint, hint_from_model = self._override_hint(override, domain_override, resource_rule)
            target_reason = (
                f"模型逐点覆盖→target_mastery={target:.2f}"
                if mastery_from_model
                else self._target_reason(target)
            )
            hint_reason = (
                f"模型指定资源→{resource_hint}" if hint_from_model else resource_reason
            )
            mastery = self.context.mastery.get(node_id)
            current = mastery.p_mastery if mastery else DEFAULT_CURRENT_MASTERY
            attempts = mastery.attempts if mastery else 0
            highlight: Literal["normal", "exam_hot", "remedial"] = "normal"
            if attempts > 0 and current < REINFORCE_THRESHOLD:
                highlight = "remedial"
            elif source.exam_frequency >= EXAM_HOT_THRESHOLD:
                highlight = "exam_hot"
            calibration = bool(low_confidence and len(nodes) < CALIBRATION_NODE_COUNT)
            calibration_reason = (
                f"；{','.join(low_confidence)}置信度<0.5→前{CALIBRATION_NODE_COUNT}关标记 calibration"
                if calibration
                else ""
            )
            nodes.append(
                NodePlan(
                    node_id=node_id,
                    stage=1,
                    # 序号在跳过判定之后才定：``seq`` 是路线里的位置，
                    # 被免修的点不占位置，不能先编号再挖空（挖空会让
                    # ``_next_node_id`` 按 (stage, seq) 找下一关时跳过一格）。
                    seq=len(nodes) + 1,
                    target_mastery=target,
                    resource_hint=resource_hint,
                    highlight=highlight,
                    calibration=calibration,
                    reason=(
                        f"{target_reason}；{hint_reason}；"
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
                    subject_id=subject_of(domain_id),
                    subject_name=title_of(subject_of(domain_id)),
                )
            ],
            nodes=nodes,
        )
        verified = await self.verify_plan(
            plan, domain_id=domain_id, assume_known={item["node_id"] for item in skipped}
        )
        return L2PlanResult(
            domain_id=domain_id,
            plan=verified.plan,
            fallback=verified.fallback,
            verify_attempts=verified.attempts,
            verification_issues=verified.issues,
            skipped=skipped,
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

    async def verify_plan(
        self,
        plan: PathPlan,
        *,
        domain_id: str | None = None,
        assume_known: Iterable[str] = (),
    ) -> VerifiedPlan:
        """Validate prerequisites, exam coverage and budget; repair twice then fall back.

        ``assume_known`` 是"判定为免修"的点。它们不在路线里，但**前驱已满足**——
        不把这一点告诉校验器，"跳过解表药第一点"会因为第二点的先修边被判成
        `prerequisite_conflict`，然后被自动修复原样补回来（用户看到自己刚砍掉的
        那一点又出现了）。免修是"不需要学"，不是"没学"。
        """

        known = {str(item) for item in assume_known}
        candidate = PathPlan.model_validate(plan)
        issues = self._verification_issues(candidate, domain_id=domain_id, assume_known=known)
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
            last_issues = self._verification_issues(candidate, domain_id=domain_id, assume_known=known)
            if not last_issues:
                return VerifiedPlan(plan=candidate, attempts=attempt)
        fallback = self._conservative_plan(domain_id=domain_id)
        return VerifiedPlan(
            plan=fallback,
            fallback=True,
            attempts=MAX_VERIFY_RETRIES,
            issues=last_issues,
        )

    def replan(self, plan: PathPlan, *, frozen: Iterable[str] = ()) -> PathPlan:
        """局部重排：已学节点保持原位当锚点，其余按拓扑序填空（计划 §7.3）。

        **只改 ``seq``，绝不改 ``section_id`` / 章节归属**——已学穴位在地图上的
        物理位置永远不动。这条比「冻结」更强：它是结构保证，不靠流程自觉。
        """

        frozen_set = {node.node_id for node in plan.nodes if node.node_id in set(frozen)}
        anchors = {node.seq: node for node in plan.nodes if node.node_id in frozen_set}
        mutable = [node.node_id for node in sorted(plan.nodes, key=lambda item: item.seq) if node.node_id not in frozen_set]
        if not mutable:
            return plan

        ordered = self._ordered_points(mutable, frozen=frozen_set)
        # 自定义节点 / 图外节点不会出现在拓扑序里（它们没有先修边），补在队尾，
        # 否则用户自己加的条目会在重排时凭空消失。
        tail = [node_id for node_id in mutable if node_id not in ordered]
        sequence = [*ordered, *tail]

        by_id = {node.node_id: node for node in plan.nodes}
        slots = [index for index in range(1, len(plan.nodes) + 1) if index not in anchors]
        rebuilt: list[NodePlan] = []
        for index, node_id in zip(slots, sequence, strict=False):
            node = by_id[node_id]
            rebuilt.append(node.model_copy(update={"seq": index}))
        rebuilt.extend(anchors.values())
        return plan.model_copy(update={"nodes": sorted(rebuilt, key=lambda item: item.seq)})

    def _domains_in_scope(self) -> list[str]:
        """本次要排的域（用户点名了学科就只留这些学科的章）。

        两个边界条件：

        * 没点名学科 → 全图。这是"我自己也不知道学啥"的正常情况，交给模型取舍。
        * 点名的学科在图里一门都对不上（说错了名字、学科改过名）→ **退回全图**。
          排一份空计划比多排几门更糟：用户会以为系统坏了，连"改个说法"的机会
          都没有。真正的纠错放在追问那一轮（``clarify`` 会把认不出的学科说明）。
        """

        every = [node.id for node in self.context.knowledge_nodes if node.level == "domain"]
        if not self.subjects:
            return every
        wanted = [domain_id for domain_id in every if subject_of(domain_id) in self.subjects]
        return wanted or every

    def _scoped_domain_edges(self) -> list[PrerequisiteEdgeInput]:
        """只保留两端都在本次范围内的域间边。

        范围外的边留着有害：``topo_sort`` 拿到未知节点会把它算进层里（或者更糟，
        抛环错），而校验器会把"范围外的域没进来"读成"整科被悄悄抹掉"。
        """

        allowed = set(self._domains_in_scope())
        return [
            edge
            for edge in self._project_domain_edges()
            if edge.from_node_id in allowed and edge.to_node_id in allowed
        ]

    def _l1_topological_plan(self) -> tuple[PathPlan, list[list[str]]]:
        domains = self._domains_in_scope()
        projected = self._scoped_domain_edges()
        layers = topo_sort(domains, [edge.model_dump() for edge in projected])
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

    async def _request_plan_proposal(
        self,
        base_plan: PathPlan,
        scope: ProposalScope,
        feedback: Sequence[RejectedItem] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """让模型做取舍。返回 (解析后的 JSON, prompt 原文)。

        prompt 里给的是**题面**：拓扑底稿、strict 边、画像（低置信度维度已打码）、
        规划参数、以及硬边界。模型能改的是输出 schema，不是这些输入。
        """

        messages: list[ChatMessage] = [
            {"role": "system", "content": _PROPOSAL_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        # 用户原话放第一位：这是唯一一条来自"人"的输入，
                        # 其余都是系统推出来的。模型需要先读懂这句话，再看数据。
                        "request": self.context.request_text,
                        "subjects": self._subject_facts(base_plan),
                        "chapters": self._chapter_facts(base_plan),
                        "existing_route": self._existing_route_facts(),
                        "min_include": scope.min_include,
                        "domain_count": len(scope.domain_ids),
                        "strict_edges": [
                            {"from": source, "to": target} for source, target in scope.strict_edges
                        ],
                        "profile": self._safe_profile_for_llm(),
                        "planning_params": self.context.planning_params.model_dump(mode="json"),
                        "previous_rejections": [
                            {"code": item.code, "target": item.target, "message": item.message}
                            for item in (feedback or [])
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        prompt = "\n".join(str(message["content"]) for message in messages)
        raw = await (self._llm_call(messages) if self._llm_call is not None else _router_call(messages))
        return _json_object(raw), prompt

    def _domain_hotness(self) -> dict[str, float]:
        """域的热度取**后代知识点的最大值**。

        不能把域节点自己的 ``exam_frequency`` 算进去：那是容器级的占位值（种子里
        一律 0.8），一算就把 19 个域全变成“热点”，考点导向下任何取舍都被
        ``proposal.exam_coverage`` 拒掉 —— 取舍功能等于没接。真正的频率在知识点上
        （117 个点里只有 17 个 ≥0.8）。域下没有知识点时才回退到域自己的值。
        """

        hotness: dict[str, float] = {}
        for node in self.context.knowledge_nodes:
            if node.level != "domain":
                continue
            values = [self._nodes[point_id].exam_frequency for point_id in self._descendant_point_ids(node.id)]
            hotness[node.id] = round(max(values), 4) if values else round(node.exam_frequency, 4)
        return hotness

    def proposal_scope(self) -> ProposalScope:
        """对外暴露本次规划的取舍边界（域清单 / strict 边 / 层号 / 硬下限）。

        wizard 要拿它渲染"哪些方向可以砍"，测试要拿它直接验校验器，都不该去
        复制一遍 ``_project_domain_edges`` + ``topo_sort`` 的逻辑。
        """

        base_plan, layers = self._l1_topological_plan()
        return self._proposal_scope(base_plan, layers)

    # -- 草稿编辑要用的确定性视图（P5） ------------------------------------

    def strict_domain_pairs(self) -> list[tuple[str, str]]:
        """域与域之间的 strict 先修对。向导调顺序时拿它判 409。"""

        return [
            (edge.from_node_id, edge.to_node_id)
            for edge in self._project_domain_edges()
            if edge.order_sensitivity == "strict"
        ]

    def strict_point_pairs(self, domain_id: str) -> list[tuple[str, str]]:
        """某个域内知识点之间的 strict 先修对。"""

        points = set(self._descendant_point_ids(domain_id))
        return sorted(self._effective_point_edges(points, sensitivity="strict"))

    def estimate_domain_minutes(self, domain_id: str) -> int:
        """骨架阶段只谈章不谈点，但仍要让用户看见「这一节大概要花多久」。

        按目标掌握度与当前掌握度估每一点，再求和——和 L2 落库用的是同一个
        ``_estimated_minutes``，不然草稿上的分钟数和确认后的对不上。
        """

        return self.estimate_points_minutes(self._descendant_point_ids(domain_id))

    def estimate_points_minutes(self, point_ids: Iterable[str]) -> int:
        """一组知识点的预计分钟数。行是**单元**而不是章之后，必须按点集算。"""

        target = self._target_mastery()
        total = 0
        for node_id in point_ids:
            probe = NodePlan(
                node_id=node_id,
                stage=1,
                seq=1,
                target_mastery=target,
                resource_hint=DEFAULT_RESOURCE_HINT,
                reason="estimate",
            )
            total += self._estimated_minutes(probe)
        return total

    def point_ids_of(self, domain_id: str) -> list[str]:
        return self._descendant_point_ids(domain_id)

    def units_of(self, domain_id: str) -> list[StudyUnit]:
        """该域的学习单元（小节 / 节 / 章，按教材层级逐级下降）。"""

        return self.curriculum.units_of(domain_id)

    def units_of_domains(self, domain_ids: Iterable[str]) -> list[StudyUnit]:
        """按传入域的先后，展开成单元序列（域内保持教材顺序）。"""

        out: list[StudyUnit] = []
        for domain_id in domain_ids:
            out.extend(self.units_of(domain_id))
        return out


    def _proposal_scope(self, base_plan: PathPlan, layers: list[list[str]]) -> ProposalScope:
        layer_of = {domain_id: index for index, layer in enumerate(layers) for domain_id in layer}
        hotness = self._domain_hotness()
        # 学科视图：域 → 学科、学科内已排好的章序、两路"已掌握"证据。
        # 这三样都在这里一次算清：校验器只该拿事实，不该自己再遍历一遍图。
        subject_of_map = {
            node.id: subject_of(node.id)
            for node in self.context.knowledge_nodes
            if node.level == "domain"
        }
        within = {
            subject: tuple(domains)
            for subject, domains in group_domains([stage.domain_id for stage in base_plan.stages]).items()
        }
        mastered, learned = self._subject_evidence()
        # 热点只在**本次范围内**的域里算。
        #
        # `_domain_hotness()` 覆盖全图，拿它直接做 `hot_domain_ids` 会出人意料地致命：
        # 用户说"只想考中药学"、模型也照着只排了中药学，校验器却拿**方剂学**的热点域
        # 判"含高频考点不能整域跳过"，整单拒 → 重试 → 还是同样一句 → 回退教材全量。
        # 界面上看到的就是"我说了只要中药学，它把中药学 65 节一节不少地排了出来"。
        in_scope = {stage.domain_id for stage in base_plan.stages}
        return ProposalScope(
            domain_ids=tuple(stage.domain_id for stage in base_plan.stages),
            node_ids=tuple(self._nodes),
            strict_edges=tuple(
                (edge.from_node_id, edge.to_node_id)
                for edge in self._scoped_domain_edges()
                if edge.order_sensitivity == "strict"
            ),
            hot_domain_ids=frozenset(
                domain_id
                for domain_id, value in hotness.items()
                if value >= EXAM_HOT_THRESHOLD and domain_id in in_scope
            ),
            layer_of=layer_of,
            breadth=self.context.planning_params.breadth,
            dimension_confidence={
                key: float(getattr(self.context.profile, key).confidence) for key in DIMENSION_KEYS
            },
            subject_of=subject_of_map,
            within_subject=within,
            subject_mastered=mastered,
            subject_learned=learned,
            request_text=self.context.request_text,
            requested_subjects=self.subjects,
        )

    def _subject_evidence(self) -> tuple[dict[str, float], dict[str, float]]:
        """每个学科的「已掌握比例」与「已有路线完成比例」。

        两个口径都按**知识点条数**算，不按章算：一章里学完 9 个点中的 1 个，
        不该被读成"这章学过 100%"。掌握度看 BKT 的 ``p_mastery``，
        路线看 ``user_path_state`` 的 ``completed``——一个反映"测得会"，
        一个反映"点过完成"，都算证据，取大者。
        """

        totals: dict[str, int] = {}
        mastered: dict[str, int] = {}
        done: dict[str, int] = {}
        for domain_id, subject in {
            node.id: subject_of(node.id)
            for node in self.context.knowledge_nodes
            if node.level == "domain"
        }.items():
            if not subject:
                continue
            for point_id in self._descendant_point_ids(domain_id):
                totals[subject] = totals.get(subject, 0) + 1
                state = self.context.mastery.get(point_id)
                if state is not None and state.p_mastery >= MASTERY_THRESHOLD:
                    mastered[subject] = mastered.get(subject, 0) + 1
                if self.context.route_state.get(point_id) == "completed":
                    done[subject] = done.get(subject, 0) + 1
        return (
            {
                subject: round(mastered.get(subject, 0) / total, 3)
                for subject, total in totals.items()
                if total
            },
            {
                subject: round(done.get(subject, 0) / total, 3)
                for subject, total in totals.items()
                if total
            },
        )

    def _subject_facts(self, base_plan: PathPlan) -> list[dict[str, Any]]:
        """给模型的学科清单。**这是它第一次知道「哪几章合起来叫中药学」。**"""

        mastered, learned = self._subject_evidence()
        domains = {stage.domain_id: stage for stage in base_plan.stages}
        out: list[dict[str, Any]] = []
        for subject in sorted(
            {subject_of(domain_id) for domain_id in domains if subject_of(domain_id)},
            key=order_key,
        ):
            chapter_ids = [d for d in domains if subject_of(d) == subject]
            points = sum(len(self._descendant_point_ids(d)) for d in chapter_ids)
            hot = sum(
                1
                for d in chapter_ids
                if any(
                    self._nodes[p].exam_frequency >= EXAM_HOT_THRESHOLD
                    for p in self._descendant_point_ids(d)
                )
            )
            out.append(
                {
                    "subject_id": subject,
                    "name": title_of(subject),
                    "chapters": len(chapter_ids),
                    "points": points,
                    "mastered_ratio": mastered.get(subject, 0.0),
                    "done_ratio": learned.get(subject, 0.0),
                    "hot_chapters": hot,
                }
            )
        return out

    def _chapter_facts(self, base_plan: PathPlan) -> list[dict[str, Any]]:
        """章级事实：模型据此决定哪一章可以整章跳过。

        一次给 90 行是刻意的：它要做的判断（"这章我学过吗"）本来就是逐章的，
        只给学科汇总会让它只能整科砍。字段名压到最短，是为了把 token 花在
        "这一章学到什么程度"上，而不是一遍遍重复 ``exam_frequency``。
        """

        layer_of = {stage.domain_id: stage.order for stage in base_plan.stages}
        hotness = self._domain_hotness()
        # **只给本次范围内的章**。范围外那 68 章既不该出现在题面里（模型会去排它们，
        # 然后被 unknown_target 逐条拒），也不该占 token——用户要的是中药学，
        # 90 章 × 每章 3 节的清单只是噪声。
        in_scope = set(self._domains_in_scope())
        rows: list[dict[str, Any]] = []
        for node in sorted(
            (
                item
                for item in self.context.knowledge_nodes
                if item.level == "domain" and item.id in in_scope
            ),
            key=lambda item: self._node_sort_key(item.id),
        ):
            points = self._descendant_point_ids(node.id)
            done = sum(1 for p in points if self.context.route_state.get(p) == "completed")
            known = sum(
                1
                for p in points
                if (state := self.context.mastery.get(p)) is not None
                and state.p_mastery >= MASTERY_THRESHOLD
            )
            rows.append(
                {
                    "domain_id": node.id,
                    "name": node.name,
                    "subject": subject_of(node.id),
                    "layer": layer_of.get(node.id, 0),
                    "points": len(points),
                    "known": max(done, known),
                    "hot": hotness.get(node.id, node.exam_frequency) >= EXAM_HOT_THRESHOLD,
                    # 章内的小节/节清单。没有它，模型只能说"这一章不学"（整章砍），
                    # 说不了"这章还学、但第二节不考"——而期末考从来是后一种。
                    # 只给 id / 标题 / 点数：token 要花在判断上，不是把教材再抄一遍。
                    "units": [
                        {
                            "unit_id": unit.id,
                            "title": unit.title,
                            "points": len(unit.point_ids),
                        }
                        for unit in self.curriculum.units_of(node.id)
                    ],
                }
            )
        return rows

    def _existing_route_facts(self) -> dict[str, Any]:
        """已有路线的摘要。模型被要求"结合已有路线的节点"，前提是它看得见。"""

        done = [
            node_id
            for node_id, status in self.context.route_state.items()
            if status in {"completed", "current"}
        ]
        if not done and not self.context.mastery:
            return {"has_plan": False, "completed_points": 0, "chapters": []}
        by_chapter: dict[str, list[str]] = {}
        for node_id in done:
            domain = self._domain_ancestor(node_id)
            if domain:
                by_chapter.setdefault(domain, []).append(node_id)
        return {
            "has_plan": bool(done),
            "completed_points": len(done),
            "chapters": [
                {
                    "domain_id": domain_id,
                    "name": self._nodes[domain_id].name if domain_id in self._nodes else domain_id,
                    "done": len(points),
                    "total": len(self._descendant_point_ids(domain_id)),
                }
                for domain_id, points in sorted(by_chapter.items())
            ],
        }

    def _apply_verdict(self, verdict: ProposalVerdict, base_plan: PathPlan) -> PathPlan:
        by_id = {stage.domain_id: stage for stage in base_plan.stages}
        stages: list[StagePlan] = []
        for order, domain_id in enumerate(verdict.order, 1):
            original = by_id[domain_id]
            note = "被下游依赖的先修→自动补回" if domain_id in verdict.auto_added else "模型取舍"
            if domain_id in verdict.skipped_chapters:
                note = "模型建议跳过（见 rejected/why）"
            subject = subject_of(domain_id)
            stages.append(
                original.model_copy(
                    update={
                        "order": order,
                        "reason": f"{original.reason}；{note}",
                        # 学科带到外层：向导要按学科分组显示，否则 54 行平铺，
                        # 用户看不出"这几章是一门课"——这正是他抱怨的"跳跃交织"。
                        "subject_id": subject,
                        "subject_name": title_of(subject),
                    }
                )
            )
        return PathPlan(stages=stages, nodes=[])

    def _proposal_suggestion(self, verdict: ProposalVerdict, base_plan: PathPlan) -> str:
        """把校验结果翻译成人话。别把 rejected 码念给用户听。

        这里的重点是**先交代"为什么没有那门课"**。用户说过"已经掌握中药学"，
        如果建议里一个字都不提，他会以为模型没听懂——哪怕路线其实是对的。
        """

        parts: list[str] = []
        claims = [item.claim for item in verdict.rationale if item.claim]
        if verdict.exempt_subjects:
            # 模型自己往往已经说了"中药学整科跳过"。它说了就不要再补一句
            # 机械的"按已有基础整科跳过"——两句话意思一样，读起来像系统在复读。
            said = "；".join(claims)
            covered = [
                subject
                for subject in verdict.exempt_subjects
                if title_of(subject) in said and "跳过" in said
            ]
            rest = [subject for subject in verdict.exempt_subjects if subject not in covered]
            if rest:
                parts.append(
                    "；".join(f"{title_of(subject)}按「已有基础」整科跳过" for subject in rest)
                )
        parts.extend(claims)
        parts = parts[:3]
        if verdict.auto_added:
            parts.append(f"补回前置 {len(verdict.auto_added)} 个")
        if verdict.skipped_chapters:
            parts.append(f"另跳过 {len(verdict.skipped_chapters)} 章")
        conflicts = [item for item in verdict.rejected if item.code == "proposal.strict_conflict"]
        if conflicts:
            parts.append(f"{len(conflicts)} 处顺序违反先修边，已按拓扑修正")
        if verdict.untrusted_dimensions:
            parts.append("模型理由引用了低置信度维度，已改标规则默认")
        if not parts:
            # 提案没附理由也要交代发生了什么：否则「稳妥推进」会被理解成一处没砍。
            pruned = max(0, len(base_plan.stages) - len(verdict.order))
            parts.append(
                f"按先修顺序推进，保留 {len(verdict.order)} 个方向"
                + (f"（跳过 {pruned} 个）" if pruned else "")
            )
        return "小岐建议：" + "；".join(parts)

    def _proposal_record(
        self,
        payload: dict[str, Any],
        proposal: PlanProposal,
        verdict: ProposalVerdict,
    ) -> dict[str, Any]:
        return {
            "accepted": verdict.accepted,
            "raw": payload,
            "include": list(verdict.order),
            "auto_added": list(verdict.auto_added),
            "exempt_subjects": list(verdict.exempt_subjects),
            "exempt_domains": list(verdict.exempt_domains),
            "skipped_chapters": dict(verdict.skipped_chapters),
            "skipped_units": dict(verdict.skipped_units),
            "rejected": [item.model_dump(mode="json") for item in verdict.rejected],
            "overrides": {
                node_id: item.model_dump(mode="json", exclude_none=True)
                for node_id, item in verdict.overrides.items()
            },
            "confidence": proposal.confidence,
            "reason": verdict.reason,
        }

    def _model_meta(self, *, prompt_hash: str) -> dict[str, Any]:
        """复现与 A/B 的锚点。``temperature`` 恒 0：个性化靠提案内容，不靠采样抖动。"""

        return {
            "model": _proposal_model_name() if self._personalization_enabled else "rule:topological",
            "temperature": 0,
            "prompt_hash": prompt_hash,
            "input_hash": self.input_hash,
        }

    def _verification_issues(
        self,
        plan: PathPlan,
        *,
        domain_id: str | None,
        assume_known: set[str] | None = None,
    ) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        conflicts: set[str] = set()
        known = assume_known or set()
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
                if target not in node_positions or source in known:
                    continue
                if source not in node_positions or node_positions[source] >= node_positions[target]:
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

    def _ordered_points(self, point_ids: Iterable[str], *, frozen: Iterable[str] = ()) -> list[str]:
        """拓扑序。``frozen`` 里的点视为「已满足的前驱」，它们参与的边全部丢弃。

        这是局部重排（计划 §7.3）的关键一步：已学过的穴位位置是资产，重排不许
        把它们往前挪或往后推——用户会看到自己学过的关被移到还没学的地方。
        """

        selected = {node_id for node_id in point_ids if node_id in self._nodes and self._nodes[node_id].level == "point"}
        if not selected:
            return []
        frozen_set = {node_id for node_id in frozen if node_id in selected}
        projected: list[dict[str, Any]] = []
        for sensitivity in ("strict", "free"):
            for source, target in self._effective_point_edges(selected, sensitivity=sensitivity):
                # 端点落在已学集合里的边不算数：已学是已满足的前驱，不再约束新顺序。
                if source in frozen_set or target in frozen_set:
                    continue
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

    def _override_target(self, primary: Any, secondary: Any, fallback: float) -> tuple[float, bool]:
        """取逐点掌握度覆盖：点级优先、域级兜底。校验阶段已滤过越界项，这里再夹一次是防御。"""

        value = _override_field(primary, "target_mastery")
        if value is None:
            value = _override_field(secondary, "target_mastery")
        if value is None:
            return fallback, False
        try:
            number = float(value)
        except (TypeError, ValueError):
            return fallback, False
        return round(min(MAX_TARGET_MASTERY, max(MIN_TARGET_MASTERY, number)), 2), True

    def _override_hint(self, primary: Any, secondary: Any, fallback: str) -> tuple[str, bool]:
        """资源提示同样逐字段合并：点级只改掌握度时，资源该继承域级，不该掉回规则值。"""

        value = _override_field(primary, "resource_hint")
        if value is None:
            value = _override_field(secondary, "resource_hint")
        text = str(value).strip() if value is not None else ""
        return (text, True) if text else (fallback, False)

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
        return dimension_value(getattr(self.context.profile, key))

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

    def _node_sort_key(self, node_id: str) -> tuple[int, int, int, int, str]:
        """排序键：**学科 → 教材章号 → 教材节号 → 节内序号 → id**。

        第一顺位是学科，这是"路线学完解表药接着学方剂起源"的正面修法。
        过去的第一顺位是父节点的 `sort_hint`，而域节点的 `sort_hint` 是
        **学科内**章号（d01 / f01 / b01 全是 1），于是六门课的第 1 章被排到一起、
        第 2 章被排到一起，读起来就是"学科跳跃交织"。学科不参与排序时，
        教材内部正确的章序反而制造了错误的整体顺序。

        第二、三顺位改成**教材章号/节号**（来自本地大纲），而不是父节点的
        ``sort_hint``：中药学总论这一个容器里装着教材第一章到第七章，14 个节的
        ``sort_hint`` 各自从 1 开始，照旧排序会把"中药的起源、中药的产地、炮制的
        目的、四气…"所有第一节排在一起——这正是"总论里的节乱序"的成因。
        """

        node = self._nodes[node_id]
        domain_id = self._domain_ancestor(node_id) or node_id
        subject_rank = order_key(subject_of(domain_id))
        if node.level == "point":
            unit = self.curriculum.unit_of_point(node_id)
            if unit is not None:
                return (subject_rank, unit.chapter_no, unit.section_no, node.sort_hint, node_id)
        units = self.units_of(domain_id)
        if units:
            first = min(units, key=lambda item: item.order)
            section = node.sort_hint if node.level == "subtype" else 0
            return (subject_rank, first.chapter_no, section, node.sort_hint, node_id)
        parent_sort = self._nodes[node.parent_id].sort_hint if node.parent_id in self._nodes else 0
        return (subject_rank, node.sort_hint, parent_sort, node.sort_hint, node_id)

    @staticmethod
    def _resequence(nodes: list[NodePlan]) -> list[NodePlan]:
        return [node.model_copy(update={"seq": index}) for index, node in enumerate(nodes, 1)]


def _children_by_parent(nodes: Iterable[KnowledgeNodeInput]) -> dict[str | None, list[str]]:
    children: dict[str | None, list[str]] = defaultdict(list)
    for node in nodes:
        children[node.parent_id].append(node.id)
    return children


def _override_field(override: Any, field: str) -> Any:
    """覆盖项可能是 pydantic 模型，也可能是从 payload_json 读回来的裸 dict。"""

    if override is None:
        return None
    if isinstance(override, Mapping):
        return override.get(field)
    return getattr(override, field, None)


def dimension_value(dimension: Any) -> Any:
    """取一个画像维度的可展示值：数值维看 score，类别维看 label，列表维看条目名。"""

    if getattr(dimension, "score", None) is not None:
        return dimension.score
    if getattr(dimension, "label", None) is not None:
        return dimension.label
    items = getattr(dimension, "items", None)
    if items:
        return [getattr(item, "label", str(item)) for item in items]
    return "未确定"


def build_input_snapshot(context: PlanningContext) -> dict[str, Any]:
    """七维分数 + 规划参数 + 知识树指纹。

    它是**复现的锚点**：同一份快照必须算出同一个 ``input_hash``，否则"同画像两次
    跑出一样的结果"就只是运气。注意这里刻意**不含掌握度** —— 进度变了不该逼着
    重新问一遍模型要取舍，那是 L3 的活。
    """

    profile = {
        key: {
            "value": dimension_value(getattr(context.profile, key)),
            "confidence": round(float(getattr(context.profile, key).confidence), 4),
        }
        for key in DIMENSION_KEYS
    }
    edges = sorted(
        f"{edge.from_node_id}>{edge.to_node_id}:{edge.order_sensitivity}"
        for edge in context.prerequisite_edges
    )
    return {
        # 取舍能力的版本号。**模型能做的判断变了，旧缓存就不能再用**：
        # 加了"按节砍"之后，同一句话仍会命中升级前那份"整章砍"的结果，
        # 用户反复试同一句会以为"怎么改都不变"——而代码明明已经改了。
        # 能力有增减时 bump 这个数，旧 L1 自然失效、重新问一次模型。
        "proposal_version": PROPOSAL_VERSION,
        # 用户原话必须进快照：同一份画像、换一句诉求（"想学方剂" vs "只想补针灸"），
        # 就是另一份计划。不含它的话，第二次请求会命中第一次的缓存，
        # 用户会以为"我说了想学什么，系统根本没理我"——而这恰恰是会发生的。
        "request": " ".join(str(context.request_text or "").split()),
        "profile": profile,
        "planning_params": context.planning_params.model_dump(mode="json"),
        "time_budget": context.time_budget.model_dump(mode="json") if context.time_budget else None,
        "knowledge_graph": {
            "node_count": len(context.knowledge_nodes),
            "domain_count": sum(1 for node in context.knowledge_nodes if node.level == "domain"),
            "edge_count": len(context.prerequisite_edges),
            "edge_hash": hash_text("\n".join(edges)),
        },
    }


def input_hash_of(snapshot: Mapping[str, Any]) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hash_text(canonical)


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _proposal_model_name() -> str:
    """记录“这次是哪张权重做的取舍”。取不到就写 task 名，不留空。"""

    try:
        return str(get_settings().llm_task_model_map.get("reason") or "reason")
    except Exception:  # pragma: no cover - 配置缺项时不该影响规划
        return "reason"


DIMENSION_KEYS: tuple[str, ...] = (
    "knowledge_base",
    "learning_goal",
    "learning_style",
    "weak_points",
    "pace",
    "motivation",
    "resource_preference",
)

_PROPOSAL_SYSTEM_PROMPT = (
    "你是小岐学习路线取舍器。输入是：用户原话 request、六门课的学科汇总 subjects、"
    "逐章事实 chapters、已有路线的进度 existing_route、画像 profile、规划参数。"
    "你要回答三件事：①每门课学还是整科跳过；②学科的先后；③哪些章、哪些点可以不学。\n"
    "只输出一个 JSON 对象，不要解释、不要 markdown。结构："
    '{"subjects":[{"subject_id":"herb","decision":"skip","reason":"用户原话已掌握中药学"}],'
    '"subject_order":["basic","diagnosis","formula","acup","internal"],'
    '"skip_chapters":[{"domain_id":"f05","reason":"与已学内容重复"}],'
    '"skip_points":[{"node_id":"f07.s1.p1","reason":"已有基础"}],'
    '"depth_overrides":{"d02":{"target_mastery":0.9}},'
    '"rationale":[{"claim":"…","basis":[{"dimension":"learning_goal","value":"应试提分","confidence":0.9}]}],'
    '"confidence":0.74}。\n'
    "硬规则："
    "1) decision 取 study / skip / partial。用 skip 必须有据：request 里点名学过这门课，"
    "或该科的 mastered_ratio / done_ratio ≥ 0.6；无据的 skip 会被驳回，该科仍留在范围内；"
    "2) 不能把课删光：保留章数不得低于 min_include；中药学仍在范围内时 general 必保留；"
    "3) 每门课要么写明 skip（带理由），要么至少留一章——不能在章级微调里把整科悄悄删掉；"
    "4) subject_order 只能出现 subjects 里给出的 subject_id；**章内顺序由系统按教材拓扑铺开，"
    "你不需要也不应该逐章排序**；"
    "5) 不得违反 strict_edges；target_mastery 只能取 0.7–0.95；"
    "6) rationale.basis 只能引用 profile 里 confidence≥0.5 的维度，且必须写出数值或等级，"
    "引用了低置信度维度的理由会被丢弃；**但 request 例外**——它是用户自己说的话，"
    "永远算数：dimension 写 \"request\"、value 写原话里的那几个字即可，冷启动时尤其要用它；"
    "7) 已有路线：done/points 已满的章可以整章跳过，学了一半的章保留剩余点，"
    "不要重排已经学过的内容；"
    "8) skip_points 只会被当作「免修」处理（不进路线、确认时标为已掌握），"
    "不要用它来表达「这章不重要」；\n"
    "9) **这一章还学、但其中某几节不学**，用 skip_units（unit_id 见 chapters[].units）。"
    "应考场景优先用它：期末从来不是整章不考，而是「这章只考那两节」。"
    "别因为教材上有就全排——用户付的是他的时间，不是教材的篇幅。"
)


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


__all__ = ["LLMCall", "PathAgent", "ReplanHook", "build_input_snapshot", "hash_text", "input_hash_of"]
