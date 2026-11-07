"""KG 数据访问层：连接、实体、别名、边。

只做 CRUD 与索引查询，不含业务判断。业务逻辑放 ``query.py`` / ``service.py``，
纯图算法放 ``traverse.py``。
"""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.kg.constants import Tier
from app.kg.schema import ensure_schema
from app.profile.store import DB_PATH, connect


@dataclass(frozen=True)
class KgEntity:
    id: str
    kind: str
    name: str
    pinyin: str = ""
    canonical_id: str | None = None
    knowledge_node_id: str | None = None
    status: str = "approved"
    payload_ref: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_orphan(self) -> bool:
        return self.status == "orphan"


@dataclass(frozen=True)
class KgEdge:
    source_id: str
    target_id: str
    relation_type: str
    tier: int
    label: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.8
    derived: int = 0
    status: str = "approved"
    meta: dict[str, Any] = field(default_factory=dict)
    id: int | None = None


def _entity_from_row(row: Sequence[Any]) -> KgEntity:
    return KgEntity(
        id=str(row[0]),
        kind=str(row[1]),
        name=str(row[2]),
        pinyin=str(row[3] or ""),
        canonical_id=row[4],
        knowledge_node_id=row[5],
        status=str(row[6]),
        payload_ref=row[7],
        extra=json.loads(row[8] or "{}"),
    )


def _edge_from_row(row: Sequence[Any]) -> KgEdge:
    return KgEdge(
        id=int(row[0]),
        source_id=str(row[1]),
        target_id=str(row[2]),
        relation_type=str(row[3]),
        tier=int(row[4]),
        label=str(row[5]),
        evidence=json.loads(row[6] or "[]"),
        confidence=float(row[7]),
        derived=int(row[8]),
        status=str(row[9]),
        meta=json.loads(row[10] or "{}"),
    )


_ENTITY_COLUMNS = (
    "id, kind, name, pinyin, canonical_id, knowledge_node_id, status, payload_ref, extra_json"
)
_EDGE_COLUMNS = (
    "id, source_id, target_id, relation_type, tier, label, evidence_json, "
    "confidence, derived, status, meta_json"
)


# --------------------------------------------------------------------------- #
# 实体
# --------------------------------------------------------------------------- #


def upsert_entity(entity: KgEntity, db_path: Path = DB_PATH) -> None:
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO kg_entity(id, kind, name, pinyin, canonical_id, knowledge_node_id,
                                  status, payload_ref, extra_json, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                kind=excluded.kind, name=excluded.name, pinyin=excluded.pinyin,
                canonical_id=excluded.canonical_id, knowledge_node_id=excluded.knowledge_node_id,
                status=excluded.status, payload_ref=excluded.payload_ref,
                extra_json=excluded.extra_json, updated_at=datetime('now')
            """,
            (
                entity.id,
                entity.kind,
                entity.name,
                entity.pinyin,
                entity.canonical_id,
                entity.knowledge_node_id,
                entity.status,
                entity.payload_ref,
                json.dumps(entity.extra, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.commit()


def upsert_entities(entities: Iterable[KgEntity], db_path: Path = DB_PATH) -> int:
    ensure_schema(db_path)
    rows = [
        (
            e.id,
            e.kind,
            e.name,
            e.pinyin,
            e.canonical_id,
            e.knowledge_node_id,
            e.status,
            e.payload_ref,
            json.dumps(e.extra, ensure_ascii=False, sort_keys=True),
        )
        for e in entities
    ]
    if not rows:
        return 0
    with closing(connect(db_path)) as conn:
        conn.executemany(
            """
            INSERT INTO kg_entity(id, kind, name, pinyin, canonical_id, knowledge_node_id,
                                  status, payload_ref, extra_json, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                kind=excluded.kind, name=excluded.name, pinyin=excluded.pinyin,
                canonical_id=excluded.canonical_id, knowledge_node_id=excluded.knowledge_node_id,
                status=excluded.status, payload_ref=excluded.payload_ref,
                extra_json=excluded.extra_json, updated_at=datetime('now')
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def get_entity(entity_id: str, db_path: Path = DB_PATH) -> KgEntity | None:
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            f"SELECT {_ENTITY_COLUMNS} FROM kg_entity WHERE id=?", (entity_id,)
        ).fetchone()
    return _entity_from_row(row) if row else None


def get_entities(
    entity_ids: Sequence[str], db_path: Path = DB_PATH
) -> dict[str, KgEntity]:
    """批量取实体，**一次建连**。

    ``neighbors()`` 一跳能带出几十个节点，逐个 ``get_entity`` 就是几十次建连 —
    实测关闭连接比查询本身贵得多（一次 19ms），70 个节点光关连接就 1.4 秒，
    而整条 GraphRAG 链路给它的预算是 2 秒。批量取是这里唯一划得来的做法。
    """

    ids = list(dict.fromkeys(str(item) for item in entity_ids if item))
    if not ids:
        return {}
    ensure_schema(db_path)
    placeholders = ",".join("?" * len(ids))
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            f"SELECT {_ENTITY_COLUMNS} FROM kg_entity WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
    return {str(row[0]): _entity_from_row(row) for row in rows}


def find_by_name(
    name: str, *, kind: str | None = None, db_path: Path = DB_PATH
) -> list[KgEntity]:
    """按显示名精确查找。``kind`` 设为 keyword-only，避免把 db_path 误传成 kind。"""

    ensure_schema(db_path)
    sql = f"SELECT {_ENTITY_COLUMNS} FROM kg_entity WHERE name=?"
    params: list[Any] = [name]
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    # 同名多类型时药材优先，其余按 kind 字典序，保证结果稳定。
    sql += " ORDER BY CASE kind WHEN 'herb' THEN 0 WHEN 'formula' THEN 1 WHEN 'syndrome' THEN 2 ELSE 3 END"
    with closing(connect(db_path)) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_entity_from_row(row) for row in rows]


