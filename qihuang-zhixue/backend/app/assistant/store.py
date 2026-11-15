"""会话与轮次的持久化。

四张表，与 ``capabilities/invocation.py`` 同一套写法：``CREATE TABLE IF NOT EXISTS``
在每次读写前跑一遍，不引入迁移框架。理由：这个工程只有一个 sqlite 文件，迁移
框架的收益抵不过「测试要自己造一遍 schema」的成本。

**为什么产物单独一张表**：右栏「本次产出」按会话聚合，且要在任务跑完后回填
``resource_id``。塞进消息 JSON 就得整行读出—改—写回，两个人同时收卷时必丢一次。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.profile.store import DB_PATH, connect

from .contract import ArtifactState, RunStatus

_DDL = """
CREATE TABLE IF NOT EXISTS assistant_conversation (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assistant_conversation_user
    ON assistant_conversation(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS assistant_message (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    run_id          TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL,
    content_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assistant_message_conv
    ON assistant_message(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS assistant_run (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_message_id TEXT NOT NULL DEFAULT '',
    turn_key        TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL,
    steps_json      TEXT NOT NULL DEFAULT '[]',
    usage_json      TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_assistant_run_conv
    ON assistant_run(conversation_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_run_turn
    ON assistant_run(conversation_id, turn_key) WHERE turn_key <> '';

CREATE TABLE IF NOT EXISTS assistant_artifact (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    call_id         TEXT NOT NULL,
    capability      TEXT NOT NULL,
    task_id         TEXT NOT NULL DEFAULT '',
    resource_id     TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL,
    open_url        TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL DEFAULT 'accepted',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assistant_artifact_conv
    ON assistant_artifact(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_assistant_artifact_task
    ON assistant_artifact(task_id);
"""


def init_db(db_path: Path = DB_PATH) -> None:
    with closing(_connect(db_path)) as conn:
        conn.executescript(_DDL)
        _migrate(conn)
        conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """补列。``CREATE TABLE IF NOT EXISTS`` 不会给老库加字段。

    会话摘要用 ALTER 补：老库里的会话摘要为空，下次进对话时按需生成，
    不需要重跑迁移脚本，也不需要用户清会话。
    """

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(assistant_conversation)")}
    if "summary" not in columns:
        conn.execute("ALTER TABLE assistant_conversation ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
    if "summary_upto" not in columns:
        # 摘要覆盖到哪条消息为止：据此判断「还差几条才值得再压一次」。
        conn.execute("ALTER TABLE assistant_conversation ADD COLUMN summary_upto TEXT NOT NULL DEFAULT ''")


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """连库并开 ``row_factory``。

    不直接用 ``profile.store.connect``：那边不给行工厂，返回的是元组。元组下标
    写错时不会报错（``row[0]`` 恰好是 id），只会在某天改了列顺序时静默错位。
    """

    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:20]}"


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #


def _conversation_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "conversation_id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_conversation(user_id: str, title: str = "", db_path: Path = DB_PATH) -> dict[str, Any]:
    init_db(db_path)
    ident = new_id("c")
    now = _now()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO assistant_conversation (id, user_id, title, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (ident, user_id, title.strip()[:60], "active", now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM assistant_conversation WHERE id=?", (ident,)).fetchone()
    return _conversation_row(row)


def list_conversations(user_id: str, *, limit: int = 30, offset: int = 0, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM assistant_conversation WHERE user_id=? AND status='active' ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()
    return [_conversation_row(row) for row in rows]


def get_conversation(conversation_id: str, user_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM assistant_conversation WHERE id=? AND user_id=?",
            (conversation_id, user_id),
        ).fetchone()
    return _conversation_row(row) if row else None


def get_summary(conversation_id: str, db_path: Path = DB_PATH) -> dict[str, str]:
    """取会话摘要与它覆盖到的消息 id。没有就返回空串，不返回 None。"""

    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT summary, summary_upto FROM assistant_conversation WHERE id=?",
            (conversation_id,),
        ).fetchone()
    if row is None:
        return {"summary": "", "summary_upto": ""}
    return {"summary": str(row["summary"] or ""), "summary_upto": str(row["summary_upto"] or "")}


def update_summary(conversation_id: str, summary: str, upto_message_id: str, db_path: Path = DB_PATH) -> None:
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "UPDATE assistant_conversation SET summary=?, summary_upto=? WHERE id=?",
            (summary[:2000], upto_message_id, conversation_id),
        )
        conn.commit()


def update_conversation(
    conversation_id: str,
    user_id: str,
    *,
    title: str | None = None,
    archived: bool | None = None,
    db_path: Path = DB_PATH,
) -> dict[str, Any] | None:
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        if title is not None:
            conn.execute(
                "UPDATE assistant_conversation SET title=?, updated_at=? WHERE id=? AND user_id=?",
                (title.strip()[:60], _now(), conversation_id, user_id),
            )
        if archived is not None:
            conn.execute(
                "UPDATE assistant_conversation SET status=?, updated_at=? WHERE id=? AND user_id=?",
                ("archived" if archived else "active", _now(), conversation_id, user_id),
            )
        conn.commit()
    return get_conversation(conversation_id, user_id, db_path)


def touch_conversation(conversation_id: str, db_path: Path = DB_PATH) -> None:
    """把会话顶到列表最前。失败时静默——排序不是关键路径，不该让一次提问失败。"""

    try:
        init_db(db_path)
        with closing(_connect(db_path)) as conn:
            conn.execute("UPDATE assistant_conversation SET updated_at=? WHERE id=?", (_now(), conversation_id))
            conn.commit()
    except sqlite3.Error:
        return


def auto_title(text: str) -> str:
    """首条消息的前 18 个字。不调模型起标题：省一次调用，且 18 字够分辨了。"""

    clean = " ".join(str(text or "").split())
    return clean[:18] or "新对话"


# --------------------------------------------------------------------------- #
# 消息
# --------------------------------------------------------------------------- #


def append_message(
    conversation_id: str,
    role: str,
    content: dict[str, Any],
    *,
    run_id: str = "",
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    init_db(db_path)
    ident = new_id("m")
    now = _now()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO assistant_message (id, conversation_id, run_id, role, content_json, created_at) VALUES (?,?,?,?,?,?)",
            (ident, conversation_id, run_id, role, json.dumps(content, ensure_ascii=False), now),
        )
        conn.commit()
    return {
        "message_id": ident,
        "conversation_id": conversation_id,
        "run_id": run_id,
        "role": role,
        "content": content,
        "created_at": now,
    }


def list_messages(conversation_id: str, user_id: str, *, limit: int = 200, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """按时间正序。先校验会话归属，避免拿着别人的会话号读出消息。"""

    if get_conversation(conversation_id, user_id, db_path) is None:
        return []
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM assistant_message WHERE conversation_id=? ORDER BY created_at ASC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
    return [
        {
            "message_id": row["id"],
            "run_id": row["run_id"],
            "role": row["role"],
            "content": json.loads(row["content_json"] or "{}"),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# 轮次
# --------------------------------------------------------------------------- #


def _run_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_id": row["id"],
        "conversation_id": row["conversation_id"],
        "user_message_id": row["user_message_id"],
        "turn_key": row["turn_key"],
        "status": row["status"],
        "steps": json.loads(row["steps_json"] or "[]"),
        "usage": json.loads(row["usage_json"] or "{}"),
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
    }


def create_run(
    conversation_id: str,
    user_message_id: str,
    *,
    turn_key: str = "",
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    init_db(db_path)
    ident = new_id("r")
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO assistant_run (id, conversation_id, user_message_id, turn_key, status, created_at) VALUES (?,?,?,?,?,?)",
            (ident, conversation_id, user_message_id, turn_key, "running", _now()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM assistant_run WHERE id=?", (ident,)).fetchone()
    return _run_row(row)


def find_run_by_turn(conversation_id: str, turn_key: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    if not turn_key:
        return None
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM assistant_run WHERE conversation_id=? AND turn_key=?",
            (conversation_id, turn_key),
        ).fetchone()
    return _run_row(row) if row else None


def update_run(
    run_id: str,
    *,
    status: RunStatus | None = None,
    steps: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    finished: bool = False,
    db_path: Path = DB_PATH,
) -> None:
    """只写非空字段。``finished`` 才落 ``finished_at``——状态频繁变，完成时刻只记一次。"""

    init_db(db_path)
    sets: list[str] = []
    values: list[Any] = []
    if status is not None:
        sets.append("status=?")
        values.append(status)
    if steps is not None:
        sets.append("steps_json=?")
        values.append(json.dumps(steps, ensure_ascii=False))
    if usage is not None:
        sets.append("usage_json=?")
        values.append(json.dumps(usage, ensure_ascii=False))
    if finished:
        sets.append("finished_at=?")
        values.append(_now())
    if not sets:
        return
    values.append(run_id)
    with closing(_connect(db_path)) as conn:
        conn.execute(f"UPDATE assistant_run SET {', '.join(sets)} WHERE id=?", values)
        conn.commit()


def get_run(run_id: str, user_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT r.* FROM assistant_run r JOIN assistant_conversation c ON c.id = r.conversation_id "
            "WHERE r.id=? AND c.user_id=?",
            (run_id, user_id),
        ).fetchone()
    return _run_row(row) if row else None


# --------------------------------------------------------------------------- #
# 产物
# --------------------------------------------------------------------------- #


def add_artifact(
    *,
    run_id: str,
    conversation_id: str,
    call_id: str,
    capability: str,
    task_id: str,
    kind: str,
    open_url: str = "",
    state: ArtifactState = "accepted",
    resource_id: str = "",
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """记一张产物卡。``task_id`` 为空表示快照型产物（当次就绪，无需轮询）。"""
    init_db(db_path)
    ident = new_id("a")
    now = _now()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO assistant_artifact (id, run_id, conversation_id, call_id, capability, task_id, resource_id, kind, open_url, state, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ident, run_id, conversation_id, call_id, capability, task_id, resource_id, kind, open_url, state, now, now),
        )
        conn.commit()
    return {"artifact_id": ident, "state": state, "kind": kind, "capability": capability, "task_id": task_id}


def update_artifact_by_task(task_id: str, *, state: ArtifactState, resource_id: str = "", open_url: str = "", db_path: Path = DB_PATH) -> None:
    if not task_id:
        return
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "UPDATE assistant_artifact SET state=?, resource_id=COALESCE(NULLIF(?,''), resource_id), "
            "open_url=COALESCE(NULLIF(?,''), open_url), updated_at=? WHERE task_id=?",
            (state, resource_id, open_url, _now(), task_id),
        )
        conn.commit()


def list_artifacts(conversation_id: str, user_id: str, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    if get_conversation(conversation_id, user_id, db_path) is None:
        return []
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM assistant_artifact WHERE conversation_id=? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
    return [
        {
            "artifact_id": row["id"],
            "run_id": row["run_id"],
            "call_id": row["call_id"],
            "capability": row["capability"],
            "task_id": row["task_id"],
            "resource_id": row["resource_id"],
            "kind": row["kind"],
            "open_url": row["open_url"],
            "state": row["state"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def pending_plan_artifacts(conversation_id: str, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """刷新恢复用：还在等确认的产物。"""

    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM assistant_artifact WHERE conversation_id=? AND state='awaiting_plan' ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_artifact_by_task(task_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    """按任务号找回产物行。确认续跑时用它把事件挂回原来的会话。"""

    if not task_id:
        return None
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM assistant_artifact WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    return dict(row) if row else None
