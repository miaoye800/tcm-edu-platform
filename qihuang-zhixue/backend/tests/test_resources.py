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
    GraphLink,
    GraphNode,
    GraphNodeCategory,
    GraphPayload,
    GraphRelation,
    QuizOption,
    QuizQuestion,
    QuizType,
    ResourceEnvelope,
)
from app.agents.resources_graph import run_resource_generation
from app.agents.verify_agent import _verify_graph, verify_claims
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


def test_formula_whitelist_holds_display_name_not_only_id() -> None:
    """方剂白名单必须同时收 id 和正名，否则方剂节点会被静默删光。

    ``docs`` 里的 ``formula_ref`` 是 id（``formula_yupingfengsan``），
    模型生成的节点 ``name`` 是正名（「玉屏风散」），校验比的是 ``node.name``。
    只收 id 的话，每个方剂节点都会被判成「无依据」——不报错，图里悄悄少一类节点。
    """

    docs = asyncio.run(retrieve_resource_docs("黄芪"))
    whitelist = build_whitelist(docs)

    assert whitelist["formulas"], "黄芪的配伍里应该有方剂"
    assert any(item.startswith("formula_") for item in whitelist["formulas"]), "id 要保留，下游还要反查"
    assert "玉屏风散" in whitelist["formulas"], f"正名没进白名单：{whitelist['formulas']}"


def test_formula_node_named_by_display_name_survives_verification() -> None:
    docs = asyncio.run(retrieve_resource_docs("黄芪"))
    whitelist = build_whitelist(docs)

    payload = GraphPayload(
        nodes=[
            GraphNode(id="黄芪", name="黄芪", category=GraphNodeCategory.HERB, symbolSize=58),
            GraphNode(
                id="formula_yupingfengsan",
                name="玉屏风散",
                category=GraphNodeCategory.FORMULA,
                symbolSize=36,
            ),
        ],
        links=[
            GraphLink(
                source="黄芪",
                target="formula_yupingfengsan",
                relation=GraphRelation.XIANG_XU,
                evidence="益气固表止汗，用于表虚自汗",
            )
        ],
    )

    kept, unsupported = _verify_graph(payload, whitelist)

    assert "玉屏风散" not in unsupported, "方剂正名被误判成越界节点"
    assert {node.name for node in kept.nodes} == {"黄芪", "玉屏风散"}
    assert kept.links


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


def test_doc_task_uses_params_topic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """doc 的 layout 步必须读 params.topic 自由主题，而不是写死节点名。

    文档工坊以 node_id=general + params.topic 起稿；若 layout 步回落到
    storage.node_name，生成内容会永远是「中药学总论」而不是用户输入的话题。
    """
    db_path = tmp_path / "qihuang.db"
    upsert_node({"id": "general", "name": "中药学总论", "level": "domain"}, db_path)
    app.dependency_overrides[get_resource_db_path] = lambda: db_path

    seen: dict[str, str] = {}

    async def stub(resource_type: str, topic: str):
        if resource_type == "doc":
            seen["topic"] = topic
            return ({"blocks": [{"type": "heading", "text": topic}]}, [])
        return ({"nodes": []}, [])

    monkeypatch.setattr(resource_service, "generate_content", stub)
    client = TestClient(app)
    session = auth_session(client, "doc-topic-user", db_path)
    created = client.post(
        "/api/resource/tasks",
        json={"node_id": "general", "resource_type": "doc", "params": {"topic": "五行学说的临床应用"}},
        headers=session["headers"],
    )
    assert created.status_code == 201, created.text
    events = asyncio.run(_collect(resource_service.stream_task(created.json()["task_id"], session["user"]["user_id"], db_path)))
    assert events[-1]["event"] == "done"
    assert seen["topic"] == "五行学说的临床应用"
    # 标题同样跟随自由主题（_task_topic），资源卡不再显示「中药学总论」。
    resource_id = events[-1]["payload"]["resource_id"]
    detail = client.get(f"/api/resources/{resource_id}", headers=session["headers"])
    assert detail.status_code == 200
    assert detail.json()["title"].startswith("五行学说的临床应用")


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
