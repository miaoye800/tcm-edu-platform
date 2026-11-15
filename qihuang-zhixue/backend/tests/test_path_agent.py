from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import path as path_api
from app.main import app
from app.path.constants import DEFAULT_RESOURCE_HINT, DEFAULT_TARGET_MASTERY, MAX_VERIFY_RETRIES
from app.path.plan_store import list_plan_versions, save_plan_version
from app.path.planner import PathAgent
from app.path.schema import NodePlan, PathPlan, PlanningContext, StagePlan
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
from tests.helpers import auth_headers_for_user_id


def _profile(
    user_id: str,
    *,
    goal: str,
    style: str,
    style_confidence: float = 0.9,
) -> Profile:
    return Profile(
        user_id=user_id,
        knowledge_base=NumericDimension(score=55, confidence=0.9),
        learning_goal=CategoricalDimension(label=goal, confidence=0.9),
        learning_style=CategoricalDimension(label=style, confidence=style_confidence),
        weak_points=WeakPointsDimension(label="功效鉴别", confidence=0.8),
        pace=NumericDimension(score=60, confidence=0.8),
        motivation=MotivationDimension(score=75, label="内驱", confidence=0.8),
        resource_preference=ResourcePreferenceDimension(label="知识图谱", confidence=0.8),
        goal=Goal(raw_text=goal, normalized=goal, confirmed_by_user=True, confidence=0.9),
    )


def _context(
    *,
    user_id: str = "student",
    goal: str = "应试提分",
    style: str = "文字型",
    style_confidence: float = 0.9,
    params: PlanningParams | None = None,
) -> PlanningContext:
    profile = _profile(user_id, goal=goal, style=style, style_confidence=style_confidence)
    planning_params = params or PlanningParams(depth="深", breadth="考点导向", assessment_strictness="严格")
    return PlanningContext(
        user_id=user_id,
        profile=profile,
        planning_params=planning_params,
        knowledge_nodes=[
            {"id": "general", "name": "总论", "level": "domain", "sort_hint": 0, "exam_frequency": 1},
            {"id": "d_exam", "name": "解表药", "level": "domain", "sort_hint": 1, "exam_frequency": 0.9},
            {"id": "d_fun", "name": "补虚药", "level": "domain", "sort_hint": 2, "exam_frequency": 0.7},
            {"id": "s_exam", "name": "发散风寒", "level": "subtype", "parent_id": "d_exam", "sort_hint": 1},
            {"id": "p_hot", "name": "麻黄", "level": "point", "parent_id": "s_exam", "sort_hint": 1, "exam_frequency": 0.95},
            {"id": "p_common", "name": "桂枝", "level": "point", "parent_id": "s_exam", "sort_hint": 2, "exam_frequency": 0.6},
            {"id": "p_low", "name": "紫苏", "level": "point", "parent_id": "s_exam", "sort_hint": 3, "exam_frequency": 0.3},
            {"id": "s_fun", "name": "补气药", "level": "subtype", "parent_id": "d_fun", "sort_hint": 1},
            {"id": "p_fun", "name": "人参", "level": "point", "parent_id": "s_fun", "sort_hint": 1, "exam_frequency": 0.9},
        ],
        prerequisite_edges=[
            {"from_node_id": "general", "to_node_id": "d_exam", "order_sensitivity": "strict", "reason": "总论先修"},
            {"from_node_id": "general", "to_node_id": "d_fun", "order_sensitivity": "strict", "reason": "总论先修"},
            {"from_node_id": "d_exam", "to_node_id": "d_fun", "order_sensitivity": "free", "reason": "可并行"},
            {"from_node_id": "p_hot", "to_node_id": "p_common", "order_sensitivity": "strict", "reason": "先麻黄后桂枝"},
            {"from_node_id": "p_common", "to_node_id": "p_low", "order_sensitivity": "strict", "reason": "教材顺序"},
        ],
        mastery={},
    )


