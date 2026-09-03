"""Deprecated ECharts Graph resource generator.

Persisted phase-2 graph resources use ``graph_intelligence.GraphAgent`` instead.
This module remains for the pre-existing learning-resource LangGraph API.
"""

from __future__ import annotations

from typing import Any

from app.agents.resource_agent_utils import build_envelope, cached_or_none
from app.agents.resource_context import ResourceDoc, ResourceWhitelist, build_whitelist
from app.agents.resource_schemas import (
    GraphLink,
    GraphNode,
    GraphNodeCategory,
    GraphPayload,
    GraphRelation,
    ResourceEnvelope,
)
from app.agents.structured_output import structured_create

SYSTEM_PROMPT = (
    "你是中药学知识图谱 Agent。只输出 ECharts Graph JSON 的 nodes/links。"
    "节点必须来自检索命中的药材、证型、方剂白名单，禁止新造。"
    "link.relation 只能取：相须、相使、相畏、相杀、功效相似、同类、主治同证。"
)


async def generate_graph_resource(
    *,
    topic: str,
    retrieved_docs: list[ResourceDoc],
    profile: dict[str, Any] | None = None,
) -> ResourceEnvelope:
    """生成 graph ResourceEnvelope，可被单独 import 调用。"""

    cached = cached_or_none(topic, "graph", profile)
    if cached is not None:
        return cached

    whitelist = build_whitelist(retrieved_docs)
    payload = await structured_create(
        task_type="generate",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"topic={topic}\nwhitelist={whitelist}\nretrieved_docs={retrieved_docs}",
            },
        ],
        response_model=GraphPayload,
        mock_factory=lambda: _mock_payload(retrieved_docs, whitelist),
    )
    return build_envelope(
        topic=topic,
        resource_type="graph",
        payload=payload,
        docs=retrieved_docs,
        profile=profile,
        trace_node="graph_agent",
    )


def _mock_payload(docs: list[ResourceDoc], whitelist: ResourceWhitelist) -> GraphPayload:
    nodes: list[GraphNode] = []
    links: list[GraphLink] = []
    if not docs:
        return GraphPayload(nodes=[], links=[])

    for doc in docs:
        nodes.append(
            GraphNode(
                id=doc["name"],
                name=doc["name"],
                category=GraphNodeCategory.HERB,
                symbolSize=58 if doc is docs[0] else 44,
            )
        )
        for indication in doc["indications"][:2]:
            syndrome = indication["syndrome"]
            if syndrome in whitelist["syndromes"]:
                nodes.append(
                    GraphNode(
                        id=syndrome,
                        name=syndrome,
                        category=GraphNodeCategory.SYNDROME,
                        symbolSize=38,
                    )
                )
                links.append(
                    GraphLink(
                        source=doc["name"],
                        target=syndrome,
                        relation=GraphRelation.SAME_SYNDROME,
                        evidence=indication.get("description", ""),
                    )
                )
        for combo in doc["combinations"][:1]:
            formula = str(combo.get("formula_ref", "")).strip()
            if formula and formula in whitelist["formulas"]:
                nodes.append(
                    GraphNode(
                        id=formula,
                        name=formula,
                        category=GraphNodeCategory.FORMULA,
                        symbolSize=36,
                    )
                )
                links.append(
                    GraphLink(
                        source=doc["name"],
                        target=formula,
                        relation=GraphRelation.XIANG_XU,
                        evidence=str(combo.get("purpose", "")),
                    )
                )
    for left, right in zip(docs, docs[1:], strict=False):
        if left["subcategory"] == right["subcategory"]:
            links.append(
                GraphLink(
                    source=left["name"],
                    target=right["name"],
                    relation=GraphRelation.SAME_CLASS,
                    evidence=f"同属{left['subcategory']}",
                )
            )

    deduped_nodes = {node.id: node for node in nodes}
    kept_links = [
        link
        for link in links
        if link.source in deduped_nodes and link.target in deduped_nodes
    ]
    return GraphPayload(nodes=list(deduped_nodes.values()), links=kept_links)
