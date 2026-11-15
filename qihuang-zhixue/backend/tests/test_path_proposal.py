"""P4 取舍提案：校验规则、逐点覆盖、以及"同画像同计划"的可复现性。

这里刻意不 mock ``validate_proposal``：错的是**规则本身**才最值得测。所以两支
角度都测——直接喂提案给校验器验码，以及走 ``PathAgent`` 验最终落到计划上的效果。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.path.constants import (
    CONFIDENCE_THRESHOLD,
    MAX_PROPOSAL_RETRIES,
    MAX_TARGET_MASTERY,
    MIN_INCLUDE_DOMAINS,
    MIN_TARGET_MASTERY,
    STRICTNESS_ADJUSTMENT,
    TARGET_BY_DEPTH,
)
from app.path.plan_store import find_reusable_l1, latest_l1_overrides, list_plan_versions
from app.path.planner import PathAgent, build_input_snapshot, input_hash_of
from app.path.proposal import PlanProposal, validate_proposal
from app.path.schema import KnowledgeNodeInput, PlanningContext, PrerequisiteEdgeInput
from app.path.service import create_l1_plan, create_l2_plan
from app.path.store import upsert_edge, upsert_node
from app.profile.store import upsert_profile
from app.profile.schema import (
    CategoricalDimension,
    Goal,
    MotivationDimension,
    NumericDimension,
    PlanningParams,
    Profile,
    ResourcePreferenceDimension,
    WeakPointsDimension,
)

# 8 个方向：多到能验"不许把课删光"（下限 = max(6, ceil(0.6×8)) = 6），
# 又小到能一眼看清每条断言。
# d05 的**域节点**故意写 0.2、**知识点**写 0.9：真实种子里域的 exam_frequency 是占位值，
# 热度必须由知识点决定；这一对数字就是用来盯死"别拿容器值当热度"的。
DOMAINS: tuple[tuple[str, str, float], ...] = (
    ("general", "总论", 0.6),
    ("d01", "解表药", 0.5),
    ("d02", "清热药", 0.5),
    ("d03", "温里药", 0.3),
    ("d04", "补虚药", 0.4),
    ("d05", "驱虫药", 0.2),
    ("d06", "收涩药", 0.2),
    ("d07", "涌吐药", 0.2),
)

EDGES: tuple[tuple[str, str, str], ...] = (
    ("general", "d01", "strict"),
    ("d01", "d02", "strict"),  # 解表先于清热：排除 d01 就会触发"补前置"
    ("d03", "d04", "strict"),
    ("d06", "d07", "free"),
)


def _nodes() -> list[KnowledgeNodeInput]:
    nodes = [
        KnowledgeNodeInput(id=domain_id, name=name, level="domain", sort_hint=index, exam_frequency=exam)
        for index, (domain_id, name, exam) in enumerate(DOMAINS)
    ]
    for domain_id, _name, _exam in DOMAINS:
        subtype_id = f"{domain_id}.s1"
        nodes.append(
            KnowledgeNodeInput(
                id=subtype_id,
                name=f"{domain_id} 第一节",
                level="subtype",
                parent_id=domain_id,
                sort_hint=1,
            )
        )
        for index in (1, 2):
            nodes.append(
                KnowledgeNodeInput(
                    id=f"{subtype_id}.p{index}",
                    name=f"{domain_id} 知识点{index}",
                    level="point",
                    parent_id=subtype_id,
                    sort_hint=index,
                    # 只有 d05 的那个点是高频考点，域的 exam_frequency 故意写低
                    exam_frequency=0.9 if (domain_id == "d05" and index == 1) else 0.3,
                )
            )
    return nodes


def _profile(*, weak_points_confidence: float = 0.8, user_id: str = "student") -> Profile:
    return Profile(
        user_id=user_id,
        knowledge_base=NumericDimension(score=55, confidence=0.9),
        learning_goal=CategoricalDimension(label="应试提分", confidence=0.9),
        learning_style=CategoricalDimension(label="文字型", confidence=0.9),
        weak_points=WeakPointsDimension(label="功效鉴别", confidence=weak_points_confidence),
        pace=NumericDimension(score=60, confidence=0.8),
        motivation=MotivationDimension(score=75, label="内驱", confidence=0.8),
        resource_preference=ResourcePreferenceDimension(label="知识图谱", confidence=0.8),
        goal=Goal(raw_text="应试提分", normalized="应试提分", confirmed_by_user=True, confidence=0.9),
    )


def _context(
    *,
    breadth: str = "考点导向",
    weak_points_confidence: float = 0.8,
    user_id: str = "student",
) -> PlanningContext:
    profile = _profile(weak_points_confidence=weak_points_confidence, user_id=user_id)
    return PlanningContext(
        user_id=user_id,
        profile=profile,
        planning_params=PlanningParams(depth="深", breadth=breadth, assessment_strictness="严格"),
        knowledge_nodes=_nodes(),
        prerequisite_edges=[
            PrerequisiteEdgeInput(from_node_id=source, to_node_id=target, order_sensitivity=sensitivity)
            for source, target, sensitivity in EDGES
        ],
        mastery={},
    )


def _agent(**kwargs) -> PathAgent:
    return PathAgent(_context(**kwargs))


def _codes(verdict) -> list[str]:
    return [item.code for item in verdict.rejected]


def _include(skip: tuple[str, ...] = ()) -> list[str]:
    return [domain_id for domain_id, _name, _exam in DOMAINS if domain_id not in skip]


def _scope_order(skip: tuple[str, ...] = ()) -> list[str]:
    """按层号排好的 include。DOMAINS 的书写顺序**不是**合法的拓扑序，
    拿它当 order 会触发层号回退修正，测出来的就不是想测的那件事了。
    """

    scope = _agent().proposal_scope()
    include = _include(skip)
    return sorted(include, key=lambda domain_id: (scope.layer_of[domain_id], include.index(domain_id)))


def _proposal(**overrides) -> PlanProposal:
    include = _scope_order()
    payload: dict[str, object] = {"coverage": {"include": include}, "order": include}
    payload.update(overrides)
    return PlanProposal.parse(payload)


# ── 校验规则：每条一个码 ──────────────────────────────────────────────────


def test_order_must_be_a_permutation_of_include() -> None:
    agent = _agent()
    order = _scope_order()[:-1]  # 少一个
    verdict = validate_proposal(_proposal(order=order), agent.proposal_scope())

    assert verdict.accepted is False
    assert "proposal.order_mismatch" in _codes(verdict)


def test_order_may_be_omitted_and_is_derived_from_include() -> None:
    """模型没写 order 不等于给了错 order——按 include 顺序补全，不整单拒。"""

    agent = _agent()
    verdict = validate_proposal(
        PlanProposal.parse({"coverage": {"include": _scope_order()}}),
        agent.proposal_scope(),
    )

    assert verdict.accepted is True
    assert verdict.order == _scope_order()
    assert verdict.rejected == []


def test_excluding_a_prereq_domain_is_auto_added() -> None:
    """排掉 d01（解表药），但 d02（清热药）依赖它 → 必须补回来，并记 auto_added。"""

    agent = _agent()
    include = _scope_order(skip=("d01",))
    verdict = validate_proposal(
        PlanProposal.parse({"coverage": {"include": include}, "order": include}),
        agent.proposal_scope(),
    )

    assert verdict.accepted is True
    assert verdict.auto_added == ["d01"]
    assert "proposal.missing_prereq" in _codes(verdict)
    # 补回来还得**放在下游之前**，否则补了个寂寞
    assert verdict.order.index("d01") < verdict.order.index("d02")
    assert set(verdict.order) == set(include) | {"d01"}


def test_prereq_chain_is_filled_transitively() -> None:
    """d01→d02 是链：排掉 d01 且排掉 d02 时，只补 d01 就够（d02 本就不在）。"""

    agent = _agent()
    include = _include(skip=("d01", "d02"))
    verdict = validate_proposal(
        PlanProposal.parse({"coverage": {"include": include}, "order": include}),
        agent.proposal_scope(),
    )

    assert verdict.auto_added == []
    assert "d01" not in verdict.order and "d02" not in verdict.order


def test_hot_domain_cannot_be_excluded_under_exam_breadth() -> None:
    agent = _agent(breadth="考点导向")
    include = _include(skip=("d05",))
    verdict = validate_proposal(
        PlanProposal.parse({"coverage": {"include": include}, "order": include}),
        agent.proposal_scope(),
    )

    assert verdict.accepted is False
    assert "proposal.exam_coverage" in _codes(verdict)


def test_hot_domain_can_be_excluded_when_breadth_is_not_exam_driven() -> None:
    """同一个提案，换 breadth 就该放行——否则"考点导向"这个旋钮等于没接。"""

    agent = _agent(breadth="全面")
    include = _include(skip=("d05",))
    verdict = validate_proposal(
        PlanProposal.parse({"coverage": {"include": include}, "order": include}),
        agent.proposal_scope(),
    )

    assert verdict.accepted is True
    assert "d05" not in verdict.order


def test_over_pruned_when_general_is_dropped() -> None:
    agent = _agent(breadth="全面")
    include = _include(skip=("general",))
    verdict = validate_proposal(
        PlanProposal.parse({"coverage": {"include": include}, "order": include}),
        agent.proposal_scope(),
    )

    assert verdict.accepted is False
    assert "proposal.over_pruned" in _codes(verdict)


def test_over_pruned_when_too_few_domains_remain() -> None:
    agent = _agent(breadth="全面")
    include = _include()[: MIN_INCLUDE_DOMAINS - 1]
    verdict = validate_proposal(
        PlanProposal.parse({"coverage": {"include": include}, "order": include}),
        agent.proposal_scope(),
    )

    assert verdict.accepted is False
    assert "proposal.over_pruned" in _codes(verdict)


def test_hotness_comes_from_points_not_from_the_domain_placeholder() -> None:
    """真实种子里 19 个域的 exam_frequency 一律是 0.8 占位值。

    若把容器自己的值算进热度，19 个域全变热点，考点导向下任何取舍都会被
    ``proposal.exam_coverage`` 拒掉——取舍功能等于没接（2026-11-11 在真实 19 域
    图上踩到，`auto_added` 一次都没触发过）。
    """

    scope = _agent().proposal_scope()

    # d05 域节点写的是 0.2，热点来自它那个 0.9 的知识点
    assert "d05" in scope.hot_domain_ids
    # general 域节点写 0.6、知识点全是 0.3：既不该因为 0.6 变热，也不该凭空变热
    assert "general" not in scope.hot_domain_ids


def test_min_include_never_exceeds_the_graph() -> None:
    """小图里下限不能大于域总数，否则任何提案都过不了——测试图只有 8 个域也一样。"""

    scope = _agent().proposal_scope()

    assert scope.min_include <= len(scope.domain_ids)


def test_strict_conflict_is_repaired_per_item_not_whole_plan() -> None:
    """顺序违规只改那一段，其余取舍保留——这是"单条拒，保留其余"的含义。"""

    agent = _agent()
    include = _include()
    shuffled = ["d02", "general", *[item for item in include if item not in {"general", "d02"}]]
    verdict = validate_proposal(
        PlanProposal.parse({"coverage": {"include": include}, "order": shuffled}),
        agent.proposal_scope(),
    )

    assert verdict.accepted is True
    assert "proposal.strict_conflict" in _codes(verdict)
    assert verdict.order.index("general") < verdict.order.index("d01") < verdict.order.index("d02")


def test_layer_regression_is_repaired_even_without_a_strict_edge() -> None:
    """层号回退不一定有边可查：把第 1 层的域提到第 0 层前面，一条 strict 边都没碰。

    只查 strict 边就会漏掉这种情况，于是"层号不得回退"变成一句空话。
    """

    agent = _agent()
    include = _include()
    order = ["d04", *[item for item in include if item != "d04"]]
    verdict = validate_proposal(
        PlanProposal.parse({"coverage": {"include": include}, "order": order}),
        agent.proposal_scope(),
    )

    assert verdict.accepted is True
    assert "proposal.strict_conflict" in _codes(verdict)
    layers = [agent.proposal_scope().layer_of[domain_id] for domain_id in verdict.order]
    assert layers == sorted(layers)
    assert verdict.order != order


def test_mastery_out_of_range_is_dropped_and_rule_value_kept() -> None:
    agent = _agent()
    over_limit = validate_proposal(
        _proposal(depth_overrides={"d02": {"target_mastery": 0.99}}),
        agent.proposal_scope(),
    )
    assert "proposal.mastery_range" in _codes(over_limit)
    assert "d02" not in over_limit.overrides

    too_shallow = validate_proposal(
        _proposal(depth_overrides={"d02": {"target_mastery": 0.4}}),
        agent.proposal_scope(),
    )
    assert "proposal.mastery_range" in _codes(too_shallow)

    in_range = validate_proposal(
        _proposal(depth_overrides={"d02": {"target_mastery": 0.92}}),
        agent.proposal_scope(),
    )
    assert "proposal.mastery_range" not in _codes(in_range)
    assert in_range.overrides["d02"].target_mastery == 0.92


def test_mastery_override_survives_with_a_valid_resource_hint() -> None:
    """同一项里掌握度越界、资源提示合法：丢掌握度、留资源，别整项作废。"""

    agent = _agent()
    verdict = validate_proposal(
        _proposal(depth_overrides={"d02": {"target_mastery": 0.99, "resource_hint": "对比记忆"}}),
        agent.proposal_scope(),
    )

    assert verdict.overrides["d02"].target_mastery is None
    assert verdict.overrides["d02"].resource_hint == "对比记忆"


def test_override_on_unknown_node_is_ignored() -> None:
    agent = _agent()
    verdict = validate_proposal(
        _proposal(depth_overrides={"d99.s1.p1": {"target_mastery": 0.9}}),
        agent.proposal_scope(),
    )

    assert "proposal.unknown_target" in _codes(verdict)
    assert "d99.s1.p1" not in verdict.overrides


def test_untrusted_basis_keeps_decision_but_drops_reason() -> None:
    """薄弱点置信度 0.3 < 0.5：可以照这个砍章，但不能拿它当砍章的理由。"""

    agent = _agent(weak_points_confidence=0.3)
    verdict = validate_proposal(
        _proposal(
            rationale=[
                {"claim": "跳过驱虫药", "basis": [{"dimension": "weak_points", "value": "功效鉴别", "confidence": 0.9}]},
                {"claim": "先攻解表", "basis": [{"dimension": "learning_goal", "value": "应试提分", "confidence": 0.9}]},
            ]
        ),
        agent.proposal_scope(),
    )

    assert verdict.accepted is True
    assert "proposal.untrusted_basis" in _codes(verdict)
    # 比的是画像实测置信度，不是模型自报的 0.9
    assert verdict.untrusted_dimensions == ["weak_points"]
    assert [item.claim for item in verdict.rationale] == ["先攻解表"]


def test_model_declared_confidence_cannot_outvote_the_profile() -> None:
    """模型给 basis 写 confidence=0.99，也救不了一个 0.31 置信度的维度。"""

    agent = _agent(weak_points_confidence=0.31)
    verdict = validate_proposal(
        _proposal(
            rationale=[
                {"claim": "砍三章", "basis": [{"dimension": "weak_points", "value": "x", "confidence": 0.99}]}
            ]
        ),
        agent.proposal_scope(),
    )

    assert verdict.rationale == []
    assert "proposal.untrusted_basis" in _codes(verdict)


def test_empty_proposal_is_rejected_instead_of_crashing() -> None:
    agent = _agent()
    verdict = validate_proposal(PlanProposal.parse({}), agent.proposal_scope())

    assert verdict.accepted is False
    assert verdict.reason


# ── 落到计划上：plan_l1 / expand_domain ───────────────────────────────────


def test_plan_l1_applies_proposal_and_records_it() -> None:
    include = _scope_order(skip=("d05", "d07"))

    async def llm(messages):
        return json.dumps(
            {
                "coverage": {
                    "include": include,
                    "exclude": [{"domain_id": "d05", "reason": "驱虫药考研低频"}],
                },
                "order": include,
                "rationale": [
                    {"claim": "先攻解表与清热", "basis": [{"dimension": "learning_goal", "value": "应试提分", "confidence": 0.9}]}
                ],
                "confidence": 0.74,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(PathAgent(_context(breadth="全面"), llm_call=llm).plan_l1())

    assert [stage.domain_id for stage in result.plan.stages] == include
    assert "先攻解表与清热" in result.suggestion
    assert result.proposal["accepted"] is True
    assert result.proposal["rejected"] == []
    assert result.model_meta["temperature"] == 0
    assert result.model_meta["input_hash"] == input_hash_of(build_input_snapshot(_context(breadth="全面")))


def test_clean_prune_without_rationale_still_says_what_was_cut() -> None:
    """提案不附理由时也不能回落到"稳妥推进"——那会被理解成一处没砍。"""

    include = _scope_order(skip=("d05", "d07"))

    async def llm(messages):
        return json.dumps({"coverage": {"include": include}, "order": include}, ensure_ascii=False)

    result = asyncio.run(PathAgent(_context(breadth="全面"), llm_call=llm).plan_l1())

    assert "跳过 2 个" in result.suggestion
    assert len(result.plan.stages) == len(include)


def test_retry_feeds_rejections_back_and_succeeds_second_time() -> None:
    """第一次把课删光 → 内核整单拒 → 把拒收理由交回模型 → 第二次收下。"""

    calls: list[dict] = []
    include = _include()

    async def llm(messages):
        payload = json.loads(messages[-1]["content"])
        calls.append(payload)
        if len(calls) == 1:
            return json.dumps(
                {"coverage": {"include": ["general", "d01"]}, "order": ["general", "d01"]},
                ensure_ascii=False,
            )
        return json.dumps({"coverage": {"include": include}, "order": include}, ensure_ascii=False)

    result = asyncio.run(PathAgent(_context(breadth="全面"), llm_call=llm).plan_l1())

    assert len(calls) == 2
    assert calls[1]["previous_rejections"][0]["code"] == "proposal.over_pruned"
    assert result.fallback is False
    assert len(result.plan.stages) == len(include)


def test_giving_up_after_retries_falls_back_to_rule_plan() -> None:
    async def llm(messages):
        return json.dumps({"coverage": {"include": ["general", "d01"]}, "order": ["general", "d01"]}, ensure_ascii=False)

    result = asyncio.run(PathAgent(_context(breadth="全面"), llm_call=llm).plan_l1())

    assert result.fallback is True
    assert len(result.plan.stages) == len(DOMAINS)
    assert "回退规则路线" in result.suggestion


def test_giving_up_retries_exactly_max_times() -> None:
    calls: list[int] = []

    async def llm(messages):
        calls.append(1)
        raise OSError("provider unavailable")

    result = asyncio.run(PathAgent(_context(breadth="全面"), llm_call=llm).plan_l1())

    assert len(calls) == 1, "provider 挂了不该重试；重试只用于提案不合法"
    assert result.fallback is False
    assert len(result.plan.stages) == len(DOMAINS)


def test_malformed_proposal_shape_falls_back_instead_of_raising() -> None:
    """模型把 include 写成 [1,2,3]：该退回底稿，不该让 500 冒到接口上。"""

    async def llm(messages):
        return json.dumps({"coverage": {"include": [1, 2, 3]}, "order": [1, 2, 3]})

    result = asyncio.run(PathAgent(_context(breadth="全面"), llm_call=llm).plan_l1())

    assert result.fallback is False
    assert len(result.plan.stages) == len(DOMAINS)
    assert result.proposal is None


def test_depth_override_reaches_point_level_expansion() -> None:
    """逐点覆盖：点级 > 域级 > 规则值。以前是"整域一个值"，看起来千人一面。"""

    agent = PathAgent(_context())
    plan = asyncio.run(
        agent.expand_domain(
            "d01",
            overrides={
                "d01": {"target_mastery": 0.9, "resource_hint": "对比记忆"},
                "d01.s1.p2": {"target_mastery": 0.95},
            },
        )
    )

    by_id = {node.node_id: node for node in plan.plan.nodes}
    assert by_id["d01.s1.p1"].target_mastery == 0.9
    assert by_id["d01.s1.p1"].resource_hint == "对比记忆"
    assert by_id["d01.s1.p2"].target_mastery == 0.95
    # 点级覆盖了掌握度，但没覆盖资源 → 继承域级
    assert by_id["d01.s1.p2"].resource_hint == "对比记忆"
    assert "模型逐点覆盖" in by_id["d01.s1.p2"].reason


def test_expansion_without_override_matches_rule_value() -> None:
    agent = PathAgent(_context())
    plan = asyncio.run(agent.expand_domain("d01"))
    expected = TARGET_BY_DEPTH["深"] + STRICTNESS_ADJUSTMENT["严格"]

    assert {node.target_mastery for node in plan.plan.nodes} == {round(expected, 2)}
    assert all("模型逐点覆盖" not in node.reason for node in plan.plan.nodes)


def test_override_mastery_is_clamped_even_if_it_slips_past_validation() -> None:
    """校验层已滤过越界项；展开层再夹一次，防的是从 payload_json 读回旧数据。"""

    plan = asyncio.run(
        PathAgent(_context()).expand_domain("d01", overrides={"d01.s1.p1": {"target_mastery": 5.0}})
    )

    node = next(item for item in plan.plan.nodes if item.node_id == "d01.s1.p1")
    assert MIN_TARGET_MASTERY <= node.target_mastery <= MAX_TARGET_MASTERY


# ── 可复现：同画像 → 同 input_hash → 同计划，第二次不再问模型 ─────────────


def test_same_profile_yields_identical_input_hash_and_plan() -> None:
    include = _include()

    async def llm(messages):
        return json.dumps({"coverage": {"include": include}, "order": include}, ensure_ascii=False)

    first = PathAgent(_context(), llm_call=llm)
    second = PathAgent(_context(), llm_call=llm)
    first_plan = asyncio.run(first.plan_l1())
    second_plan = asyncio.run(second.plan_l1())

    assert first.input_hash == second.input_hash
    assert first.input_hash == input_hash_of(build_input_snapshot(_context()))
    assert first_plan.plan.model_dump() == second_plan.plan.model_dump()


def test_input_hash_moves_when_a_dimension_moves() -> None:
    """hash 必须对画像敏感，否则"复用"会把旧画像的路线锁死在新画像上。"""

    base = input_hash_of(build_input_snapshot(_context()))
    other_breadth = input_hash_of(build_input_snapshot(_context(breadth="全面")))
    other_confidence = input_hash_of(build_input_snapshot(_context(weak_points_confidence=0.4)))

    assert base != other_breadth
    assert base != other_confidence


def test_input_hash_is_user_agnostic_because_the_lookup_is_not() -> None:
    """两个画像完全一致的用户算出同一个 hash 是对的：复用查询本身按 user_id 过滤。

    把 user_id 塞进 hash 只会让"同一份输入"看起来不同，反而破坏了 A/B 对比。
    """

    assert PathAgent(_context(user_id="a")).input_hash == PathAgent(_context(user_id="b")).input_hash


def test_input_hash_ignores_mastery_progress() -> None:
    """进度变了不该逼着重新问模型要取舍——那是 L3 的活。"""

    context = _context()
    progressed = context.model_copy(deep=True)
    progressed.mastery = {"d01.s1.p1": {"p_mastery": 0.9, "attempts": 3, "last_seen": None}}

    assert input_hash_of(build_input_snapshot(context)) == input_hash_of(build_input_snapshot(progressed))


def _seed_graph(db_path: Path, *, user_id: str = "u1", breadth: str = "全面") -> None:
    """把图与画像写进临时库：service 层从库里读，不吃的内存里构造好的对象。"""

    upsert_profile(
        _profile(user_id=user_id).model_copy(
            update={"planning_params": PlanningParams(depth="深", breadth=breadth, assessment_strictness="严格")}
        ),
        db_path=db_path,
    )
    for domain_id, name, exam in DOMAINS:
        upsert_node({"id": domain_id, "name": name, "level": "domain", "exam_frequency": exam}, db_path=db_path)
    for domain_id, _name, _exam in DOMAINS:
        subtype_id = f"{domain_id}.s1"
        upsert_node(
            {"id": subtype_id, "name": f"{domain_id} 第一节", "level": "subtype", "parent_id": domain_id},
            db_path=db_path,
        )
        for index in (1, 2):
            upsert_node(
                {
                    "id": f"{subtype_id}.p{index}",
                    "name": f"{domain_id} 知识点{index}",
                    "level": "point",
                    "parent_id": subtype_id,
                    "sort_hint": index,
                    "exam_frequency": 0.9 if (domain_id == "d05" and index == 1) else 0.3,
                },
                db_path=db_path,
            )
    for source, target, sensitivity in EDGES:
        upsert_edge(
            {
                "from_node_id": source,
                "to_node_id": target,
                "order_sensitivity": sensitivity,
                "reason": "test",
            },
            db_path=db_path,
        )


def test_repeated_plan_request_reuses_persisted_l1(tmp_path: Path) -> None:
    """同画像第二次请求：命中 input_hash，直接返回同一条版本，不再问模型。"""

    db_path = tmp_path / "plans.db"
    _seed_graph(db_path)
    include = _include()
    calls: list[int] = []

    async def llm(messages):
        calls.append(1)
        return json.dumps(
            {
                "coverage": {"include": include},
                "order": include,
                "depth_overrides": {"d01.s1.p1": {"target_mastery": 0.93}},
                "rationale": [
                    {"claim": "先攻解表", "basis": [{"dimension": "learning_goal", "value": "应试提分", "confidence": 0.9}]}
                ],
            },
            ensure_ascii=False,
        )

    first = asyncio.run(create_l1_plan("u1", db_path=db_path, llm_call=llm))
    second = asyncio.run(create_l1_plan("u1", db_path=db_path, llm_call=llm))

    assert len(calls) == 1, "第二次不该再调模型"
    assert first.version == second.version == 1
    assert first.model_meta["input_hash"] == second.model_meta["input_hash"]
    assert [item["version"] for item in list_plan_versions("u1", db_path)] == [1]

    # 三样都落了库
    payload = list_plan_versions("u1", db_path)[0]["payload"]
    assert payload["proposal"]["overrides"]["d01.s1.p1"]["target_mastery"] == 0.93
    assert payload["model_meta"]["temperature"] == 0
    assert payload["input_snapshot"]["knowledge_graph"]["domain_count"] == len(DOMAINS)
    assert payload["input_snapshot"]["profile"]["learning_goal"]["confidence"] == 0.9


def test_replan_ignores_the_cached_plan(tmp_path: Path) -> None:
    """用户主动"重新规划"时不能复用：他要的是新草稿，不是旧版本原样返还。"""

    db_path = tmp_path / "plans.db"
    _seed_graph(db_path)

    async def llm(messages):
        return json.dumps({"coverage": {"include": _include()}, "order": _include()}, ensure_ascii=False)

    first = asyncio.run(create_l1_plan("u1", db_path=db_path, llm_call=llm))
    again = asyncio.run(create_l1_plan("u1", db_path=db_path, llm_call=llm, reuse=False))

    assert first.version == 1
    assert again.version == 2


def test_fallback_plan_is_not_reused(tmp_path: Path) -> None:
    """底稿是"模型没给成"的产物，下次该重新问，不该被永久固化。"""

    db_path = tmp_path / "plans.db"
    _seed_graph(db_path)

    async def bad_llm(messages):
        return json.dumps({"coverage": {"include": ["general", "d01"]}, "order": ["general", "d01"]}, ensure_ascii=False)

    first = asyncio.run(create_l1_plan("u1", db_path=db_path, llm_call=bad_llm))

    assert first.fallback is True
    assert find_reusable_l1("u1", first.model_meta["input_hash"], db_path) is None


def test_l2_reads_overrides_from_the_latest_l1_payload(tmp_path: Path) -> None:
    """提案留下的逐点掌握度要活到 L2：L1 只排阶段、不排节点，覆盖不落库就等于丢了。"""

    db_path = tmp_path / "plans.db"
    _seed_graph(db_path)

    async def llm(messages):
        return json.dumps(
            {
                "coverage": {"include": _include()},
                "order": _include(),
                "depth_overrides": {"d01.s1.p1": {"target_mastery": 0.94}},
            },
            ensure_ascii=False,
        )

    asyncio.run(create_l1_plan("u1", db_path=db_path, llm_call=llm))
    assert latest_l1_overrides("u1", db_path)["d01.s1.p1"]["target_mastery"] == 0.94

    expanded = asyncio.run(create_l2_plan("u1", "d01", db_path=db_path, llm_call=llm))
    by_id = {node.node_id: node for node in expanded.nodes}

    assert by_id["d01.s1.p1"].target_mastery == 0.94
    assert by_id["d01.s1.p2"].target_mastery == round(
        TARGET_BY_DEPTH["深"] + STRICTNESS_ADJUSTMENT["严格"], 2
    )


def test_low_confidence_dimension_is_hidden_from_the_prompt() -> None:
    """打码过的画像不能被提案理由引用——两条路径必须看同一份置信度。"""

    agent = PathAgent(_context(weak_points_confidence=0.2))
    safe = agent._safe_profile_for_llm()

    assert safe["weak_points"]["value"] == "保守默认"
    assert safe["weak_points"]["confidence"] < CONFIDENCE_THRESHOLD
    assert MAX_PROPOSAL_RETRIES >= 1