def test_profiles_change_free_order_target_and_profile_reasons() -> None:
    async def exam_llm(messages):
        return json.dumps(
            {
                "ordered_domain_ids": ["general", "d_exam", "d_fun"],
                "suggestion": "学习目标应试提分0.9→先解表药后补虚药",
            },
            ensure_ascii=False,
        )

    async def interest_llm(messages):
        return json.dumps(
            {
                "ordered_domain_ids": ["general", "d_fun", "d_exam"],
                "suggestion": "学习目标兴趣科普0.9→先补虚药后解表药",
            },
            ensure_ascii=False,
        )

    exam = PathAgent(
        _context(params=PlanningParams(depth="深", breadth="考点导向", fun_preference=0.2, assessment_strictness="严格")),
        llm_call=exam_llm,
    )
    interest = PathAgent(
        _context(
            goal="兴趣科普",
            style="视觉型",
            params=PlanningParams(depth="浅", breadth="兴趣优先", fun_preference=0.9, assessment_strictness="宽松"),
        ),
        llm_call=interest_llm,
    )

    exam_l1 = asyncio.run(exam.plan_l1())
    interest_l1 = asyncio.run(interest.plan_l1())
    exam_l2 = asyncio.run(exam.expand_domain("d_exam"))
    interest_l2 = asyncio.run(interest.expand_domain("d_exam"))

    assert [stage.domain_id for stage in exam_l1.plan.stages] != [stage.domain_id for stage in interest_l1.plan.stages]
    assert exam_l2.plan.nodes[0].target_mastery == 0.95
    assert interest_l2.plan.nodes[0].target_mastery == 0.7
    assert "学习深度深" in exam_l2.plan.nodes[0].reason
    assert "趣味偏好0.90" in interest_l2.plan.nodes[0].reason
    assert "学习目标" in exam_l1.plan.stages[1].reason


def test_llm_strict_edge_violation_is_rejected_to_topological_order() -> None:
    async def invalid_llm(messages):
        return json.dumps(
            {
                "ordered_domain_ids": ["d_exam", "general", "d_fun"],
                "suggestion": "学习目标0.9→违规提前解表药",
            },
            ensure_ascii=False,
        )

    result = asyncio.run(PathAgent(_context(), llm_call=invalid_llm).plan_l1())

    assert [stage.domain_id for stage in result.plan.stages] == ["general", "d_exam", "d_fun"]
    # 从"整单退回底稿"改成"单条拒 + 说清楚修了什么"：模型的话保留，但必须交代
    # 哪一处被内核按拓扑改掉了，否则用户看到的是自己没同意过的顺序。
    assert "违规提前解表药" in result.suggestion
    assert "违反先修边" in result.suggestion
    assert [item["code"] for item in result.proposal["rejected"]] == ["proposal.strict_conflict"]


def test_llm_failure_does_not_raise_and_disables_aggressive_parameters() -> None:
    async def failed_llm(messages):
        raise OSError("provider unavailable")

    agent = PathAgent(
        _context(params=PlanningParams(depth="深", assessment_strictness="严格")),
        llm_call=failed_llm,
    )

    l1 = asyncio.run(agent.plan_l1())
    l2 = asyncio.run(agent.expand_domain("d_exam"))

    assert [stage.domain_id for stage in l1.plan.stages] == ["general", "d_exam", "d_fun"]
    assert all(node.target_mastery == DEFAULT_TARGET_MASTERY for node in l2.plan.nodes)
    assert all(node.resource_hint == DEFAULT_RESOURCE_HINT for node in l2.plan.nodes)


def test_missing_exam_hot_node_triggers_replan_and_is_restored() -> None:
    agent = PathAgent(_context(), llm_call=lambda messages: None)  # type: ignore[arg-type]
    incomplete = PathPlan(
        stages=[StagePlan(domain_id="d_exam", order=1, order_locked=True, reason="学习目标应试提分")],
        nodes=[
            NodePlan(
                node_id="p_common",
                stage=1,
                seq=1,
                target_mastery=0.9,
                resource_hint="教材",
                reason="学习风格文字型",
            )
        ],
    )

    result = asyncio.run(agent.verify_plan(incomplete, domain_id="d_exam"))

    assert not result.fallback
    assert result.attempts == 1
    assert "p_hot" in {node.node_id for node in result.plan.nodes}


