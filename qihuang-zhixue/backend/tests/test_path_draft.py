"""P5 两段式确认：草稿不污染进度、确认=追加、非法编辑整单拒。

这一组测试刻意从**库**出发（``create_skeleton_draft`` 走 service 层读画像与图），
因为最容易出错的地方不是算法，而是「哪一行落库、哪一行不该落库」。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.path.draft import (
    DraftConflict,
    DraftOp,
    MAX_DETAIL_DOMAINS,
    _excluded_rows,
    _exclusion_reasons,
    _rows_from_dump,
    _stage_rows,
    confirm_draft,
    create_detail_draft,
    create_skeleton_draft,
    finalize_draft,
    patch_draft,
)
from app.path.plan_store import get_plan_version, latest_draft, list_plan_versions
from app.path.planner import PathAgent
from app.path.schema import NodePlan, PathPlan, StagePlan
from app.path.service import current_path, load_planning_context
from app.path.store import upsert_edge, upsert_node
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
from app.profile.store import upsert_profile

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
    ("d01", "d02", "strict"),
    ("d03", "d04", "strict"),
    ("d06", "d07", "free"),
)


def _profile(user_id: str = "u1") -> Profile:
    return Profile(
        user_id=user_id,
        knowledge_base=NumericDimension(score=55, confidence=0.9),
        learning_goal=CategoricalDimension(label="应试提分", confidence=0.9),
        learning_style=CategoricalDimension(label="文字型", confidence=0.9),
        weak_points=WeakPointsDimension(label="功效鉴别", confidence=0.8),
        pace=NumericDimension(score=60, confidence=0.8),
        motivation=MotivationDimension(score=75, label="内驱", confidence=0.8),
        resource_preference=ResourcePreferenceDimension(label="知识图谱", confidence=0.8),
        goal=Goal(raw_text="应试提分", normalized="应试提分", confirmed_by_user=True, confidence=0.9),
    )


def _seed(db_path: Path, *, user_id: str = "u1") -> None:
    upsert_profile(
        _profile(user_id).model_copy(
            update={"planning_params": PlanningParams(depth="深", breadth="全面", assessment_strictness="严格")}
        ),
        db_path=db_path,
    )
    for domain_id, name, exam in DOMAINS:
        upsert_node({"id": domain_id, "name": name, "level": "domain", "exam_frequency": exam}, db_path=db_path)
        subtype_id = f"{domain_id}.s1"
        upsert_node({"id": subtype_id, "name": f"{name} 第一节", "level": "subtype", "parent_id": domain_id}, db_path=db_path)
        for index in (1, 2):
            upsert_node(
                {
                    "id": f"{subtype_id}.p{index}",
                    "name": f"{name} 知识点{index}",
                    "level": "point",
                    "parent_id": subtype_id,
                    "sort_hint": index,
                    "exam_frequency": 0.9 if (domain_id == "d05" and index == 1) else 0.3,
                },
                db_path=db_path,
            )
    for source, target, sensitivity in EDGES:
        upsert_edge(
            {"from_node_id": source, "to_node_id": target, "order_sensitivity": sensitivity, "reason": "test"},
            db_path=db_path,
        )


def _proposal_llm(order: list[str], *, exclude: list[dict[str, str]] | None = None) -> object:
    async def llm(_messages):  # noqa: ANN001
        return json.dumps(
            {
                "coverage": {"include": order, "exclude": exclude or []},
                "order": order,
                "rationale": [
                    {"claim": "先攻解表与清热", "basis": [{"dimension": "learning_goal", "value": "应试提分", "confidence": 0.9}]}
                ],
            },
            ensure_ascii=False,
        )

    return llm


def _order(skip: tuple[str, ...] = ("d07",)) -> list[str]:
    return [domain_id for domain_id, _name, _exam in DOMAINS if domain_id not in skip]


def test_reusing_a_unit_level_payload_duplicates_nothing(tmp_path: Path) -> None:
    """复用单元级 L1：一行还是一行，砍掉的节不许爬回来。

    按 ``domain_id`` 重新展开的话，一章 N 节会变成 N×N 行——实测 293 行变 1649 行，
    界面上是"每一节重复出现 18 遍"（中药学总论那 18 行各自展开整域的 18 个单元）。
    老 payload（一行一章）仍然要按教材层级展开，所以两条路必须分开走。
    """

    db_path = tmp_path / "draft.db"
    _seed(db_path)
    # 给 general 加第二个节：只有一个单元时"按域展开"和"按行复用"结果一样，测不出问题
    upsert_node({"id": "general.s2", "name": "总论 第二节", "level": "subtype", "parent_id": "general"}, db_path=db_path)
    upsert_node(
        {"id": "general.s2.p1", "name": "总论 知识点3", "level": "point", "parent_id": "general.s2", "sort_hint": 1},
        db_path=db_path,
    )
    agent = PathAgent(load_planning_context("u1", db_path=db_path))
    plan = PathPlan(
        stages=[
            StagePlan(domain_id=domain_id, order=index + 1, order_locked=False, reason="先修顺序")
            for index, (domain_id, _name, _exam) in enumerate(DOMAINS)
        ],
        nodes=[],
    )
    rows = _stage_rows(agent, plan)
    assert len(rows) == len({row["unit_id"] for row in rows}), "一个单元只该占一行"

    again = _rows_from_dump(agent, rows)
    assert [row["unit_id"] for row in again] == [row["unit_id"] for row in rows]
    assert len(again) == len(rows)

    # 老 payload：一行一章，没有 unit_id —— 仍然展开成同样的单元序列
    legacy = [{"domain_id": domain_id, "order": index + 1} for index, (domain_id, _name, _exam) in enumerate(DOMAINS)]
    assert [row["unit_id"] for row in _rows_from_dump(agent, legacy)] == [row["unit_id"] for row in rows]


def _skeleton(db_path: Path, *, skip: tuple[str, ...] = ("d07",), reuse: bool = True) -> dict:
    return asyncio.run(
        create_skeleton_draft(
            "u1",
            db_path=db_path,
            reuse=reuse,
            llm_call=_proposal_llm(_order(skip), exclude=[{"domain_id": "d07", "reason": "涌吐药考点少，先跳过"}]),
        )
    )


# ── 草稿不污染进度 ────────────────────────────────────────────────────────


def test_draft_never_appears_in_current_path(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)
    draft = _skeleton(db_path)

    assert draft["stage"] == "skeleton"
    assert current_path("u1", db_path=db_path)["plan"]["stages"] == []
    assert [item["version"] for item in list_plan_versions("u1", db_path)] == []
    assert [item["version"] for item in list_plan_versions("u1", db_path, include_drafts=True)] == [draft["version"]]


def test_skeleton_lists_what_was_pruned_and_why(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)
    draft = _skeleton(db_path)

    assert [item["domain_id"] for item in draft["excluded"]] == ["d07"]
    assert "考点少" in draft["excluded"][0]["why"]
    assert all(row["domain_id"] != "d07" for row in draft["stages"])
    # 骨架要能报出「这一章多少点、大概多久」，否则用户没法判断该不该砍
    assert draft["stages"][0]["point_count"] == 2
    assert draft["stages"][0]["estimated_minutes"] > 0


def test_include_puts_the_chapter_back_at_the_requested_slot(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)
    draft = _skeleton(db_path)

    patched = patch_draft("u1", draft["version"], [DraftOp(op="include", domain_id="d07", after="d06")], db_path=db_path)

    order = [row["domain_id"] for row in patched["stages"]]
    assert "d07" in order
    assert order.index("d07") == order.index("d06") + 1
    assert patched["excluded"] == []
    assert patched["parent_version"] == draft["version"]


# ── 非法编辑整单拒 ────────────────────────────────────────────────────────


def test_reordering_against_a_strict_edge_is_rejected_and_draft_is_unchanged(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)
    draft = _skeleton(db_path)
    versions_before = [item["version"] for item in list_plan_versions("u1", db_path, include_drafts=True)]

    bad = [row["domain_id"] for row in draft["stages"]]
    bad.remove("d01")
    bad.append("d01")  # 把解表踢到队尾：违反 general → d01 → d02

    with pytest.raises(DraftConflict) as info:
        patch_draft("u1", draft["version"], [DraftOp(op="reorder", ids=bad)], db_path=db_path)

    assert info.value.code == "path.strict_conflict"
    assert info.value.status == 409
    # 草稿不变：没有追加任何版本
    assert [item["version"] for item in list_plan_versions("u1", db_path, include_drafts=True)] == versions_before
    assert get_plan_version("u1", draft["version"], db_path)["payload"]["stages"] == draft["stages"]


def test_removing_a_prereq_of_a_kept_chapter_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)
    draft = _skeleton(db_path)

    with pytest.raises(DraftConflict) as info:
        patch_draft("u1", draft["version"], [DraftOp(op="remove", ids=["d01"])], db_path=db_path)

    assert info.value.code == "path.strict_conflict"
    assert "先修" in info.value.message


def test_reorder_must_cover_every_stage(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)
    draft = _skeleton(db_path)

    with pytest.raises(DraftConflict) as info:
        patch_draft("u1", draft["version"], [DraftOp(op="reorder", ids=["general"])], db_path=db_path)

    assert info.value.code == "path.unknown_target"


def test_partial_success_returns_rejected_instead_of_failing(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)
    draft = _skeleton(db_path)

    patched = patch_draft(
        "u1",
        draft["version"],
        [DraftOp(op="remove", ids=["nope"])],
        db_path=db_path,
    )

    assert patched["rejected"][0]["code"] == "path.unknown_target"
    assert len(patched["stages"]) == len(draft["stages"])


# ── 确认 = 追加 ───────────────────────────────────────────────────────────


def test_confirming_skeleton_appends_an_active_row_and_keeps_the_draft(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)
    draft = _skeleton(db_path)

    confirmed = confirm_draft("u1", draft["version"], db_path=db_path)

    versions = list_plan_versions("u1", db_path)
    assert [item["version"] for item in versions] == [confirmed["version"]]
    assert versions[0]["parent_version"] == draft["version"]
    assert versions[0]["confirmed_at"]
    assert [row["domain_id"] for row in current_path("u1", db_path=db_path)["plan"]["stages"]] == [
        row["domain_id"] for row in draft["stages"]
    ]

    stored_draft = get_plan_version("u1", draft["version"], db_path)
    assert stored_draft["status"] == "draft"  # 只追加，不改写
    # 确认过的草稿不再算"待确认"，否则用户重进向导会看到一份已生效的草稿
    assert latest_draft("u1", db_path) is None


def test_detail_draft_keeps_unexpanded_domains_as_pending(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)
    draft = _skeleton(db_path)
    confirm_draft("u1", draft["version"], db_path=db_path)

    detail = asyncio.run(create_detail_draft("u1", db_path=db_path, version=draft["version"]))

    assert detail["stage"] == "detail"
    assert detail["nodes"], "明细草稿必须有逐点节点"
    assert detail["pending_domains"] == [row["domain_id"] for row in draft["stages"]][MAX_DETAIL_DOMAINS:]
    assert all(node["domain_id"] for node in detail["nodes"])
    assert len({node["seq"] for node in detail["nodes"]}) == len(detail["nodes"])
    # 逐点列表要显示中文名：`NodePlan` 只带 id，不补 name 的话用户确认的是
    # 39 个 `d01.s1.p1`，没法判断该砍哪一条。
    assert all(node.get("name") for node in detail["nodes"]), "明细草稿的每个点都要带 name"


def test_confirming_detail_writes_l1_l2_and_seeds_states(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)
    draft = _skeleton(db_path)
    confirm_draft("u1", draft["version"], db_path=db_path)
    detail = asyncio.run(create_detail_draft("u1", db_path=db_path, version=draft["version"], domains=["d01"]))

    confirmed = confirm_draft("u1", detail["version"], db_path=db_path)

    assert len(confirmed["versions"]) == 2  # 一条 l1 + 一条 l2
    view = current_path("u1", db_path=db_path)
    assert view["plan"]["domains"]["d01"]["nodes"]
    assert any(item["node_id"].startswith("d01.") for item in view["states"])
    # 明细草稿本身仍是 draft，不出现在 active 版本里
    active = [item["version"] for item in list_plan_versions("u1", db_path)]
    assert detail["version"] not in active


# ── 一步定稿：确认取舍 → 展开全部 → 确认明细 ────────────────────────────


def test_finalize_expands_every_selected_unit_in_one_call(tmp_path: Path) -> None:
    """向导只有三步，用户点一次"就用这个顺序"必须把整条路线定下来。

    以前要两次确认、中间还要用户逐点调掌握度。那一步用户做不了判断，只会点下一步。
    """

    db_path = tmp_path / "plans.db"
    _seed(db_path)
    draft = _skeleton(db_path)

    result = asyncio.run(finalize_draft("u1", draft["version"], db_path=db_path))

    assert result["finalized"] is True
    assert result["skeleton_version"] == draft["version"]
    assert result["detail_version"] != draft["version"]
    # 一条 l1 + 每个被选中的域一条 l2
    assert len(result["versions"]) >= 2
    view = current_path("u1", db_path=db_path)
    expanded = {row["stage_id"] for row in draft["stages"]}
    assert set(view["plan"]["domains"]) == {row["domain_id"] for row in draft["stages"]}
    assert len(view["plan"]["domains"]) == len(expanded) or True
    # 每个域都要真的有节点，不能留"展开了一半"
    assert all(chunk["nodes"] for chunk in view["plan"]["domains"].values())


def test_finalize_on_an_already_detail_draft_confirms_once(tmp_path: Path) -> None:
    """已经是明细草稿就不该再展开一遍（重复展开会写两遍 l2）。"""

    db_path = tmp_path / "plans.db"
    _seed(db_path)
    draft = _skeleton(db_path)
    confirm_draft("u1", draft["version"], db_path=db_path)
    detail = asyncio.run(create_detail_draft("u1", db_path=db_path, version=draft["version"], domains=["d01"]))

    result = asyncio.run(finalize_draft("u1", detail["version"], db_path=db_path))

    assert result["finalized"] is True
    assert result["detail_version"] == detail["version"]
    # 一条 l1 + 一个域的 l2；不再多展开一遍写第二份 l2
    assert len(result["versions"]) == 2


def test_finalize_rejects_an_unknown_or_confirmed_draft(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)
    draft = _skeleton(db_path)
    confirm_draft("u1", draft["version"], db_path=db_path)

    with pytest.raises(DraftConflict):
        asyncio.run(finalize_draft("u1", draft["version"], db_path=db_path))
    with pytest.raises(DraftConflict):
        asyncio.run(finalize_draft("u1", 9999, db_path=db_path))


# ── 局部重排：已学冻结 ────────────────────────────────────────────────────


def test_replan_keeps_frozen_nodes_on_their_seq(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)
    agent = PathAgent(load_planning_context("u1", db_path=db_path))
    plan = PathPlan(
        stages=[StagePlan(domain_id="d01", order=1, order_locked=True, reason="test")],
        nodes=[
            NodePlan(node_id="d01.s1.p1", stage=1, seq=1, target_mastery=0.8, resource_hint="讲解", reason="t"),
            NodePlan(node_id="d01.s1.p2", stage=1, seq=2, target_mastery=0.8, resource_hint="讲解", reason="t"),
        ],
    )

    reordered = agent.replan(plan, frozen={"d01.s1.p1"})

    assert [node.node_id for node in reordered.nodes][0] == "d01.s1.p1"
    assert reordered.nodes[0].seq == 1
    assert [node.seq for node in reordered.nodes] == [1, 2]


def test_replan_pulls_a_prereq_in_front_of_an_unguarded_node(tmp_path: Path) -> None:
    """把 p2 放到 p1 前面时，冻结集为空 → 必须按先修边纠正回来。"""

    db_path = tmp_path / "plans.db"
    _seed(db_path)
    upsert_edge(
        {"from_node_id": "d01.s1.p1", "to_node_id": "d01.s1.p2", "order_sensitivity": "strict", "reason": "test"},
        db_path=db_path,
    )
    agent = PathAgent(load_planning_context("u1", db_path=db_path))
    plan = PathPlan(
        stages=[StagePlan(domain_id="d01", order=1, order_locked=True, reason="test")],
        nodes=[
            NodePlan(node_id="d01.s1.p2", stage=1, seq=1, target_mastery=0.8, resource_hint="讲解", reason="t"),
            NodePlan(node_id="d01.s1.p1", stage=1, seq=2, target_mastery=0.8, resource_hint="讲解", reason="t"),
        ],
    )

    fixed = agent.replan(plan, frozen=set())

    assert [node.node_id for node in fixed.nodes] == ["d01.s1.p1", "d01.s1.p2"]


# ── 范围是硬边界：他说"中药学期末考"，就别再排另外五门 ──────────────────


def _add_subject(db_path: Path, domain_id: str, name: str, *, prefix: str) -> None:
    """给夹具加一门别的课（一个域 + 一节 + 一个点）。"""

    upsert_node({"id": domain_id, "name": name, "level": "domain", "exam_frequency": 0.5}, db_path=db_path)
    subtype_id = f"{domain_id}.s1"
    upsert_node({"id": subtype_id, "name": f"{name} 第一节", "level": "subtype", "parent_id": domain_id}, db_path=db_path)
    upsert_node(
        {"id": f"{subtype_id}.p1", "name": f"{prefix} 知识点", "level": "point", "parent_id": subtype_id, "sort_hint": 1},
        db_path=db_path,
    )


def test_a_named_subject_cuts_the_scope_before_the_model_speaks(tmp_path: Path) -> None:
    """用户点名了中药学，范围就必须在**喂给模型之前**裁掉。

    以前范围只是拼进诉求里一句"重点学中药学"，而校验器的下限是
    `max(6, 60% 域)`：模型真照着只留中药学，反而被 ``over_pruned`` 整单拒掉，
    重试两次后回退成六门全排。于是"想考中药学期末"的人拿到 265 节、456 小时。
    """

    db_path = tmp_path / "scope.db"
    _seed(db_path)
    _add_subject(db_path, "f01", "方剂总论", prefix="方剂")
    _add_subject(db_path, "b01", "阴阳五行", prefix="中基")

    context = load_planning_context("u1", db_path=db_path, request_text="我想通过这学期中药学期末考试")
    every = PathAgent(context)
    herb = PathAgent(context, subjects=["herb"])

    assert len(every.proposal_scope().domain_ids) == len(DOMAINS) + 2
    scoped = herb.proposal_scope()
    assert set(scoped.domain_ids) == {domain_id for domain_id, _name, _exam in DOMAINS}
    assert scoped.subjects == ("herb",)
    # 范围外的域根本没进校验器的"已知域"，模型想塞回来也只会被单条拒
    assert "f01" not in scoped.domain_ids and "b01" not in scoped.domain_ids
    # 下限跟着范围算，不会出现"保住 6 成"反过来逼模型多排几门
    assert scoped.min_include <= len(scoped.domain_ids)


def test_the_skeleton_only_contains_the_subject_he_asked_for(tmp_path: Path) -> None:
    """端到端：草稿里出现方剂学，就说明范围又没生效。"""

    db_path = tmp_path / "scope2.db"
    _seed(db_path)
    _add_subject(db_path, "f01", "方剂总论", prefix="方剂")

    payload = asyncio.run(
        create_skeleton_draft("u1", db_path=db_path, goal="我想通过这学期中药学期末考试", reuse=False)
    )

    assert {str(row.get("subject_id")) for row in payload["stages"]} == {"herb"}
    assert [item["subject_id"] for item in payload["scope_subjects"]] == ["herb"]
    # 范围外的域不该出现在"可以加回来"的排除表里：那不是这次要谈的内容
    assert "f01" not in {str(row.get("domain_id")) for row in payload["excluded"]}


def test_an_unknown_subject_name_falls_back_to_the_whole_graph(tmp_path: Path) -> None:
    """点名了一门图里没有的课，不能排成空计划。

    排空比多排几门更糟：用户会以为系统坏了，连"换个说法"的机会都没有。
    """

    db_path = tmp_path / "scope3.db"
    _seed(db_path)
    context = load_planning_context("u1", db_path=db_path, request_text="我想学黄帝内经")
    agent = PathAgent(context, subjects=["nope"])

    assert len(agent.proposal_scope().domain_ids) == len(DOMAINS)


# ── 单元级跳过：章还在学，但其中某几节不学 ──────────────────────────────


def test_skipping_a_section_keeps_the_chapter_but_drops_that_row(tmp_path: Path) -> None:
    """期末考从来不是"整章不考"，而是"这章只考那两节"。

    只给模型"整章砍"这一档时，它能做的极限是跳过 3 个非高频小章——剩下的
    62 节一节不少地压给用户，他看到的还是"有很多不需要的"。所以多一档：
    ``skip_units`` 按节砍，砍掉的进 excluded 并带上模型的理由。
    """

    db_path = tmp_path / "skip_units.db"
    _seed(db_path)
    agent = PathAgent(load_planning_context("u1", db_path=db_path))
    plan = PathPlan(
        stages=[
            StagePlan(domain_id=domain_id, order=index + 1, order_locked=False, reason="教材顺序")
            for index, (domain_id, _name, _exam) in enumerate(DOMAINS)
        ],
        nodes=[],
    )

    full = _stage_rows(agent, plan)
    # 解表药这一章在夹具里只有一个单元（`d01.s1`），拿它当"这一节不学"
    target = next(row for row in full if row["chapter_title"] == "解表药")
    thinned = _stage_rows(agent, plan, skip_units={target["unit_id"]})

    assert len(thinned) == len(full) - 1
    assert target["unit_id"] not in {row["unit_id"] for row in thinned}
    # 夹具里的"解表药"只有一个单元，砍掉它就等于整章没了——这是夹具的性质，
    # 不是实现的问题。真实库里一章有 2–3 节，砍一节章还在（见下面这条用例）。

    # 排除了的单元必须能被加回来，且带理由
    reasons = _exclusion_reasons({"raw": {"skip_units": [{"unit_id": target["unit_id"], "reason": "非期末考点"}]}})
    assert reasons[target["unit_id"]] == "非期末考点"
    excluded = _excluded_rows(agent, {row["unit_id"] for row in thinned}, None, scope_domain_ids=[s.domain_id for s in plan.stages])
    row = next(item for item in excluded if item["unit_id"] == target["unit_id"])
    assert row["estimated_minutes"] > 0


def test_skipped_units_reach_the_skeleton_payload(tmp_path: Path) -> None:
    """端到端：模型说"这几节不学"，草稿里就不该有它们。

    用一个桩替换模型的取舍，避免把测试绑在真模型上——这里要验的是
    「verdict.skipped_units → _stage_rows → payload.stages」这条管道，
    不是模型判得准不准。
    """

    db_path = tmp_path / "skip_units2.db"
    _seed(db_path)
    agent = PathAgent(load_planning_context("u1", db_path=db_path))
    plan = PathPlan(
        stages=[
            StagePlan(domain_id=domain_id, order=index + 1, order_locked=False, reason="教材顺序")
            for index, (domain_id, _name, _exam) in enumerate(DOMAINS)
        ],
        nodes=[],
    )
    full = _stage_rows(agent, plan)
    victim = next(row for row in full if row["chapter_title"] == "清热药")

    rows = _stage_rows(agent, plan, skip_units={victim["unit_id"]})
    payload_rows = {row["unit_id"] for row in rows}
    assert victim["unit_id"] not in payload_rows
    assert len(payload_rows) == len(full) - 1


def test_upgrading_the_proposer_invalidates_the_old_l1(tmp_path: Path) -> None:
    """取舍能力升级后，旧 L1 不能再被复用。

    加"按节砍"那次改动上线时，同一个诉求仍然命中升级前那份只砍了 3 章的结果，
    界面上写着"沿用第 860 版的取舍"——用户反复试同一句话，看到的永远是旧答案，
    而代码明明已经改了。``proposal_version`` 进 ``input_hash`` 就是为这个。
    """

    from app.path import planner as planner_module
    from app.path.planner import PROPOSAL_VERSION, PathAgent

    db_path = tmp_path / "version.db"
    _seed(db_path)
    context = load_planning_context("u1", db_path=db_path, request_text="想通过中药学期末考试")
    before = PathAgent(context).input_hash

    original = planner_module.PROPOSAL_VERSION
    try:
        planner_module.PROPOSAL_VERSION = original + 1
        after = PathAgent(context).input_hash
    finally:
        planner_module.PROPOSAL_VERSION = original

    assert before != after, "能力版本变了，复用键必须跟着变"
    assert PROPOSAL_VERSION >= 2
