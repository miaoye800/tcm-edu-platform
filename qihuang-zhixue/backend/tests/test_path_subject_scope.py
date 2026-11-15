"""P10 学科层：让模型真正参与取舍。

这一组盯的是三件在真机上被用户抓到的事：

1. **路线不再按章号跨学科交织**。域节点的 `sort_hint` 是学科内章号（d01 与 f01
   都是 1），而六门课之间一条先修边都没有，于是过去 90 个域被排成
   "六门课的第 1 章 → 六门课的第 2 章"。教材内部正确的章序，制造了整体上错误的顺序。
2. **用户原话真的进了模型**。"我已经掌握中药学"过去只被一张关键词表翻成
   breadth/pace/depth 就丢掉了，模型从头到尾没看见过这句话。
3. **"已掌握"要有据**。模型不能凭一句"看起来学过"把一门课砍掉；但用户亲口说了，
   规则也不该否决他。
"""

from __future__ import annotations

import asyncio
import json

from app.path.planner import PathAgent, build_input_snapshot, input_hash_of
from app.path.proposal import PlanProposal, validate_proposal
from app.path.schema import KnowledgeNodeInput, PlanningContext, PrerequisiteEdgeInput
from app.path.subjects import CANONICAL_ORDER, order_key, subject_of, title_of
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

# 九章 / 六科。章号故意都从 1 开始——真实种子就是这样，
# 也是"按章号排就会交织"的根。
DOMAINS: tuple[tuple[str, str, str], ...] = (
    ("general", "中药学总论", "herb"),
    ("b01", "中医学的哲学基础", "basic"),
    ("b02", "藏象", "basic"),
    ("z01", "望诊", "diagnosis"),
    ("d01", "解表药", "herb"),
    ("d02", "清热药", "herb"),
    ("f01", "方剂的起源与发展", "formula"),
    ("a01", "经络总论", "acup"),
    ("n01", "肺系病证", "internal"),
)


def _nodes() -> list[KnowledgeNodeInput]:
    nodes: list[KnowledgeNodeInput] = []
    for index, (domain_id, name, _subject) in enumerate(DOMAINS):
        nodes.append(
            KnowledgeNodeInput(
                id=domain_id,
                name=name,
                level="domain",
                # 真实种子里同一年级跨学科共享一个 sort_hint，正是交织的来源
                sort_hint=index % 3,
                exam_frequency=0.8 if domain_id.startswith("d") else 0.6,
            )
        )
        subtype = f"{domain_id}.s1"
        nodes.append(
            KnowledgeNodeInput(id=subtype, name=f"{name} 第一节", level="subtype", parent_id=domain_id, sort_hint=1)
        )
        for point in (1, 2):
            nodes.append(
                KnowledgeNodeInput(
                    id=f"{subtype}.p{point}",
                    name=f"{name} 知识点{point}",
                    level="point",
                    parent_id=subtype,
                    sort_hint=point,
                    exam_frequency=0.9 if domain_id == "d01" and point == 1 else 0.3,
                )
            )
    return nodes


def _profile(*, knowledge: int = 55, confidence: float = 0.8) -> Profile:
    return Profile(
        user_id="student",
        knowledge_base=NumericDimension(score=knowledge, confidence=confidence),
        learning_goal=CategoricalDimension(label="系统学习", confidence=0.9),
        learning_style=CategoricalDimension(label="文字型", confidence=0.9),
        weak_points=WeakPointsDimension(label="功效鉴别", confidence=0.8),
        pace=NumericDimension(score=60, confidence=0.8),
        motivation=MotivationDimension(score=75, label="内驱", confidence=0.8),
        resource_preference=ResourcePreferenceDimension(label="知识图谱", confidence=0.8),
        goal=Goal(raw_text="系统学习", normalized=None, confirmed_by_user=True, confidence=0.9),
    )


