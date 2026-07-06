"""SQLite persistence for the learning profile data foundation."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

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
