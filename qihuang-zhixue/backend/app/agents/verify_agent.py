"""资源生成后的证据 Guard 节点。"""

from __future__ import annotations

import re
from typing import Any

from app.agents.resource_context import ResourceDoc, ResourceWhitelist, all_evidence_text, retrieve_resource_docs
from app.agents.resource_schemas import (
    ExplainPayload,
    ExplainSection,
    GraphPayload,
    GroundingReport,
    MindmapPayload,
    QuizPayload,
    ReadingPayload,
    ResourceEnvelope,
)

GROUNDING_THRESHOLD = 0.65
UNSUPPORTED_PLACEHOLDER = "知识库暂未覆盖"


async def verify_resource_agent(
    resource: ResourceEnvelope,
    docs: list[ResourceDoc],
    whitelist: ResourceWhitelist,
) -> ResourceEnvelope:
    """对单个资源执行证据对齐和白名单裁剪。"""

    if resource.type == "graph" and isinstance(resource.payload, GraphPayload):
        payload, unsupported = _verify_graph(resource.payload, whitelist)
        return resource.model_copy(
            update={
                "payload": payload,
                "grounding": GroundingReport(
                    score=1.0 if not unsupported else 0.85,
                    supported_claims=[node.name for node in payload.nodes],
                    unsupported_claims=unsupported,
                    note="" if not unsupported else "已剔除越界节点或关系",
                ),
            }
        )

    if resource.type == "mindmap" and isinstance(resource.payload, MindmapPayload):
        payload, unsupported = _verify_mindmap(resource.payload, whitelist)
        return resource.model_copy(
            update={
                "payload": payload,
                "grounding": GroundingReport(
                    score=1.0 if not unsupported else 0.85,
                    supported_claims=payload.node_names,
                    unsupported_claims=unsupported,
                    note="" if not unsupported else "已标记越界导图节点",
                ),
            }
        )

    if resource.type == "explain" and isinstance(resource.payload, ExplainPayload):
        payload, report = await _verify_explain(resource.payload, docs)
        return resource.model_copy(update={"payload": payload, "grounding": report})

    if resource.type == "quiz" and isinstance(resource.payload, QuizPayload):
        payload, report = await _verify_quiz(resource.payload, docs)
        return resource.model_copy(update={"payload": payload, "grounding": report})

    if resource.type == "reading" and isinstance(resource.payload, ReadingPayload):
        payload, report = await _verify_reading(resource.payload, docs)
        return resource.model_copy(update={"payload": payload, "grounding": report})

    return resource


def verify_claims(claims: list[str], docs: list[ResourceDoc]) -> GroundingReport:
    """逐条断言查证，测试可直接注入无证据 claim。"""

    supported: list[str] = []
    unsupported: list[str] = []
    for claim in claims:
        normalized = claim.strip()
        if not normalized:
            continue
        if _claim_supported(normalized, docs):
            supported.append(normalized)
        else:
            unsupported.append(normalized)
    total = len(supported) + len(unsupported)
    score = round(len(supported) / total, 4) if total else 1.0
    return GroundingReport(
        score=score,
        supported_claims=supported,
        unsupported_claims=unsupported,
        note="" if score >= GROUNDING_THRESHOLD else "证据不足，请人工复核",
    )


async def async_verify_claims(claims: list[str], docs: list[ResourceDoc]) -> GroundingReport:
    """先用当前证据对齐，未命中时再回 retriever 复查。"""

    supported: list[str] = []
    unsupported: list[str] = []
    for claim in claims:
        normalized = claim.strip()
        if not normalized:
            continue
        if _claim_supported(normalized, docs):
            supported.append(normalized)
            continue
        refreshed_docs = await retrieve_resource_docs(normalized, top_k=3)
        if refreshed_docs and _claim_supported(normalized, refreshed_docs):
            supported.append(normalized)
        else:
            unsupported.append(normalized)
    total = len(supported) + len(unsupported)
    score = round(len(supported) / total, 4) if total else 1.0
    return GroundingReport(
        score=score,
        supported_claims=supported,
        unsupported_claims=unsupported,
        note="" if score >= GROUNDING_THRESHOLD else "证据不足，请人工复核",
    )


async def _verify_explain(payload: ExplainPayload, docs: list[ResourceDoc]) -> tuple[ExplainPayload, GroundingReport]:
    report = await async_verify_claims([claim for section in payload.sections for claim in section.claims], docs)
    unsupported = set(report.unsupported_claims)
    sections: list[ExplainSection] = []
    markdown_parts: list[str] = []
    for section in payload.sections:
        kept_claims = [claim for claim in section.claims if claim not in unsupported]
        content = section.content
        if section.claims and not kept_claims:
            content = UNSUPPORTED_PLACEHOLDER
        else:
            for claim in unsupported:
                content = content.replace(claim, UNSUPPORTED_PLACEHOLDER)
        next_section = section.model_copy(update={"content": content, "claims": kept_claims})
        sections.append(next_section)
        markdown_parts.append(_section_to_markdown(next_section))
    return payload.model_copy(update={"sections": sections, "markdown": "\n\n".join(markdown_parts)}), report


