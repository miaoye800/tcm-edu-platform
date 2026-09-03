import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.profile import GoalConfirmRequest, confirm_goal
from app.api.profile import get_profile_db_path
from app.auth.models import AuthUser
from app.main import app
from app.profile.goal_agent import GoalAgentResult, run_goal_agent
from app.profile.schema import Goal, PlanningParams
from app.profile.store import list_evidence, list_profile_changes
from tests.helpers import auth_headers_for_user_id


def _response(raw_text: str, *, depth: str, breadth: str, pace: str, fun: float, strictness: str, time_budget=None) -> str:
    return json.dumps(
        {
            "planning_params": {
                "depth": depth,
                "breadth": breadth,
                "pace": pace,
                "fun_preference": fun,
                "assessment_strictness": strictness,
                "time_budget": time_budget,
            },
            "goal": {"raw_text": raw_text, "normalized": "兴趣科普", "confirmed_by_user": False, "confidence": 0.8},
            "restatement": "已理解你的目标。",
        },
        ensure_ascii=False,
    )


def test_goal_agent_few_shot_goals_produce_plannable_parameters() -> None:
    cases = [
        ("就想学着玩玩", "浅", "兴趣优先", "松", 0.9, "宽松"),
        ("准备考研", "深", "考点导向", "紧", 0.3, "严格"),
        ("临床能用就行", "中", "考点导向", "中", 0.5, "标准"),
    ]
    for raw, depth, breadth, pace, fun, strictness in cases:
        async def stub(_: list[object], raw=raw, depth=depth, breadth=breadth, pace=pace, fun=fun, strictness=strictness) -> str:
            return _response(raw, depth=depth, breadth=breadth, pace=pace, fun=fun, strictness=strictness)

        result = asyncio.run(run_goal_agent(raw, llm_call=stub))
        assert result.planning_params.depth == depth
        assert result.planning_params.breadth == breadth
        assert result.planning_params.pace == pace
        assert result.planning_params.fun_preference == fun
        assert result.planning_params.assessment_strictness == strictness
        assert result.planning_params.time_budget is None


def test_goal_agent_rejects_enum_outside_contract_and_retries() -> None:
    calls = 0

    async def stub(_: list[object]) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _response("准备考研", depth="超深", breadth="考点导向", pace="紧", fun=0.3, strictness="严格")
        return _response("准备考研", depth="深", breadth="考点导向", pace="紧", fun=0.3, strictness="严格")

    result = asyncio.run(run_goal_agent("准备考研", llm_call=stub))
    assert calls == 2
    assert result.planning_params.depth == "深"


def test_goal_agent_never_accepts_hallucinated_time_budget() -> None:
    calls = 0

    async def stub(_: list[object]) -> str:
        nonlocal calls
        calls += 1
        return _response(
            "临床能用就行",
            depth="中",
            breadth="考点导向",
            pace="中",
            fun=0.5,
            strictness="标准",
            time_budget={"total_days": 30, "daily_minutes": 60},
        )

    result = asyncio.run(run_goal_agent("临床能用就行", llm_call=stub))
    assert calls == 2
    assert result.planning_params.time_budget is None
    assert result.goal.confidence == 0.3


def test_goal_agent_uses_conservative_default_without_profile() -> None:
    async def invalid(_: list[object]) -> str:
        return "not-json"

    result = asyncio.run(run_goal_agent("随便看看", llm_call=invalid))
    assert result.planning_params == PlanningParams()
    assert result.goal.confidence == 0.3


def test_confirm_persists_goal_change_and_learning_goal_evidence(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "goal.db"
    user = AuthUser(user_id="goal-user", username="goal", display_name="Goal", is_guest=False, created_at="2026-01-01")
    candidate = GoalAgentResult(
        planning_params=PlanningParams(depth="深", breadth="考点导向", pace="紧", fun_preference=0.3, assessment_strictness="严格"),
        goal=Goal(raw_text="准备考研", normalized="应试提分", confidence=0.9),
        restatement="你准备考研，我会按考点导向安排。",
    )

    async def no_jury(user_id: str, *, db_path: Path):
        from app.profile.store import get_profile
        return get_profile(user_id, db_path=db_path)

    monkeypatch.setattr("app.api.profile.get_latest_profile", no_jury)
    response = asyncio.run(confirm_goal(GoalConfirmRequest(candidate=candidate), current_user=user, db_path=db_path))

    assert response["goal"]["confirmed_by_user"] is True
    assert any(change["dim"] == "goal" for change in list_profile_changes(user.user_id, db_path=db_path))
    cards = list_evidence(user.user_id, db_path=db_path)
    assert len(cards) == 1
    assert cards[0].dimension == "learning_goal"
    assert cards[0].tier == "self_report"


def test_confirm_rejects_invalid_frontend_candidate_with_422(tmp_path: Path) -> None:
    app.dependency_overrides[get_profile_db_path] = lambda: tmp_path / "goal-api.db"
    client = TestClient(app)
    headers = auth_headers_for_user_id(client, "goal-invalid", tmp_path / "goal-api.db")

    response = client.post(
        "/api/profile/goal/confirm",
        json={
            "candidate": {
                "planning_params": {
                    "depth": "超深",
                    "breadth": "全面",
                    "pace": "中",
                    "fun_preference": 1.5,
                    "assessment_strictness": "标准",
                    "time_budget": {"total_days": 0, "daily_minutes": -1},
                },
                "goal": {"raw_text": "备考", "normalized": "应试提分", "confirmed_by_user": False, "confidence": 0.8},
                "restatement": "无效候选",
            }
        },
        headers=headers,
    )

    assert response.status_code == 422
