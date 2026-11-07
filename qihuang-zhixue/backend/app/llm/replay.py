"""LLM 录制回放（VCR 模式）。

解决的问题：e2e / page-check 脚本必须走真实模型，才能验证 JSON 结构、评分
分布和边界情况，但每跑一次都烧钱，而且每轮 26-48 秒。做法是首次真调时把
``(请求指纹 → 真实响应)`` 落库，之后同一请求直接回放真实响应：

    首次 live  → 真调模型，同时录制
    之后 replay → 命中即返回原文，毫秒级、零成本、结果与真实模型完全一致

指纹只覆盖影响输出的参数（模型、消息、温度、max_tokens、思考模式等），
超时时间之类的运行参数不参与，避免无谓的漏命中。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from app.config import resolve_database_path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_replay (
    fingerprint TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    request_json TEXT,
    response_text TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL
);
"""

# 参与指纹计算的参数，必须与实际发往模型的字段保持一致。
FINGERPRINT_KEYS = (
    "temperature",
    "max_tokens",
    "top_p",
    "thinking",
    "reasoning_effort",
    "response_format",
    "prefer_reasoning_content",
)


def fingerprint(
    *,
    task_type: str,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
) -> str:
    material = {
        "task_type": task_type,
        "model": model,
        "messages": [dict(message) for message in messages],
        "params": {key: params[key] for key in FINGERPRINT_KEYS if params.get(key) is not None},
    }
    blob = json.dumps(material, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load(fingerprint_value: str, db_path: Path | None = None) -> str | None:
    try:
        with _db(db_path) as conn:
            row = conn.execute(
                "SELECT response_text FROM llm_replay WHERE fingerprint = ?",
                (fingerprint_value,),
            ).fetchone()
    except sqlite3.Error as exc:
        logger.warning("llm_replay 读取失败：%s", exc)
        return None
    return str(row[0]) if row else None


def save(
    *,
    fingerprint_value: str,
    task_type: str,
    provider: str,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    response_text: str,
    usage: Mapping[str, Any] | None = None,
    db_path: Path | None = None,
) -> None:
    if not response_text:
        return
    request_json = json.dumps(
        {"messages": [dict(message) for message in messages], "params": dict(params)},
        ensure_ascii=False,
        default=str,
    )
    try:
        with _db(db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_replay "
                "(fingerprint, task_type, provider, model, request_json, response_text, "
                " prompt_tokens, completion_tokens, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fingerprint_value,
                    task_type,
                    provider,
                    model,
                    request_json,
                    response_text,
                    _as_int((usage or {}).get("prompt_tokens")),
                    _as_int((usage or {}).get("completion_tokens")),
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
    except sqlite3.Error as exc:  # 录制失败不影响本次真实调用
        logger.warning("llm_replay 写入失败：%s", exc)


def stats(db_path: Path | None = None) -> dict[str, int]:
    """给 e2e 报告用：已录制多少条、按 task_type 分布。"""

    try:
        with _db(db_path) as conn:
            rows = conn.execute(
                "SELECT task_type, COUNT(*) FROM llm_replay GROUP BY task_type ORDER BY 2 DESC"
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(task): int(count) for task, count in rows}


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or resolve_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.executescript(_SCHEMA)
    return conn


@contextmanager
def _db(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """``with sqlite3.connect()`` 只提交事务、不关闭连接，会泄漏文件句柄。"""

    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
