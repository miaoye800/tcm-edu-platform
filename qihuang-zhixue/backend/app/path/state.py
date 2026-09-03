"""User-specific route progress, deliberately separate from immutable plans."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.path.constants import PATH_STATES, PathState
from app.profile.store import DB_PATH, connect


def init_state_db(db_path: Path = DB_PATH) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_path_state (
                user_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('locked', 'available', 'current', 'completed', 'paused', 'custom')),
                updated_at TEXT NOT NULL,
                meta JSON NOT NULL DEFAULT '{}',
                PRIMARY KEY(user_id, node_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_path_state_user ON user_path_state(user_id, updated_at)")
        conn.commit()


def set_state(
    user_id: str,
    node_id: str,
    status: PathState,
    *,
    meta: dict[str, Any] | None = None,
    db_path: Path = DB_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    if status not in PATH_STATES:
        raise ValueError(f"unsupported path state: {status}")
    updated_at = (now or datetime.now(UTC)).isoformat()
    value = dict(meta or {})
    init_state_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute(
            """INSERT INTO user_path_state(user_id, node_id, status, updated_at, meta)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, node_id) DO UPDATE SET status=excluded.status,
                   updated_at=excluded.updated_at, meta=excluded.meta""",
            (user_id, node_id, status, updated_at, json.dumps(value, ensure_ascii=False, sort_keys=True)),
        )
        conn.commit()
    return {"node_id": node_id, "status": status, "updated_at": updated_at, "meta": value}


def list_states(user_id: str, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_state_db(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT node_id, status, updated_at, meta FROM user_path_state WHERE user_id = ? ORDER BY updated_at, node_id",
            (user_id,),
        ).fetchall()
    return [{"node_id": row[0], "status": row[1], "updated_at": row[2], "meta": json.loads(row[3])} for row in rows]


def get_state(user_id: str, node_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    return next((item for item in list_states(user_id, db_path) if item["node_id"] == node_id), None)
