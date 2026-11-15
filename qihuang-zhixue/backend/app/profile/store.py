"""SQLite persistence for the learning profile data foundation."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from app.profile.schema import (
    EvidenceCard,
    Profile,
    create_blank_profile,
    evidence_card_from_dict,
    evidence_card_to_json_dict,
)
from app.profile.evidence import EvidenceTier
from app.config import resolve_database_path

DB_PATH = resolve_database_path()
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
USER_TRUST_COLUMNS: dict[str, str] = {
    "user_id": "TEXT",
    "trust": "REAL NOT NULL DEFAULT 0.7",
    "updated_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
}
PROFILE_CHANGE_LOG_COLUMNS: dict[str, str] = {
    "id": "INTEGER",
    "ts": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
    "user_id": "TEXT NOT NULL DEFAULT ''",
    "dim": "TEXT NOT NULL DEFAULT ''",
    "old_value": "TEXT NOT NULL DEFAULT 'null'",
    "new_value": "TEXT NOT NULL DEFAULT 'null'",
    "reason": "TEXT NOT NULL DEFAULT ''",
    "source_event": "TEXT NOT NULL DEFAULT ''",
    "evidence_ids": "TEXT NOT NULL DEFAULT '[]'",
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

_DIALOGUE_TURN_LEASE_SECONDS = 600


def claim_dialogue_turn(
    user_id: str,
    session_id: str,
    client_turn_id: str,
    db_path: Path = DB_PATH,
) -> str:
    """Atomically claim a dialogue turn across API workers.

    Returns ``claimed`` for the worker that owns execution, ``completed`` for
    an idempotent replay, and ``processing`` while another worker owns it.
    """

    init_db(db_path)
    now = datetime.now(UTC)
    now_text = now.isoformat()
    lease_text = (now.timestamp() + _DIALOGUE_TURN_LEASE_SECONDS)
    lease_iso = datetime.fromtimestamp(lease_text, UTC).isoformat()
    with closing(connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, lease_expires_at FROM dialogue_turn_requests WHERE user_id = ? AND session_id = ? AND client_turn_id = ?",
            (user_id, session_id, client_turn_id),
        ).fetchone()
        if row is not None and str(row[0]) == "completed":
            conn.commit()
            return "completed"
        if row is not None and str(row[0]) == "processing":
            try:
                active = datetime.fromisoformat(str(row[1])) > now
            except (TypeError, ValueError):
                active = False
            if active:
                conn.commit()
                return "processing"
            conn.execute(
                "UPDATE dialogue_turn_requests SET lease_expires_at = ?, updated_at = ?, status = 'processing' WHERE user_id = ? AND session_id = ? AND client_turn_id = ?",
                (lease_iso, now_text, user_id, session_id, client_turn_id),
            )
            conn.commit()
            return "claimed"
        try:
            conn.execute(
                "INSERT INTO dialogue_turn_requests(user_id, session_id, client_turn_id, status, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, 'processing', ?, ?, ?)",
                (user_id, session_id, client_turn_id, lease_iso, now_text, now_text),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return "processing"
        conn.commit()
    return "claimed"


def complete_dialogue_turn(
    user_id: str,
    session_id: str,
    client_turn_id: str,
    db_path: Path = DB_PATH,
) -> None:
    init_db(db_path)
    now = datetime.now(UTC).isoformat()
    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE dialogue_turn_requests SET status = 'completed', updated_at = ?, completed_at = ? WHERE user_id = ? AND session_id = ? AND client_turn_id = ?",
            (now, now, user_id, session_id, client_turn_id),
        )
        conn.commit()


def release_dialogue_turn(
    user_id: str,
    session_id: str,
    client_turn_id: str,
    db_path: Path = DB_PATH,
) -> None:
    """Release a failed claim so a retry can safely execute it again."""

    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute(
            "DELETE FROM dialogue_turn_requests WHERE user_id = ? AND session_id = ? AND client_turn_id = ? AND status = 'processing'",
            (user_id, session_id, client_turn_id),
        )
        conn.commit()


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection configured for concurrent profile writes.

    这里**不碰 journal_mode**。`PRAGMA journal_mode=WAL` 是切换操作，要独占锁；
    WAL 是写在库头的持久属性，由 `init_db` 设一次就够。每次建连都执行它的后果是
    并发线程互相抢锁，抢不到的一方在 Windows 上把连接降级成只读，后续写入报
    `attempt to write a readonly database`（不是 `database is locked`，所以调
    busy_timeout 没用）。

    实测（12 线程 × 4 次写）：原实现 8 轮 3 轮失败；加进程锁后降到 ~6%；
    彻底不在 connect 里切换之后 0 失败。
    """

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


#: `init_db` 的进程级串行锁与已初始化标记。
#:
#: `init_db` 被每个写操作调用（本模块 10 处），每次都跑一遍建表 + 列迁移 + 切 WAL。
#: 12 个线程同时进来时这些 DDL 会互相抢锁；串行化之后只真正执行一次。
_INIT_LOCK = threading.Lock()
_INITIALIZED: set[str] = set()