def _context(*, request: str = "", route_state: dict[str, str] | None = None) -> PlanningContext:
    return PlanningContext(
        user_id="student",
        profile=_profile(),
        planning_params=PlanningParams(breadth="全面"),
        knowledge_nodes=_nodes(),
        # 真实图里域与域之间**一条先修边都没有**（`hierarchy` 边归另一张语义，
        # 规划器只读 `prerequisite`）。这里照实仿：只给节内"按教材编排顺序"，
        # 也就是"章与章之间不存在依赖"——这正是交织能发生的前提。
        prerequisite_edges=[
            PrerequisiteEdgeInput(
                from_node_id=f"{domain_id}.s1.p1",
                to_node_id=f"{domain_id}.s1.p2",
                order_sensitivity="strict",
            )
            for domain_id, _name, _subject in DOMAINS
        ],
        mastery={},
        request_text=request,
        route_state=route_state or {},
    )


def _agent(**kwargs) -> PathAgent:
    return PathAgent(_context(**kwargs))


def _codes(verdict) -> list[str]:
    return [item.code for item in verdict.rejected]


# ── 学科推导 ──────────────────────────────────────────────────────────────


def test_subject_is_derived_from_the_domain_prefix() -> None:
    assert subject_of("b04") == "basic"
    assert subject_of("z05") == "diagnosis"
    assert subject_of("d17") == "herb"
    assert subject_of("f22") == "formula"
    assert subject_of("a04") == "acup"
    assert subject_of("n05") == "internal"
    # 中药学总论没有前缀，但它是中药学的第一章
    assert subject_of("general") == "herb"
    # 推不出来的不硬塞进某门课
    assert subject_of("custom:abc") == ""
    assert title_of("herb") == "中药学"


def test_canonical_order_puts_foundations_first() -> None:
    assert order_key("basic") < order_key("diagnosis") < order_key("herb") < order_key("formula")
    assert order_key("formula") < order_key("internal")
    assert order_key("unknown") >= len(CANONICAL_ORDER)


# ── 顺序：学科成块 ────────────────────────────────────────────────────────


def test_base_plan_is_grouped_by_subject_not_by_chapter_number() -> None:
    """没有模型参与时，底稿也不能按章号跨学科来回跳。"""

    plan = asyncio.run(_agent().plan_l1()).plan
    subjects = [subject_of(stage.domain_id) for stage in plan.stages]

    # 同一门课的章必须连续：把重复出现的学科视为"块被打断"
    seen: list[str] = []
    for subject in subjects:
        if subject and (not seen or seen[-1] != subject):
            seen.append(subject)
    assert len(seen) == len(set(subjects)), f"学科被打断：{subjects}"
    # 块内保持教材顺序：中基在前，内科在后
    assert subjects.index("basic") < subjects.index("herb") < subjects.index("internal")


def test_subject_order_decides_the_blocks_and_keeps_chapters_inside() -> None:
    agent = _agent()
    verdict = validate_proposal(
        PlanProposal.parse({"subjects": [{"subject_id": "herb", "decision": "study"}], "subject_order": ["herb", "basic"]}),
        agent.proposal_scope(),
    )

    assert verdict.accepted is True
    # 学科先后听模型的
    assert verdict.order[0].startswith("d") or verdict.order[0] == "general"
    # 但章序仍由系统按拓扑铺：解表药在清热药之前，不会因为"想先学清热"就颠倒
    assert verdict.order.index("general") < verdict.order.index("d01") < verdict.order.index("d02")


# ── 整科跳过：要证据 ──────────────────────────────────────────────────────


def test_user_words_are_evidence_for_skipping_a_subject() -> None:
    agent = _agent(request="我想学中医，已经掌握中药学，想学其他学科的完整内容")
    verdict = validate_proposal(
        PlanProposal.parse(
            {
                "subjects": [{"subject_id": "herb", "decision": "skip", "reason": "用户已掌握中药学"}],
            }
        ),
        agent.proposal_scope(),
    )

    assert verdict.accepted is True
    assert verdict.exempt_subjects == ["herb"]
    assert not {"general", "d01", "d02"} & set(verdict.order)


