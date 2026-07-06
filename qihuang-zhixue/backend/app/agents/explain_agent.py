"""结构化讲解资源 Agent。"""

from __future__ import annotations

from typing import Any

from app.agents.resource_agent_utils import build_envelope, cached_or_none, list_text
from app.agents.resource_context import ResourceDoc, primary_doc, source_refs
from app.agents.resource_schemas import ExplainPayload, ExplainSection, ResourceEnvelope, SourceRef
from app.agents.structured_output import structured_create

SYSTEM_PROMPT = (
    "你是中药学结构化讲解 Agent。只允许使用 retrieved_docs，禁止引入检索外药材。"
    "按定义、性味归经、功效、主治证型、配伍(带方剂)、相似药鉴别、用量与使用注意分节，"
    "每节末尾必须挂 sources，并输出可被 Pydantic 校验的 JSON。"
)

SECTION_TITLES = ["定义", "性味归经", "功效", "主治证型", "配伍(带方剂)", "相似药鉴别", "用量与使用注意"]


async def generate_explain_resource(
    *,
    topic: str,
    retrieved_docs: list[ResourceDoc],
    profile: dict[str, Any] | None = None,
) -> ResourceEnvelope:
    """生成 explain ResourceEnvelope，可被单独 import 调用。"""

    cached = cached_or_none(topic, "explain", profile)
    if cached is not None:
        return cached

    payload = await structured_create(
        task_type="generate",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"topic={topic}\nretrieved_docs={retrieved_docs}"},
        ],
        response_model=ExplainPayload,
        mock_factory=lambda: _mock_payload(topic, retrieved_docs),
    )
    return build_envelope(
        topic=topic,
        resource_type="explain",
        payload=payload,
        docs=retrieved_docs,
        profile=profile,
        trace_node="explain_agent",
    )


def _mock_payload(topic: str, docs: list[ResourceDoc]) -> ExplainPayload:
    doc = primary_doc(docs)
    if doc is None:
        section = ExplainSection(
            title="定义",
            content="知识库暂未覆盖",
            sources=[],
            claims=[],
        )
        return ExplainPayload(markdown="## 定义\n知识库暂未覆盖\n\nsources: []", sections=[section])

    sources = [SourceRef.model_validate(source) for source in source_refs(doc)]
    sections = [
        _section(
            "定义",
            f"{doc['name']}属于{doc['category']}中的{doc['subcategory']}，本资源仅基于检索命中文档讲解。",
            sources,
            [doc["name"], doc["category"], doc["subcategory"]],
        ),
        _section(
            "性味归经",
            f"{doc['name']}性{doc['nature']}，味{list_text(doc['flavor'])}，归{list_text(doc['meridians'])}。",
            sources,
            [doc["nature"], *doc["flavor"], *doc["meridians"]],
        ),
        _section(
            "功效",
            f"{doc['name']}的功效为{list_text(doc['effects'])}。",
            sources,
            doc["effects"],
        ),
        _section(
            "主治证型",
            f"常用于{list_text([item['syndrome'] for item in doc['indications']])}。",
            sources,
            [item["syndrome"] for item in doc["indications"]],
        ),
        _section(
            "配伍(带方剂)",
            _combination_text(doc),
            sources,
            [str(combo.get("purpose", "")) for combo in doc["combinations"]],
        ),
        _section(
            "相似药鉴别",
            _differentiation_text(doc),
            sources,
            [item["key_points"] for item in doc["differentiation"]],
        ),
        _section(
            "用量与使用注意",
            f"用量为{doc['dosage'].get('text', '知识库暂未覆盖')}。使用注意：{list_text(doc['cautions'])}。",
            sources,
            [str(doc["dosage"].get("text", "")), *doc["cautions"]],
        ),
    ]
    markdown = "\n\n".join(_section_to_markdown(section) for section in sections)
    return ExplainPayload(markdown=markdown, sections=sections)


def _section(title: str, content: str, sources: list[SourceRef], claims: list[str]) -> ExplainSection:
    return ExplainSection(
        title=title,
        content=content,
        sources=sources,
        claims=[claim for claim in claims if claim],
    )


def _combination_text(doc: ResourceDoc) -> str:
    if not doc["combinations"]:
        return "知识库暂未覆盖明确配伍。"
    parts = []
    for combo in doc["combinations"]:
        with_names = list_text([str(name) for name in combo.get("with_names", [])])
        formula = str(combo.get("formula_ref", "")).strip() or "未标注方剂"
        parts.append(f"与{with_names}配伍，{combo.get('purpose', '')}；方剂：{formula}")
    return "；".join(parts) + "。"


def _differentiation_text(doc: ResourceDoc) -> str:
    if not doc["differentiation"]:
        return "知识库暂未覆盖相似药鉴别。"
    return "；".join(
        f"与{item.get('compare_name') or item.get('compare_with')}鉴别：{item['key_points']}"
        for item in doc["differentiation"]
    )


def _section_to_markdown(section: ExplainSection) -> str:
    source_text = "；".join(source.ref for source in section.sources) or "[]"
    return f"## {section.title}\n{section.content}\n\nsources: {source_text}"

