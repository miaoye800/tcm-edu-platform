"""纯图算法：BFS 邻域、最短路径、T3 相似邻居。

**这一层不碰数据库之外的东西，也不做业务判断。** 它只回答「图里谁连着谁」。
业务语义（这条边能不能用来答某个问题）放 ``query.py``。

T3 相似边在这里**运行时计算**，不落库。原因：T3 是 ``Herb`` 属性的纯函数，
落库就必然和属性不同步——旧的 915 条「同归经/同功效/同性味」边就是这么来的，
它们占了原图 58% 却回答不了任何一个真实问题。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from app.kg import store
from app.kg.constants import Tier, tier_of
from app.kg.store import KgEdge, KgEntity
from app.kb.ingest import load_herbs
from app.profile.store import DB_PATH

#: 药材属性索引缓存。知识库 JSON 只在进程内读一次。
_HERB_INDEX: dict[str, dict[str, object]] | None = None


@dataclass(frozen=True)
class SimilarityHit:
    entity_id: str
    name: str
    relation_type: str
    label: str
    score: float


@dataclass
class Subgraph:
    center_id: str
    entities: list[KgEntity] = field(default_factory=list)
    edges: list[KgEdge] = field(default_factory=list)
    depth: dict[str, int] = field(default_factory=dict)
    truncated: bool = False
    #: 截断前一共有几个节点。``truncated`` 只说「砍过」，说不出砍了多少——
    #: 前端要显示「已隐藏 N 条」就必须知道这个数，靠 ``limit`` 反推是错的
    #: （真实邻域可能比 limit 小，那样一条都没隐藏）。
    total_nodes: int = 0

    @property
    def node_count(self) -> int:
        return len(self.entities)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    @property
    def hidden_nodes(self) -> int:
        return max(0, self.total_nodes - len(self.entities))


def _herb_index() -> dict[str, dict[str, object]]:
    global _HERB_INDEX
    if _HERB_INDEX is None:
        _HERB_INDEX = {
            herb.id: {
                "name": herb.name,
                "effects": set(herb.effects),
                "meridians": set(herb.meridians),
                "nature": str(herb.nature),
                "flavor": {str(item) for item in herb.flavor},
                "subcategory": herb.subcategory,
            }
            for herb in load_herbs()
        }
    return _HERB_INDEX


def reset_similarity_cache() -> None:
    """知识库变更后清缓存，避免继续用旧属性算相似度。"""

    global _HERB_INDEX
    _HERB_INDEX = None


def similar_neighbors(
    entity_id: str,
    *,
    limit: int = 12,
    relation_types: Sequence[str] | None = None,
    db_path: Path = DB_PATH,
) -> list[SimilarityHit]:
    """按药材属性算 T3 相似邻居。**不写库**，纯函数。

    相似度构成（与旧 ``_rule_relations`` 同口径，但不再持久化）：
    - 功效交集：每个共享功效 0.35
    - 归经交集：每个共享归经 0.20
    - 性同 + 味有交集：0.25
    """

    entity = store.get_entity(entity_id, db_path)
    if entity is None or entity.kind != "herb":
        return []

    index = _herb_index()
    center = index.get(entity.id)
    if center is None:
        # 没有 monograph 的药材（orphan）算不出属性相似度——这是诚实的空结果。
        return []

    allowed = set(relation_types) if relation_types else None
    hits: list[SimilarityHit] = []
    for other_id, other in index.items():
        if other_id == entity.id:
            continue
        score = 0.0
        labels: list[tuple[str, str]] = []

        shared_effects = sorted(center["effects"] & other["effects"])  # type: ignore[operator]
        if shared_effects:
            score += 0.35 * min(len(shared_effects), 2)
            labels.append(("功效相通", f"功效相通·{shared_effects[0]}"))
        shared_meridians = sorted(center["meridians"] & other["meridians"])  # type: ignore[operator]
        if shared_meridians:
            score += 0.20 * min(len(shared_meridians), 2)
            labels.append(("归经聚类", f"归经聚类·{shared_meridians[0]}经"))
        shared_flavor = center["flavor"] & other["flavor"]  # type: ignore[operator]
        if center["nature"] == other["nature"] and shared_flavor:
            score += 0.25
            labels.append(
                ("性味相通", f"性味相通·{center['nature']}{sorted(shared_flavor)[0]}")
            )

        if not labels:
            continue
        relation_type, label = max(labels, key=lambda item: _type_rank(item[0]))
        if allowed is not None and not ({t for t, _ in labels} & allowed):
            continue
        hits.append(
            SimilarityHit(
                entity_id=other_id,
                name=str(other["name"]),
                relation_type=relation_type,
                label=label,
                score=round(min(score, 1.0), 3),
            )
        )

    hits.sort(key=lambda hit: (-hit.score, hit.entity_id))
    return hits[:limit]


def _type_rank(relation_type: str) -> int:
    """多条相似理由并存时，优先报更强的那个：功效 > 性味 > 归经。"""

    return {"功效相通": 3, "性味相通": 2, "归经聚类": 1}.get(relation_type, 0)


def neighbors(
    center_id: str,
    *,
    hops: int = 1,
    tiers: Sequence[int] = (int(Tier.CONSTRAINT), int(Tier.FACT)),
    limit: int = 60,
    db_path: Path = DB_PATH,
) -> Subgraph:
    """从中心实体 BFS 取局部子图。只走指定 tier，默认 T1+T2。

    每跳一次 SQL（``edges_touching``），depth ≤ 3 时开销可忽略。
    """

    hops = max(1, min(int(hops), 3))
    center = store.get_entity(center_id, db_path)
    if center is None:
        return Subgraph(center_id=center_id)

    depth: dict[str, int] = {center_id: 0}
    frontier: list[str] = [center_id]
    collected: dict[int, KgEdge] = {}

    for level in range(1, hops + 1):
        if not frontier:
            break
        edges = store.edges_touching(frontier, tiers=tiers, db_path=db_path)
        next_frontier: list[str] = []
        for edge in edges:
            if edge.id is not None:
                collected.setdefault(edge.id, edge)
            for node_id in (edge.source_id, edge.target_id):
                if node_id not in depth:
                    depth[node_id] = level
                    next_frontier.append(node_id)
        frontier = next_frontier

    # 超过 limit 时优先保留：中心 > 跳数近 > tier 硬 > 置信度高。
    ordered_nodes = sorted(depth, key=lambda nid: (depth[nid], nid))
    truncated = len(ordered_nodes) > limit
    kept_nodes = set(ordered_nodes[:limit])

    kept_edges = [
        edge
        for edge in collected.values()
        if edge.source_id in kept_nodes and edge.target_id in kept_nodes
    ]
    kept_edges.sort(key=lambda e: (e.tier, -e.confidence, e.id or 0))

    # 一次查全，不逐个 ``get_entity``：一圈几十个节点就是几十次建连，
    # 而每次关闭连接的开销比查询本身大两个数量级（详见 ``store.get_entities``）。
    wanted = [node_id for node_id in ordered_nodes if node_id in kept_nodes]
    found = store.get_entities(wanted, db_path)
    entities = [found[node_id] for node_id in wanted if node_id in found]

    return Subgraph(
        center_id=center_id,
        entities=entities,
        edges=kept_edges,
        depth={nid: depth[nid] for nid in kept_nodes},
        truncated=truncated,
        total_nodes=len(ordered_nodes),
    )


def shortest_path(
    from_id: str,
    to_id: str,
    *,
    max_hops: int = 4,
    tiers: Sequence[int] = (int(Tier.CONSTRAINT), int(Tier.FACT)),
    db_path: Path = DB_PATH,
) -> list[KgEdge]:
    """最短路径（BFS，等权）。找不到返回空列表。

    等权是有意的：真实场景里「麻黄 → 麻黄汤 → 风寒表实证」这类路径不该因为
    某条边置信度低就绕远路。要按权重排序时再引入 Dijkstra。
    """

    max_hops = max(1, min(int(max_hops), 6))
    if from_id == to_id:
        return []
    if store.get_entity(from_id, db_path) is None or store.get_entity(to_id, db_path) is None:
        return []

    # parent[node] = (prev_node, edge)
    parent: dict[str, tuple[str, KgEdge]] = {}
    visited = {from_id}
    frontier = [from_id]

    for _ in range(max_hops):
        if not frontier:
            break
        edges = store.edges_touching(frontier, tiers=tiers, db_path=db_path)
        next_frontier: list[str] = []
        for edge in edges:
            for src, dst in ((edge.source_id, edge.target_id), (edge.target_id, edge.source_id)):
                if src in visited and dst not in visited:
                    visited.add(dst)
                    parent[dst] = (src, edge)
                    if dst == to_id:
                        return _rebuild_path(parent, from_id, to_id)
                    next_frontier.append(dst)
        frontier = next_frontier
    return []


def _rebuild_path(
    parent: dict[str, tuple[str, KgEdge]], from_id: str, to_id: str
) -> list[KgEdge]:
    path: list[KgEdge] = []
    cursor = to_id
    while cursor != from_id and cursor in parent:
        prev, edge = parent[cursor]
        path.append(edge)
        cursor = prev
    path.reverse()
    return path


def tier_distribution(edges: Iterable[KgEdge]) -> dict[str, int]:
    counts: dict[str, int] = {f"T{t}": 0 for t in (1, 2, 3)}
    for edge in edges:
        key = f"T{int(tier_of(edge.relation_type))}"
        counts[key] = counts.get(key, 0) + 1
    return counts


__all__ = [
    "SimilarityHit",
    "Subgraph",
    "similar_neighbors",
    "neighbors",
    "shortest_path",
    "tier_distribution",
    "reset_similarity_cache",
]