def find_by_alias(alias: str, db_path: Path = DB_PATH) -> list[KgEntity]:
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            f"""SELECT {', '.join('e.' + c.strip() for c in _ENTITY_COLUMNS.split(','))}
                FROM kg_alias a JOIN kg_entity e ON e.id = a.entity_id
                WHERE a.alias=?""",
            (alias,),
        ).fetchall()
    return [_entity_from_row(row) for row in rows]


def find_by_knowledge_node(
    node_id: str, db_path: Path = DB_PATH
) -> list[KgEntity]:
    """按 ``knowledge_node.id`` 反查实体——考点到图谱的**直连**。

    错题归因里这条必须优先于按名字解析：``exam_point.node_id`` 和
    ``kg_entity.knowledge_node_id`` 是建库时写进去的外键，同名不同物也串不了；
    而按名字查「紫苏」时，图谱里只有「紫苏叶」，靠 ``kg_alias`` 才碰得上——
    那是兜底，不是首选。
    """

    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            f"SELECT {_ENTITY_COLUMNS} FROM kg_entity WHERE knowledge_node_id=?"
            " ORDER BY CASE kind WHEN 'herb' THEN 0 WHEN 'formula' THEN 1"
            " WHEN 'syndrome' THEN 2 ELSE 3 END",
            (node_id,),
        ).fetchall()
    return [_entity_from_row(row) for row in rows]


def upsert_alias(alias: str, entity_id: str, source: str = "", db_path: Path = DB_PATH) -> None:
    ensure_schema(db_path)
    alias = alias.strip()
    if not alias:
        return
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO kg_alias(alias, entity_id, source) VALUES (?,?,?)",
            (alias, entity_id, source),
        )
        conn.commit()


def upsert_aliases(
    pairs: Iterable[tuple[str, str, str]], db_path: Path = DB_PATH
) -> int:
    rows = [(a.strip(), e, s) for a, e, s in pairs if a and a.strip()]
    if not rows:
        return 0
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO kg_alias(alias, entity_id, source) VALUES (?,?,?)", rows
        )
        conn.commit()
    return len(rows)


def aliases_of(entity_id: str, db_path: Path = DB_PATH) -> list[str]:
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT alias FROM kg_alias WHERE entity_id=? ORDER BY alias", (entity_id,)
        ).fetchall()
    return [str(row[0]) for row in rows]


