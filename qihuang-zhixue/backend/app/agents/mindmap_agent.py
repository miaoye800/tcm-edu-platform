"""markmap 层级 Markdown 资源 Agent。"""

from __future__ import annotations

from typing import Any

from app.agents.resource_agent_utils import build_envelope, cached_or_none, list_text
from app.agents.resource_context import ResourceDoc, primary_doc
from app.agents.resource_schemas import MindmapPayload, ResourceEnvelope
from app.agents.structured_output import structured_create

SYSTEM_PROMPT = (
    "你是 markmap 思维导图 Agent。输出 markmap 兼容层级 Markdown。"
    "层级应类似：解表药→发散风寒药→麻黄→性味/功效/主治/用量。"
    "实体节点只能来自 retrieved_docs。"
)


async def generate_mindmap_resource(
    *,
    topic: str,
    retrieved_docs: list[ResourceDoc],
    profile: dict[str, Any] | None = None,
) -> ResourceEnvelope:
    """生成 mindmap ResourceEnvelope，可被单独 import 调用。"""

    cached = cached_or_none(topic, "mindmap", profile)
    if cached is not None:
        return cached

    payload = await structured_create(
        task_type="generate",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"topic={topic}\nretrieved_docs={retrieved_docs}"},
        ],
        response_model=MindmapPayload,
        mock_factory=lambda: _mock_payload(topic, retrieved_docs),
    )
    return build_envelope(
        topic=topic,
        resource_type="mindmap",
        payload=payload,
        docs=retrieved_docs,
        profile=profile,
        trace_node="mindmap_agent",
    )


def _mock_payload(topic: str, docs: list[ResourceDoc]) -> MindmapPayload:
    doc = primary_doc(docs)
    if doc is None:
        return MindmapPayload(markdown=f"# {topic}\n## 知识库暂未覆盖", root=topic, node_names=[])

    markdown = "\n".join(
        [
            f"# {doc['category']}",
            f"## {doc['subcategory']}",
            f"### {doc['name']}",
            f"#### 性味",
            f"- {doc['nature']}；{list_text(doc['flavor'])}",
            f"#### 归经",
            f"- {list_text(doc['meridians'])}",
            f"#### 功效",
            *[f"- {effect}" for effect in doc["effects"]],
            f"#### 主治",
            *[f"- {item['syndrome']}" for item in doc["indications"]],
            f"#### 用量",
            f"- {doc['dosage'].get('text', '知识库暂未覆盖')}",
        ]
    )
    return MindmapPayload(
        markdown=markdown,
        root=doc["category"],
        node_names=[
            doc["name"],
            *[item["syndrome"] for item in doc["indications"]],
            *[
                str(combo.get("formula_ref", "")).strip()
                for combo in doc["combinations"]
                if str(combo.get("formula_ref", "")).strip()
            ],
        ],
    )

