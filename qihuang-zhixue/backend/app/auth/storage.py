"""用户账号 SQLite 存储。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.auth.models import AuthUser
from app.auth.passwords import hash_password, verify_password
from app.profile.store import DB_PATH


class DuplicateUsernameError(ValueError):
    """用户名已存在。"""


def init_db(db_path: Path = DB_PATH) -> None:
    """初始化用户表。"""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                display_name TEXT,
                is_guest INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def create_user(
    *,
    username: str,
    password: str,
    display_name: str | None = None,
    db_path: Path = DB_PATH,
) -> AuthUser:
    """创建普通账号。"""

    normalized_username = _normalize_username(username)
    return _insert_user(
        user_id=str(uuid4()),
        username=normalized_username,
        password_hash=hash_password(password),
        display_name=(display_name or normalized_username).strip() or normalized_username,
        is_guest=False,
        db_path=db_path,
    )


def upsert_seed_user(
    *,
    username: str,
    password: str,
    display_name: str,
    user_id: str | None = None,
    db_path: Path = DB_PATH,
) -> AuthUser:
    """创建或更新演示账号，保证 demo 密码可重复初始化。"""

    init_db(db_path)
    normalized_username = _normalize_username(username)
    now = datetime.now(UTC).isoformat()
    resolved_user_id = user_id or _existing_user_id(normalized_username, db_path) or str(uuid4())
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO users(user_id, username, password_hash, display_name, is_guest, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
            ON CONFLICT(username) DO UPDATE SET
                password_hash = excluded.password_hash,
                display_name = excluded.display_name,
                is_guest = 0
            """,
            (resolved_user_id, normalized_username, hash_password(password), display_name, now),
        )
        conn.commit()
    user = get_user_by_username(normalized_username, db_path=db_path)
    if user is None:
        raise RuntimeError("演示账号初始化失败")
    return user


def create_guest_user(*, db_path: Path = DB_PATH) -> AuthUser:
    """创建一键体验游客账号。"""

    suffix = uuid4().hex[:8]
    return _insert_user(
        user_id=str(uuid4()),
        username=f"guest_{suffix}",
        password_hash=None,
        display_name=f"游客 {suffix}",
        is_guest=True,
        db_path=db_path,
    )


def authenticate_user(*, username: str, password: str, db_path: Path = DB_PATH) -> AuthUser | None:
    """校验用户名密码并返回用户。"""

    normalized_username = _normalize_username(username)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT user_id, username, password_hash, display_name, is_guest, created_at
            FROM users
            WHERE username = ?
            """,
            (normalized_username,),
        ).fetchone()
    if row is None:
        return None
    if not verify_password(password, str(row[2]) if row[2] is not None else None):
        return None
    return _user_from_row(row)


def get_user_by_id(user_id: str, *, db_path: Path = DB_PATH) -> AuthUser | None:
    """按 user_id 读取用户。"""

    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT user_id, username, password_hash, display_name, is_guest, created_at
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    return _user_from_row(row) if row else None


def get_user_by_username(username: str, *, db_path: Path = DB_PATH) -> AuthUser | None:
    """按 username 读取用户。"""

    normalized_username = _normalize_username(username)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT user_id, username, password_hash, display_name, is_guest, created_at
            FROM users
            WHERE username = ?
            """,
            (normalized_username,),
        ).fetchone()
    return _user_from_row(row) if row else None


def _insert_user(
    *,
    user_id: str,
    username: str,
    password_hash: str | None,
    display_name: str,
    is_guest: bool,
    db_path: Path,
) -> AuthUser:
    init_db(db_path)
    now = datetime.now(UTC).isoformat()
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                """
                INSERT INTO users(user_id, username, password_hash, display_name, is_guest, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, username, password_hash, display_name, 1 if is_guest else 0, now),
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise DuplicateUsernameError(username) from exc
    user = get_user_by_id(user_id, db_path=db_path)
    if user is None:
        raise RuntimeError("用户创建失败")
    return user


def _existing_user_id(username: str, db_path: Path) -> str | None:
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute("SELECT user_id FROM users WHERE username = ?", (username,)).fetchone()
    return str(row[0]) if row else None


def _normalize_username(username: str) -> str:
    return username.strip().lower()


def _user_from_row(row: tuple[object, ...]) -> AuthUser:
    return AuthUser(
        user_id=str(row[0]),
        username=str(row[1]),
        display_name=str(row[3] or row[1]),
        is_guest=bool(row[4]),
        created_at=str(row[5]),
    )
