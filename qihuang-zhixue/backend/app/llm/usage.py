"""LLM token 用量记录。

没有计量就无法回答"钱花在哪"，也无法判断哪个模型还剩多少免费额度。
这里只做两件事：建表、写一条。汇总交给 ``scripts/llm_cost_report.py``。

写库失败绝不允许影响主流程 —— 计量是旁路，不是依赖。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.config import resolve_database_path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    mode TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_task ON llm_usage(task_type);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or resolve_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.executescript(_SCHEMA)
    return conn


@contextmanager
def _db(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """``with sqlite3.connect()`` 只提交事务、不关闭连接，会泄漏文件句柄
    （Windows 上表现为临时目录无法清理）。这里显式关闭。"""

    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_usage(
    *,
    task_type: str,
    provider: str,
    model: str,
    usage: Mapping[str, Any] | None,
    mode: str = "live",
    db_path: Path | None = None,
) -> None:
    """把一次调用的 token 用量落库。usage 缺失时静默跳过。"""

    if not usage:
        return
    prompt_tokens = _as_int(usage.get("prompt_tokens"))
    completion_tokens = _as_int(usage.get("completion_tokens"))
    if not prompt_tokens and not completion_tokens:
        return
    try:
        with _db(db_path) as conn:
            conn.execute(
                "INSERT INTO llm_usage "
                "(task_type, provider, model, prompt_tokens, completion_tokens, mode, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    task_type,
                    provider,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    mode,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
    except sqlite3.Error as exc:  # 计量失败不能打断业务调用
        logger.warning("llm_usage 写入失败：%s", exc)


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
