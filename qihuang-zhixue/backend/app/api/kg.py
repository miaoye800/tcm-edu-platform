"""知识图谱服务 API（/api/kg）。

与旧 ``/api/graph`` 的关系：``/api/graph`` 是「按中心节点取一张子图」的旧契约，
前端 ``GraphView.vue`` 依赖它；``/api/kg`` 是「图谱作为一个可查询服务」的新契约，
提供邻域、对比、处方审查、反向检索、GraphRAG 等能力。

两者并存：``/api/graph`` 内部改成调 ``/api/kg`` 的实现并适配回旧结构。
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.kg import entities as kg_entities
from app.kg import store
from app.kg.adapters import no_edge_brief, placeholder_for_unresolved, placeholder_graph, to_graph_content
from app.kg.ask import ask as kg_ask
from app.kg.compare import compare as kg_compare
from app.kg.compare import find_path as kg_find_path
from app.kg.prescription import review as review_prescription
from app.kg.reverse import reverse_lookup as kg_reverse_lookup
from app.kg.schema import ensure_schema
from app.kg.symptom_llm import map_case_to_syndromes
from app.kg.traverse import neighbors as kg_neighbors_impl
from app.profile.store import DB_PATH, connect

router = APIRouter(prefix="/api/kg", tags=["kg"])


def get_kg_db_path() -> Path:
    return DB_PATH


def _tree_point_total(db_path: Path) -> int:
    try:
        with closing(connect(db_path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM knowledge_node WHERE level='point'"
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _dangling_links(db_path: Path) -> int | None:
    """挂在「已不存在的知识节点」上的实体数。

    聚合计数只回答「有多少」，回答不了「对不对」。``tree_points_linked`` 数的是
    ``knowledge_node_id IS NOT NULL`` 的行——节点被删掉了它照样数进去。实测就漏过
    一味当归：它指向已被淘汰的 ``d12.s2.p1``，而真正存活的节点是 ``d17.s2.p1``。
    所以这里显式 LEFT JOIN 一次，把悬空引用报出来，让「对齐率」这个数字不能自欺。

    表不存在时返回 None（而不是 0）——「查不了」和「一条都没有」是两件事。
    """

    try:
        with closing(connect(db_path)) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM kg_entity e
                LEFT JOIN knowledge_node n ON n.id = e.knowledge_node_id
                WHERE e.knowledge_node_id IS NOT NULL AND n.id IS NULL
                """
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return None


@router.get("/health")
async def kg_health(db_path: Path = Depends(get_kg_db_path)) -> dict[str, object]:
    """图谱服务健康度。**验收用**：T3 必须为 0，orphan 必须可见，dangling 必须为 0。

    不要求鉴权：只返回聚合计数，不含任何用户数据。
    """

    ensure_schema(db_path)
    stats = store.stats(db_path)
    unlinked = kg_entities.tree_points_without_entity(db_path)
    tree_total = _tree_point_total(db_path)

    by_kind = stats["entities_by_kind"]
    by_status = stats["entities_by_status"]
    herbs_total = by_kind.get("herb", 0)
    herbs_orphan = by_status.get("orphan", 0)
    herbs_draft = by_status.get("draft", 0)

    return {
        "entities": {
            "total": stats["entities_total"],
            "by_kind": by_kind,
            "by_status": by_status,
        },
        "edges": {
            "total": stats["edges_total"],
            "by_tier": stats["edges_by_tier"],
            "pending": stats["edges_pending"],
        },
        "alignment": {
            "tree_points": tree_total,
            "tree_points_without_entity": len(unlinked),
            # 注意：这个数是「挂着节点 id 的实体」，含悬空引用，所以它 + without_entity
            # 可能比 tree_points 多；多出来的部分就是 dangling_links。
            "tree_points_linked": stats["entities_linked_to_tree"],
            "dangling_links": _dangling_links(db_path),
            # 三种药材要分开数，否则「教材没有」和「我们没抽」会被混成一个数字：
            #   with_monograph 有完整结构化条目（approved）
            #   draft          教材立了条目，但字段还没抽（点开能看到名字，看不到性味功效）
            #   orphan         教材没立条目（只在附药/别名里出现过）
            "herbs_with_monograph": herbs_total - herbs_orphan - herbs_draft,
            "herbs_draft": herbs_draft,
            "herbs_orphan": herbs_orphan,
        },
        # T3 相似边按设计不落库；>0 说明有人又把相似度写进图里了。
        "tier3_persisted": stats["tier3_persisted"],
        # 图里一条边都没有时，前端看到的只能是兜底 mock。
        "kg_ready": stats["edges_total"] > 0 and stats["entities_total"] > 0,
    }