async def _verify_quiz(payload: QuizPayload, docs: list[ResourceDoc]) -> tuple[QuizPayload, GroundingReport]:
    all_claims = [claim for question in payload.questions for claim in question.claims]
    report = await async_verify_claims(all_claims, docs)
    unsupported = set(report.unsupported_claims)
    questions = []
    for question in payload.questions:
        if any(claim in unsupported for claim in question.claims):
            questions.append(
                question.model_copy(
                    update={
                        "explanation": f"{UNSUPPORTED_PLACEHOLDER}，请人工复核本题证据。",
                        "claims": [claim for claim in question.claims if claim not in unsupported],
                    }
                )
            )
        else:
            questions.append(question)
    return payload.model_copy(update={"questions": questions}), report


async def _verify_reading(payload: ReadingPayload, docs: list[ResourceDoc]) -> tuple[ReadingPayload, GroundingReport]:
    all_claims = [claim for item in payload.items for claim in item.claims]
    report = await async_verify_claims(all_claims, docs)
    unsupported = set(report.unsupported_claims)
    items = []
    for item in payload.items:
        if any(claim in unsupported for claim in item.claims):
            items.append(
                item.model_copy(
                    update={
                        "reason": f"{UNSUPPORTED_PLACEHOLDER}，请人工复核该阅读建议。",
                        "claims": [claim for claim in item.claims if claim not in unsupported],
                    }
                )
            )
        else:
            items.append(item)
    return payload.model_copy(update={"items": items}), report


def _verify_graph(payload: GraphPayload, whitelist: ResourceWhitelist) -> tuple[GraphPayload, list[str]]:
    allowed = set().union(whitelist["herbs"], whitelist["syndromes"], whitelist["formulas"])
    kept_nodes = [node for node in payload.nodes if node.name in allowed or node.id in allowed]
    kept_ids = {node.id for node in kept_nodes}
    unsupported = [
        node.name
        for node in payload.nodes
        if node.id not in kept_ids
    ]
    kept_links = [
        link
        for link in payload.links
        if link.source in kept_ids and link.target in kept_ids
    ]
    return payload.model_copy(update={"nodes": kept_nodes, "links": kept_links}), unsupported


def _verify_mindmap(payload: MindmapPayload, whitelist: ResourceWhitelist) -> tuple[MindmapPayload, list[str]]:
    allowed = set().union(whitelist["herbs"], whitelist["syndromes"], whitelist["formulas"])
    unsupported = [name for name in payload.node_names if _looks_like_entity(name) and name not in allowed]
    if not unsupported:
        return payload, []
    markdown = payload.markdown
    for name in unsupported:
        markdown = re.sub(rf"^#+\s+{re.escape(name)}\s*$", f"- {UNSUPPORTED_PLACEHOLDER}：{name}", markdown, flags=re.M)
    node_names = [name for name in payload.node_names if name not in unsupported]
    return payload.model_copy(update={"markdown": markdown, "node_names": node_names}), unsupported


def _claim_supported(claim: str, docs: list[ResourceDoc]) -> bool:
    evidence = _compact(all_evidence_text(docs))
    compact_claim = _compact(claim)
    if not compact_claim:
        return True
    if compact_claim in evidence:
        return True

    tokens = _claim_tokens(claim)
    if not tokens:
        return True
    hits = sum(1 for token in tokens if _compact(token) in evidence)
    required = 1 if len(tokens) <= 2 else max(2, int(len(tokens) * 0.55))
    return hits >= required


def _claim_tokens(claim: str) -> list[str]:
    parts = [part for part in re.split(r"[，。；、：:;,\s]+", claim) if len(part) >= 2]
    tokens: list[str] = []
    for part in parts:
        tokens.append(part)
        if len(part) > 6:
            tokens.extend(part[index : index + 3] for index in range(0, len(part) - 2))
    return tokens


def _compact(text: str) -> str:
    return re.sub(r"[\s，。；、：:;,.!?！？（）()\[\]【】《》\"'“”‘’~-]+", "", text)


def _section_to_markdown(section: ExplainSection) -> str:
    source_text = "；".join(source.ref for source in section.sources) or "本地知识库"
    return f"## {section.title}\n{section.content}\n\nsources: {source_text}"


def _looks_like_entity(name: str) -> bool:
    return len(name.strip()) >= 2 and "/" not in name and name not in {
        "性味",
        "功效",
        "主治",
        "用量",
        "注意",
        "配伍",
        "鉴别",
    }
