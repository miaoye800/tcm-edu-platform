"""掌握度与复习事件的 SQLite 存储。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[2] / ".data" / "qihuang.db"


def init_db(db_path: Path = DB_PATH) -> None:
    """初始化掌握度事件表。"""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mastery_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                kc_id TEXT NOT NULL,
                correct INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                elapsed_seconds REAL,
                evidence_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mastery_events_user_kc_id
            ON mastery_events(user_id, kc_id, id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_states (
                user_id TEXT NOT NULL,
                kc_id TEXT NOT NULL,
                difficulty REAL NOT NULL,
                stability REAL NOT NULL,
                retrievability REAL NOT NULL,
                due_at TEXT NOT NULL,
                last_rating TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                PRIMARY KEY(user_id, kc_id)
            )
            """
        )
        conn.commit()


def add_mastery_event(
    *,
    user_id: str,
    kc_id: str,
    correct: bool,
    event_type: str = "quiz",
    elapsed_seconds: float | None = None,
    evidence: dict[str, Any] | None = None,
    created_at: datetime | None = None,
    db_path: Path = DB_PATH,
) -> None:
    """写入一次做题或开药事件。"""

    init_db(db_path)
    now = created_at or datetime.now(UTC)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO mastery_events(
                user_id, kc_id, correct, event_type, elapsed_seconds, evidence_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                kc_id,
                1 if correct else 0,
                event_type,
                elapsed_seconds,
                json.dumps(evidence or {}, ensure_ascii=False),
                _to_iso(now),
            ),
        )
        conn.commit()


def list_mastery_events(user_id: str, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """按写入顺序读取用户掌握度事件。"""

    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT kc_id, correct, event_type, elapsed_seconds, evidence_json, created_at
            FROM mastery_events
            WHERE user_id = ?
            ORDER BY id ASC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "kc_id": str(kc_id),
            "correct": bool(correct),
            "event_type": str(event_type),
            "elapsed_seconds": elapsed_seconds,
            "evidence": json.loads(str(evidence_json)),
            "created_at": datetime.fromisoformat(str(created_at)),
        }
        for kc_id, correct, event_type, elapsed_seconds, evidence_json, created_at in rows
    ]


def upsert_review_state(
    *,
    user_id: str,
    kc_id: str,
    difficulty: float,
    stability: float,
    retrievability: float,
    due_at: datetime,
    last_rating: str,
    attempts: int,
    evidence: dict[str, Any] | None = None,
    updated_at: datetime | None = None,
    db_path: Path = DB_PATH,
) -> None:
    """保存一个知识点的 FSRS 复习状态。"""

    init_db(db_path)
    now = updated_at or datetime.now(UTC)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO review_states(
                user_id, kc_id, difficulty, stability, retrievability, due_at,
                last_rating, attempts, updated_at, evidence_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, kc_id) DO UPDATE SET
                difficulty = excluded.difficulty,
                stability = excluded.stability,
                retrievability = excluded.retrievability,
                due_at = excluded.due_at,
                last_rating = excluded.last_rating,
                attempts = excluded.attempts,
                updated_at = excluded.updated_at,
                evidence_json = excluded.evidence_json
            """,
            (
                user_id,
                kc_id,
                difficulty,
                stability,
                retrievability,
                _to_iso(due_at),
                last_rating,
                attempts,
                _to_iso(now),
                json.dumps(evidence or {}, ensure_ascii=False),
            ),
        )
        conn.commit()


def list_review_states(user_id: str, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """读取用户全部复习状态。"""

    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT kc_id, difficulty, stability, retrievability, due_at,
                   last_rating, attempts, updated_at, evidence_json
            FROM review_states
            WHERE user_id = ?
            ORDER BY due_at ASC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "kc_id": str(kc_id),
            "difficulty": float(difficulty),
            "stability": float(stability),
            "retrievability": float(retrievability),
            "due_at": datetime.fromisoformat(str(due_at)),
            "last_rating": str(last_rating),
            "attempts": int(attempts),
            "updated_at": datetime.fromisoformat(str(updated_at)),
            "evidence": json.loads(str(evidence_json)),
        }
        for kc_id, difficulty, stability, retrievability, due_at, last_rating, attempts, updated_at, evidence_json in rows
    ]


def get_review_state(user_id: str, kc_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    """读取单个知识点复习状态。"""

    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT kc_id, difficulty, stability, retrievability, due_at,
                   last_rating, attempts, updated_at, evidence_json
            FROM review_states
            WHERE user_id = ? AND kc_id = ?
            """,
            (user_id, kc_id),
        ).fetchone()
    if row is None:
        return None
    state = list_review_states(user_id, db_path=db_path)
    for item in state:
        if item["kc_id"] == kc_id:
            return item
    return None


def _to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()

