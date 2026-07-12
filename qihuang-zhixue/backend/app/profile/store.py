"""SQLite persistence for the learning profile data foundation."""

from __future__ import annotations

import json
import logging
import re
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
logger = logging.getLogger(__name__)


PROFILE_COLUMNS: dict[str, str] = {
    "user_id": "TEXT",
    "profile_json": "TEXT NOT NULL DEFAULT '{}'",
    "created_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
    "updated_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
}
EVIDENCE_COLUMNS: dict[str, str] = {
    "id": "INTEGER",
    "user_id": "TEXT NOT NULL DEFAULT ''",
    "dimension": "TEXT NOT NULL DEFAULT ''",
    "source": "TEXT NOT NULL DEFAULT ''",
    "fact": "TEXT NOT NULL DEFAULT ''",
    "objective_signal_json": "TEXT NOT NULL DEFAULT '{}'",
    "weight": "REAL NOT NULL DEFAULT 0",
    "timestamp": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
    "evidence_json": "TEXT NOT NULL DEFAULT '{}'",
}
PROFILE_SESSION_COLUMNS: dict[str, str] = {
    "session_id": "TEXT",
    "user_id": "TEXT NOT NULL DEFAULT ''",
    "mode": "TEXT NOT NULL DEFAULT 'apprentice'",
    "current_step": "INTEGER NOT NULL DEFAULT 0",
    "step_index": "INTEGER DEFAULT 0",
    "finished": "INTEGER NOT NULL DEFAULT 0",
    "persisted": "INTEGER NOT NULL DEFAULT 0",
    "step_data_json": "TEXT NOT NULL DEFAULT '{}'",
    "evidence_cards_json": "TEXT NOT NULL DEFAULT '[]'",
    "created_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
    "updated_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
}
CHAT_SESSION_COLUMNS: dict[str, str] = {
    "id": "INTEGER",
    "user_id": "TEXT NOT NULL DEFAULT ''",
    "session_id": "TEXT NOT NULL DEFAULT ''",
    "role": "TEXT NOT NULL DEFAULT ''",
    "content": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
}


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection configured for concurrent profile writes."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Create and lightly migrate profile-owned SQLite tables."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as conn:
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
        _migrate_table_columns(conn, "profile", PROFILE_COLUMNS)
        _migrate_table_columns(conn, "evidence", EVIDENCE_COLUMNS)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                current_step INTEGER NOT NULL,
                step_index INTEGER,
                finished INTEGER NOT NULL DEFAULT 0,
                persisted INTEGER NOT NULL DEFAULT 0,
                step_data_json TEXT NOT NULL DEFAULT '{}',
                evidence_cards_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _migrate_profile_sessions(conn)
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_profile_sessions_user_id
            ON profile_sessions(user_id)
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
        _migrate_table_columns(conn, "chat_sessions", CHAT_SESSION_COLUMNS)
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_session
            ON chat_sessions(user_id, session_id, id)
            """
        )
        conn.commit()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_table_columns(conn: sqlite3.Connection, table: str, expected_columns: dict[str, str]) -> None:
    columns = _table_columns(conn, table)
    for name, definition in expected_columns.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _migrate_profile_sessions(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "profile_sessions")
    if not columns:
        return
    if "session_id" not in columns:
        logger.warning(
            "profile_sessions schema is missing primary key column session_id; dropping temporary session table"
        )
        conn.execute("DROP TABLE profile_sessions")
        conn.execute(
            """
            CREATE TABLE profile_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                current_step INTEGER NOT NULL,
                step_index INTEGER,
                finished INTEGER NOT NULL DEFAULT 0,
                persisted INTEGER NOT NULL DEFAULT 0,
                step_data_json TEXT NOT NULL DEFAULT '{}',
                evidence_cards_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        return
    _migrate_table_columns(conn, "profile_sessions", PROFILE_SESSION_COLUMNS)
    if "persisted" not in columns and "finished" in columns:
        conn.execute("UPDATE profile_sessions SET persisted = COALESCE(finished, 0)")


_MISSING_COLUMN_RE = re.compile(
    r"(?:table (?P<table>\w+) has no column named|no such column:)\s+(?:(?P<table2>\w+)\.)?(?P<column>\w+)",
    re.IGNORECASE,
)


