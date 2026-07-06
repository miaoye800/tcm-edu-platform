"""SQLite persistence for the learning profile data foundation."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.profile.schema import (
    EvidenceCard,
    Profile,
    create_blank_profile,
    evidence_card_from_dict,
    evidence_card_to_json_dict,
)

DB_PATH = Path(__file__).resolve().parents[2] / ".data" / "qihuang.db"


def init_db(db_path: Path = DB_PATH) -> None:
    """Create profile and evidence tables if missing."""

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
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                current_step INTEGER NOT NULL,
                step_index INTEGER,
                finished INTEGER NOT NULL DEFAULT 0,
                evidence_cards_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_profile_sessions_user_id
            ON profile_sessions(user_id)
            """
        )
        conn.commit()


def upsert_profile(profile: Profile, db_path: Path = DB_PATH) -> Profile:
    """Insert or replace a full profile snapshot."""

    init_db(db_path)
    profile.updated_at = datetime.now(UTC)
    payload = profile.model_dump_json()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO profile(user_id, profile_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                profile_json = excluded.profile_json,
                updated_at = excluded.updated_at
            """,
            (
                profile.user_id,
                payload,
                profile.created_at.isoformat(),
                profile.updated_at.isoformat(),
            ),
        )
        conn.commit()
    return profile


def get_profile(user_id: str, db_path: Path = DB_PATH) -> Profile:
    """Load a profile, returning a blank low-confidence profile when absent."""

    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT profile_json FROM profile WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return create_blank_profile(user_id)
    return Profile.model_validate_json(str(row[0]))


def add_evidence(user_id: str, card: EvidenceCard, db_path: Path = DB_PATH) -> EvidenceCard:
    """Persist an objective evidence card."""

    init_db(db_path)
    payload = evidence_card_to_json_dict(card)
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
    return [evidence_card_from_dict(json.loads(str(row[0]))) for row in rows]


def upsert_profile_session_row(row: dict[str, Any], db_path: Path = DB_PATH) -> None:
    """Insert or replace one persisted profile collection session."""

    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO profile_sessions(
                session_id,
                user_id,
                mode,
                current_step,
                step_index,
                finished,
                evidence_cards_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                user_id = excluded.user_id,
                mode = excluded.mode,
                current_step = excluded.current_step,
                step_index = excluded.step_index,
                finished = excluded.finished,
                evidence_cards_json = excluded.evidence_cards_json,
                updated_at = excluded.updated_at
            """,
            (
                row["session_id"],
                row["user_id"],
                row["mode"],
                row["current_step"],
                row.get("step_index"),
                row["finished"],
                row["evidence_cards_json"],
                row["created_at"],
                row["updated_at"],
            ),
        )
        conn.commit()


def get_profile_session_row(session_id: str, user_id: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Load a profile collection session row for its owner or raise KeyError."""

    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT
                session_id,
                user_id,
                mode,
                current_step,
                step_index,
                finished,
                evidence_cards_json,
                created_at,
                updated_at
            FROM profile_sessions
            WHERE session_id = ? AND user_id = ?
            """,
            (session_id, user_id),
        ).fetchone()
    if row is None:
        raise KeyError(session_id)
    return {
        "session_id": row[0],
        "user_id": row[1],
        "mode": row[2],
        "current_step": row[3],
        "step_index": row[4],
        "finished": row[5],
        "evidence_cards_json": row[6],
        "created_at": row[7],
        "updated_at": row[8],
    }


def delete_profile_session_rows(db_path: Path = DB_PATH) -> None:
    """Delete persisted session rows; used by tests and demos only."""

    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("DELETE FROM profile_sessions")
        conn.commit()
