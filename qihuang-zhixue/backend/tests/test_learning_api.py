from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import learning as learning_api
from app.main import app
from app.mastery.review import record_review_result
from tests.helpers import auth_headers_for_user_id


def test_resource_generate_stream_returns_resource_done(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / ".data" / "learning_resource.db"
    monkeypatch.setattr("app.profile.update.DB_PATH", db_path)
    monkeypatch.setattr(learning_api, "get_latest_profile", lambda user_id: _profile_for_test(user_id, db_path))

    client = TestClient(app)
    headers = auth_headers_for_user_id(client, "student-stream", db_path)
    response = client.post(
        "/api/resources/generate",
        json={"topic": "麻黄", "types": ["explain", "quiz"]},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: node_start" in response.text
    assert "event: token" in response.text
    assert "event: resource_done" in response.text
    assert '"type":"quiz"' in response.text


def test_learning_path_and_reviews_endpoints(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / ".data" / "learning_path.db"
    record_review_result(
        user_id="student-review-api",
        kc_id="herb_mahuang",
        rating="again",
        reviewed_at=datetime.now(UTC) - timedelta(days=1),
        db_path=db_path,
    )

    client = TestClient(app)
    app.dependency_overrides[learning_api.get_mastery_db_path] = lambda: db_path
    headers = auth_headers_for_user_id(client, "student-review-api", db_path)
    try:
        path_response = client.get("/api/path", headers=headers)
        reviews_response = client.get("/api/reviews", headers=headers)
    finally:
        app.dependency_overrides.clear()

    assert path_response.status_code == 200
    assert path_response.json()["skill_tree"]
    assert reviews_response.status_code == 200
    assert reviews_response.json()["items"][0]["kc_id"] == "herb_mahuang"


def test_quiz_submit_updates_mastery_and_review(tmp_path: Path, monkeypatch) -> None:
    recorded_mastery: list[dict[str, object]] = []
    recorded_reviews: list[dict[str, object]] = []
    monkeypatch.setattr(learning_api, "ProfileUpdateEvent", lambda **kwargs: {"type": kwargs["type"], "payload": kwargs["payload"]})

    async def fake_update_profile(*args, **kwargs):
        return None

    def fake_record_mastery_event(**kwargs):
        recorded_mastery.append(dict(kwargs))

    def fake_record_review_result(**kwargs):
        recorded_reviews.append(dict(kwargs))
        return _ReviewForTest(kc_id=str(kwargs["kc_id"]))

    monkeypatch.setattr(learning_api, "update_profile_from_event", fake_update_profile)
    monkeypatch.setattr(learning_api, "record_mastery_event", fake_record_mastery_event)
    monkeypatch.setattr(learning_api, "record_review_result", fake_record_review_result)
    monkeypatch.setattr(
        learning_api,
        "get_mastery",
        lambda user_id, **kwargs: {"herb_mahuang": {"p_mastery": 0.72, "attempts": 1}},
    )
    monkeypatch.setattr(learning_api, "due_reviews", lambda user_id, **kwargs: {"items": [], "suggested_count": 0})

    client = TestClient(app)
    headers = auth_headers_for_user_id(client, "student-submit", tmp_path / ".data" / "learning_submit_auth.db")
    response = client.post(
        "/api/quiz/submit",
        json={
            "items": [
                {
                    "question_id": "q1",
                    "kc_id": "麻黄",
                    "answer": ["A"],
                    "correct_answer": ["A"],
                    "difficulty": 2,
                    "bloom_level": "记忆",
                    "topic": "麻黄",
                }
            ],
        },
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["correct"] is True
    assert "herb_mahuang" in payload["mastery"]
    assert payload["user_id"] == "student-submit"
    assert recorded_mastery[0]["kc_id"] == "herb_mahuang"
    assert recorded_mastery[0]["user_id"] == "student-submit"
    assert recorded_reviews[0]["kc_id"] == "herb_mahuang"


async def _profile_for_test(user_id: str, db_path: Path):
    from app.profile.update import get_latest_profile

    return await get_latest_profile(user_id, db_path=db_path)


class _ReviewForTest:
    def __init__(self, kc_id: str) -> None:
        self.kc_id = kc_id

    def as_dict(self) -> dict[str, object]:
        return {"kc_id": self.kc_id, "rating": "good"}
