"""幂等与受理记录。

对话场景里一定会重试：超时、断流、用户点两下、Agent 自己重放。没有这张表，
重试的代价是「生成两张图、占两条历史」，用户只看到莫名其妙的重复。

三个决定：

**请求摘要而不是整包比对**
    只存 ``sha256(capability|query|params 的规范化 json)``。同一个请求号配不同
    参数，是调用方的 bug（多半是编号复用），必须报错而不是悄悄接受——所以
    ``idempotency_mismatch`` 是 409，不是重试。

**先落记录再建任务**
    并发两个相同请求时，只有先插进来的那个能建任务。落地顺序反过来就会出现
    两条记录指向两个任务、只能靠事后去重。代价是极小概率留下一条 ``pending``
    且没有 task_id 的记录——调用方按 ``status`` 轮询即可，不会拿到错误答案。

**状态只进不退**
    ``pending → accepted → done/failed/cancelled``。重复提交不会把已有的
    ``done`` 改回 ``accepted``，否则「重放返回同一 task」这句话就不成立了。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.profile.store import DB_PATH

from .errors import CapabilityError, ErrorCode

_DDL = """
CREATE TABLE IF NOT EXISTS capability_invocation (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    digest TEXT NOT NULL,
    status TEXT NOT NULL,
    task_id TEXT,
    resource_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capability_invocation_user
    ON capability_invocation(user_id, created_at DESC);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    with closing(connect(db_path)) as conn:
        conn.executescript(_DDL)
        conn.commit()


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def digest_of(capability: str, query: str = "", params: dict[str, Any] | None = None) -> str:
    """规范化摘要。键排序、去空白，避免「参数顺序不同」被当成不同请求。"""

    payload = {"capability": capability, "query": " ".join(str(query or "").split()), "params": params or {}}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def new_id() -> str:
    return f"inv_{uuid4().hex[:20]}"


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "invocation_id": row["id"],
        "capability": row["capability"],
        "status": row["status"],
        "task_id": row["task_id"],
        "resource_id": row["resource_id"],
        "created_at": row["created_at"],
        "payload": json.loads(row["payload_json"] or "{}"),
    }


def get(invocation_id: str, user_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM capability_invocation WHERE id=? AND user_id=?",
            (invocation_id, user_id),
        ).fetchone()
    return _row(row) if row else None


def claim(
    *,
    capability: str,
    user_id: str,
    digest: str,
    invocation_id: str = "",
    payload: dict[str, Any] | None = None,
    db_path: Path = DB_PATH,
) -> tuple[dict[str, Any], bool]:
    """登记一次调用。返回 ``(记录, 是否重放)``。

    没给 ``invocation_id`` 就是一次全新调用（生成一个）；给了说明调用方自己
    在管幂等键，重放时返回同一份记录。
    """

    init_db(db_path)
    ident = invocation_id or new_id()
    now = _now()
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO capability_invocation (id, user_id, capability, digest, status, payload_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                ident,
                user_id,
                capability,
                digest,
                "pending",
                json.dumps(payload or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM capability_invocation WHERE id=?", (ident,)).fetchone()
        conn.commit()

    if row is None:  # pragma: no cover - INSERT 后必定能读到
        raise CapabilityError(ErrorCode.CONFLICT, "受理记录写入失败", remedy="稍后重试")
    if row["user_id"] != user_id or row["capability"] != capability:
        # 编号被另一个用户或另一个能力占用：不泄露原记录内容，直接拒绝。
        raise CapabilityError(ErrorCode.FORBIDDEN, "这个请求号已被占用", remedy="换一个 client_invocation_id")
    if row["digest"] != digest:
        raise CapabilityError(
            ErrorCode.IDEMPOTENCY_MISMATCH,
            "同一个 client_invocation_id 被用于不同的请求",
            remedy="换一个编号，或保持参数一致",
        )

    record = _row(row)
    replayed = row["status"] != "pending" or bool(row["task_id"])
    return record, replayed


def attach(invocation_id: str, *, task_id: str = "", status: str = "accepted", db_path: Path = DB_PATH) -> None:
    """接上任务号。只往前写，不回退已有状态。"""

    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE capability_invocation SET task_id=COALESCE(NULLIF(?,''), task_id), status=?, updated_at=? WHERE id=?",
            (task_id, status, _now(), invocation_id),
        )
        conn.commit()


def finish(invocation_id: str, *, status: str, resource_id: str = "", db_path: Path = DB_PATH) -> None:
    """终态。已经到终态的记录不再改写——重复提交不该把 done 打回 accepted。"""

    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE capability_invocation SET status=?, resource_id=COALESCE(NULLIF(?,''), resource_id), updated_at=? WHERE id=? AND status NOT IN ('done','failed','cancelled')",
            (status, resource_id, _now(), invocation_id),
        )
        conn.commit()


def reconcile(invocation_id: str, task: dict[str, Any] | None, user_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    """用任务的真实状态纠正受理记录。

    任务已经跑完，而受理记录还挂着 ``accepted``，调用方就会一直轮询。
    这一步把两边对齐：done → done，failed → failed，其余保持 accepted。
    """

    if task is None:
        return get(invocation_id, user_id, db_path)
    status = {"done": "done", "failed": "failed"}.get(str(task.get("status")), "accepted")
    finish(invocation_id, status=status, resource_id=str(task.get("resource_id") or ""), db_path=db_path)
    return get(invocation_id, user_id, db_path)