def search_entities(
    query: str, kind: str | None = None, limit: int = 20, db_path: Path = DB_PATH
) -> list[tuple[KgEntity, str]]:
    """按 名字 / 别名 / 拼音 / id 匹配，返回 (实体, 命中方式)。

    命中方式用于前端排序与调试：``name`` > ``alias`` > ``pinyin`` > ``id``。
    """

    ensure_schema(db_path)
    q = query.strip()
    if not q:
        return []
    lowered = q.lower()
    compact = lowered.replace(" ", "")
    kind_clause = " AND kind=?" if kind else ""
    kind_params: tuple[Any, ...] = (kind,) if kind else ()

    with closing(connect(db_path)) as conn:
        exact_name = conn.execute(
            f"SELECT {_ENTITY_COLUMNS} FROM kg_entity WHERE name=?{kind_clause}",
            (q, *kind_params),
        ).fetchall()
        exact_alias = conn.execute(
            f"""SELECT {', '.join('e.' + c.strip() for c in _ENTITY_COLUMNS.split(','))}
                FROM kg_alias a JOIN kg_entity e ON e.id = a.entity_id
                WHERE a.alias=?{(' AND e.kind=?' if kind else '')}""",
            (q, *kind_params),
        ).fetchall()
        by_id = conn.execute(
            f"SELECT {_ENTITY_COLUMNS} FROM kg_entity WHERE id=?{kind_clause}",
            (q, *kind_params),
        ).fetchall()
        fuzzy = conn.execute(
            f"""SELECT {_ENTITY_COLUMNS} FROM kg_entity
                WHERE (name LIKE ? OR pinyin LIKE ? OR replace(pinyin,' ','') LIKE ?){kind_clause}
                LIMIT ?""",
            (f"%{q}%", f"%{lowered}%", f"%{compact}%", *kind_params, limit),
        ).fetchall()

    ordered: list[tuple[KgEntity, str]] = []
    seen: set[str] = set()
    for rows, source in (
        (exact_name, "name"),
        (exact_alias, "alias"),
        (by_id, "id"),
        (fuzzy, "fuzzy"),
    ):
        for row in rows:
            entity = _entity_from_row(row)
            if entity.id in seen:
                continue
            seen.add(entity.id)
            ordered.append((entity, source))
            if len(ordered) >= limit:
                return ordered
    return ordered


def list_entities(
    kind: str | None = None, status: str | None = None, db_path: Path = DB_PATH
) -> list[KgEntity]:
    ensure_schema(db_path)
    sql = f"SELECT {_ENTITY_COLUMNS} FROM kg_entity WHERE 1=1"
    params: list[Any] = []
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY kind, name"
    with closing(connect(db_path)) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_entity_from_row(row) for row in rows]


# --------------------------------------------------------------------------- #
# 边
# --------------------------------------------------------------------------- #


def upsert_edge(edge: KgEdge, db_path: Path = DB_PATH) -> int | None:
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO kg_edge(source_id, target_id, relation_type, tier, label, evidence_json,
                                confidence, derived, status, created_at, meta_json)
            VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),?)
            ON CONFLICT(source_id, target_id, relation_type, label) DO UPDATE SET
                tier=excluded.tier, evidence_json=excluded.evidence_json,
                confidence=excluded.confidence, derived=excluded.derived,
                status=excluded.status, meta_json=excluded.meta_json
            """,
            (
                edge.source_id,
                edge.target_id,
                edge.relation_type,
                int(edge.tier),
                edge.label,
                json.dumps(edge.evidence, ensure_ascii=False),
                edge.confidence,
                int(edge.derived),
                edge.status,
                json.dumps(edge.meta, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid) if cursor.lastrowid else None


def upsert_edges(edges: Iterable[KgEdge], db_path: Path = DB_PATH) -> int:
    ensure_schema(db_path)
    rows = [
        (
            e.source_id,
            e.target_id,
            e.relation_type,
            int(e.tier),
            e.label,
            json.dumps(e.evidence, ensure_ascii=False),
            e.confidence,
            int(e.derived),
            e.status,
            json.dumps(e.meta, ensure_ascii=False, sort_keys=True),
        )
        for e in edges
    ]
    if not rows:
        return 0
    with closing(connect(db_path)) as conn:
        conn.executemany(
            """
            INSERT INTO kg_edge(source_id, target_id, relation_type, tier, label, evidence_json,
                                confidence, derived, status, created_at, meta_json)
            VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),?)
            ON CONFLICT(source_id, target_id, relation_type, label) DO UPDATE SET
                tier=excluded.tier, evidence_json=excluded.evidence_json,
                confidence=excluded.confidence, derived=excluded.derived,
                status=excluded.status, meta_json=excluded.meta_json
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def edges_of(
    entity_id: str,
    *,
    tiers: Sequence[int] | None = None,
    statuses: Sequence[str] | None = ("approved",),
    db_path: Path = DB_PATH,
) -> list[KgEdge]:
    """取某实体的全部边（双向），可按 tier / status 过滤。"""

    ensure_schema(db_path)
    sql = f"SELECT {_EDGE_COLUMNS} FROM kg_edge WHERE (source_id=? OR target_id=?)"
    params: list[Any] = [entity_id, entity_id]
    if tiers:
        sql += f" AND tier IN ({','.join('?' * len(tiers))})"
        params.extend(int(t) for t in tiers)
    if statuses:
        sql += f" AND status IN ({','.join('?' * len(statuses))})"
        params.extend(statuses)
    sql += " ORDER BY tier, confidence DESC, id"
    with closing(connect(db_path)) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_edge_from_row(row) for row in rows]


