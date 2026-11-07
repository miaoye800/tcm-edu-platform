"""KG 表结构定义与版本化迁移。

设计原则（与旧 graph_storage 的关键差别）：

1. **新表并存，不 ALTER 旧表**。``herb_relation.relation_type`` 有 14 值 CHECK，
   SQLite 无法 ALTER CHECK，加一个关系类型就得 DROP TABLE。所以 KG 用全新表，
   ``relation_type`` 刻意不加 CHECK —— 枚举演进由 ``app/kg/constants.py`` 管。
2. **禁止 DROP TABLE**。旧 ``build_graph.py`` 里「CHECK 不含某值就整表重建」的
   写法不要在 KG 里复制。
3. **JSON 是 source of truth，SQLite 是可重建索引**。``kg_entity.payload_ref``
   只存文件路径，不复制正文。
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from app.profile.store import DB_PATH, connect

#: 每次 CRUD 都跑一遍 DDL 太浪费；同一进程内每个库只需建一次表。
_ENSURED: set[Path] = set()

#: 每次改表结构都要 +1，迁移函数据此判断该跑哪些步骤。
SCHEMA_VERSION = 3

_ENTITY_DDL = """
CREATE TABLE IF NOT EXISTS kg_entity (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('herb','syndrome','formula','category','meridian','effect','symptom')),
    name TEXT NOT NULL,
    pinyin TEXT NOT NULL DEFAULT '',
    canonical_id TEXT REFERENCES kg_entity(id),
    knowledge_node_id TEXT,
    status TEXT NOT NULL DEFAULT 'approved'
        CHECK(status IN ('orphan','draft','pending','approved','rejected')),
    payload_ref TEXT,
    extra_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_ALIAS_DDL = """
CREATE TABLE IF NOT EXISTS kg_alias (
    alias TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES kg_entity(id),
    source TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(alias, entity_id)
)
"""

_EDGE_DDL = """
CREATE TABLE IF NOT EXISTS kg_edge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES kg_entity(id),
    target_id TEXT NOT NULL REFERENCES kg_entity(id),
    relation_type TEXT NOT NULL,
    tier INTEGER NOT NULL CHECK(tier IN (1,2,3)),
    label TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    derived INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'approved'
        CHECK(status IN ('pending','approved','rejected')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_id, target_id, relation_type, label)
)
"""

_ATTRIBUTION_DDL = """
CREATE TABLE IF NOT EXISTS kg_attribution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mistake_id TEXT NOT NULL,
    entity_id TEXT,
    edge_id INTEGER,
    cause_type TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(mistake_id, entity_id, edge_id, cause_type)
)
"""

