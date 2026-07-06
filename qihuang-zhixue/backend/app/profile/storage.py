"""SQLite persistence for legacy StudentProfile snapshots and evidence cards."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.profile.evidence import EvidenceCard
from app.profile.models import ProfileEvent, StudentProfile, default_profile

DB_PATH = Path(__file__).resolve().parents[2] / ".data" / "qihuang.db"


def init_db(db_path: Path = DB_PATH) -> None:
    """Initialize SQLite tables and additive migrations."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile (
                user_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS student_profiles (
                user_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                dimension TEXT NOT NULL,
                source TEXT NOT NULL,
                fact TEXT NOT NULL,
                objective_signal_json TEXT NOT NULL,
                weight REAL NOT NULL,
                timestamp TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_evidence_user_id_id
            ON evidence(user_id, id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_session
            ON chat_sessions(user_id, session_id, id)
            """
        )
        conn.commit()


def get_profile(user_id: str, db_path: Path = DB_PATH) -> StudentProfile:
    """Load a profile, migrating old JSON snapshots on read."""

    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT profile_json FROM profile WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is not None:
            try:
                return StudentProfile.model_validate_json(str(row[0]))
            except ValidationError:
                pass
        row = conn.execute(
            "SELECT profile_json FROM student_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return default_profile(user_id)
        profile = StudentProfile.model_validate_json(str(row[0]))
        _save_profile_with_connection(conn, profile)
        conn.commit()
        return profile


def save_profile(profile: StudentProfile, db_path: Path = DB_PATH) -> StudentProfile:
    """Save a profile snapshot with the upgraded schema."""

    init_db(db_path)
    profile.updated_at = datetime.now(UTC)
    with closing(sqlite3.connect(db_path)) as conn:
        _save_profile_with_connection(conn, profile)
        conn.commit()
    return profile


def add_evidence(user_id: str, card: EvidenceCard, db_path: Path = DB_PATH) -> EvidenceCard:
    """Persist an objective evidence card."""

    init_db(db_path)
    payload = card.model_dump(mode="json")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO evidence(
                user_id,
                dimension,
                source,
                fact,
                objective_signal_json,
                weight,
                timestamp,
                evidence_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                card.dimension,
                card.source,
                card.fact,
                json.dumps(payload["objective_signal"], ensure_ascii=False),
                card.weight,
                payload["timestamp"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()
    return card


def list_evidence(user_id: str, db_path: Path = DB_PATH) -> list[EvidenceCard]:
    """List evidence cards for a user in insertion order."""

    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT evidence_json FROM evidence
            WHERE user_id = ?
            ORDER BY id ASC
            """,
            (user_id,),
        ).fetchall()
    return [EvidenceCard.model_validate_json(str(row[0])) for row in rows]


def save_session_event(event: ProfileEvent, db_path: Path = DB_PATH) -> None:
    """Record a legacy profile update event."""

    init_db(db_path)
    payload = event.model_dump_json()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO profile_sessions(user_id, event_type, event_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (event.user_id, event.event_type, payload, event.created_at.isoformat()),
        )
        conn.commit()


def list_session_events(user_id: str, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """List legacy session events for debugging and later analysis."""

    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT event_json FROM profile_sessions
            WHERE user_id = ?
            ORDER BY id ASC
            """,
            (user_id,),
        ).fetchall()
    return [json.loads(str(row[0])) for row in rows]


def append_chat_messages(
    user_id: str,
    session_id: str,
    messages: list[dict[str, str]],
    db_path: Path | None = None,
) -> None:
    """Append chat messages for a user/session pair."""

    if not messages:
        return

    target_db = db_path or DB_PATH
    init_db(target_db)
    now = datetime.now(UTC).isoformat()
    rows = [
        (user_id, session_id, message["role"], message["content"], now)
        for message in messages
        if message.get("role") and message.get("content")
    ]
    if not rows:
        return

    with closing(sqlite3.connect(target_db)) as conn:
        conn.executemany(
            """
            INSERT INTO chat_sessions(user_id, session_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def list_chat_messages(
    user_id: str,
    session_id: str,
    db_path: Path | None = None,
) -> list[dict[str, str]]:
    """Read chat messages for a user/session pair."""

    target_db = db_path or DB_PATH
    init_db(target_db)
    with closing(sqlite3.connect(target_db)) as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM chat_sessions
            WHERE user_id = ? AND session_id = ?
            ORDER BY id ASC
            """,
            (user_id, session_id),
        ).fetchall()
    return [{"role": str(role), "content": str(content)} for role, content in rows]


def _save_profile_with_connection(conn: sqlite3.Connection, profile: StudentProfile) -> None:
    payload = profile.model_dump_json()
    now = profile.updated_at.isoformat()
    conn.execute(
        """
        INSERT INTO student_profiles(user_id, profile_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            profile_json = excluded.profile_json,
            updated_at = excluded.updated_at
        """,
        (profile.user_id, payload, now),
    )
