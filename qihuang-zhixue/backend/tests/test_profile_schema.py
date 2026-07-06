import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.profile.evidence import EvidenceCard
from app.profile.models import CategoricalDimension, ProfileDimension, StudentProfile, default_profile
from app.profile.schema import Profile, create_blank_profile
from app.profile.storage import add_evidence as add_legacy_evidence
from app.profile.storage import get_profile as get_legacy_profile
from app.profile.storage import list_evidence as list_legacy_evidence
from app.profile.storage import save_profile as save_legacy_profile
from app.profile.store import add_evidence, get_profile, list_evidence, upsert_profile


def test_profile_store_creates_tables_and_round_trips_blank_profile(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    profile = create_blank_profile("student-001")

    assert isinstance(profile, Profile)
    upsert_profile(profile, db_path=db_path)
    loaded = get_profile("student-001", db_path=db_path)

    assert loaded.user_id == "student-001"
    assert loaded.knowledge_base.score is None
    assert loaded.pace.score is None
    assert loaded.motivation.score is None
    assert loaded.learning_goal.label is None
    assert loaded.learning_style.label is None
    assert loaded.resource_preference.label is None
    assert loaded.weak_points.items == []
    assert loaded.knowledge_base.confidence == 0
    assert loaded.resource_preference.confidence == 0


def test_student_profile_confidence_and_category_round_trip(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    profile = default_profile("student-legacy-001")
    profile.knowledge_base = ProfileDimension(score=64, confidence=0.7, source="jury")
    profile.goal = CategoricalDimension(
        label="临床实用",
        distribution={"临床实用": 0.8, "应试提分": 0.2},
        confidence=0.75,
        source="llm",
    )

    save_legacy_profile(profile, db_path=db_path)
    loaded = get_legacy_profile("student-legacy-001", db_path=db_path)

    assert isinstance(loaded, StudentProfile)
    assert loaded.knowledge_base.score == 64
    assert loaded.knowledge_base.confidence == 0.7
    assert loaded.knowledge_base.source == "jury"
    assert loaded.goal.label == "临床实用"
    assert loaded.goal.derived_score == 80
    assert loaded.goal.confidence == 0.75
    assert "score" not in loaded.goal.model_dump()


def test_legacy_profile_json_migrates_category_scores_to_labels(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    old_payload = {
        "user_id": "student-migrate-001",
        "knowledge_base": {"score": 50, "evidence": "old numeric"},
        "learning_style": {"score": 68, "evidence": "视觉型"},
        "weak_points": {"score": 40, "evidence": "剂量", "items": ["剂量"]},
        "goal": {"score": 72, "evidence": "应试提分"},
        "pace": {"score": 60, "evidence": "steady"},
        "motivation": {"score": 70, "evidence": "active"},
        "resource_preference": {"score": 66, "evidence": "知识图谱"},
        "updated_at": datetime.fromisoformat("2026-07-02T16:00:00+08:00").isoformat(),
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE student_profiles (
                user_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO student_profiles(user_id, profile_json, updated_at) VALUES (?, ?, ?)",
            ("student-migrate-001", json.dumps(old_payload, ensure_ascii=False), old_payload["updated_at"]),
        )

    loaded = get_legacy_profile("student-migrate-001", db_path=db_path)

    assert loaded.goal.label == "应试提分"
    assert loaded.learning_style.label == "视觉型"
    assert loaded.resource_preference.label == "知识图谱"
    assert loaded.knowledge_base.confidence == 0


def test_evidence_card_write_read_round_trip(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    card = EvidenceCard(
        dimension="knowledge_base",
        source="开药实战",
        fact="桂枝配伍正确；清热药剂量偏低",
        objective_signal={"对症": True, "配伍": True, "剂量": False},
        weight=1.5,
        timestamp=datetime.fromisoformat("2026-07-02T16:00:00+08:00"),
    )

    add_evidence("student-001", card, db_path=db_path)
    cards = list_evidence("student-001", db_path=db_path)

    assert cards == [card]
    assert cards[0].objective_signal["剂量"] is False


def test_legacy_storage_evidence_card_write_read_round_trip(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    card = EvidenceCard(
        dimension="knowledge_base",
        source="开药实战",
        fact="桂枝配伍正确；清热药剂量偏低",
        objective_signal={"对症": True, "配伍": True, "剂量": False},
        weight=1.5,
        timestamp=datetime.fromisoformat("2026-07-02T16:00:00+08:00"),
    )

    add_legacy_evidence("student-001", card, db_path=db_path)
    cards = list_legacy_evidence("student-001", db_path=db_path)

    assert cards == [card]
    assert cards[0].objective_signal["剂量"] is False


def test_categorical_dimension_derived_score_uses_distribution_concentration() -> None:
    legacy_dimension = CategoricalDimension(label="知识图谱", distribution={"知识图谱": 0.625, "汤头歌诀": 0.375})
    profile_dimension = create_blank_profile("student-001").resource_preference
    profile_dimension.label = "知识图谱"
    profile_dimension.distribution = {"知识图谱": 0.625, "汤头歌诀": 0.375}  # type: ignore[dict-item]

    assert legacy_dimension.derived_score == 62.5
    assert profile_dimension.derived_score == 62.5


def _test_db_path(tmp_path: Path) -> Path:
    root = tmp_path / ".data"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"profile_schema_test_{uuid4().hex}.db"