@router.get("/search")
async def kg_search(
    q: str = Query(min_length=1, description="药名 / 别名 / 拼音 / id"),
    kind: str | None = Query(default=None, description="herb|syndrome|formula"),
    limit: int = Query(default=20, ge=1, le=100),
    _current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_kg_db_path),
) -> dict[str, object]:
    """实体搜索。返回命中方式，便于前端排序与排查。"""

    ensure_schema(db_path)
    hits = store.search_entities(q, kind=kind, limit=limit, db_path=db_path)
    return {
        "query": q,
        "items": [
            {
                "id": entity.id,
                "kind": entity.kind,
                "name": entity.name,
                "status": entity.status,
                "matched_by": matched_by,
            }
            for entity, matched_by in hits
        ],
    }


class AskRequest(BaseModel):
    """自然语言提问。"""

    question: str = Field(min_length=1, description="问题原文")
    hops: int = Field(default=1, ge=1, le=3, description="图扩展跳数")
    limit: int = Field(default=40, ge=5, le=120, description="节点上限")
    #: 上游已经做过向量检索时，直接把命中的实体 id 传进来。
    #: 传了就只按 id 起步，不再解析问题文本——否则等于把检索白做一遍，
    #: 而且问题措辞和实体名不一致时还会解析不出来。
    seed_ids: list[str] | None = Field(
        default=None, description="已知实体 id（来自 SearchHit.id），给了就不再解析问题"
    )
    #: 对照用：只做实体识别，不沿图扩展。默认 True——关掉就没有关系也没有原文，
    #: 只适合做「有/无图扩展」的对比演示，不适合当正式答案。
    expand: bool = Field(default=True, description="是否沿图谱扩展（False 仅用于对照演示）")


@router.post("/ask")
async def kg_ask_endpoint(
    payload: AskRequest,
    _current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_kg_db_path),
) -> dict[str, object]:
    """GraphRAG 问答：实体种子 → 图扩展 → 结构化上下文 + 确定性摘要。

    **默认不调模型**。能靠查到的教材原文说清楚的事，再让模型复述一遍只会引入
    幻觉。``context`` 是给前端/后续 LLM 用的原料，``citations`` 是每句话的出处。
    """

    ensure_schema(db_path)
    result = kg_ask(
        payload.question,
        db_path=db_path,
        hops=payload.hops,
        limit=payload.limit,
        seed_ids=payload.seed_ids,
        expand=payload.expand,
    )
    return result.as_dict()


class PrescriptionReviewRequest(BaseModel):
    """处方审查请求。

    两种输入都支持：``prescription`` 自由文本（"麻黄6g，桂枝6g"），
    或 ``herbs`` 结构化列表（``[{"name":"麻黄","dose":6}]``）。
    结构化优先——用户已经给清楚了就别再猜。
    """

    case: str = Field(default="", description="病例/症状描述")
    prescription: str = Field(default="", description="处方原文")
    herbs: list[dict[str, Any]] | None = Field(default=None, description="结构化药味")
    symptoms: str = ""
    scenario: str = ""


@router.post("/prescription/review")
async def kg_prescription_review(
    payload: PrescriptionReviewRequest,
    _current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_kg_db_path),
) -> dict[str, object]:
    """审查一张处方：十八反/十九畏（T1）、妊娠禁忌（T1）、剂量毒性、对症。

    「话 → 证」这一跳分四级，逐级放宽，``trace.candidates[].layer`` 会标明命中来源：

    - ``name``  证候名原样出现在病例里
    - ``alias`` 口语别名（嗓子疼 → 咽喉肿痛）
    - ``phrase`` 教材症状短语倒排（1029 条，从 299 条主治描述自动生成）
    - ``fuzzy`` 字符二元组重叠（「咳嗽有痰」≈「咳嗽痰多」）
    - ``llm``   前四级都没命中时，让模型**从证候清单里选**（闭集，不给自由生成）

    前四级同步、不调模型；只有到 ``llm`` 才会真的发请求，且没有 key 时直接跳过。
    每条 finding 都带 ``edge_ids``（指回 ``kg_edge``）和 ``evidence``（页码 + 原文）。
    """

    ensure_schema(db_path)
    payload_dict = payload.model_dump(exclude_none=True)
    result = review_prescription(payload_dict, db_path)

    # 前四级都没定位到证候，才值得花一次模型调用。定位到了就别浪费。
    if result.trace.get("layer") is None and not result.trace.get("candidates"):
        picks = await map_case_to_syndromes(payload.case, db_path=db_path)
        if picks:
            result = review_prescription(payload_dict, db_path, syndrome_hint=picks)

    return {
        "verdict": result.verdict,
        "symptom_match": result.symptom_match,
        "symptom_match_status": result.symptom_match_status,
        "compatible": result.compatible,
        "dosage_ok": result.dosage_ok,
        "toxic_misuse": result.toxic_misuse,
        "checks": result.checks,
        "findings": result.finding_dicts(),
        "herbs": [line.as_dict() for line in result.herbs],
        "knowledge_base_refs": result.knowledge_base_refs,
        "quote": result.quote,
        "trace": result.trace,
        "blocking": [item.as_dict() for item in result.blocking],
    }


