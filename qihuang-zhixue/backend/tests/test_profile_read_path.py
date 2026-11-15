"""画像读路径与历史会话的回归测试。

现场问题：对话式画像收尾、又确认了一条学习目标之后进弟子档案，整页显示
"画像档案加载超时，已切换为本地兜底数据"，雷达是空的，再点开对话式也看不到
之前的对话。数据其实都在库里（59 张证据卡、100KB 画像、会话行）。

根因有两条，这里的用例分别钉住：

1. 读路径（``GET /api/profile``）会在读里跑三师评审——凡是"有证据但还没评分"
   的维度都要等一次模型调用。前端几百毫秒的超时就先到了，于是整页被换成空白
   兜底。读现在是纯读：返回落库快照，不跑模型、不重算。
2. 会话 id 只活在前端 sessionStorage 里，关掉浏览器就再也回不到那次对话。
   历史列表必须由后端按 user_id 给出。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

import app.profile.modes as modes_module
from app.api.profile import get_profile_db_path
from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.main import app
from app.profile.schema import create_blank_profile
from app.profile.store import (
    append_chat_messages,
    list_evidence,
    upsert_profile,
)

client = TestClient(app)


def _override_current_user(user_id: str) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        user_id=user_id,
        username=user_id,
        display_name=user_id,
        is_guest=False,
        created_at="2026-07-06T00:00:00+00:00",
    )


def _forbid_model(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("读画像不该调用评分模型")

    monkeypatch.setattr("app.profile.update.score_dimension", boom)


def test_profile_read_returns_persisted_snapshot_without_scoring(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "read.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    _override_current_user("read-user")
    try:
        profile = create_blank_profile("read-user")
        profile.knowledge_base.score = 61.5
        profile.knowledge_base.radar_score = 61.5
        profile.knowledge_base.confidence = 0.62
        profile.knowledge_base.score_summary = "能认药但辨析不稳"
        upsert_profile(profile, db_path=db_path)

        _forbid_model(monkeypatch)
        response = client.get("/api/profile")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["knowledge_base"]["score"] == 61.5
    axis = next(item for item in payload["radar"]["axes"] if item["key"] == "knowledge_base")
    assert axis["value"] == 61.5
    assert axis["confidence"] == 0.62


def test_read_keeps_the_values_the_dialogue_finished_with(tmp_path: Path, monkeypatch) -> None:
    """收尾写进去的分数与置信度，读一次之后必须原样还在。

    旧读路径会把同一批证据重新做规则融合、再跑三师评审，档案里的数字因此和
    对话里刚看到的不是同一个——用户会以为"两边对不上"。
    """

    db_path = tmp_path / "snapshot.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    _override_current_user("snapshot-user")
    try:
        session = modes_module.start_profile_session("snapshot-user", "dialogue", db_path=db_path)
        profile = create_blank_profile("snapshot-user")
        profile.learning_goal.distribution = {"学术深造": 0.6, "临床实用": 0.4}
        profile.learning_goal.radar_score = 57.8
        profile.learning_goal.confidence = 0.68
        profile.learning_goal.label = "学术深造"
        upsert_profile(profile, db_path=db_path)

        _forbid_model(monkeypatch)
        first = client.get("/api/profile").json()
        second = client.get("/api/profile").json()
    finally:
        app.dependency_overrides.clear()

    assert first["learning_goal"]["confidence"] == second["learning_goal"]["confidence"] == 0.68
    axis = next(item for item in first["radar"]["axes"] if item["key"] == "learning_goal")
    assert axis["value"] == 57.8
    assert axis["lit"] is True
    # 其余维度没有任何证据，读接口不得凭空给出结论。
    assert next(item for item in first["radar"]["axes"] if item["key"] == "pace")["lit"] is False
    assert session.session_id


def test_profile_sessions_endpoint_lists_history_and_restores_chat(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    _override_current_user("history-user")
    try:
        older = modes_module.start_profile_session("history-user", "dialogue", db_path=db_path)
        append_chat_messages(
            "history-user",
            older.session_id,
            [{"role": "user", "content": "先说个早的"}, {"role": "assistant", "content": "记下了。"}],
            db_path=db_path,
        )
        newer = modes_module.start_profile_session("history-user", "dialogue", db_path=db_path)
        append_chat_messages(
            "history-user",
            newer.session_id,
            [{"role": "user", "content": "我想考公，但更喜欢临床"}, {"role": "assistant", "content": "那我们把目标拆开看。"}],
            db_path=db_path,
        )

        listing = client.get("/api/profile/sessions").json()
        restored = client.get(f"/api/profile/session/{older.session_id}").json()
    finally:
        app.dependency_overrides.clear()

    assert [item["session_id"] for item in listing["items"]][0] == newer.session_id
    assert {item["session_id"] for item in listing["items"]} >= {older.session_id, newer.session_id}
    newest = listing["items"][0]
    assert newest["last_user_message"] == "我想考公，但更喜欢临床"
    assert newest["message_count"] == 2
    # 回跳某一次历史对话，要能读出当时说过的话。
    assert [item["content"] for item in restored["chat_history"]] == ["先说个早的", "记下了。"]


def test_confirming_a_goal_keeps_the_existing_learning_goal_result(tmp_path: Path) -> None:
    """确认学习目标只补证据，不重算目标维度。

    重算会把对话里按组件评出来的值换成一次规则融合的结果，档案与对话就不再
    是同一份画像了。
    """

    from app.api.profile import GoalConfirmRequest, confirm_goal
    from app.profile.goal_agent import GoalAgentResult
    from app.profile.schema import Goal, PlanningParams

    db_path = tmp_path / "goal-keeps.db"
    user = AuthUser(user_id="goal-keeps", username="goal", display_name="Goal", is_guest=False, created_at="2026-01-01")
    profile = create_blank_profile(user.user_id)
    profile.learning_goal.distribution = {"临床实用": 1.0}
    profile.learning_goal.radar_score = 64.0
    profile.learning_goal.confidence = 0.7
    upsert_profile(profile, db_path=db_path)

    candidate = GoalAgentResult(
        planning_params=PlanningParams(depth="深", breadth="考点导向", pace="紧", fun_preference=0.3, assessment_strictness="严格"),
        goal=Goal(raw_text="想考公，但也想把临床摸熟", normalized="应试提分", confidence=0.9),
        restatement="你更想考公，但也不想丢临床。",
    )
    asyncio.run(confirm_goal(GoalConfirmRequest(candidate=candidate), current_user=user, db_path=db_path))

    stored = list_evidence(user.user_id, db_path=db_path)
    refreshed = client_profile(user.user_id, db_path)

    assert len(stored) == 1
    assert stored[0].dimension == "learning_goal"
    assert refreshed.learning_goal.confidence == 0.7
    assert refreshed.learning_goal.radar_score == 64.0
    # 新证据必须进档案的依据列表，否则"我确认过目标"这件事没有痕迹。
    assert [card.evidence_id for card in refreshed.learning_goal.evidence] == [stored[0].evidence_id]


def client_profile(user_id: str, db_path: Path):
    from app.profile.update import read_profile_snapshot

    return read_profile_snapshot(user_id, db_path=db_path)
