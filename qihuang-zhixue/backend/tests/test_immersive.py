import asyncio
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.immersive import get_immersive_db_path, get_immersive_settings
from app.config import Settings
from app.immersive import service as immersive_service
from app.immersive.schemas import RecommendationOutput
from app.main import app
from app.path.store import upsert_node
from app.profile.schema import CategoricalDimension, Goal, PlanningParams, Profile, ResourcePreferenceDimension
from app.profile.store import get_profile, upsert_profile
from tests.helpers import auth_session


def _prepare(client: TestClient, tmp_path: Path) -> tuple[Path, dict, dict]:
    db_path = tmp_path / "immersive.db"
    upsert_node({"id": "herbs", "name": "解表药", "level": "domain"}, db_path)
    upsert_node({"id": "sweat", "name": "发散风寒药", "level": "subtype", "parent_id": "herbs"}, db_path)
    upsert_node({"id": "mahuang", "name": "麻黄", "level": "point", "parent_id": "sweat", "exam_frequency": 0.95}, db_path)
    app.dependency_overrides[get_immersive_db_path] = lambda: db_path
    app.dependency_overrides[get_immersive_settings] = lambda: Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None)
    fun = auth_session(client, "fun-user", db_path)
    exam = auth_session(client, "exam-user", db_path)
    return db_path, fun, exam


def _profile(user_id: str, *, fun: float, goal: str | None, confidence: float = 0.9) -> Profile:
    return Profile(
        user_id=user_id,
        learning_goal=CategoricalDimension(label=goal, confidence=confidence),
        learning_style=CategoricalDimension(label="视觉型", confidence=confidence),
        resource_preference=ResourcePreferenceDimension(label="知识图谱", confidence=confidence),
        planning_params=PlanningParams(fun_preference=fun, assessment_strictness="严格"),
        goal=Goal(confirmed_by_user=True),
    )


