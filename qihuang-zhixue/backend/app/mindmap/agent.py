from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.agents.resource_context import ResourceDoc, collect_source_refs
from app.config import Settings, get_settings
from app.llm.router import ModelRouter

from .prompts import EXPAND_SYSTEM_PROMPT, MINDMAP_SYSTEM_PROMPT
from .rules import ALLOWED_TEMPLATE_IDS, choice_reason, content_feature, default_template
from .schemas import MindElixirData, MindmapContent, MindmapMeta, MindmapNode

LLMCall = Callable[[list[dict[str, str]]], Awaitable[str]]


class MindmapAgent:
    """Small LangGraph-style agent boundary: retrieval in, validated content out."""

    def __init__(self, *, settings: Settings | None = None, llm_call: LLMCall | None = None) -> None:
        self.settings = settings or get_settings()
        self.llm_call = llm_call

    async def generate(self, topic: str, docs: list[ResourceDoc], profile: dict[str, Any] | None = None) -> MindmapContent:
        sources = collect_source_refs(docs)
        feature = content_feature(topic, docs)
        candidates = tuple(feature["candidates"])
        data = await self._generate_data(topic, docs, sources)
        template_id = default_template(candidates, profile)
        # An injected/available model may choose only after deterministic filtering.
        if self.llm_call and len(candidates) > 1:
            raw = await self.llm_call([{"role": "user", "content": json.dumps({"candidates": candidates, "topic": topic}, ensure_ascii=False)}])
            selected = json.loads(raw).get("template_id")
            if selected in candidates:
                template_id = selected
        if template_id not in ALLOWED_TEMPLATE_IDS:
            raise ValueError("template is outside the approved template enum")
        return MindmapContent(template_id=template_id, choice_reason=choice_reason(feature, template_id, profile), mindmap_data=data, sources=sources)

    async def expand(self, topic: str, docs: list[ResourceDoc], parent: MindmapNode, existing_topics: set[str]) -> list[MindmapNode]:
        # Structured fallback is also the keyless behavior and guarantees grounding.
        candidates = self._children_for_doc(docs[0] if docs else None, parent.topic, len(existing_topics))
        return [node for node in candidates if node.topic not in existing_topics]

    async def _generate_data(self, topic: str, docs: list[ResourceDoc], sources: list[dict[str, str]]) -> MindElixirData:
        if self.llm_call and (self.settings.deepseek_api_key or self.settings.zhipu_api_key):
            raw = await self.llm_call([{"role": "system", "content": MINDMAP_SYSTEM_PROMPT}, {"role": "user", "content": json.dumps({"topic": topic, "docs": docs, "sources": sources}, ensure_ascii=False)}])
            try:
                return MindElixirData.model_validate(json.loads(raw))
            except Exception:
                pass
        return self._fallback_data(topic, docs)

    def _fallback_data(self, topic: str, docs: list[ResourceDoc]) -> MindElixirData:
        doc = docs[0] if docs else None
        root = MindmapNode(id="root", topic=topic, children=self._children_for_doc(doc, topic, 0))
        return MindElixirData(nodeData=root)

    def _children_for_doc(self, doc: ResourceDoc | None, parent_topic: str, offset: int) -> list[MindmapNode]:
        if not doc:
            return [MindmapNode(id=f"{offset}-overview", topic="知识要点", direction=0, meta=MindmapMeta(source_indexes=[]), children=[MindmapNode(id=f"{offset}-review", topic="待补充资料", meta=MindmapMeta(source_indexes=[]))])]
        fields: list[tuple[str, list[str]]] = [
            ("性味归经", ["、".join([doc["nature"], *doc["flavor"], *doc["meridians"]])]),
            ("核心功效", list(doc["effects"])[:4]),
            ("主治要点", [item["syndrome"] for item in doc["indications"]][:4]),
            ("用量注意", [str(doc["dosage"].get("text", "常规用量")), *list(doc["cautions"])[:2]]),
        ]
        nodes: list[MindmapNode] = []
        for index, (label, details) in enumerate(fields):
            children = [MindmapNode(id=f"n-{offset}-{index}-{child_index}", topic=item[:30], meta=MindmapMeta(source_indexes=[0])) for child_index, item in enumerate(details) if item]
            nodes.append(MindmapNode(id=f"n-{offset}-{index}", topic=label, direction=index % 2, meta=MindmapMeta(source_indexes=[0]), children=children))
        return nodes
