"""药材知识库检索。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.kb.embedding import EmbeddingClient
from app.kb.ingest import load_herbs
from app.kb.models import Herb
from app.kb.store import get_collection


MOCK_SEMANTIC_CONCEPTS: dict[str, tuple[float, tuple[str, ...]]] = {
    "release_wind_cold": (
        2.4,
        ("发汗解表", "解表散寒", "发散风寒", "辛温解表", "风寒表实", "风寒"),
    ),
    "clear_heat_drain_fire": (
        2.8,
        ("清热泻火", "泻火清热", "清热泻火药", "清肺胃热", "清肝泻火"),
    ),
    "high_fever_thirst": (
        2.4,
        ("高热烦渴", "热病烦渴", "气分实热", "除烦止渴", "烦渴", "大热"),
    ),
    "tonify_qi_spleen": (
        2.8,
        ("补气健脾", "健脾益气", "益气健脾", "补脾益气", "补脾肺气", "补脾益肺", "脾气不足", "脾气虚", "脾虚"),
    ),
    "fatigue": (
        1.8,
        ("乏力", "倦怠", "体倦", "气短", "少气", "神疲"),
    ),
    "promote_urination_edema": (
        2.6,
        ("利水消肿", "利水退肿", "利水", "利尿", "燥湿利水", "水肿", "退肿", "风水"),
    ),
}

MOCK_REJECT_CONCEPTS = {"antibiotic": ("抗生素", "消炎药", "头孢", "阿莫西林")}


@dataclass(frozen=True)
class SearchHit:
    """RAG 检索命中结果。

    ``id`` 是**图谱实体 id**（``herb_huangqi``），不是名字。没有它有两个后果：

    1. 只能按名字反查知识库，一旦出现同名不同物（炮制品、地方用药）就串了；
    2. **没法从检索结果接着沿图扩展**——GraphRAG 要的是实体 id，
       光有名字就只能回头再解析一遍问题文本，等于把检索白做了。
    """

    name: str
    score: float
    verified: bool
    sources: list[dict[str, Any]]
    id: str = ""


async def search(
    query: str,
    top_k: int = 3,
    category: str | None = None,
    persist_dir: Path | None = None,
    threshold: float | None = None,
) -> list[SearchHit]:
    """检索药材，支持按 category 元数据过滤。"""

    score_threshold = get_settings().retrieval_score_threshold if threshold is None else threshold
    client = EmbeddingClient()
    if not client.api_key:
        return _search_mock_semantic(query, top_k=top_k, category=category, threshold=score_threshold)

    embedding = await client.embed(query)
    where = {"category": category} if category else None
    result = get_collection(persist_dir).query(
        query_embeddings=[embedding],
        n_results=top_k,
        where=where,
        include=["metadatas", "distances"],
    )
    metadatas = _first(result.get("metadatas"))
    distances = _first(result.get("distances"))

    hits: list[SearchHit] = []
    for metadata, distance in zip(metadatas, distances, strict=False):
        hits.append(
            SearchHit(
                name=str(metadata.get("name", "")),
                score=round(1.0 - float(distance), 4),
                verified=bool(metadata.get("verified", False)),
                sources=_parse_sources(metadata.get("sources", "[]")),
                # 入库时 ``_metadata_for_herb`` 已经把 id 写进 metadata 了，
                # 这里只是把它取出来——不要按名字回查，那正是要避免的做法。
                id=str(metadata.get("id", "")),
            )
        )
    return _apply_threshold(hits, score_threshold)


def _search_mock_semantic(query: str, top_k: int, category: str | None, threshold: float) -> list[SearchHit]:
    herbs = [
        herb
        for herb in load_herbs()
        if category is None or str(herb.category) == category
    ]
    ranked = sorted(
        (
            (_mock_semantic_score(query, herb), _exact_effect_hits(query, herb), herb)
            for herb in herbs
        ),
        # 同分时先比「功效原样命中」的条数，最后才按名字。
        # 名字只作确定性兜底，它没有任何医学含义，不该决定谁进 top_k。
        key=lambda item: (-item[0], -item[1], item[2].name),
    )

    hits = [
        SearchHit(
            name=herb.name,
            score=round(score, 4),
            verified=herb.verified,
            sources=[source.model_dump(mode="json") for source in herb.sources],
            id=herb.id,
        )
        for score, _, herb in ranked[:top_k]
    ]
    return _apply_threshold(hits, threshold)


def _exact_effect_hits(query: str, herb: Herb) -> int:
    """查询里**原样出现**了几条该药的功效项。

    只用于同分排序，不进分数。原因是分数本身分不开：教材写「利水消肿」和写
    「逐水消肿」是两回事，前者是同一句话，后者只是同一个概念域，但原来的打分
    对两者完全一样——药材从 76 味扩到 162 味之后，「利水消肿」有 **8 味并列
    1.0**，top3 直接由汉字码位决定（商陆、桑白皮、海藻），麻黄、黄芪全被挤掉。
    分数不该为这件事背锅，同分时补一个和原文有关的次序就够了。
    """

    query_text = _compact_text(query)
    return sum(1 for effect in herb.effects if effect and _compact_text(effect) in query_text)


def _mock_semantic_score(query: str, herb: Herb) -> float:
    query_text = _compact_text(query)
    if not query_text or _has_reject_concept(query_text):
        return 0.0

    doc_text = _compact_text(_mock_semantic_text(herb))
    query_concepts = _concept_scores(query_text)
    doc_concepts = _concept_scores(doc_text)
    query_terms = _query_terms(query_text)

    concept_total = sum(query_concepts.values())
    concept_hits = sum(weight for concept, weight in query_concepts.items() if concept in doc_concepts)
    concept_score = concept_hits / concept_total if concept_total else 0.0

    term_total = sum(_term_weight(term) for term in query_terms)
    term_hits = sum(_term_weight(term) for term in query_terms if term and term in doc_text)
    lexical_score = term_hits / term_total if term_total else 0.0

    if query_concepts:
        score = 0.72 * concept_score + 0.28 * lexical_score
    else:
        score = lexical_score * 0.85

    if herb.name and herb.name in query_text:
        score = max(score, 1.0)
    return min(score, 1.0)


def _mock_semantic_text(herb: Herb) -> str:
    pieces: list[str] = [
        herb.name,
        " ".join(herb.aliases),
        str(herb.category),
        herb.subcategory,
        herb.embedding_text,
        " ".join(herb.effects),
        " ".join(f"{item.syndrome} {item.description}" for item in herb.indications),
    ]
    if herb.tags is not None:
        pieces.append(" ".join(herb.tags.common_confusions))
    return " ".join(piece for piece in pieces if piece)


def _concept_scores(text: str) -> dict[str, float]:
    concepts: dict[str, float] = {}
    for concept, (weight, aliases) in MOCK_SEMANTIC_CONCEPTS.items():
        if any(_compact_text(alias) in text for alias in aliases):
            concepts[concept] = weight
    return concepts


def _has_reject_concept(text: str) -> bool:
    return any(_compact_text(alias) in text for aliases in MOCK_REJECT_CONCEPTS.values() for alias in aliases)


def _query_terms(text: str) -> set[str]:
    terms = {term for term in re.split(r"[，。；、,.;:\s]+", text) if len(term) >= 2}
    for size in (2, 3, 4):
        for index in range(0, max(len(text) - size + 1, 0)):
            token = text[index : index + size]
            if any("\u4e00" <= char <= "\u9fff" for char in token):
                terms.add(token)
    for aliases in (value[1] for value in MOCK_SEMANTIC_CONCEPTS.values()):
        for alias in aliases:
            compact_alias = _compact_text(alias)
            if compact_alias in text:
                terms.add(compact_alias)
    return terms


def _term_weight(term: str) -> float:
    if len(term) >= 4:
        return 1.6
    if len(term) == 3:
        return 1.2
    return 1.0


def _compact_text(text: str) -> str:
    return re.sub(r"[\s，。；、,.;:：/（）()《》<>【】\[\]\"'“”‘’]+", "", text)


def _apply_threshold(hits: list[SearchHit], threshold: float) -> list[SearchHit]:
    """按分数下限过滤。

    原写法先判 ``hits[0].score < threshold`` 就整体返回空，看起来像「第一名不可信
    就整批否决」，其实是个多余分支：``hits`` 按分数降序，第一名都不达标，其余只会
    更低，下面的推导本来就是空的。留着只会让人以为这里有「一票否决」的语义，
    从而不敢动阈值。
    """

    return [hit for hit in hits if hit.score >= threshold]


def _first(value: Any) -> list[Any]:
    if isinstance(value, list) and value and isinstance(value[0], list):
        return value[0]
    if isinstance(value, list):
        return value
    return []


def _parse_sources(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, str):
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]
