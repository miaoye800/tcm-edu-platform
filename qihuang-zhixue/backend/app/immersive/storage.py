"""Durable metadata for immersive sessions; message history stays in chat_sessions."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.profile.store import DB_PATH, connect, init_db as init_profile_db


def init_db(db_path: Path = DB_PATH) -> None:
    init_profile_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS immersive_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                opening_template INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_immersive_sessions_user ON immersive_sessions(user_id, created_at)")
        conn.commit()


def last_opening_template(user_id: str, db_path: Path = DB_PATH) -> int | None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT opening_template FROM immersive_sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return int(row[0]) if row else None


def create_session(session_id: str, user_id: str, node_id: str, opening_template: int, db_path: Path = DB_PATH) -> None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO immersive_sessions(session_id, user_id, node_id, opening_template, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, node_id, opening_template, datetime.now(UTC).isoformat()),
        )
        conn.commit()


def get_session(session_id: str, user_id: str, db_path: Path = DB_PATH) -> dict[str, str | int] | None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT session_id, node_id, opening_template FROM immersive_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
    if row is None:
        return None
    return {"session_id": str(row[0]), "node_id": str(row[1]), "opening_template": int(row[2])}
