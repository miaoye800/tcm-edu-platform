import asyncio

import pytest
from pydantic import ValidationError

from app.agents.graph_agent import generate_graph_resource
from app.agents.quiz_agent import generate_quiz_resource
from app.agents.resource_cache import clear_resource_cache
from app.agents.resource_context import build_whitelist, retrieve_resource_docs
from app.agents.resource_schemas import (
    GraphPayload,
    QuizOption,
    QuizQuestion,
    QuizType,
    ResourceEnvelope,
)
from app.agents.resources_graph import run_resource_generation
from app.agents.verify_agent import verify_claims


def test_each_resource_has_valid_envelope() -> None:
    asyncio.run(_assert_each_resource_has_valid_envelope())


async def _assert_each_resource_has_valid_envelope() -> None:
    clear_resource_cache()
    state = await run_resource_generation("麻黄", max_concurrency=5)

    assert set(state["resources"]) == {"explain", "graph", "mindmap", "quiz", "reading"}
    for resource in state["resources"].values():
        validated = ResourceEnvelope.model_validate(resource.model_dump(mode="json"))
        assert validated.type == resource.type
        assert validated.cache_key
        assert validated.generated_by


def test_graph_nodes_all_in_retrieval_whitelist() -> None:
    asyncio.run(_assert_graph_nodes_all_in_retrieval_whitelist())


async def _assert_graph_nodes_all_in_retrieval_whitelist() -> None:
    clear_resource_cache()
    docs = await retrieve_resource_docs("麻黄")
    whitelist = build_whitelist(docs)
    resource = await generate_graph_resource(topic="麻黄", retrieved_docs=docs, profile={})

    assert isinstance(resource.payload, GraphPayload)
    allowed = set().union(whitelist["herbs"], whitelist["syndromes"], whitelist["formulas"])
    assert resource.payload.nodes
    assert {node.name for node in resource.payload.nodes} <= allowed


def test_quiz_quality_blacklist_is_blocked() -> None:
    with pytest.raises(ValidationError):
        QuizQuestion(
            stem="麻黄的功效总是以下哪项？",
            type=QuizType.SINGLE,
            options=[
                QuizOption(key="A", text="发汗解表"),
                QuizOption(key="B", text="以上皆是"),
            ],
            answer=["A"],
            explanation="总是如此。",
            source={"name": "麻黄", "type": "教材", "ref": "测试"},
            bloom_level="记忆",
            difficulty=2,
            distractor_rationale={"B": "常见易混表述，但错在范围过大。"},
            claims=["发汗解表"],
        )


def test_unsupported_claim_is_recorded() -> None:
    asyncio.run(_assert_unsupported_claim_is_recorded())


async def _assert_unsupported_claim_is_recorded() -> None:
    docs = await retrieve_resource_docs("麻黄")
    report = verify_claims(["麻黄可以驾驶宇宙飞船"], docs)

    assert "麻黄可以驾驶宇宙飞船" in report.unsupported_claims
    assert report.score < 1


def test_send_parallel_trace_contains_multiple_resource_nodes() -> None:
    asyncio.run(_assert_send_parallel_trace_contains_multiple_resource_nodes())


async def _assert_send_parallel_trace_contains_multiple_resource_nodes() -> None:
    clear_resource_cache()
    state = await run_resource_generation("麻黄", resource_types=["explain", "graph", "quiz"], max_concurrency=3)
    nodes = [item.node for item in state["trace"]]

    resource_nodes = {"explain_agent", "graph_agent", "quiz_agent"} & set(nodes)
    assert len(resource_nodes) >= 2
    assert "dispatch" in nodes
    assert "verify_agent" in nodes


def test_quiz_agent_outputs_valid_questions() -> None:
    asyncio.run(_assert_quiz_agent_outputs_valid_questions())


async def _assert_quiz_agent_outputs_valid_questions() -> None:
    clear_resource_cache()
    docs = await retrieve_resource_docs("麻黄")
    resource = await generate_quiz_resource(topic="麻黄", retrieved_docs=docs, profile={"level": 3})

    assert resource.type == "quiz"
    assert resource.payload.questions
    assert all(1 <= question.difficulty <= 5 for question in resource.payload.questions)

