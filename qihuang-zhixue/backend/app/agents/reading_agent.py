"""延伸阅读资源 Agent。"""

from __future__ import annotations

from typing import Any

from app.agents.resource_agent_utils import build_envelope, cached_or_none
from app.agents.resource_context import ResourceDoc, source_refs
from app.agents.resource_schemas import ReadingItem, ReadingPayload, ResourceEnvelope, SourceRef
from app.agents.structured_output import structured_create

SYSTEM_PROMPT = (
    "你是中药学延伸阅读 Agent。只基于 retrieved_docs 推荐 3 到 5 条阅读。"
    "每条包含标题、一句话理由和 sources，理由必须能被知识库证据支持。"
)


async def generate_reading_resource(
    *,
    topic: str,
    retrieved_docs: list[ResourceDoc],
    profile: dict[str, Any] | None = None,
) -> ResourceEnvelope:
    """生成 reading ResourceEnvelope，可被单独 import 调用。"""

    cached = cached_or_none(topic, "reading", profile)
    if cached is not None:
        return cached

    payload = await structured_create(
        task_type="generate",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"topic={topic}\nretrieved_docs={retrieved_docs}"},
        ],
        response_model=ReadingPayload,
        mock_factory=lambda: _mock_payload(retrieved_docs),
    )
    return build_envelope(
        topic=topic,
        resource_type="reading",
        payload=payload,
        docs=retrieved_docs,
        profile=profile,
        trace_node="reading_agent",
    )


def _mock_payload(docs: list[ResourceDoc]) -> ReadingPayload:
    if not docs:
        items = [
            ReadingItem(
                title=f"知识库暂未覆盖阅读建议 {index}",
                reason="当前知识库暂未覆盖该主题，请人工补充资料后复核。",
                sources=[],
                claims=[],
            )
            for index in range(1, 4)
        ]
        return ReadingPayload(items=items)

    items: list[ReadingItem] = []
    primary = docs[0]
    primary_sources = [SourceRef.model_validate(source) for source in source_refs(primary)]
    items.append(
        ReadingItem(
            title=f"{primary['name']}的性味归经与功效",
            reason=f"先复习{primary['name']}的{primary['nature']}性、{primary['subcategory']}归属与核心功效。",
            sources=primary_sources,
            claims=[primary["name"], primary["nature"], primary["subcategory"], *primary["effects"][:1]],
        )
    )
    items.append(
        ReadingItem(
            title=f"{primary['name']}主治证型辨析",
            reason=f"围绕{primary['indications'][0]['syndrome']}理解主治与功效之间的联系。",
            sources=primary_sources,
            claims=[primary["name"], primary["indications"][0]["syndrome"]],
        )
    )
    if primary["combinations"]:
        combo = primary["combinations"][0]
        formula = str(combo.get("formula_ref", "")).strip()
        items.append(
            ReadingItem(
                title=f"{primary['name']}配伍与方剂线索",
                reason=f"从{formula or '配伍'}理解{combo.get('purpose', '')}。",
                sources=primary_sources,
                claims=[primary["name"], str(combo.get("purpose", "")), formula],
            )
        )
    if primary["differentiation"]:
        diff = primary["differentiation"][0]
        items.append(
            ReadingItem(
                title=f"{primary['name']}与{diff.get('compare_name') or diff.get('compare_with')}鉴别",
                reason=f"通过鉴别要点减少与常见易混药的混淆。",
                sources=primary_sources,
                claims=[primary["name"], diff["key_points"]],
            )
        )
    for doc in docs[1:]:
        if len(items) >= 5:
            break
        items.append(
            ReadingItem(
                title=f"对照阅读：{doc['name']}",
                reason=f"{doc['name']}同属检索命中范围，可与{primary['name']}作横向比较。",
                sources=[SourceRef.model_validate(source) for source in source_refs(doc)],
                claims=[doc["name"], doc["subcategory"]],
            )
        )
    return ReadingPayload(items=items[:5])
