from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.api import learning as learning_api
from app.api.profile import get_profile_db_path
from app.main import app
from app.profile.ability import (
    coverage_confidence_factor,
    rating_to_score,
    record_ability_attempt,
)
from datetime import UTC, datetime, timedelta

from app.profile.evidence import EvidenceCard, EvidenceTier
from app.profile.fusion import fuse_dimension
from tests.helpers import auth_headers_for_user_id


def test_correct_hard_question_gains_more_than_easy_question(tmp_path: Path) -> None:
    hard = record_ability_attempt(
        user_id="hard", kc_id="kc", topic="topic", correct=True, difficulty="hard", db_path=tmp_path / "hard.db"
    )
    easy = record_ability_attempt(
        user_id="easy", kc_id="kc", topic="topic", correct=True, difficulty="easy", db_path=tmp_path / "easy.db"
    )

    assert hard.rating - 1000 > easy.rating - 1000


def test_high_rating_user_barely_gains_from_easy_question(tmp_path: Path) -> None:
    db_path = tmp_path / "high.db"
    snapshot = None
    for index in range(15):
        snapshot = record_ability_attempt(
            user_id="high", kc_id=f"hard-{index}", topic=f"topic-{index}", correct=True, difficulty="hard", db_path=db_path
        )
    assert snapshot is not None and snapshot.rating > 1100
    before = snapshot.rating
    after = record_ability_attempt(
        user_id="high", kc_id="easy", topic="easy", correct=True, difficulty="easy", db_path=db_path
    )

    assert after.rating - before < 3


def test_wrong_easy_question_has_large_penalty(tmp_path: Path) -> None:
    snapshot = record_ability_attempt(
        user_id="wrong", kc_id="kc", topic="topic", correct=False, difficulty="easy", db_path=tmp_path / "wrong.db"
    )

    assert snapshot.rating < 980


def test_low_coverage_reduces_knowledge_confidence() -> None:
    card = EvidenceCard(
        dimension="knowledge_base",
        source="Elo measured ability",
        tier=EvidenceTier.MEASURED,
        fact="measured ability; topics assessed 1/8",
        objective_signal={"score": 50, "coverage_count": 1, "coverage_target": 8},
        weight=3.0,
    )
    result = fuse_dimension("knowledge_base", [card])

    assert result.confidence == 0.5
    assert result.metadata["coverage_label"] == "已考察主题 1/8"
    assert coverage_confidence_factor(1) == 0.5


def test_latest_elo_snapshot_controls_knowledge_score() -> None:
    now = datetime.now(UTC)
    snapshots = [
        EvidenceCard(
            dimension="knowledge_base",
            source="Elo 实测能力",
            tier=EvidenceTier.MEASURED,
            fact=f"Elo snapshot {score}",
            objective_signal={"score": score, "rating": rating, "coverage_count": 2, "coverage_target": 8},
            weight=3.0,
            timestamp=now + timedelta(minutes=index),
        )
        for index, (score, rating) in enumerate(((42, 950), (54, 1030), (69, 1130)))
    ]

    result = fuse_dimension("knowledge_base", snapshots, now=now + timedelta(minutes=3))

    assert result.score == 69
    assert result.metadata["effective_total_weight"] == 3.0
    assert result.evidence_refs == ["Elo 实测能力#1"]


def test_repeat_correct_is_discounted_after_two_answers(tmp_path: Path) -> None:
    db_path = tmp_path / "repeat.db"
    first = record_ability_attempt(user_id="repeat", kc_id="kc", topic="topic", correct=True, difficulty="hard", db_path=db_path)
    second = record_ability_attempt(user_id="repeat", kc_id="kc", topic="topic", correct=True, difficulty="hard", db_path=db_path)
    third = record_ability_attempt(user_id="repeat", kc_id="kc", topic="topic", correct=True, difficulty="hard", db_path=db_path)

    assert third.rating - second.rating < (second.rating - first.rating) * 0.35


def test_rating_mapping_clips_at_boundaries() -> None:
    assert rating_to_score(799.9) == 20.0
    assert rating_to_score(1200.1) == 80.0


def test_quiz_updates_ability_and_profile_returns_it(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ability_api.db"
    monkeypatch.setattr(learning_api, "get_latest_profile", lambda user_id: _profile_for_test(user_id, db_path))
    monkeypatch.setattr("app.profile.update._has_configured_llm_key", lambda settings: False)
    app.dependency_overrides[learning_api.get_mastery_db_path] = lambda: db_path
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    client = TestClient(app)
    headers = auth_headers_for_user_id(client, "ability-api", db_path)
    try:
        submitted = client.post(
            "/api/quiz/submit",
            json={
                "items": [
                    {
                        "question_id": "q-hard",
                        "kc_id": "mahuang-efficacy",
                        "answer": ["A"],
                        "correct_answer": ["A"],
                        "difficulty": 5,
                        "topic": "麻黄功效",
                    }
                ]
            },
            headers=headers,
        )
        profile = client.get("/api/profile", headers=headers)
    finally:
        app.dependency_overrides.clear()

    assert submitted.status_code == 200
    assert profile.status_code == 200
    ability = profile.json()["ability"]
    assert ability["rating"] > 1000
    assert ability["coverage_count"] == 1
    evidence = profile.json()["knowledge_base"]["evidence"]
    assert any(card["tier"] == "measured" and card["source"] == "Elo 实测能力" for card in evidence)


async def _profile_for_test(user_id: str, db_path: Path):
    from app.profile.update import get_latest_profile

    return await get_latest_profile(user_id, db_path=db_path)
