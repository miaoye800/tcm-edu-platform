import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.profile.evidence import EvidenceCard
from app.profile.schema import CategoricalDimension, Profile, create_blank_profile
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


def test_profile_confidence_and_category_round_trip(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    profile = create_blank_profile("student-new-001")
    profile.knowledge_base.score = 64
    profile.knowledge_base.confidence = 0.7
    profile.learning_goal.label = "临床实用"
    profile.learning_goal.distribution = {"临床实用": 0.8, "应试提分": 0.2}
    profile.learning_goal.confidence = 0.75

    upsert_profile(profile, db_path=db_path)
    loaded = get_profile("student-new-001", db_path=db_path)

    assert loaded.knowledge_base.score == 64
    assert loaded.knowledge_base.confidence == 0.7
    assert loaded.learning_goal.label == "临床实用"
    assert loaded.learning_goal.derived_score == 80
    assert loaded.learning_goal.confidence == 0.75


def test_profile_store_does_not_create_legacy_table(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    upsert_profile(create_blank_profile("student-001"), db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }

    assert "profile" in tables
    legacy_table = "student" + "_profiles"
    assert legacy_table not in tables


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


def test_categorical_dimension_derived_score_uses_distribution_concentration() -> None:
    dimension = CategoricalDimension(label="知识图谱", distribution={"知识图谱": 0.625, "汤头歌诀": 0.375})
    profile_dimension = create_blank_profile("student-001").resource_preference
    profile_dimension.label = "知识图谱"
    profile_dimension.distribution = {"知识图谱": 0.625, "汤头歌诀": 0.375}  # type: ignore[dict-item]

    assert dimension.derived_score == 62.5
    assert profile_dimension.derived_score == 62.5


def _test_db_path(tmp_path: Path) -> Path:
    root = tmp_path / ".data"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"profile_schema_test_{uuid4().hex}.db"