#: 可以靠重试恢复的 SQLite 错误。
#:
#: 并发打开同一个 WAL 库时，SQLite 内部要重建 `-shm` 索引，竞争失败的一方会把
#: 连接降级成只读。这不是 schema 问题也不是数据问题，重试就能过。
_RETRYABLE_DB_ERRORS = ("readonly database", "database is locked", "database table is locked")
_WRITE_RETRY_ATTEMPTS = 5
_WRITE_RETRY_BASE_DELAY = 0.02

T = TypeVar("T")


def _write_with_retry(db_path: Path, action: Callable[[sqlite3.Connection], T]) -> T:
    """在独立连接上执行一次写事务，遇到瞬时只读 / 锁冲突就重试。

    实测（纯 sqlite3，12 线程 × 4 次写，48 次写）：不重试时每轮有 2–7 次报
    `attempt to write a readonly database`；重试 5 次后连续 3 轮都是 48/48。
    把这种瞬时文件竞争直接抛给用户是错的——用户会看到「保存失败」，但数据本身
    没有任何问题。
    """

    last_error: sqlite3.OperationalError | None = None
    for attempt in range(_WRITE_RETRY_ATTEMPTS):
        try:
            with closing(connect(db_path)) as conn:
                result = action(conn)
                conn.commit()
            return result
        except sqlite3.OperationalError as exc:
            if not any(token in str(exc).lower() for token in _RETRYABLE_DB_ERRORS):
                raise
            last_error = exc
            logger.debug("写操作遇到瞬时错误，第 %d 次重试：%s", attempt + 1, exc)
            time.sleep(_WRITE_RETRY_BASE_DELAY * (attempt + 1))
    assert last_error is not None
    raise last_error


def init_db(db_path: Path = DB_PATH) -> None:
    """Create and lightly migrate profile-owned SQLite tables."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    cache_key = str(Path(db_path).resolve())
    with _INIT_LOCK:
        if cache_key in _INITIALIZED:
            return
        with closing(connect(db_path)) as conn:
            # WAL 只在这里切一次。`connect()` 刻意不做这件事——切换要独占锁，
            # 并发建连时会互相抢，抢输的一方把连接降级成只读。
            conn.execute("PRAGMA journal_mode=WAL;")
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dialogue_turn_requests (
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    client_turn_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY(user_id, session_id, client_turn_id)
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_dialogue_turn_requests_one_active
                ON dialogue_turn_requests(user_id, session_id)
                WHERE status = 'processing'
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_trust (
                    user_id TEXT PRIMARY KEY,
                    trust REAL NOT NULL DEFAULT 0.7,
                    updated_at TEXT NOT NULL
                )
                """
            )
            _migrate_table_columns(conn, "user_trust", USER_TRUST_COLUMNS)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_change_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    dim TEXT NOT NULL,
                    old_value TEXT NOT NULL,
                    new_value TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    source_event TEXT NOT NULL,
                    evidence_ids TEXT NOT NULL
                )
                """
            )
            _migrate_table_columns(conn, "profile_change_log", PROFILE_CHANGE_LOG_COLUMNS)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_profile_change_log_user_id_id
                ON profile_change_log(user_id, id DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS calibration_tasks (
                    claim_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    questions_json TEXT NOT NULL DEFAULT '[]',
                    answers_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_calibration_tasks_user_status
                ON calibration_tasks(user_id, status, created_at)
                """
            )
            conn.commit()
        _INITIALIZED.add(cache_key)


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
        backup_name = f"profile_sessions_backup_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        logger.warning(
            "profile_sessions schema is missing primary key column session_id; preserving it as %s",
            backup_name,
        )
        before_count = int(conn.execute("SELECT COUNT(*) FROM profile_sessions").fetchone()[0])
        conn.execute(f'ALTER TABLE profile_sessions RENAME TO "{backup_name}"')
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
        after_count = int(conn.execute("SELECT COUNT(*) FROM profile_sessions").fetchone()[0])
        if after_count != 0:
            raise RuntimeError("profile_sessions migration created unexpected rows")
        logger.warning(
            "profile_sessions rows=%s retained in backup table %s; rows cannot be safely mapped without session_id",
            before_count,
            backup_name,
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
        "user_trust": USER_TRUST_COLUMNS,
        "profile_change_log": PROFILE_CHANGE_LOG_COLUMNS,
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
    if (
        card.tier == EvidenceTier.SELF_REPORT
        and card.dimension in {"knowledge_base", "weak_points", "pace"}
        and card.claim_status is None
    ):
        card.claim_status = "pending"
    payload = evidence_card_to_json_dict(card)

    def _insert(conn: sqlite3.Connection) -> None:
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

    # 证据卡是最高频的写（每次答题、每轮对话都写），并发下最容易撞上瞬时只读。
    _write_with_retry(db_path, _insert)
    return card


def update_evidence_claim_status(
    user_id: str,
    evidence_id: str,
    status: str,
    db_path: Path = DB_PATH,
) -> EvidenceCard:
    """Update one claim in both its structured JSON and denormalized row."""

    if status not in {"pending", "verified", "refuted"}:
        raise ValueError("invalid claim status")
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id, evidence_json FROM evidence WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()
        for row_id, raw in rows:
            payload = json.loads(str(raw))
            if str(payload.get("evidence_id") or "") != evidence_id:
                continue
            payload["claim_status"] = status
            conn.execute(
                "UPDATE evidence SET evidence_json = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), row_id),
            )
            conn.commit()
            return evidence_card_from_dict(payload)
    raise KeyError(evidence_id)


def get_trust(user_id: str, db_path: Path = DB_PATH) -> float:
    """Return the bounded self-report trust coefficient, creating its default."""

    init_db(db_path)
    now = datetime.now(UTC).isoformat()
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_trust(user_id, trust, updated_at) VALUES (?, 0.7, ?)",
            (user_id, now),
        )
        row = conn.execute("SELECT trust FROM user_trust WHERE user_id = ?", (user_id,)).fetchone()
        conn.commit()
    return max(0.3, min(1.0, float(row[0] if row else 0.7)))