@router.get("/neighbors/{center_id}")
async def kg_neighbors(
    center_id: str,
    hops: int = Query(default=1, ge=1, le=3, description="扩展跳数"),
    limit: int = Query(default=40, ge=5, le=120, description="节点上限"),
    tiers: str = Query(default="1,2", description="只取这些可信层的边，逗号分隔；T3 永不进图"),
    _current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_kg_db_path),
) -> dict[str, object]:
    """按中心节点取局部子图（P7 的落点，方案 §3.5 的 ``/api/kg/neighbors/{id}``）。

    返回结构仍是旧 ``GraphContent``（``center_id/nodes/edges/sources``），外加一个
    ``meta``。**这一点是 P7 能只改一个文件的前提**：``GraphView.vue`` 与
    ``config/graph.ts`` 读的是那四个字段，适配在 ``adapters.to_graph_content`` 里做完了。

    三个必须说清楚的行为：

    1. 解析不到中心节点时返回「教材未收录」单节点，**不返回 404**。404 会让
       ``stores/graph.ts`` 回落 15 个硬编码节点的麻黄假图——宁可显示「没有」，
       也不要显示「假的」。
    2. 节点存在但一条边都没有时，``brief`` 说明缺的是什么（未收录 / 待抽取 /
       暂无关系），三种情况不能混成一句话。
    3. ``tiers`` 传 ``3`` 不会 400，只会拿到空集合：T3 相似聚类按设计不落库
       （``/api/kg/health`` 的 ``tier3_persisted`` 就是它的自检位）。
    """

    ensure_schema(db_path)
    tier_set = _parse_tiers(tiers)

    entity = kg_entities.resolve(center_id, db_path)
    if entity is None:
        payload = placeholder_for_unresolved(center_id, db_path)
        payload["meta"] = {
            "using_mock": False,
            "center_resolved": False,
            "tiers": list(tier_set),
            "tier_counts": {},
            "truncated": False,
            "total_nodes": 0,
        }
        return payload

    subgraph = kg_neighbors_impl(entity.id, hops=hops, limit=limit, tiers=tier_set, db_path=db_path)
    if not subgraph.edges:
        payload = placeholder_graph(entity.id, db_path, brief=no_edge_brief(entity))
        payload["meta"] = {
            "using_mock": False,
            "center_resolved": True,
            "tiers": list(tier_set),
            "tier_counts": {},
            "truncated": False,
            "total_nodes": len(payload.get("nodes") or []),
        }
        return payload

    payload = to_graph_content(subgraph, db_path)
    # 按层计数交给前端做筛选器角标，不必让它自己 group 一遍再算。
    counts: dict[str, int] = {}
    for edge in payload["edges"]:
        key = str(edge.get("tier"))
        counts[key] = counts.get(key, 0) + 1
    payload["meta"] = {
        "using_mock": False,
        "center_resolved": True,
        "tiers": list(tier_set),
        "tier_counts": counts,
        # 前端要显示「已隐藏 N 条」。``truncated`` 只说砍过，说不出砍了几条，
        # 而前端按 ``limit`` 反推是错的（邻域可能本来就不够 limit）。
        # 两个都给，让「隐藏」这个数字有出处。
        "truncated": bool(subgraph.truncated),
        "total_nodes": int(subgraph.total_nodes or len(payload["nodes"])),
    }
    return payload