def edges_touching(
    entity_ids: Sequence[str],
    *,
    tiers: Sequence[int] | None = (1, 2),
    statuses: Sequence[str] | None = ("approved",),
    db_path: Path = DB_PATH,
) -> list[KgEdge]:
    """取「至少一端落在给定集合里」的边。BFS 每跳只需一次查询。"""

    ids = [str(item) for item in entity_ids if item]
    if not ids:
        return []
    ensure_schema(db_path)
    placeholders = ",".join("?" * len(ids))
    sql = (
        f"SELECT {_EDGE_COLUMNS} FROM kg_edge "
        f"WHERE (source_id IN ({placeholders}) OR target_id IN ({placeholders}))"
    )
    params: list[Any] = [*ids, *ids]
    if tiers:
        sql += f" AND tier IN ({','.join('?' * len(tiers))})"
        params.extend(int(t) for t in tiers)
    if statuses:
        sql += f" AND status IN ({','.join('?' * len(statuses))})"
        params.extend(statuses)
    sql += " ORDER BY tier, confidence DESC, id"
    with closing(connect(db_path)) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_edge_from_row(row) for row in rows]


def get_edge(edge_id: int, db_path: Path = DB_PATH) -> KgEdge | None:
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            f"SELECT {_EDGE_COLUMNS} FROM kg_edge WHERE id=?", (edge_id,)
        ).fetchone()
    return _edge_from_row(row) if row else None


def find_edge(
    source_id: str,
    target_id: str,
    relation_types: Sequence[str] | None = None,
    db_path: Path = DB_PATH,
) -> list[KgEdge]:
    """查两实体之间的边（两个方向都查）。处方审查的 T1 命中靠这个。"""

    ensure_schema(db_path)
    sql = f"""SELECT {_EDGE_COLUMNS} FROM kg_edge
              WHERE ((source_id=? AND target_id=?) OR (source_id=? AND target_id=?))
              AND status='approved'"""
    params: list[Any] = [source_id, target_id, target_id, source_id]
    if relation_types:
        sql += f" AND relation_type IN ({','.join('?' * len(relation_types))})"
        params.extend(relation_types)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_edge_from_row(row) for row in rows]


# --------------------------------------------------------------------------- #
# 统计（/api/kg/health 用）
# --------------------------------------------------------------------------- #


def stats(db_path: Path = DB_PATH) -> dict[str, Any]:
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        by_kind = {
            str(kind): int(count)
            for kind, count in conn.execute(
                "SELECT kind, COUNT(*) FROM kg_entity GROUP BY kind ORDER BY kind"
            )
        }
        by_status = {
            str(status): int(count)
            for status, count in conn.execute(
                "SELECT status, COUNT(*) FROM kg_entity GROUP BY status"
            )
        }
        by_tier = {
            f"T{tier}": int(count)
            for tier, count in conn.execute(
                "SELECT tier, COUNT(*) FROM kg_edge GROUP BY tier ORDER BY tier"
            )
        }
        edges_total = int(conn.execute("SELECT COUNT(*) FROM kg_edge").fetchone()[0])
        pending_edges = int(
            conn.execute("SELECT COUNT(*) FROM kg_edge WHERE status='pending'").fetchone()[0]
        )
        linked = int(
            conn.execute(
                "SELECT COUNT(*) FROM kg_entity WHERE knowledge_node_id IS NOT NULL"
            ).fetchone()[0]
        )
    return {
        "entities_total": sum(by_kind.values()),
        "entities_by_kind": by_kind,
        "entities_by_status": by_status,
        "edges_total": edges_total,
        "edges_by_tier": {f"T{t}": by_tier.get(f"T{t}", 0) for t in (1, 2, 3)},
        "edges_pending": pending_edges,
        "entities_linked_to_tree": linked,
        "tier3_persisted": by_tier.get(f"T{int(Tier.SIMILARITY)}", 0),
    }


__all__ = [
    "KgEntity",
    "KgEdge",
    "upsert_entity",
    "upsert_entities",
    "get_entity",
    "find_by_name",
    "find_by_alias",
    "find_by_knowledge_node",
    "upsert_alias",
    "upsert_aliases",
    "aliases_of",
    "search_entities",
    "list_entities",
    "upsert_edge",
    "upsert_edges",
    "edges_of",
    "edges_touching",
    "get_edge",
    "find_edge",
    "stats",
]