def test_two_failed_repairs_fall_back_to_conservative_template() -> None:
    attempts: list[int] = []

    def never_repairs(plan, issues, attempt):
        attempts.append(attempt)
        return plan

    agent = PathAgent(_context(), replan_hook=never_repairs)
    invalid = PathPlan(
        stages=[StagePlan(domain_id="d_exam", order=1, order_locked=True, reason="学习目标应试提分")],
        nodes=[
            NodePlan(
                node_id="p_common",
                stage=1,
                seq=1,
                target_mastery=0.9,
                resource_hint="教材",
                reason="学习风格文字型",
            )
        ],
    )

    result = asyncio.run(agent.verify_plan(invalid, domain_id="d_exam"))

    assert result.fallback is True
    assert result.attempts == MAX_VERIFY_RETRIES
    assert attempts == [1, 2]
    assert all(node.target_mastery == DEFAULT_TARGET_MASTERY for node in result.plan.nodes)
    assert all("fallback保守模板" in node.reason for node in result.plan.nodes)


def test_low_confidence_style_uses_conservative_resource_and_calibration() -> None:
    async def valid_llm(messages):
        return json.dumps(
            {
                "ordered_domain_ids": ["general", "d_exam", "d_fun"],
                "suggestion": "学习目标应试提分0.9→按考点推进",
            },
            ensure_ascii=False,
        )

    agent = PathAgent(_context(style="视觉型", style_confidence=0.2), llm_call=valid_llm)
    result = asyncio.run(agent.expand_domain("d_exam"))

    assert all(node.resource_hint == DEFAULT_RESOURCE_HINT for node in result.plan.nodes)
    assert all(node.calibration for node in result.plan.nodes[:3])
    assert "学习风格置信度0.20<0.5" in result.plan.nodes[0].reason


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        ({"p_hot": {"p_mastery": 0.4}}, "remedial"),
        ({"p_hot": {"p_mastery": 0.7}}, "reinforce"),
        ({"p_hot": {"p_mastery": 0.85}, "p_common": {"p_mastery": 0.9}}, "skip"),
        ({"p_hot": {"p_mastery": 0.85}, "p_common": {"p_mastery": 0.5}}, "normal"),
    ],
)
def test_l3_four_local_diff_decisions(snapshot: dict[str, dict[str, float]], expected: str) -> None:
    diff = PathAgent(_context()).adjust_after_node("p_hot", snapshot)

    assert diff.action == expected
    assert "知识基础" in diff.reason
    if expected == "remedial":
        assert diff.inserted_nodes and diff.inserted_nodes[0].highlight == "remedial"
    if expected == "reinforce":
        assert diff.updates
    if expected == "skip":
        assert "p_common" in diff.skip_node_ids


def test_plan_versions_append_and_keep_history(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    first = save_plan_version(user_id="u", plan_kind="l1", payload={"stages": []}, db_path=db_path)
    second = save_plan_version(user_id="u", plan_kind="l2", domain_id="d_exam", payload={"nodes": []}, db_path=db_path)

    assert (first, second) == (1, 2)
    assert [item["version"] for item in list_plan_versions("u", db_path)] == [1, 2]


def test_three_post_apis_persist_incrementing_versions(tmp_path: Path) -> None:
    db_path = tmp_path / "path-api.db"
    seed_path = Path(__file__).resolve().parents[1] / "scripts" / "seed_knowledge_tree.py"
    spec = importlib.util.spec_from_file_location("path_agent_seed", seed_path)
    assert spec and spec.loader
    seed_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_module)
    seed_module.seed(db_path)

    app.dependency_overrides[path_api.get_path_db_path] = lambda: db_path
    client = TestClient(app)
    headers = auth_headers_for_user_id(client, "path-api-user", db_path)
    plan = client.post("/api/path/plan", headers=headers, json={})
    expand = client.post("/api/path/expand", headers=headers, json={"domain_id": "d01"})
    adjust = client.post(
        "/api/path/adjust",
        headers=headers,
        json={"node_id": "d01.s1.p1", "bkt_snapshot": {"d01.s1.p1": {"p_mastery": 0.4}}},
    )

    assert plan.status_code == 200
    assert plan.json()["version"] == 1
    assert len(plan.json()["stages"]) == 19
    assert expand.status_code == 200
    assert expand.json()["version"] == 2
    assert {node["node_id"] for node in expand.json()["nodes"]} >= {"d01.s1.p1", "d01.s2.p3"}
    assert adjust.status_code == 200
    assert adjust.json()["version"] == 3
    assert adjust.json()["diff"]["action"] == "remedial"
    assert [item["version"] for item in list_plan_versions("path-api-user", db_path)] == [1, 2, 3]
