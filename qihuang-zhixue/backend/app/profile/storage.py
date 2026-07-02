"""SQLite 画像持久化。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from app.profile.models import ProfileEvent, StudentProfile, default_profile

DB_PATH = Path(__file__).resolve().parents[2] / ".data" / "qihuang.db"


def init_db(db_path: Path = DB_PATH) -> None:
    """初始化 SQLite 表结构。"""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
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
            CREATE TABLE IF NOT EXISTS profile_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def get_profile(user_id: str, db_path: Path = DB_PATH) -> StudentProfile:
    """读取画像；不存在时返回默认空画像。"""

    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT profile_json FROM student_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return default_profile(user_id)
    return StudentProfile.model_validate_json(str(row[0]))


def save_profile(profile: StudentProfile, db_path: Path = DB_PATH) -> StudentProfile:
    """保存画像快照。"""

    init_db(db_path)
    payload = profile.model_dump_json()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO student_profiles(user_id, profile_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                profile_json = excluded.profile_json,
                updated_at = excluded.updated_at
            """,
            (profile.user_id, payload, profile.updated_at.isoformat()),
        )
        conn.commit()
    return profile


def save_session_event(event: ProfileEvent, db_path: Path = DB_PATH) -> None:
    """记录画像更新会话事件。"""

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
    """读取用户会话事件，供调试和后续分析使用。"""

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
