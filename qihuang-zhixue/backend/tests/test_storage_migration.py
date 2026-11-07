"""CHECK 白名单迁移：把老库的两张表补上 'ppt' 类型且不丢数据。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.path.store import init_db as init_path_tables
from app.resources import storage as resource_storage

# 加 ppt 之前的建表语句，原样抄一份：迁移测试必须站在真实的老 schema 上。
_OLD_USER_RESOURCE = """
CREATE TABLE user_resource (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, node_id TEXT NOT NULL REFERENCES knowledge_node(id),
    type TEXT NOT NULL CHECK(type IN ('doc','graph','mindmap','quiz','story')), title TEXT NOT NULL,
    content_json TEXT NOT NULL, sources_json TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('generating','ready','failed')), favorited INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, meta_json TEXT NOT NULL DEFAULT '{}'
)
"""

_OLD_RESOURCE_TASK = """
CREATE TABLE resource_task (
    task_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, node_id TEXT NOT NULL REFERENCES knowledge_node(id),
    resource_type TEXT NOT NULL CHECK(resource_type IN ('doc','graph','mindmap','quiz','story')),
    status TEXT NOT NULL CHECK(status IN ('pending','running','done','failed')), current_step INTEGER NOT NULL DEFAULT 0,
    steps_done_json TEXT NOT NULL DEFAULT '{}', resource_id TEXT REFERENCES user_resource(id), created_at TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}'
)
"""


def _legacy_db(db_path: Path) -> None:
    init_path_tables(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO knowledge_node(id,name,level) VALUES('n1','麻黄','point')")
        conn.executescript(_OLD_USER_RESOURCE)
        conn.executescript(_OLD_RESOURCE_TASK)
        conn.execute(
            "INSERT INTO user_resource(id,user_id,node_id,type,title,content_json,status,created_at)"
            " VALUES('r1','u1','n1','doc','讲义',?,'ready','2026-01-01')",
            ('{"blocks":[]}',),
        )
        conn.execute(
            "INSERT INTO user_resource(id,user_id,node_id,type,title,content_json,status,created_at)"
            " VALUES('r2','u1','n1','mindmap','导图',?,'ready','2026-01-02')",
            ('{"nodeData":{}}',),
        )
        conn.execute(
            "INSERT INTO resource_task(task_id,user_id,node_id,resource_type,status,current_step,created_at)"
            " VALUES('t1','u1','n1','doc','done',5,'2026-01-01')"
        )
        conn.commit()
    finally:
        conn.close()


def _schema(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return str(row[0]) if row else ""


def test_migration_adds_ppt_without_losing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _legacy_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        assert "'ppt'" not in _schema(conn, "user_resource")
        assert conn.execute("SELECT COUNT(*) FROM user_resource").fetchone()[0] == 2
    finally:
        conn.close()

    resource_storage.init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        assert "ppt" in _schema(conn, "user_resource")
        assert "ppt" in _schema(conn, "resource_task")
        assert conn.execute("SELECT COUNT(*) FROM user_resource").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM resource_task").fetchone()[0] == 1
        # 老数据逐字段还在，不能只剩行数对得上。
        row = conn.execute("SELECT type,title,content_json,favorited FROM user_resource WHERE id='r1'").fetchone()
        assert row == ("doc", "讲义", '{"blocks":[]}', 0)
    finally:
        conn.close()


def test_migration_allows_ppt_and_keeps_foreign_key(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _legacy_db(db_path)
    resource_storage.init_db(db_path)

    resource_storage.create_task("t2", "u1", "n1", "ppt", db_path)
    task = resource_storage.get_task("t2", "u1", db_path)
    assert task is not None and task["resource_type"] == "ppt"

    resource_storage.create_resource(
        {
            "id": "r3",
            "user_id": "u1",
            "node_id": "n1",
            "type": "ppt",
            "title": "麻黄 · 讲稿",
            "content": {"theme_id": "paper", "slides": []},
            "sources": [],
            "notes": "",
            "status": "ready",
            "created_at": "2026-01-03",
            "meta": {"ppt_version": 1},
        },
        db_path,
    )
    saved = resource_storage.get_resource("r3", "u1", db_path)
    assert saved is not None and saved["type"] == "ppt"
    assert saved["content"]["theme_id"] == "paper"

    # 外键仍然生效：不存在的 node_id 必须被拒。
    with pytest.raises(sqlite3.IntegrityError):
        resource_storage.create_resource(
            {
                "id": "r4",
                "user_id": "u1",
                "node_id": "missing",
                "type": "ppt",
                "title": "坏数据",
                "content": {},
                "sources": [],
                "notes": "",
                "status": "ready",
                "created_at": "2026-01-04",
                "meta": {},
            },
            db_path,
        )


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _legacy_db(db_path)
    resource_storage.init_db(db_path)
    resource_storage.init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM user_resource").fetchone()[0] == 2
        # RENAME 后索引必须重建，否则列表查询会全表扫。
        indexes = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='user_resource'")
        }
        assert "idx_user_resource_list" in indexes
    finally:
        conn.close()


def test_migration_tolerates_old_table_without_meta_json(tmp_path: Path) -> None:
    """更早的库没有 meta_json：按列交集搬迁，缺列走默认值而不是整表失败。"""

    db_path = tmp_path / "ancient.db"
    init_path_tables(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO knowledge_node(id,name,level) VALUES('n1','麻黄','point')")
        conn.execute(
            """CREATE TABLE user_resource (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, node_id TEXT NOT NULL REFERENCES knowledge_node(id),
                type TEXT NOT NULL CHECK(type IN ('doc','graph','mindmap','quiz','story')), title TEXT NOT NULL,
                content_json TEXT NOT NULL, sources_json TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK(status IN ('generating','ready','failed')), favorited INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO user_resource(id,user_id,node_id,type,title,content_json,status,created_at)"
            " VALUES('r1','u1','n1','doc','讲义','{}','ready','2026-01-01')"
        )
        conn.commit()
    finally:
        conn.close()

    resource_storage.init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT title,meta_json FROM user_resource WHERE id='r1'").fetchone()
        assert row == ("讲义", "{}")
    finally:
        conn.close()