def _diagnose_operational_error(
    conn: sqlite3.Connection,
    table: str,
    exc: sqlite3.OperationalError,
) -> tuple[str | None, str | None]:
    message = str(exc)
    match = _MISSING_COLUMN_RE.search(message)
    error_table = (match.group("table") or match.group("table2")) if match else None
    missing_column = match.group("column") if match else None
    logger.exception(
        "SQLite OperationalError while accessing table=%s missing_column=%s existing_columns=%s error=%s",
        error_table or table,
        missing_column,
        sorted(_table_columns(conn, table)),
        message,
    )
    return error_table or table, missing_column


def _repair_table_after_operational_error(
    conn: sqlite3.Connection,
    table: str,
    exc: sqlite3.OperationalError,
) -> bool:
    error_table, missing_column = _diagnose_operational_error(conn, table, exc)
    if missing_column is None or error_table != table:
        return False
    if table == "profile_sessions":
        _migrate_profile_sessions(conn)
        return missing_column in _table_columns(conn, table)
    migrations = {
        "profile": PROFILE_COLUMNS,
        "evidence": EVIDENCE_COLUMNS,
        "chat_sessions": CHAT_SESSION_COLUMNS,
    }
    if table in migrations:
        _migrate_table_columns(conn, table, migrations[table])
        return missing_column in _table_columns(conn, table)
    return False


def upsert_profile(profile: Profile, db_path: Path = DB_PATH) -> Profile:
    """Insert or replace a full profile snapshot."""

    init_db(db_path)
    profile.updated_at = datetime.now(UTC)
    payload = profile.model_dump_json()
    with closing(connect(db_path)) as conn:
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
    with closing(connect(db_path)) as conn:
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
    with closing(connect(db_path)) as conn:
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
    with closing(connect(db_path)) as conn:
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
    with closing(connect(db_path)) as conn:
        try:
            _execute_profile_session_upsert(conn, row)
        except sqlite3.OperationalError as exc:
            if not _repair_table_after_operational_error(conn, "profile_sessions", exc):
                raise
            _execute_profile_session_upsert(conn, row)
        conn.commit()


def get_profile_session_row(session_id: str, user_id: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Load a profile collection session row for its owner or raise KeyError."""

    init_db(db_path)
    with closing(connect(db_path)) as conn:
        try:
            row = _execute_profile_session_select(conn, session_id, user_id)
        except sqlite3.OperationalError as exc:
            if not _repair_table_after_operational_error(conn, "profile_sessions", exc):
                raise
            row = _execute_profile_session_select(conn, session_id, user_id)
    if row is None:
        raise KeyError(session_id)
    return {
        "session_id": row[0],
        "user_id": row[1],
        "mode": row[2],
        "current_step": row[3],
        "step_index": row[4],
        "finished": row[5],
        "persisted": row[6],
        "step_data_json": row[7],
        "evidence_cards_json": row[8],
        "created_at": row[9],
        "updated_at": row[10],
    }


def _execute_profile_session_upsert(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO profile_sessions(
            session_id,
            user_id,
            mode,
            current_step,
            step_index,
            finished,
            persisted,
            step_data_json,
            evidence_cards_json,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            user_id = excluded.user_id,
            mode = excluded.mode,
            current_step = excluded.current_step,
            step_index = excluded.step_index,
            finished = excluded.finished,
            persisted = excluded.persisted,
            step_data_json = excluded.step_data_json,
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
            row["persisted"],
            row["step_data_json"],
            row["evidence_cards_json"],
            row["created_at"],
            row["updated_at"],
        ),
    )


def _execute_profile_session_select(
    conn: sqlite3.Connection,
    session_id: str,
    user_id: str,
) -> tuple[Any, ...] | None:
    return conn.execute(
        """
        SELECT
            session_id,
            user_id,
            mode,
            current_step,
            step_index,
            finished,
            persisted,
            step_data_json,
            evidence_cards_json,
            created_at,
            updated_at
        FROM profile_sessions
        WHERE session_id = ? AND user_id = ?
        """,
        (session_id, user_id),
    ).fetchone()


def delete_profile_session_rows(db_path: Path = DB_PATH) -> None:
    """Delete persisted session rows; used by tests and demos only."""

    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute("DELETE FROM profile_sessions")
        conn.commit()


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

    with closing(connect(target_db)) as conn:
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
    with closing(connect(target_db)) as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM chat_sessions
            WHERE user_id = ? AND session_id = ?
            ORDER BY id ASC
            """,
            (user_id, session_id),
        ).fetchall()
    return [{"role": str(role), "content": str(content)} for role, content in rows]
