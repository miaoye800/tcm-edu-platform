"""Versioned persistence for immutable path-plan snapshots.

**只追加，不 UPDATE**（计划 §7.1）。两段式确认不是「把草稿改成正式」，而是
「确认时再追加一条正式行，``parent_version`` 指向它基于哪一版草稿」：

- ``status='draft'`` 的行只有向导看得到，``current_path`` 一律过滤掉，
  所以用户点「取消」之后地图上不会留幽灵点位；
- ``status='active'`` 的行才是路线，历史行一个都不变，``reorder`` /
  ``complete_node`` / ``_next_node_id`` 全都不用改。

加列走 ``ALTER TABLE ADD COLUMN`` 而不是重建表：``plan_kind`` 上有 CHECK
约束，SQLite 改 CHECK 要 12 步重建，为了三个字段不值得。``DEFAULT 'active'``
让所有历史行自动落在正确状态，不需要迁移脚本。
"""

from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from app.path.store import DB_PATH
from app.profile.store import connect

#: 草稿的两个阶段。骨架先谈「学哪些章」，谈定再展开逐点明细——
#: 一次就把 19 个方向 × 几十个点摆出来，用户根本没法判断砍得对不对。
DRAFT_STAGES = ("skeleton", "detail")


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
        _ensure_columns(conn)
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_user_path_plan_user_version
               ON user_path_plan(user_id, version DESC)"""
        )
        conn.commit()


def _ensure_columns(conn: Any) -> None:
    """老库补列。``ALTER TABLE ADD COLUMN`` 不能在表已最新时重复执行，所以先查。"""

    existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(user_path_plan)").fetchall()}
    additions = {
        "status": "TEXT NOT NULL DEFAULT 'active'",
        "stage": "TEXT NOT NULL DEFAULT ''",
        "parent_version": "INTEGER",
        "confirmed_at": "TEXT",
    }
    for name, ddl in additions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE user_path_plan ADD COLUMN {name} {ddl}")


def save_plan_version(
    *,
    user_id: str,
    plan_kind: str,
    payload: dict[str, Any],
    fallback: bool = False,
    domain_id: str | None = None,
    db_path: Path = DB_PATH,
    status: str = "active",
    stage: str = "",
    parent_version: int | None = None,
    confirmed_at: str | None = None,
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
                   user_id, version, plan_kind, domain_id, payload_json, fallback, created_at,
                   status, stage, parent_version, confirmed_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                version,
                plan_kind,
                domain_id,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                1 if fallback else 0,
                datetime.now(UTC).isoformat(),
                status,
                stage,
                parent_version,
                confirmed_at,
            ),
        )
        conn.commit()
    return version


def list_plan_versions(
    user_id: str,
    db_path: Path = DB_PATH,
    *,
    include_drafts: bool = False,
) -> list[dict[str, Any]]:
    """按版本号升序返回。**默认不含草稿**——路线视图不该看见没确认的东西。"""

    init_plan_db(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """SELECT version, plan_kind, domain_id, payload_json, fallback, created_at,
                      status, stage, parent_version, confirmed_at
               FROM user_path_plan WHERE user_id = ? ORDER BY version""",
            (user_id,),
        ).fetchall()
    items = [
        {
            "version": int(row[0]),
            "plan_kind": str(row[1]),
            "domain_id": row[2],
            "payload": json.loads(str(row[3])),
            "fallback": bool(row[4]),
            "created_at": str(row[5]),
            "status": str(row[6] or "active"),
            "stage": str(row[7] or ""),
            "parent_version": int(row[8]) if row[8] is not None else None,
            "confirmed_at": row[9],
        }
        for row in rows
    ]
    if include_drafts:
        return items
    return [item for item in items if item["status"] != "draft"]


def get_plan_version(
    user_id: str,
    version: int,
    db_path: Path = DB_PATH,
    *,
    include_drafts: bool = True,
) -> dict[str, Any] | None:
    for item in list_plan_versions(user_id, db_path, include_drafts=include_drafts):
        if item["version"] == version:
            return item
    return None


def latest_draft(
    user_id: str,
    db_path: Path = DB_PATH,
    *,
    stage: str | None = None,
) -> dict[str, Any] | None:
    """最近一版**尚未确认**的草稿。向导刷新、确认、二次编辑都从这里进门。

    **确认过的不算**：确认会追加一条 ``parent_version=草稿版号`` 的 active 行，
    草稿行本身永远保持 ``status='draft'``（不 UPDATE 的不变量）。不排除的话，
    用户确认完再进向导会看到一份已经生效的草稿，还以为没点上。

    **历史遗留的那几条也不算**：草稿是沿 ``parent_version`` 串起来的一条链，
    确认过的草稿能被「它被哪条 active 行认领」认出来；但链上更早的旧草稿
    没有任何东西认领——它只是还挂着 ``draft`` 状态。只靠"是否被认领"筛，
    会拣到很早以前那份 36 点的旧明细（实测：用户已经确认到 81 个点、
    125 个点的路线，呈现层照着那份旧明细导出，导出来的根本不是当前路线）。

    所以再加一道：**比当前生效路线还老的草稿不算**。当前路线的版号是
    active l1 里最大的那个，草稿的版号小于它，说明那份取舍已经被后来的
    确认取代了。同一代里取版号最大的一版，不依赖列表顺序。
    """

    items = list_plan_versions(user_id, db_path, include_drafts=True)
    confirmed = {
        int(item["parent_version"])
        for item in items
        if item["status"] == "active" and item["parent_version"] is not None
    }
    live_version = max(
        (int(item["version"]) for item in items if item["status"] == "active" and item["plan_kind"] == "l1"),
        default=0,
    )
    live = [
        item
        for item in items
        if item["status"] == "draft"
        and item["version"] not in confirmed
        and int(item["version"]) > live_version
    ]
    if stage:
        candidates = [item for item in live if item["stage"] == stage]
    else:
        # 同一代里 detail 比 skeleton 靠后，取最靠后的那一档。
        order = {"skeleton": 0, "detail": 1}
        furthest = max((order.get(str(item["stage"] or ""), -1) for item in live), default=-1)
        candidates = [item for item in live if order.get(str(item["stage"] or ""), -1) == furthest]
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(item["version"]))


def is_draft_confirmed(user_id: str, version: int, db_path: Path = DB_PATH) -> bool:
    """这一版草稿是不是已经被某条 active 行认领（=已经确认过了）。

    草稿行永远保持 ``status='draft'``（"只追加不 UPDATE"的不变量），所以
    "有没有确认过"不能看草稿自己的状态，只能看有没有 active 行的
    ``parent_version`` 指向它。少了这道判断，连点两下"就这样定"会确认出两条
    一模一样的路线。
    """

    for item in list_plan_versions(user_id, db_path, include_drafts=True):
        if item["status"] == "active" and item["parent_version"] is not None:
            if int(item["parent_version"]) == int(version):
                return True
    return False


def find_reusable_l1(user_id: str, input_hash: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    """同一份输入快照命中已有 L1 就直接复用，不再问模型（计划 §2.5）。

    只复用 **非 fallback** 的那条：底稿是"模型没给成"的产物，下次该重新问，不该被
    永久固化。``temperature`` 锁 0 只是让模型尽量稳定；真正保证"同画像同计划"的是
    这里的复用，不是采样配置。草稿不参与复用——它还没被确认，谈不上"已有版本"。
    """

    if not input_hash:
        return None
    for item in reversed(list_plan_versions(user_id, db_path)):
        if item["plan_kind"] != "l1" or item["fallback"]:
            continue
        meta = item["payload"].get("model_meta") or {}
        if meta.get("input_hash") == input_hash:
            return {"version": item["version"], "payload": item["payload"]}
    return None


def latest_l1_overrides(user_id: str, db_path: Path = DB_PATH) -> dict[str, dict[str, Any]]:
    """取最近一次 L1 提案留下的逐点覆盖，交给 L2 展开时用。"""

    for item in reversed(list_plan_versions(user_id, db_path)):
        if item["plan_kind"] != "l1":
            continue
        proposal = item["payload"].get("proposal") or {}
        overrides = proposal.get("overrides")
        return overrides if isinstance(overrides, dict) else {}
    return {}


def superseded_drafts(user_id: str, *, keep: int, db_path: Path = DB_PATH) -> Iterable[dict[str, Any]]:
    """给运维/测试用的只读视图：除最新一版外还留着的草稿。

    草稿不删：它是「用户改过什么」的唯一记录，删了就没法解释同一份画像为什么
    两次规划不一样。这里只暴露出来，不做清理。
    """

    drafts = [item for item in list_plan_versions(user_id, db_path, include_drafts=True) if item["status"] == "draft"]
    return drafts[:-keep] if keep else drafts


__all__ = [
    "DRAFT_STAGES",
    "find_reusable_l1",
    "get_plan_version",
    "init_plan_db",
    "is_draft_confirmed",
    "latest_draft",
    "latest_l1_overrides",
    "list_plan_versions",
    "save_plan_version",
    "superseded_drafts",
]