#: LLM 闭集映射的结果缓存。
#:
#: 教学场景里同一个病例描述会被反复提交（老师出同一道题、同一个班几十个学生），
#: 缓存能把模型调用从「每次一问」降到「每个病例一问」。这是控制额度的主要手段。
_MAPPING_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS kg_mapping_cache (
    text_hash TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    picks_json TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
"""

#: 教材抽取的**草稿表**（P4.4）。
#:
#: 为什么抽取结果不能直接落 ``data/herbs/``：抽取是自动的，一次正则改错就能
#: 把 442 味药全写坏，而 ``data/herbs/*.json`` 是 source of truth、直接进处方审查。
#: 所以中间必须有一张「等人签字」的表——``status='approved'`` 才允许落 JSON。
#:
#: ``UNIQUE(herb_name, block_id)`` 让重跑幂等：同一味药同一块重复抽取是 UPDATE
#: 不是 INSERT，否则每跑一次复核队列就翻一倍。
_DRAFT_DDL = """
CREATE TABLE IF NOT EXISTS herb_draft (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    herb_name TEXT NOT NULL,
    block_id TEXT NOT NULL DEFAULT '',
    page_start INTEGER,
    page_end INTEGER,
    payload_json TEXT NOT NULL DEFAULT '{}',
    gate_report_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','auto_passed','conflict','approved','rejected')),
    model TEXT NOT NULL DEFAULT '',
    prompt_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    UNIQUE(herb_name, block_id)
)
"""

#: 人工改过什么、为什么改。**这是反向定位「闸门漏了什么」的唯一依据**：
#: 只有 ``herb_draft`` 的终态，看不出人把哪个字段从什么改成了什么。
_DRAFT_PATCH_DDL = """
CREATE TABLE IF NOT EXISTS herb_draft_patch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES herb_draft(id),
    field TEXT NOT NULL,
    old_value TEXT NOT NULL DEFAULT '',
    new_value TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
"""

#: 抽样计划（P4.4「首批 50 味 100%、毒性 100%、flag 100%、其余 20%」+「每批 3 味反向核对」）。
#:
#: 抽样**必须落库**，否则它只是个建议：复核页上那个「随机抽查 20」按钮点没点、
#: 看了几条，系统一概不知，拦不住任何人把没复核的批次入库。
#: ``verdict=''`` 表示还没看——promote 前所有行都必须有 verdict。
_DRAFT_SAMPLE_DDL = """
CREATE TABLE IF NOT EXISTS herb_draft_sample (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES herb_draft(id),
    batch TEXT NOT NULL,
    reason TEXT NOT NULL,
    verdict TEXT NOT NULL DEFAULT ''
        CHECK(verdict IN ('','ok','bad','skip')),
    note TEXT NOT NULL DEFAULT '',
    reviewed_by TEXT,
    reviewed_at TEXT,
    UNIQUE(draft_id, batch, reason)
)
"""

_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS kg_entity_kind_name ON kg_entity(kind, name)",
    "CREATE INDEX IF NOT EXISTS kg_entity_status ON kg_entity(kind, status)",
    "CREATE INDEX IF NOT EXISTS kg_entity_tree ON kg_entity(knowledge_node_id)",
    "CREATE INDEX IF NOT EXISTS kg_alias_entity ON kg_alias(entity_id)",
    "CREATE INDEX IF NOT EXISTS kg_edge_src ON kg_edge(source_id, tier, status)",
    "CREATE INDEX IF NOT EXISTS kg_edge_tgt ON kg_edge(target_id, tier, status)",
    "CREATE INDEX IF NOT EXISTS kg_attribution_mistake ON kg_attribution(mistake_id)",
    "CREATE INDEX IF NOT EXISTS herb_draft_status ON herb_draft(status)",
    "CREATE INDEX IF NOT EXISTS herb_draft_name ON herb_draft(herb_name)",
    "CREATE INDEX IF NOT EXISTS herb_draft_patch_draft ON herb_draft_patch(draft_id)",
    "CREATE INDEX IF NOT EXISTS herb_draft_sample_batch ON herb_draft_sample(batch, verdict)",
)


def _staging_ddl() -> str:
    """staging 与 kg_edge 同构，抽取只写 staging，promote 是独立命令。"""

    return _EDGE_DDL.replace("kg_edge", "kg_edge_staging")


def ensure_schema(db_path: Path = DB_PATH) -> int:
    """建表 + 迁移。返回当前 schema 版本。

    幂等：可重复调用。不会删除任何已有表或数据。
    """

    key = Path(db_path)
    if key in _ENSURED:
        return SCHEMA_VERSION

    with closing(connect(key)) as conn:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        conn.execute(_ENTITY_DDL)
        conn.execute(_ALIAS_DDL)
        conn.execute(_EDGE_DDL)
        conn.execute(_staging_ddl())
        conn.execute(_ATTRIBUTION_DDL)
        conn.execute(_MAPPING_CACHE_DDL)
        conn.execute(_DRAFT_DDL)
        conn.execute(_DRAFT_PATCH_DDL)
        conn.execute(_DRAFT_SAMPLE_DDL)
        for statement in _INDEX_DDL:
            conn.execute(statement)
        if version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            version = SCHEMA_VERSION
        conn.commit()
    _ENSURED.add(key)
    return version


def reset_init_cache(db_path: Path | None = None) -> None:
    """表结构被外部改动（或库被重建）后清掉「已建表」标记。"""

    if db_path is None:
        _ENSURED.clear()
    else:
        _ENSURED.discard(Path(db_path))


def reset_db(db_path: Path = DB_PATH) -> None:
    """清空 KG 表（仅供测试与重建索引用，不动 knowledge_node / herb_relation）。

    **故意不清 ``herb_draft*``**：那三张表存的是人工复核的签字记录，不是可重建
    的索引。KG 索引重建十次也不该让「谁在什么时候看过哪味药」消失。
    """

    with closing(connect(db_path)) as conn:
        for table in (
            "kg_attribution",
            "kg_edge_staging",
            "kg_edge",
            "kg_alias",
            "kg_entity",
            "kg_mapping_cache",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()


__all__ = ["SCHEMA_VERSION", "ensure_schema", "reset_db", "reset_init_cache"]
