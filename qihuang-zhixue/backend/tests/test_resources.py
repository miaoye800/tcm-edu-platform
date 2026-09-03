import asyncio
import json
from pathlib import Path

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
from app.api.resources import get_resource_db_path
from app.path.store import upsert_node
from app.resources import service as resource_service
from app.resources import storage as resource_storage
from app.main import app
from fastapi.testclient import TestClient
from tests.helpers import auth_session


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


def test_node_resource_task_sse_resume_and_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "qihuang.db"
    upsert_node({"id": "mahuang", "name": "麻黄", "level": "point"}, db_path)
    app.dependency_overrides[get_resource_db_path] = lambda: db_path

    async def stub(resource_type: str, topic: str):
        assert topic == "麻黄"
        if resource_type == "doc":
            return ({"blocks": [{"type": "heading", "text": "麻黄"}, {"type": "paragraph", "text": "发汗解表 [0]"}]}, [{"出处": "本草纲目", "原文摘录": "麻黄", "定位": "草部"}])
        return ({"nodes": []}, [])

    monkeypatch.setattr(resource_service, "generate_content", stub)
    client = TestClient(app)
    session = auth_session(client, "resource-user", db_path)
    created = client.post("/api/resource/tasks", json={"node_id": "mahuang", "resource_type": "doc"}, headers=session["headers"])
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]

    # Consume one event then close: the resumed iterator must not restart it.
    iterator = resource_service.stream_task(task_id, session["user"]["user_id"], db_path)
    first = asyncio.run(anext(iterator))
    asyncio.run(iterator.aclose())
    assert first["event"] == "step_start"
    remaining = asyncio.run(_collect(resource_service.stream_task(task_id, session["user"]["user_id"], db_path)))
    assert remaining[0]["event"] == "step_done"
    events = [first, *remaining]
    assert events[-1]["event"] == "done"
    starts = [event for event in events if event["event"] == "step_start"]
    dones = [event for event in events if event["event"] == "step_done"]
    assert len(starts) == len(dones) == 5
    assert [event["payload"]["block"]["text"] for event in events if event["event"] == "block"] == ["麻黄", "发汗解表 [0]"]

    resource_id = events[-1]["payload"]["resource_id"]
    detail = client.get(f"/api/resources/{resource_id}", headers=session["headers"])
    assert detail.status_code == 200
    assert detail.json()["content"]["blocks"][1]["text"] == "发汗解表 [0]"
    favorite = client.post(f"/api/resources/{resource_id}/favorite", headers=session["headers"])
    assert favorite.json()["favorited"] is True
    listing = client.get("/api/resources", params={"node_id": "mahuang", "type": "doc", "favorited": True, "q": "麻黄"}, headers=session["headers"])
    assert listing.json()["total"] == 1


async def _collect(iterator):
    return [event async for event in iterator]


def test_resource_storage_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "qihuang.db"
    resource_storage.init_db(db_path)
    resource_storage.init_db(db_path)
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        assert {"user_resource", "resource_task"} <= {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_resource_task_marks_failed_when_generator_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "qihuang.db"
    upsert_node({"id": "mahuang", "name": "麻黄", "level": "point"}, db_path)
    task = resource_storage.create_task("failed-task", "u1", "mahuang", "quiz", db_path)

    async def fail(*_args, **_kwargs):
        raise RuntimeError("no key")

    monkeypatch.setattr(resource_service, "generate_content", fail)
    events = asyncio.run(_collect(resource_service.stream_task(task["task_id"], "u1", db_path)))
    assert events[-1]["event"] == "error"
    assert resource_storage.get_task("failed-task", "u1", db_path)["status"] == "failed"