def test_skipping_a_subject_without_evidence_is_refused() -> None:
    """同一份提案，用户没说过学过 → 学科留下，声明进 rejected[] 给用户看。"""

    agent = _agent(request="我想学中医，想学其他学科的完整内容")
    verdict = validate_proposal(
        PlanProposal.parse({"subjects": [{"subject_id": "herb", "decision": "skip", "reason": "看起来学过"}]}),
        agent.proposal_scope(),
    )

    assert verdict.accepted is True
    assert verdict.exempt_subjects == []
    assert "proposal.untrusted_mastery" in _codes(verdict)
    assert "d01" in verdict.order


def test_mastery_ratio_also_counts_as_evidence() -> None:
    """没说话，但真的做过题：六成以上知识点达到掌握线，也算有据。"""

    context = _context()
    context.mastery = {
        point_id: type("M", (), {"p_mastery": 0.9, "attempts": 3})()
        for domain_id, _name, subject in DOMAINS
        if subject == "herb"
        for point_id in (f"{domain_id}.s1.p1", f"{domain_id}.s1.p2")
    }
    verdict = validate_proposal(
        PlanProposal.parse({"subjects": [{"subject_id": "herb", "decision": "skip"}]}),
        PathAgent(context).proposal_scope(),
    )

    assert verdict.exempt_subjects == ["herb"]
    assert "proposal.untrusted_mastery" not in _codes(verdict)


def test_silently_deleting_a_subject_is_rejected() -> None:
    """章级微调把一门课删到零、又没在 subjects 里表态 → 整单拒，交回模型重写。"""

    agent = _agent()
    verdict = validate_proposal(
        PlanProposal.parse({"skip_chapters": ["general", "d01", "d02"]}),
        agent.proposal_scope(),
    )

    assert verdict.accepted is False
    assert "proposal.subject_dropped" in _codes(verdict)
    assert "中药学" in verdict.reason


def test_general_is_required_only_while_herb_is_in_scope() -> None:
    agent = _agent()
    dropped = validate_proposal(PlanProposal.parse({"skip_chapters": ["general"]}), agent.proposal_scope())
    assert dropped.accepted is False
    assert "总论" in dropped.reason

    # 中药学整科跳过（有原话为证）时，"总论必学"不该再反过来否掉用户的前提
    exempted = validate_proposal(
        PlanProposal.parse({"subjects": [{"subject_id": "herb", "decision": "skip"}]}),
        _agent(request="我已经掌握中药学").proposal_scope(),
    )
    assert exempted.accepted is True


# ── 逐点跳过 ──────────────────────────────────────────────────────────────


def test_skip_points_leave_the_route_and_are_reported() -> None:
    agent = _agent()
    result = asyncio.run(
        PathAgent(agent.context).expand_domain(
            "d01", overrides={"d01.s1.p1": {"skip": True, "reason": "已掌握"}}
        )
    )

    assert [node.node_id for node in result.plan.nodes] == ["d01.s1.p2"]
    assert result.skipped == [{"node_id": "d01.s1.p1", "name": "解表药 知识点1", "reason": "已掌握"}]
    # 序号必须连续：被免修的点不占位置，否则 _next_node_id 会跳过一格
    assert [node.seq for node in result.plan.nodes] == [1]


def test_skip_points_from_the_proposal_reach_the_expansion() -> None:
    agent = _agent()
    verdict = validate_proposal(
        PlanProposal.parse({"skip_points": [{"node_id": "d01.s1.p1", "reason": "基础"}]}),
        agent.proposal_scope(),
    )

    assert verdict.accepted is True
    assert verdict.overrides["d01.s1.p1"].skip is True
    assert verdict.overrides["d01.s1.p1"].reason == "基础"


def test_unknown_skip_point_is_dropped_not_fatal() -> None:
    agent = _agent()
    verdict = validate_proposal(
        PlanProposal.parse({"skip_points": [{"node_id": "zzz.s1.p1"}]}),
        agent.proposal_scope(),
    )

    assert verdict.accepted is True
    assert "proposal.unknown_target" in _codes(verdict)


# ── 进模型的输入 ──────────────────────────────────────────────────────────


