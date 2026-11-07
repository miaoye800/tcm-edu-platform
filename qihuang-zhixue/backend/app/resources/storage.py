"""SQLite storage for generated resources and resumable generation tasks."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.path.store import init_db as init_path_tables
from app.profile.store import DB_PATH, connect

# 类型白名单的唯一定义处：建表用它，重建表也用它，避免两处漂移。
_TYPES_SQL = "'doc','graph','mindmap','quiz','story','ppt'"

# 建表语句模板。{clause} 形如 "IF NOT EXISTS user_resource" 或 "user_resource__new"，
# 迁移与首次建表共用同一份 DDL 文本。
_USER_RESOURCE_DDL = """CREATE TABLE {clause} (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, node_id TEXT NOT NULL REFERENCES knowledge_node(id),
            type TEXT NOT NULL CHECK(type IN (__TYPES__)), title TEXT NOT NULL,
            content_json TEXT NOT NULL, sources_json TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK(status IN ('generating','ready','failed')), favorited INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, meta_json TEXT NOT NULL DEFAULT '{}'
        )"""

_RESOURCE_TASK_DDL = """CREATE TABLE {clause} (
            task_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, node_id TEXT NOT NULL REFERENCES knowledge_node(id),
            resource_type TEXT NOT NULL CHECK(resource_type IN (__TYPES__)),
            status TEXT NOT NULL CHECK(status IN ('pending','running','done','failed')), current_step INTEGER NOT NULL DEFAULT 0,
            steps_done_json TEXT NOT NULL DEFAULT '{}', resource_id TEXT REFERENCES user_resource(id), created_at TEXT NOT NULL,
            meta_json TEXT NOT NULL DEFAULT '{}'
        )"""

_USER_RESOURCE_INDEXES = ("CREATE INDEX IF NOT EXISTS idx_user_resource_list ON user_resource(user_id, created_at DESC)",)
_RESOURCE_TASK_INDEXES = ("CREATE INDEX IF NOT EXISTS idx_resource_task_owner ON resource_task(user_id, task_id)",)


def _render(ddl: str, clause: str) -> str:
    return ddl.replace("{clause}", clause).replace("__TYPES__", _TYPES_SQL)


def _table_ddl(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return str(row[0] or "") if row else ""


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]


def _rebuild_table(conn: sqlite3.Connection, *, table: str, ddl: str, indexes: tuple[str, ...]) -> None:
    """按列名交集搬迁数据，重建一张表。

    不写 ``INSERT INTO new SELECT *``：历史库可能缺 ``meta_json`` 之类的后加列，
    ``SELECT *`` 会因列数不齐直接失败。按交集显式列出列名，缺的列走 DDL 默认值。
    """
    before = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    tmp = f"{table}__new"
    conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    conn.execute(_render(ddl, tmp))
    old_columns = _table_columns(conn, table)
    new_columns = _table_columns(conn, tmp)
    shared = [name for name in new_columns if name in old_columns]
    if not shared:
        raise RuntimeError(f"迁移 {table} 失败：新旧表没有共同列")
    column_list = ",".join(shared)
    conn.execute(f"INSERT INTO {tmp} ({column_list}) SELECT {column_list} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
    for statement in indexes:
        conn.execute(statement)
    after = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    if after != before:
        raise RuntimeError(f"迁移 {table} 行数不一致：{before} -> {after}")


def _ensure_ppt_resource_type(conn: sqlite3.Connection) -> None:
    """把两张表的 CHECK 白名单补上 'ppt'。

    白名单写死在 CREATE TABLE 里，加类型只能重建表。三个必须守住的点：
    1. ``PRAGMA foreign_keys`` 在事务内是空操作，必须先关外键再 ``BEGIN``；
    2. ``conn.isolation_level = None`` 关掉 Python 的隐式事务，DDL 才受我们控制；
    3. ``legacy_alter_table=ON`` 阻止 RENAME 去改写 resource_task 的 FK 引用
       （否则外键会指向临时表名，重建完就断）。
    """
    targets = (
        ("user_resource", _USER_RESOURCE_DDL, _USER_RESOURCE_INDEXES),
        ("resource_task", _RESOURCE_TASK_DDL, _RESOURCE_TASK_INDEXES),
    )
    pending = [name for name, _, _ in targets if "'ppt'" not in _table_ddl(conn, name)]
    # 表还不存在（全新库）不算待迁移：稍后的 CREATE TABLE IF NOT EXISTS 会直接建新 schema。
    pending = [name for name in pending if _table_ddl(conn, name)]
    if not pending:
        return

    conn.commit()
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table, ddl, indexes in targets:
            current = _table_ddl(conn, table)
            if current and "'ppt'" not in current:
                _rebuild_table(conn, table=table, ddl=ddl, indexes=indexes)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.isolation_level = ""


def init_db(db_path: Path = DB_PATH) -> None:
    # knowledge_node 的权威 DDL 只在 app/path/store.py（带 level / exam_frequency 的 CHECK）。
    # 这里曾经复制过一份更宽松的建表语句：靠 CREATE TABLE IF NOT EXISTS「先到先得」，
    # 资源库先初始化时，path 那份就变成空操作，约束被静默丢掉（实测：level 的 CHECK 直接
    # 不存在）。统一到一处后，建表顺序不再影响 schema。
    init_path_tables(db_path)
    with closing(connect(db_path)) as conn:
        _ensure_ppt_resource_type(conn)
        conn.execute(_render(_USER_RESOURCE_DDL, "IF NOT EXISTS user_resource"))
        conn.execute(_render(_RESOURCE_TASK_DDL, "IF NOT EXISTS resource_task"))
        _add_column_if_missing(conn, "resource_task", "meta_json", "TEXT NOT NULL DEFAULT '{}'")
        for statement in _USER_RESOURCE_INDEXES + _RESOURCE_TASK_INDEXES:
            conn.execute(statement)
        conn.commit()


def now() -> str:
    return datetime.now(UTC).isoformat()


def create_task(task_id: str, user_id: str, node_id: str, resource_type: str, db_path: Path = DB_PATH, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute("INSERT INTO resource_task(task_id,user_id,node_id,resource_type,status,current_step,steps_done_json,created_at,meta_json) VALUES (?,?,?,?, 'pending',0,'{}',?,?)", (task_id, user_id, node_id, resource_type, now(), json.dumps(meta or {}, ensure_ascii=False)))
        conn.commit()
    return get_task(task_id, user_id, db_path)  # type: ignore[return-value]


def get_task(task_id: str, user_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT task_id,user_id,node_id,resource_type,status,current_step,steps_done_json,resource_id,created_at,meta_json FROM resource_task WHERE task_id=? AND user_id=?", (task_id, user_id)).fetchone()
    if not row: return None
    return {"task_id": row[0], "user_id": row[1], "node_id": row[2], "resource_type": row[3], "status": row[4], "current_step": row[5], "steps_done": json.loads(row[6]), "resource_id": row[7], "created_at": row[8], "meta": json.loads(row[9])}


def update_task(task_id: str, user_id: str, *, status: str | None = None, current_step: int | None = None, steps_done: dict[str, Any] | None = None, resource_id: str | None = None, meta: dict[str, Any] | None = None, db_path: Path = DB_PATH) -> None:
    task = get_task(task_id, user_id, db_path)
    if not task: return
    # meta 走合并而不是覆盖：``replay_of`` / ``params`` 这类键是分阶段写进去的，
    # 一次覆盖就会把前一步的输入丢掉（幻灯确认大纲正是这种「中途改输入」）。
    merged_meta = dict(task.get("meta") or {})
    if meta:
        merged_meta.update(meta)
    with closing(connect(db_path)) as conn:
        conn.execute("UPDATE resource_task SET status=?,current_step=?,steps_done_json=?,resource_id=?,meta_json=? WHERE task_id=? AND user_id=?", (status or task["status"], task["current_step"] if current_step is None else current_step, json.dumps(steps_done if steps_done is not None else task["steps_done"], ensure_ascii=False), resource_id if resource_id is not None else task["resource_id"], json.dumps(merged_meta, ensure_ascii=False), task_id, user_id))
        conn.commit()


def create_resource(resource: dict[str, Any], db_path: Path = DB_PATH) -> None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute("INSERT OR IGNORE INTO user_resource(id,user_id,node_id,type,title,content_json,sources_json,notes,status,favorited,created_at,meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (resource["id"],resource["user_id"],resource["node_id"],resource["type"],resource["title"],json.dumps(resource["content"],ensure_ascii=False),json.dumps(resource["sources"],ensure_ascii=False),resource["notes"],resource["status"],int(resource.get("favorited",False)),resource["created_at"],json.dumps(resource.get("meta",{}),ensure_ascii=False)))
        conn.commit()


def get_resource(resource_id: str, user_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row=conn.execute("SELECT id,user_id,node_id,type,title,content_json,sources_json,notes,status,favorited,created_at,meta_json FROM user_resource WHERE id=? AND user_id=?",(resource_id,user_id)).fetchone()
    return _resource(row) if row else None


def list_resources(user_id: str, *, node_id: str | None, resource_type: str | None, favorited: bool | None, query: str | None, offset: int, limit: int, db_path: Path = DB_PATH) -> tuple[list[dict[str, Any]], int]:
    init_db(db_path); clauses=["user_id=?"]; args: list[Any]=[user_id]
    if node_id: clauses.append("node_id=?"); args.append(node_id)
    if resource_type: clauses.append("type=?"); args.append(resource_type)
    if favorited is not None: clauses.append("favorited=?"); args.append(int(favorited))
    if query: clauses.append("title LIKE ?"); args.append(f"%{query}%")
    where=" AND ".join(clauses)
    with closing(connect(db_path)) as conn:
        total=conn.execute(f"SELECT COUNT(*) FROM user_resource WHERE {where}",args).fetchone()[0]
        rows=conn.execute(f"SELECT id,user_id,node_id,type,title,content_json,sources_json,notes,status,favorited,created_at,meta_json FROM user_resource WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",args+[limit,offset]).fetchall()
    return [_resource(row) for row in rows], total


def toggle_favorite(resource_id: str, user_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    item=get_resource(resource_id,user_id,db_path)
    if not item: return None
    with closing(connect(db_path)) as conn:
        conn.execute("UPDATE user_resource SET favorited=? WHERE id=? AND user_id=?",(int(not item["favorited"]),resource_id,user_id)); conn.commit()
    return get_resource(resource_id,user_id,db_path)


def update_resource_content(resource_id: str, user_id: str, content: dict[str, Any], db_path: Path = DB_PATH) -> dict[str, Any] | None:
    """Persist a resource-content mutation while retaining its immutable provenance."""
    if get_resource(resource_id, user_id, db_path) is None:
        return None
    with closing(connect(db_path)) as conn:
        conn.execute("UPDATE user_resource SET content_json=? WHERE id=? AND user_id=?", (json.dumps(content, ensure_ascii=False), resource_id, user_id))
        conn.commit()
    return get_resource(resource_id, user_id, db_path)


class VersionConflict(Exception):
    """文档版本不一致：编辑基于旧版本，或期间被「重炼」覆盖。"""

    def __init__(self, current: int) -> None:
        super().__init__("VERSION_CONFLICT")
        self.current = current


def update_document(
    resource_id: str,
    user_id: str,
    blocks: list[dict[str, Any]],
    *,
    expected_version: int,
    meta_patch: dict[str, Any] | None = None,
    db_path: Path = DB_PATH,
) -> dict[str, Any] | None:
    """保存文档块。

    版本号存在 meta_json.doc_version 里而不是新增列：SQLite 加列要动 DDL，
    而 meta 本来就是「服务端自用的附加信息」，用它托管版本号零迁移风险。
    读-比-写在同一事务内完成，避免两个标签页同时保存时都判定成功。
    """
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT content_json, meta_json FROM user_resource WHERE id=? AND user_id=?", (resource_id, user_id)).fetchone()
        if row is None:
            return None
        content = json.loads(row[0] or "{}")
        meta = json.loads(row[1] or "{}")
        current = int(meta.get("doc_version", 1))
        if expected_version != current:
            raise VersionConflict(current)
        content["blocks"] = blocks
        if meta_patch:
            meta.update(meta_patch)
        meta["doc_version"] = current + 1
        conn.execute(
            "UPDATE user_resource SET content_json=?, meta_json=? WHERE id=? AND user_id=?",
            (json.dumps(content, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), resource_id, user_id),
        )
        conn.commit()
    return {"version": current + 1, "block_count": len(blocks)}


def update_mindmap_tree(
    resource_id: str,
    user_id: str,
    content: dict[str, Any],
    *,
    expected_version: int,
    db_path: Path = DB_PATH,
) -> dict[str, Any] | None:
    """保存整棵导图。

    与 ``update_document`` 同一套乐观并发：版本号托管在 ``meta_json.mindmap_version``
    （不新增列、零迁移），读-比-写在同一事务里，避免两个标签页都判定成功。

    与 ``PATCH /resources/{id}/mindmap``（换模板）的分工：换模板只改
    ``content.template_id``、不碰版本号；这里整体写 ``content`` 但版本号 +1。
    极端并发下换模板可能被覆盖，可重试，明确接受。
    """

    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT meta_json FROM user_resource WHERE id=? AND user_id=?", (resource_id, user_id)).fetchone()
        if row is None:
            return None
        meta = json.loads(row[0] or "{}")
        current = int(meta.get("mindmap_version", 1))
        if expected_version != current:
            raise VersionConflict(current)
        meta["mindmap_version"] = current + 1
        conn.execute(
            "UPDATE user_resource SET content_json=?, meta_json=? WHERE id=? AND user_id=?",
            (json.dumps(content, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), resource_id, user_id),
        )
        conn.commit()
    return {"version": current + 1}


def update_mindmap_appearance(
    resource_id: str,
    user_id: str,
    *,
    structure: str,
    theme_id: str,
    db_path: Path = DB_PATH,
) -> dict[str, Any] | None:
    """只保存结构与配色，不动树、不动版本号。

    刻意不参与 ``mindmap_version`` 的乐观并发：换结构和换配色是视图偏好，
    两个标签页各点一次不该互相触发 409。真并发时后写覆盖先写，可接受。
    """

    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT meta_json, content_json FROM user_resource WHERE id=? AND user_id=?", (resource_id, user_id)).fetchone()
        if row is None:
            return None
        meta = json.loads(row[0] or "{}")
        content = json.loads(row[1] or "{}")
        meta["mindmap_structure"] = structure
        meta["mindmap_theme"] = theme_id
        # 同步写一份进 content：只读导出、分享快照这类场景只读 content。
        content["structure"] = structure
        content["template_id"] = theme_id
        conn.execute(
            "UPDATE user_resource SET content_json=?, meta_json=? WHERE id=? AND user_id=?",
            (json.dumps(content, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), resource_id, user_id),
        )
        conn.commit()
    return {"structure": structure, "theme_id": theme_id}


def update_deck(
    resource_id: str,
    user_id: str,
    deck: dict[str, Any],
    *,
    expected_version: int,
    db_path: Path = DB_PATH,
) -> dict[str, Any] | None:
    """保存整份幻灯。

    与 ``update_mindmap_tree`` 同一套乐观并发：版本号托管在 ``meta_json.ppt_version``。
    ``sources``（溯源证据）不随前端提交——它是生成期固化的出处，不该被编辑动作改写，
    因此只在请求显式带上时才覆盖。
    """

    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT content_json, meta_json FROM user_resource WHERE id=? AND user_id=?", (resource_id, user_id)).fetchone()
        if row is None:
            return None
        content = json.loads(row[0] or "{}")
        meta = json.loads(row[1] or "{}")
        current = int(meta.get("ppt_version", 1))
        if expected_version != current:
            raise VersionConflict(current)
        content["theme_id"] = deck.get("theme_id") or content.get("theme_id") or "paper"
        content["slides"] = deck["slides"]
        if "sources" in deck:
            content["sources"] = deck["sources"]
        meta["ppt_version"] = current + 1
        conn.execute(
            "UPDATE user_resource SET content_json=?, meta_json=? WHERE id=? AND user_id=?",
            (json.dumps(content, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), resource_id, user_id),
        )
        conn.commit()
    return {"version": current + 1, "slide_count": len(deck["slides"])}


def node_name(node_id: str, db_path: Path = DB_PATH) -> str:
    init_db(db_path)
    with closing(connect(db_path)) as conn: row=conn.execute("SELECT name FROM knowledge_node WHERE id=?",(node_id,)).fetchone()
    return str(row[0]) if row else node_id


def node_exists(node_id: str, db_path: Path = DB_PATH) -> bool:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        return conn.execute("SELECT 1 FROM knowledge_node WHERE id=?", (node_id,)).fetchone() is not None


def _resource(row: Any) -> dict[str, Any]:
    return {"id":row[0],"user_id":row[1],"node_id":row[2],"type":row[3],"title":row[4],"content":json.loads(row[5]),"sources":json.loads(row[6]),"notes":row[7],"status":row[8],"favorited":bool(row[9]),"created_at":row[10],"meta":json.loads(row[11])}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
