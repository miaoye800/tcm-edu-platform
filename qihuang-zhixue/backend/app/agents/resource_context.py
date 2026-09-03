"""资源生成组共享上下文与检索白名单。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypedDict

from app.kb.ingest import load_herbs
from app.kb.models import Herb
from app.kb.retriever import SearchHit, search


class ResourceDoc(TypedDict):
    id: str
    name: str
    aliases: list[str]
    category: str
    subcategory: str
    nature: str
    flavor: list[str]
    meridians: list[str]
    effects: list[str]
    indications: list[dict[str, str]]
    dosage: dict[str, Any]
    cautions: list[str]
    combinations: list[dict[str, Any]]
    differentiation: list[dict[str, str]]
    common_confusions: list[str]
    incompatibility: dict[str, list[str]]
    sources: list[dict[str, Any]]
    evidence_text: str


class ResourceWhitelist(TypedDict):
    herbs: set[str]
    herb_ids: set[str]
    syndromes: set[str]
    formulas: set[str]


async def retrieve_resource_docs(topic: str, top_k: int = 5) -> list[ResourceDoc]:
    """检索资源生成所需文档，优先保留题目中直接点名的药材。"""

    hits = await search(topic, top_k=top_k)
    hits = _prioritize_exact_herb(topic, hits, limit=top_k)
    return _hits_to_docs(hits)


def build_whitelist(docs: list[ResourceDoc]) -> ResourceWhitelist:
    herbs = {doc["name"] for doc in docs if doc["name"]}
    herb_ids = {doc["id"] for doc in docs if doc["id"]}
    syndromes = {
        item["syndrome"]
        for doc in docs
        for item in doc["indications"]
        if item.get("syndrome")
    }
    formulas = {
        str(combo.get("formula_ref", "")).strip()
        for doc in docs
        for combo in doc["combinations"]
        if str(combo.get("formula_ref", "")).strip()
    }
    return {
        "herbs": herbs,
        "herb_ids": herb_ids,
        "syndromes": syndromes,
        "formulas": formulas,
    }


def profile_hash(profile: dict[str, Any] | None) -> str:
    """稳定计算 profile 摘要，作为资源缓存键的一部分。"""

    normalized = json.dumps(profile or {}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def cache_key_for(topic: str, resource_type: str, profile: dict[str, Any] | None) -> str:
    return f"{topic.strip()}|{resource_type}|{profile_hash(profile)}"


def doc_by_name(docs: list[ResourceDoc]) -> dict[str, ResourceDoc]:
    return {doc["name"]: doc for doc in docs}


def primary_doc(docs: list[ResourceDoc]) -> ResourceDoc | None:
    return docs[0] if docs else None


def source_refs(doc: ResourceDoc | None) -> list[dict[str, str]]:
    if doc is None:
        return []
    refs: list[dict[str, str]] = []
    for source in doc["sources"]:
        refs.append(
            {
                "name": doc["name"],
                "type": str(source.get("type", "本地知识库")),
                "ref": str(source.get("ref", "本地中药学知识库")),
                "quote": str(source.get("quote", "")),
            }
        )
    if not refs:
        refs.append({"name": doc["name"], "type": "本地知识库", "ref": "本地中药学知识库", "quote": ""})
    return refs


def collect_source_refs(docs: list[ResourceDoc]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for doc in docs:
        for source in source_refs(doc):
            key = (source["name"], source["type"], source["ref"])
            if key in seen:
                continue
            seen.add(key)
            refs.append(source)
    return refs


def doc_evidence_text(doc: ResourceDoc) -> str:
    return doc["evidence_text"]


def all_evidence_text(docs: list[ResourceDoc]) -> str:
    return "\n".join(doc["evidence_text"] for doc in docs)


def herb_name_by_id(herb_id: str) -> str:
    for herb in load_herbs():
        if herb.id == herb_id:
            return herb.name
    return herb_id


def _prioritize_exact_herb(topic: str, hits: list[SearchHit], limit: int) -> list[SearchHit]:
    herbs_by_name = {herb.name: herb for herb in load_herbs()}
    exact_names = [name for name in herbs_by_name if name and name in topic]
    if not exact_names:
        return hits[:limit]

    hit_by_name = {hit.name: hit for hit in hits}
    prioritized: list[SearchHit] = []
    for name in exact_names:
        hit = hit_by_name.get(name)
        if hit is None:
            herb = herbs_by_name[name]
            hit = SearchHit(
                name=herb.name,
                score=1.0,
                verified=herb.verified,
                sources=[source.model_dump(mode="json") for source in herb.sources],
            )
        prioritized.append(hit)

    seen = {hit.name for hit in prioritized}
    prioritized.extend(hit for hit in hits if hit.name not in seen)
    return prioritized[:limit]


def _hits_to_docs(hits: list[SearchHit]) -> list[ResourceDoc]:
    herbs_by_name = {herb.name: herb for herb in load_herbs()}
    docs: list[ResourceDoc] = []
    for hit in hits:
        herb = herbs_by_name.get(hit.name)
        if herb is None:
            continue
        docs.append(_herb_to_doc(herb, hit.sources))
    return docs


def _herb_to_doc(herb: Herb, hit_sources: list[dict[str, Any]]) -> ResourceDoc:
    indications = [
        {"syndrome": indication.syndrome, "description": indication.description}
        for indication in herb.indications
    ]
    combinations = [
        {
            "with": list(combo.with_),
            "with_names": [herb_name_by_id(item) for item in combo.with_],
            "purpose": combo.purpose,
            "formula_ref": combo.formula_ref,
        }
        for combo in herb.combinations
    ]
    differentiation = [
        {
            "compare_with": item.compare_with,
            "compare_name": herb_name_by_id(item.compare_with),
            "key_points": item.key_points,
        }
        for item in herb.differentiation
    ]
    common_confusions = [herb_name_by_id(item) for item in herb.tags.common_confusions] if herb.tags else []
    sources = hit_sources or [source.model_dump(mode="json") for source in herb.sources]
    doc: ResourceDoc = {
        "id": herb.id,
        "name": herb.name,
        "aliases": list(herb.aliases),
        "category": str(herb.category),
        "subcategory": herb.subcategory,
        "nature": str(herb.nature),
        "flavor": [str(item) for item in herb.flavor],
        "meridians": list(herb.meridians),
        "effects": list(herb.effects),
        "indications": indications,
        "dosage": herb.dosage.model_dump(mode="json"),
        "cautions": list(herb.cautions),
        "combinations": combinations,
        "differentiation": differentiation,
        "common_confusions": common_confusions,
        "incompatibility": herb.incompatibility.model_dump(mode="json") if herb.incompatibility else {},
        "sources": sources,
        "evidence_text": "",
    }
    doc["evidence_text"] = _make_evidence_text(doc)
    return doc


def _make_evidence_text(doc: ResourceDoc) -> str:
    pieces = [
        doc["name"],
        " ".join(doc["aliases"]),
        doc["category"],
        doc["subcategory"],
        doc["nature"],
        " ".join(doc["flavor"]),
        " ".join(doc["meridians"]),
        " ".join(doc["effects"]),
        " ".join(f"{item['syndrome']} {item['description']}" for item in doc["indications"]),
        str(doc["dosage"].get("text", "")),
        str(doc["dosage"].get("usage_notes", "")),
        " ".join(doc["cautions"]),
        " ".join(f"{combo.get('purpose', '')} {combo.get('formula_ref', '')}" for combo in doc["combinations"]),
        " ".join(item["key_points"] for item in doc["differentiation"]),
        " ".join(doc["common_confusions"]),
    ]
    return " ".join(piece for piece in pieces if piece)