def test_the_prompt_carries_the_user_words_and_the_subject_view() -> None:
    """原话、学科汇总、已有路线，三样都要在 prompt 里。

    这不是"多给点上下文"，而是这个 bug 的根：模型没看见"已经掌握中药学"，
    它当然会把中药学排进来。
    """

    captured: list[dict] = []

    async def llm(messages):
        captured.append(json.loads(messages[-1]["content"]))
        return json.dumps({"subjects": [{"subject_id": "herb", "decision": "study"}]}, ensure_ascii=False)

    agent = _agent(request="我想学中医，已经掌握中药学，想学其他学科的完整内容", route_state={"b01.s1.p1": "completed"})
    asyncio.run(PathAgent(agent.context, llm_call=llm).plan_l1())

    payload = captured[0]
    assert payload["request"] == "我想学中医，已经掌握中药学，想学其他学科的完整内容"
    assert {item["subject_id"] for item in payload["subjects"]} == {
        "herb",
        "basic",
        "diagnosis",
        "formula",
        "acup",
        "internal",
    }
    herb = next(item for item in payload["subjects"] if item["subject_id"] == "herb")
    assert herb["chapters"] == 3
    assert payload["existing_route"]["has_plan"] is True
    assert payload["existing_route"]["completed_points"] == 1


def test_input_hash_changes_when_the_request_changes() -> None:
    """换个诉求就是另一份计划；不含原话的 hash 会让第二次请求命中第一次的缓存。"""

    base = input_hash_of(build_input_snapshot(_context(request="我想学方剂学")))
    other = input_hash_of(build_input_snapshot(_context(request="我只想补针灸")))
    same = input_hash_of(build_input_snapshot(_context(request="我想学方剂学")))

    assert base == same
    assert base != other


# ── 范围必须在裁域阶段生效（真机上被抓到的那条） ─────────────────────────── #


def test_scope_cuts_the_domains_before_the_model_sees_them() -> None:
    """「只考中药学」拿到的不该是六门课的先修链。

    范围只拼进 ``request_text`` 时，模型照做反而被 ``over_pruned`` 整单拒，
    最后回退全量——用户看到的是从「中医学的哲学基础」起步的计划。
    """

    plan = asyncio.run(PathAgent(_context(), subjects=["herb"]).plan_l1()).plan

    assert {subject_of(stage.domain_id) for stage in plan.stages} == {"herb"}
    # 中药学内部仍按教材章序：总论 → 解表 → 清热
    assert [stage.domain_id for stage in plan.stages] == ["general", "d01", "d02"]


def test_create_l1_plan_hands_goal_scope_and_params_to_the_agent(monkeypatch, tmp_path) -> None:
    """``service.create_l1_plan`` 是对话与接口的公共入口，两个参数都不能丢。

    真机故障链：``plan.create`` 把 goal 扔了 → 这里没拿到 request_text →
    范围退回全图、breadth 退回画像默认 → 排出一条与用户诉求无关的路线，
    而两侧的日志都"看起来正常"。
    """

    from app.path import service as svc
    from app.path.schema import L1PlanResult, PathPlan

    captured: dict[str, object] = {}

    class _FakeAgent:
        def __init__(self, context, *, subjects=None, llm_call=None, replan_hook=None) -> None:  # noqa: ANN001, ARG002
            captured["subjects"] = list(subjects or ())
            captured["breadth"] = context.planning_params.breadth
            captured["request"] = context.request_text

        async def plan_l1(self) -> L1PlanResult:
            return L1PlanResult(plan=PathPlan(stages=[], nodes=[]), suggestion="")

    monkeypatch.setattr(svc, "load_planning_context", lambda *a, **k: _context(request=k.get("request_text") or ""))
    monkeypatch.setattr(svc, "PathAgent", _FakeAgent)

    asyncio.run(
        svc.create_l1_plan("student", db_path=tmp_path / "plan.db", request_text="先中药，15 天后期末考")
    )

    assert captured["subjects"] == ["herb"]
    assert captured["breadth"] == "考点导向"
    assert captured["request"] == "先中药，15 天后期末考"
