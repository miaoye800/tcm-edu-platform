"""Versioned persistence for immutable path-plan snapshots."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.path.store import DB_PATH
from app.profile.store import connect


def init_plan_db(db_path: Path = DB_PATH) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_path_plan (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                plan_kind TEXT NOT NULL CHECK(plan_kind IN ('l1', 'l2', 'l3_adjust', 'reorder', 'custom_node')),
                domain_id TEXT,
                payload_json TEXT NOT NULL,
                fallback INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, version)
            )
            """
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_user_path_plan_user_version
               ON user_path_plan(user_id, version DESC)"""
        )
        conn.commit()


def save_plan_version(
    *,
    user_id: str,
    plan_kind: str,
    payload: dict[str, Any],
    fallback: bool = False,
    domain_id: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Append one immutable snapshot and atomically allocate its user version."""

    init_plan_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM user_path_plan WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        version = int(row[0])
        conn.execute(
            """INSERT INTO user_path_plan(
                   user_id, version, plan_kind, domain_id, payload_json, fallback, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                version,
                plan_kind,
                domain_id,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                1 if fallback else 0,
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()
    return version


def list_plan_versions(user_id: str, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_plan_db(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """SELECT version, plan_kind, domain_id, payload_json, fallback, created_at
               FROM user_path_plan WHERE user_id = ? ORDER BY version""",
            (user_id,),
        ).fetchall()
    return [
        {
            "version": int(row[0]),
            "plan_kind": str(row[1]),
            "domain_id": row[2],
            "payload": json.loads(str(row[3])),
            "fallback": bool(row[4]),
            "created_at": str(row[5]),
        }
        for row in rows
    ]
