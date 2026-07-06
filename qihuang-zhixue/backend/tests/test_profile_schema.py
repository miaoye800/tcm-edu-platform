from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.profile.schema import EvidenceCard, Profile, create_blank_profile
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


def test_evidence_card_write_read_round_trip(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    card = EvidenceCard(
        dimension="knowledge_base",
        source="抓药实战",
        fact="桂枝配伍正确；清热药剂量偏低",
        objective_signal={"对症": True, "配伍": True, "剂量": False},
        weight=1.5,
        timestamp=datetime.fromisoformat("2026-07-02T16:00:00+08:00"),
    )

    add_evidence("student-001", card, db_path=db_path)
    cards = list_evidence("student-001", db_path=db_path)

    assert cards == [card]
    assert cards[0].objective_signal["剂量"] is False


def _test_db_path(tmp_path: Path) -> Path:
    root = tmp_path / ".data"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"profile_schema_test_{uuid4().hex}.db"
