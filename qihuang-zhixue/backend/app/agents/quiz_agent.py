"""测验题资源 Agent。"""

from __future__ import annotations

from typing import Any

from app.agents.resource_agent_utils import build_envelope, cached_or_none, difficulty_from_profile, list_text
from app.agents.resource_context import ResourceDoc, primary_doc, source_refs
from app.agents.resource_schemas import (
    BloomLevel,
    QuizOption,
    QuizPayload,
    QuizQuestion,
    QuizType,
    ResourceEnvelope,
    SourceRef,
)
from app.agents.structured_output import structured_create

SYSTEM_PROMPT = (
    "你是中药学测验 Agent。只基于 retrieved_docs 出题，输出题目数组。"
    "硬规则：禁以上皆是/以上皆非，禁总是/从不，正确项不得系统性最长，不得退化为 T/F，"
    "干扰项须为常见易混药或证，并给出 distractor_rationale。"
)


async def generate_quiz_resource(
    *,
    topic: str,
    retrieved_docs: list[ResourceDoc],
    profile: dict[str, Any] | None = None,
) -> ResourceEnvelope:
    """生成 quiz ResourceEnvelope，可被单独 import 调用。"""

    cached = cached_or_none(topic, "quiz", profile)
    if cached is not None:
        return cached

    payload = await structured_create(
        task_type="generate",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"topic={topic}\nprofile={profile or {}}\nretrieved_docs={retrieved_docs}"},
        ],
        response_model=QuizPayload,
        mock_factory=lambda: _mock_payload(retrieved_docs, profile),
    )
    return build_envelope(
        topic=topic,
        resource_type="quiz",
        payload=payload,
        docs=retrieved_docs,
        profile=profile,
        trace_node="quiz_agent",
    )


def _mock_payload(docs: list[ResourceDoc], profile: dict[str, Any] | None) -> QuizPayload:
    doc = primary_doc(docs)
    if doc is None:
        return QuizPayload(questions=[])

    source = SourceRef.model_validate(source_refs(doc)[0])
    difficulty = difficulty_from_profile(profile)
    confusion = _first_confusion(doc, docs)
    first_effect = doc["effects"][0]
    second_effect = doc["effects"][1] if len(doc["effects"]) > 1 else doc["effects"][0]
    first_syndrome = doc["indications"][0]["syndrome"]
    options = [
        QuizOption(key="A", text=first_effect),
        QuizOption(key="B", text=confusion),
        QuizOption(key="C", text=doc["subcategory"]),
        QuizOption(key="D", text=doc["nature"]),
    ]
    questions = [
        QuizQuestion(
            stem=f"{doc['name']}的核心功效之一是？",
            type=QuizType.SINGLE,
            options=options,
            answer=["A"],
            explanation=f"{doc['name']}功效包括{list_text(doc['effects'])}，其中{first_effect}为知识库明确记载。",
            source=source,
            bloom_level=BloomLevel.REMEMBER,
            difficulty=max(1, difficulty - 1),
            distractor_rationale={
                "B": f"{confusion}属于常见易混药或证，但不是本题所问的{doc['name']}功效。",
                "C": f"{doc['subcategory']}是常见易混分类信息，不是具体功效。",
                "D": f"{doc['nature']}是常见易混性味信息，不是具体功效。",
            },
            claims=[doc["name"], first_effect],
        ),
        QuizQuestion(
            stem=f"关于{doc['name']}，下列哪些内容与知识库一致？",
            type=QuizType.MULTIPLE,
            options=[
                QuizOption(key="A", text=first_effect),
                QuizOption(key="B", text=second_effect),
                QuizOption(key="C", text=confusion),
                QuizOption(key="D", text=doc["cautions"][0] if doc["cautions"] else doc["nature"]),
            ],
            answer=["A", "B"],
            explanation=f"{doc['name']}功效包括{first_effect}、{second_effect}。",
            source=source,
            bloom_level=BloomLevel.UNDERSTAND,
            difficulty=difficulty,
            distractor_rationale={
                "C": f"{confusion}是常见易混对象或证候，容易与{doc['name']}混淆。",
                "D": "该项属于常见易混的使用注意或性味信息，不是本题要求的功效组合。",
            },
            claims=[doc["name"], first_effect, second_effect],
        ),
        QuizQuestion(
            stem=f"{doc['name']}常用于哪一类主治证型？",
            type=QuizType.BLANK,
            options=[],
            answer=[first_syndrome],
            explanation=f"知识库记录{doc['name']}主治包括{first_syndrome}。",
            source=source,
            bloom_level=BloomLevel.UNDERSTAND,
            difficulty=difficulty,
            distractor_rationale={},
            claims=[doc["name"], first_syndrome],
        ),
        QuizQuestion(
            stem=f"简述{doc['name']}的用量与一个使用注意。",
            type=QuizType.SHORT,
            options=[],
            answer=[str(doc["dosage"].get("text", "")), doc["cautions"][0] if doc["cautions"] else "知识库暂未覆盖"],
            explanation="答案应同时覆盖用量和使用注意，均需来自知识库。",
            source=source,
            bloom_level=BloomLevel.APPLY,
            difficulty=min(5, difficulty + 1),
            distractor_rationale={},
            claims=[str(doc["dosage"].get("text", "")), *(doc["cautions"][:1])],
        ),
        QuizQuestion(
            stem=f"病例中出现{first_syndrome}表现时，为什么可考虑复习{doc['name']}？",
            type=QuizType.CASE,
            options=[],
            answer=[f"{doc['name']}主治包括{first_syndrome}", f"功效涉及{first_effect}"],
            explanation=f"{doc['name']}的主治证型包含{first_syndrome}，并有{first_effect}等功效。",
            source=source,
            bloom_level=BloomLevel.ANALYZE,
            difficulty=min(5, difficulty + 2),
            distractor_rationale={},
            claims=[doc["name"], first_syndrome, first_effect],
        ),
    ]
    return QuizPayload(questions=questions)


def _first_confusion(doc: ResourceDoc, docs: list[ResourceDoc]) -> str:
    if doc["common_confusions"]:
        return doc["common_confusions"][0]
    for other in docs[1:]:
        if other["name"] != doc["name"]:
            return other["name"]
    if doc["indications"]:
        return doc["indications"][0]["syndrome"]
    return doc["subcategory"]

