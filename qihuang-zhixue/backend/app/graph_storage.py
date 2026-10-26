"""SQLite persistence for GraphAgent entities, evidence-bound relations and caches."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from typing import Any

from app.profile.store import DB_PATH, connect


_INITIALIZED: set[Path] = set()


def reset_init_cache(db_path: Path | None = None) -> None:
    """表结构被外部改动后清掉「已初始化」标记，下次调用会重新建表。"""

    if db_path is None:
        _INITIALIZED.clear()
    else:
        _INITIALIZED.discard(Path(db_path))


def init_db(db_path: Path = DB_PATH) -> None:
    # 逐条写入时这里会被调上千次；DDL 只需在进程内跑一次。
    key = Path(db_path)
    if key in _INITIALIZED:
        return
    with closing(connect(key)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS knowledge_node (
            id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', level TEXT NOT NULL DEFAULT 'point',
            parent_id TEXT, description TEXT NOT NULL DEFAULT '', exam_frequency REAL NOT NULL DEFAULT 0,
            sort_hint INTEGER NOT NULL DEFAULT 0, extra_json TEXT NOT NULL DEFAULT '{}'
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS graph_entity (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, entity_type TEXT NOT NULL
                CHECK(entity_type IN ('herb','syndrome','formula','category')),
            knowledge_node_id TEXT REFERENCES knowledge_node(id), meta_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS herb_relation (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
            relation_type TEXT NOT NULL CHECK(relation_type IN
                ('功效相通','相须','相使','相畏','相杀','相恶','相反','性味相通','性味相反','归经聚类','药证链','证方链','组成','同类鉴别')),
            label TEXT NOT NULL, sources_json TEXT NOT NULL, confidence REAL NOT NULL,
            created_at TEXT NOT NULL, meta_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(source_id,target_id,relation_type,label)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS graph_cache (
            center_id TEXT PRIMARY KEY, content_json TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS graph_detail_cache (
            node_id TEXT PRIMARY KEY, detail_json TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_herb_relation_source ON herb_relation(source_id)")
        conn.commit()
    _INITIALIZED.add(key)


def get_graph(center_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT content_json FROM graph_cache WHERE center_id=?", (center_id,)).fetchone()
    return json.loads(row[0]) if row else None


def put_graph(center_id: str, content: dict[str, Any], created_at: str, db_path: Path = DB_PATH) -> None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute("INSERT OR REPLACE INTO graph_cache(center_id,content_json,created_at) VALUES (?,?,?)", (center_id, json.dumps(content, ensure_ascii=False), created_at))
        conn.commit()


def put_relations(relations: list[dict[str, Any]], created_at: str, db_path: Path = DB_PATH) -> None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        for relation in relations:
            conn.execute("""INSERT OR IGNORE INTO herb_relation(source_id,target_id,relation_type,label,sources_json,confidence,created_at,meta_json)
                VALUES (?,?,?,?,?,?,?,?)""", (relation['source_id'], relation['target_id'], relation['relation_type'], relation['label'], json.dumps(relation['sources'], ensure_ascii=False), relation['confidence'], created_at, json.dumps(relation.get('meta', {}), ensure_ascii=False)))
        conn.commit()


def put_entity(entity_id: str, name: str, entity_type: str, created_at: str, *, knowledge_node_id: str | None = None, meta: dict[str, Any] | None = None, db_path: Path = DB_PATH) -> None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute("INSERT OR REPLACE INTO graph_entity(id,name,entity_type,knowledge_node_id,meta_json,created_at) VALUES (?,?,?,?,?,?)", (entity_id, name, entity_type, knowledge_node_id, json.dumps(meta or {}, ensure_ascii=False), created_at))
        conn.commit()


def get_detail(node_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT detail_json FROM graph_detail_cache WHERE node_id=?", (node_id,)).fetchone()
    return json.loads(row[0]) if row else None


def put_detail(node_id: str, detail: dict[str, Any], created_at: str, db_path: Path = DB_PATH) -> None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute("INSERT OR REPLACE INTO graph_detail_cache(node_id,detail_json,created_at) VALUES (?,?,?)", (node_id, json.dumps(detail, ensure_ascii=False), created_at))
        conn.commit()


def node_name(node_id: str, db_path: Path = DB_PATH) -> str | None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT name FROM knowledge_node WHERE id=?", (node_id,)).fetchone()
    return str(row[0]) if row else None


def node_exists(node_id: str, db_path: Path = DB_PATH) -> bool:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        return conn.execute("SELECT 1 FROM knowledge_node WHERE id=?", (node_id,)).fetchone() is not None
