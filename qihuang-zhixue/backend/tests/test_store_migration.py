import asyncio
import sqlite3
from pathlib import Path

from app.profile.evidence import EvidenceCard
from app.profile.modes import APPRENTICE_STEPS, answer_profile_session, finish_profile_session, start_profile_session
from app.profile.schema import create_blank_profile
from app.profile.store import add_evidence, get_profile, init_db, list_evidence


def test_init_db_migrates_old_profile_sessions_and_flow_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "old-sessions.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE profile_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                current_step INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO profile_sessions(session_id, user_id, mode, current_step)
            VALUES ('legacy-session', 'legacy-user', 'apprentice', 1)
            """
        )
        conn.commit()

    init_db(db_path)
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(profile_sessions)").fetchall()}
        legacy = conn.execute(
            """
            SELECT user_id, step_index, finished, evidence_cards_json, created_at, updated_at
            FROM profile_sessions
            WHERE session_id = 'legacy-session'
            """
        ).fetchone()

    assert {"step_index", "evidence_cards_json", "finished", "created_at", "updated_at"} <= columns
    assert legacy == ("legacy-user", 0, 0, "[]", "1970-01-01T00:00:00+00:00", "1970-01-01T00:00:00+00:00")

    session = start_profile_session("student-001", "apprentice", db_path=db_path)
    session, cards = asyncio.run(
        answer_profile_session(
            session.session_id,
            user_id="student-001",
            step_id=APPRENTICE_STEPS[0],
            payload={"identity_card": "clinical apprentice", "response_time_ms": 1200},
            db_path=db_path,
        )
    )
    finished = finish_profile_session(session.session_id, user_id="student-001", db_path=db_path)
    stored = list_evidence("student-001", db_path=db_path)

    assert session.step_index == 1
    assert cards
    assert finished.persisted is True
    assert len(stored) == len(finished.evidence_cards)
    assert stored == finished.evidence_cards


def test_init_db_migrates_profile_and_evidence_without_dropping_old_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "old-profile-evidence.db"
    old_profile = create_blank_profile("legacy-user")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE profile (
                user_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO profile(user_id, profile_json) VALUES (?, ?)",
            (old_profile.user_id, old_profile.model_dump_json()),
        )
        conn.execute(
            """
            CREATE TABLE evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                dimension TEXT NOT NULL,
                source TEXT NOT NULL,
                fact TEXT NOT NULL,
                weight REAL NOT NULL,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO evidence(user_id, dimension, source, fact, weight, timestamp)
            VALUES ('legacy-user', 'knowledge_base', 'legacy-source', 'legacy-fact', 1.0, '2026-07-06T00:00:00+00:00')
            """
        )
        conn.commit()

    init_db(db_path)
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        profile_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(profile)").fetchall()}
        evidence_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(evidence)").fetchall()}
        profile_row = conn.execute("SELECT user_id, profile_json FROM profile WHERE user_id = 'legacy-user'").fetchone()
        evidence_row = conn.execute(
            """
            SELECT user_id, dimension, source, fact, weight, timestamp
            FROM evidence
            WHERE user_id = 'legacy-user'
            """
        ).fetchone()

    assert {"created_at", "updated_at"} <= profile_columns
    assert {"objective_signal_json", "evidence_json"} <= evidence_columns
    assert profile_row == (old_profile.user_id, old_profile.model_dump_json())
    assert evidence_row == (
        "legacy-user",
        "knowledge_base",
        "legacy-source",
        "legacy-fact",
        1.0,
        "2026-07-06T00:00:00+00:00",
    )
    assert get_profile("legacy-user", db_path=db_path).user_id == "legacy-user"

    card = EvidenceCard(
        dimension="knowledge_base",
        source="new-source",
        fact="new-fact",
        objective_signal={"correct": True},
        weight=1.0,
    )
    add_evidence("new-user", card, db_path=db_path)
    assert list_evidence("new-user", db_path=db_path) == [card]