def _start(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post("/api/immersive/session", json={"node_id": "mahuang"}, headers=headers)
    assert response.status_code == 200, response.text
    assert "麻黄" in response.json()["greeting"]
    assert "发散风寒药" in response.json()["greeting"]
    return response.json()["session_id"]


def _recommendations(body: str) -> list[dict]:
    payload = body.split("event: recommendation\ndata: ", 1)[1].split("\n\n", 1)[0]
    import json
    return json.loads(payload)["recommendation"]


def test_opening_and_fallback_recommendations_differ_by_profile(tmp_path: Path) -> None:
    client = TestClient(app)
    db_path, fun, exam = _prepare(client, tmp_path)
    upsert_profile(_profile(fun["user"]["user_id"], fun=0.95, goal="兴趣科普"), db_path)
    upsert_profile(_profile(exam["user"]["user_id"], fun=0.2, goal="应试提分"), db_path)

    fun_session = _start(client, fun["headers"])
    exam_session = _start(client, exam["headers"])
    assert fun_session != exam_session

    fun_response = client.post("/api/immersive/chat", json={"session_id": fun_session, "message": "不喜欢枯燥，想生动一点"}, headers=fun["headers"])
    exam_response = client.post("/api/immersive/chat", json={"session_id": exam_session, "message": "准备考试，直奔重点"}, headers=exam["headers"])
    assert fun_response.status_code == exam_response.status_code == 200
    assert "event: token" in fun_response.text
    fun_recommendations = _recommendations(fun_response.text)
    exam_recommendations = _recommendations(exam_response.text)
    assert [item["type"] for item in fun_recommendations] == ["graph", "story"]
    assert fun_recommendations[1]["params"]["entry"] == "story_stage"
    assert [item["type"] for item in exam_recommendations] == ["quiz", "doc"]
    assert all("趣味偏好" in item["reason"] or "资源偏好" in item["reason"] for item in fun_recommendations)
    assert all("learning_goal" in item["reason"] for item in exam_recommendations)


def test_opening_templates_do_not_repeat_for_one_user(tmp_path: Path) -> None:
    client = TestClient(app)
    _db_path, fun, _ = _prepare(client, tmp_path)
    first = client.post("/api/immersive/session", json={"node_id": "mahuang"}, headers=fun["headers"])
    second = client.post("/api/immersive/session", json={"node_id": "mahuang"}, headers=fun["headers"])
    assert first.status_code == second.status_code == 200
    assert first.json()["greeting"] != second.json()["greeting"]


def test_llm_invalid_resource_type_is_repaired_once(tmp_path: Path) -> None:
    client = TestClient(app)
    db_path, fun, _ = _prepare(client, tmp_path)
    profile = _profile(fun["user"]["user_id"], fun=0.8, goal="兴趣科普")
    node = immersive_service.load_node_context("mahuang", db_path)
    calls: list[int] = []

    async def llm_call(_messages):
        calls.append(1)
        if len(calls) == 1:
            return '{"reply":"x","recommendation":[{"type":"video","reason":"x","params":{}}]}'
        return '{"reply":"好","recommendation":[{"type":"graph","reason":"学习风格=视觉型（learning_style 维度）","params":{}}]}'

    result = asyncio.run(immersive_service.recommend(message="看图", node=node, profile=profile, history=[], llm_call=llm_call))
    assert result.recommendation[0].type == "graph"
    assert len(calls) == 2


def test_confirm_creates_tasks_and_rejects_untrusted_type(tmp_path: Path) -> None:
    client = TestClient(app)
    db_path, fun, _ = _prepare(client, tmp_path)
    session_id = _start(client, fun["headers"])
    valid = {
        "session_id": session_id,
        "resources": [{"type": "graph", "reason": "学习风格=视觉型（learning_style 维度）", "params": {}}],
    }
    created = client.post("/api/immersive/confirm", json=valid, headers=fun["headers"])
    assert created.status_code == 201, created.text
    assert len(created.json()["task_ids"]) == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT resource_type FROM resource_task").fetchone()[0] == "graph"
    assert get_profile(fun["user"]["user_id"], db_path).resource_preference.label == "知识图谱"

    invalid = {**valid, "resources": [{"type": "video", "reason": "x", "params": {}}]}
    rejected = client.post("/api/immersive/confirm", json=invalid, headers=fun["headers"])
    assert rejected.status_code == 422


def test_chat_and_confirm_send_existing_profile_events(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(app)
    _db_path, fun, _ = _prepare(client, tmp_path)
    received: list[str] = []

    async def capture(_user_id, event, **_kwargs):
        received.append(event.payload["subtype"])

    monkeypatch.setattr(immersive_service, "update_profile_from_event", capture)
    session_id = _start(client, fun["headers"])
    chat = client.post("/api/immersive/chat", json={"session_id": session_id, "message": "先看图谱"}, headers=fun["headers"])
    assert chat.status_code == 200
    confirmed = client.post(
        "/api/immersive/confirm",
        json={"session_id": session_id, "resources": [{"type": "graph", "reason": "画像维度置信度不足0.5，采用保守默认图谱", "params": {}}]},
        headers=fun["headers"],
    )
    assert confirmed.status_code == 201
    assert received == ["chat_message", "resource_choice"]


def test_no_key_low_confidence_uses_conservative_fallback(tmp_path: Path) -> None:
    client = TestClient(app)
    db_path, fun, _ = _prepare(client, tmp_path)
    upsert_profile(_profile(fun["user"]["user_id"], fun=0.95, goal="应试提分", confidence=0.2), db_path)
    session_id = _start(client, fun["headers"])
    response = client.post("/api/immersive/chat", json={"session_id": session_id, "message": "怎么学"}, headers=fun["headers"])
    recommendations = _recommendations(response.text)
    assert [item["type"] for item in recommendations] == ["doc", "graph"]
    assert all("0.95" not in item["reason"] and "应试提分" not in item["reason"] for item in recommendations)