def adjust_trust(user_id: str, delta: float, db_path: Path = DB_PATH) -> float:
    """Atomically adjust trust within the supported [0.3, 1.0] interval."""

    current = get_trust(user_id, db_path)
    updated = max(0.3, min(1.0, current + delta))
    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE user_trust SET trust = ?, updated_at = ? WHERE user_id = ?",
            (updated, datetime.now(UTC).isoformat(), user_id),
        )
        conn.commit()
    return round(updated, 4)


def record_profile_change(
    user_id: str,
    dim: str,
    old_value: Any,
    new_value: Any,
    *,
    reason: str,
    source_event: str,
    evidence_ids: list[str],
    db_path: Path = DB_PATH,
    ts: datetime | None = None,
) -> None:
    """Record one material dimension transition through the shared audit sink."""

    if old_value == new_value:
        return
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO profile_change_log(
                ts, user_id, dim, old_value, new_value, reason, source_event, evidence_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (ts or datetime.now(UTC)).isoformat(),
                user_id,
                dim,
                json.dumps(old_value, ensure_ascii=False, default=str),
                json.dumps(new_value, ensure_ascii=False, default=str),
                reason,
                source_event,
                json.dumps(evidence_ids, ensure_ascii=False),
            ),
        )
        conn.commit()


def list_profile_changes(
    user_id: str,
    *,
    limit: int = 20,
    offset: int = 0,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Return newest profile changes with stable limit/offset pagination."""

    init_db(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT ts, dim, old_value, new_value, reason, source_event, evidence_ids
            FROM profile_change_log WHERE user_id = ?
            ORDER BY id DESC LIMIT ? OFFSET ?
            """,
            (user_id, max(0, limit), max(0, offset)),
        ).fetchall()
    return [
        {
            "ts": row[0],
            "dim": row[1],
            "old_value": json.loads(row[2]),
            "new_value": json.loads(row[3]),
            "reason": row[4],
            "source_event": row[5],
            "evidence_ids": json.loads(row[6]),
        }
        for row in rows
    ]


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


def list_profile_sessions(
    user_id: str,
    *,
    mode: str | None = None,
    limit: int = 20,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """List a user's collection sessions, newest activity first.

    画像页与入口都需要"我上次聊到哪儿了"。会话 id 只存在前端时，换个浏览器、
    清一次站点数据就再也找不回来——所以列表必须由后端按 user_id 给出，前端只
    负责展示与跳转。
    """

    init_db(db_path)
    clauses = ["user_id = ?"]
    params: list[Any] = [user_id]
    if mode:
        clauses.append("mode = ?")
        params.append(mode)
    params.append(max(1, min(100, limit)))
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            f"""
            SELECT
                session_id,
                mode,
                finished,
                step_index,
                created_at,
                updated_at,
                evidence_cards_json,
                (
                    SELECT COUNT(*) FROM chat_sessions c
                    WHERE c.user_id = profile_sessions.user_id
                      AND c.session_id = profile_sessions.session_id
                ),
                (
                    SELECT c.content FROM chat_sessions c
                    WHERE c.user_id = profile_sessions.user_id
                      AND c.session_id = profile_sessions.session_id
                      AND c.role = 'user'
                    ORDER BY c.id DESC LIMIT 1
                )
            FROM profile_sessions
            WHERE {" AND ".join(clauses)}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            evidence_count = len(json.loads(str(row[6] or "[]")))
        except (TypeError, ValueError):
            evidence_count = 0
        items.append(
            {
                "session_id": row[0],
                "mode": row[1],
                "finished": bool(row[2]),
                "turn_index": int(row[3] or 0),
                "created_at": row[4],
                "updated_at": row[5],
                "evidence_count": evidence_count,
                "message_count": int(row[7] or 0),
                "last_user_message": str(row[8] or ""),
            }
        )
    return items


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
