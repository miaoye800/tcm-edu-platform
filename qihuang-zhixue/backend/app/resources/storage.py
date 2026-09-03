"""SQLite storage for generated resources and resumable generation tasks."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.profile.store import DB_PATH, connect


def init_db(db_path: Path = DB_PATH) -> None:
    with closing(connect(db_path)) as conn:
        # knowledge_node is initialized first so the foreign key works in a fresh DB.
        conn.execute("""CREATE TABLE IF NOT EXISTS knowledge_node (id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', level TEXT NOT NULL DEFAULT 'point', parent_id TEXT, description TEXT NOT NULL DEFAULT '', exam_frequency REAL NOT NULL DEFAULT 0, sort_hint INTEGER NOT NULL DEFAULT 0, extra_json TEXT NOT NULL DEFAULT '{}')""")
        conn.execute("""CREATE TABLE IF NOT EXISTS user_resource (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, node_id TEXT NOT NULL REFERENCES knowledge_node(id),
            type TEXT NOT NULL CHECK(type IN ('doc','graph','mindmap','quiz','story')), title TEXT NOT NULL,
            content_json TEXT NOT NULL, sources_json TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK(status IN ('generating','ready','failed')), favorited INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, meta_json TEXT NOT NULL DEFAULT '{}'
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS resource_task (
            task_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, node_id TEXT NOT NULL REFERENCES knowledge_node(id),
            resource_type TEXT NOT NULL CHECK(resource_type IN ('doc','graph','mindmap','quiz','story')),
            status TEXT NOT NULL CHECK(status IN ('pending','running','done','failed')), current_step INTEGER NOT NULL DEFAULT 0,
            steps_done_json TEXT NOT NULL DEFAULT '{}', resource_id TEXT REFERENCES user_resource(id), created_at TEXT NOT NULL,
            meta_json TEXT NOT NULL DEFAULT '{}'
        )""")
        _add_column_if_missing(conn, "resource_task", "meta_json", "TEXT NOT NULL DEFAULT '{}'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_resource_list ON user_resource(user_id, created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_resource_task_owner ON resource_task(user_id, task_id)")
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


def update_task(task_id: str, user_id: str, *, status: str | None = None, current_step: int | None = None, steps_done: dict[str, Any] | None = None, resource_id: str | None = None, db_path: Path = DB_PATH) -> None:
    task = get_task(task_id, user_id, db_path)
    if not task: return
    with closing(connect(db_path)) as conn:
        conn.execute("UPDATE resource_task SET status=?,current_step=?,steps_done_json=?,resource_id=? WHERE task_id=? AND user_id=?", (status or task["status"], task["current_step"] if current_step is None else current_step, json.dumps(steps_done if steps_done is not None else task["steps_done"], ensure_ascii=False), resource_id if resource_id is not None else task["resource_id"], task_id, user_id))
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