@router.get("/entity/{entity_id}")
async def kg_entity_detail(
    entity_id: str,
    tiers: str = Query(default="1,2", description="只取这些层的关系"),
    _current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_kg_db_path),
) -> dict[str, object]:
    """单实体详情 + **分 tier 的边** + 每条边的教材依据（方案 §3.5）。

    与 ``/api/graph/node/{id}/detail`` 的分工：那条是**前端详情面板**的旧契约
    （中文 key 的 ``性味归经``/``功效主治``），返回的是「给人看的字段」；
    这条是**图谱服务**的视图，返回的是「图里连了什么、每条连线的依据是什么」。

    为什么必须分 tier 返回，而不是给一锅平铺的边：``同类鉴别`` 和 ``十八反``
    在界面上长得一样，但在决策里权重差一个数量级——前者是「考试会考的辨析」，
    后者是「不能一起开」。混在一起，调用方只能靠关系类型字符串自己判，
    而那正是重构要消灭的「把语义塞进字符串」。
    """

    ensure_schema(db_path)
    tier_set = _parse_tiers(tiers)
    entity = kg_entities.resolve(entity_id, db_path)
    if entity is None:
        raise HTTPException(status_code=404, detail="图谱里没有这个实体（教材未收录）")

    edges = store.edges_of(entity.id, tiers=tier_set, db_path=db_path)
    by_tier: dict[str, list[dict[str, object]]] = {str(tier): [] for tier in tier_set}
    for edge in edges:
        peer = edge.target_id if edge.source_id == entity.id else edge.source_id
        by_tier.setdefault(str(edge.tier), []).append(
            {
                "edge_id": edge.id,
                # direction 让调用方不必自己去比 source_id：反查「谁指向我」
                #（比如十八反的「藜芦反人参」只写了一个方向）全靠这个字段。
                "direction": "out" if edge.source_id == entity.id else "in",
                "peer": {"id": peer, "name": _entity_name(peer, db_path)},
                "relation_type": edge.relation_type,
                "label": edge.label,
                "confidence": edge.confidence,
                "evidence": edge.evidence,
            }
        )

    aliases = store.aliases_of(entity.id, db_path)
    return {
        "id": entity.id,
        "name": entity.name,
        "kind": entity.kind,
        "status": entity.status,
        "pinyin": entity.pinyin,
        "canonical_id": entity.canonical_id,
        "knowledge_node_id": entity.knowledge_node_id,
        "aliases": aliases,
        "edges": by_tier,
        "edge_counts": {key: len(value) for key, value in by_tier.items()},
        "tiers": list(tier_set),
    }


def _entity_name(entity_id: str, db_path: Path) -> str:
    entity = store.get_entity(entity_id, db_path)
    return entity.name if entity else entity_id


@router.get("/reverse")
async def kg_reverse(
    target: str = Query(min_length=1, description="症状短语或证候名，如「咽喉肿痛」"),
    top_k: int = Query(default=3, ge=1, le=5, description="候选证候数量"),
    _current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_kg_db_path),
) -> dict[str, object]:
    """反向检索：症 → 证 → 方 → 药（方案 §0 第 2 类问题）。

    全程查表，不调模型。**候选证候为空就返回空**，并说明「教材词表对不上」——
    模型在这种场景下不会说「不知道」，它会就近编一个证，而那个证一旦进到处方
    审查里，错的就不是一条展示，是一次开方。
    """

    ensure_schema(db_path)
    return kg_reverse_lookup(target, top_k=top_k, db_path=db_path)


def _parse_tiers(raw: str) -> tuple[int, ...]:
    """``"1,2"`` → ``(1, 2)``。非法值退回默认的 T1+T2，而不是抛 400。

    T3 是运行时相似度，按设计不落库（``tier3_persisted`` 就是它的自检位）；
    即使有人传 ``3`` 也只会拿到空集合，这里显式剔掉，让「请求 T3」变成
    「没有 T3 边」而不是「服务报错」。
    """

    parsed: list[int] = []
    for token in str(raw).split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            continue
        if value in (1, 2) and value not in parsed:
            parsed.append(value)
    return tuple(sorted(parsed)) or (1, 2)


@router.get("/compare")
async def kg_compare_endpoint(
    left: str = Query(min_length=1, description="左：药名 / 别名 / id"),
    right: str = Query(min_length=1, description="右：药名 / 别名 / id"),
    _current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_kg_db_path),
) -> dict[str, object]:
    """两药对比：逐项结构差异 + 教材明文鉴别句 + 共同关系 + T1 约束。

    **不调模型**。差异是可枚举的，教材原文里也有现成的鉴别句，
    让模型重写一遍只会引入不可溯源的表述。
    """

    ensure_schema(db_path)
    return kg_compare(left, right, db_path=db_path)


@router.get("/path")
async def kg_path_endpoint(
    from_id: str = Query(min_length=1, description="起点实体"),
    to_id: str = Query(min_length=1, description="终点实体"),
    max_hops: int = Query(default=4, ge=1, le=6),
    _current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_kg_db_path),
) -> dict[str, object]:
    """两实体间最短关系链。回答「这两味药/证/方是怎么扯上关系的」。"""

    ensure_schema(db_path)
    return kg_find_path(from_id, to_id, max_hops=max_hops, db_path=db_path)
