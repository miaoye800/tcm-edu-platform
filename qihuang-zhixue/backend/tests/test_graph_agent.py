import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from app.agents.graph_contract import GraphRelationDraft
from app.agents.graph_details import node_detail
from app.agents.graph_intelligence import GraphAgent
from app.api.graph import get_graph_db_path
from app.api.resources import get_resource_db_path
from app.main import app
from app.path.store import upsert_node
from app.resources import service as resource_service
from app.resources import storage as resource_storage
from app.kb.ingest import load_herbs
from tests.helpers import auth_session


def _center() -> tuple[str, str]:
    herb = next(item for item in load_herbs() if item.combinations and item.indications)
    return herb.id, herb.name


async def _collect(iterator):
    return [item async for item in iterator]


def test_relation_enum_invalid_response_retries_then_drops() -> None:
    async def run() -> None:
        _, name = _center()
        agent = GraphAgent(llm_call=stub)
        doc = agent._doc_by_name(name)
        assert doc is not None
        relations = await agent._llm_relations("center", doc, [doc])
        assert relations == []

    calls = 0

    async def stub(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"relations":[{"source_id":"a","target_id":"b","relation_type":"不存在","label":"x","sources":[{"出处":"教材","原文摘录":"x","定位":"p"}],"confidence":0.8}]}'

    asyncio.run(run())
    assert calls == 2


def test_relation_without_source_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GraphRelationDraft.model_validate({"source_id": "a", "target_id": "b", "relation_type": "功效相通", "label": "功效相通", "sources": [], "confidence": 0.8})


def test_rules_have_sources_and_complete_herb_syndrome_formula_chain(tmp_path: Path, monkeypatch) -> None:
    center_id, name = _center()
    monkeypatch.setattr(GraphAgent, "_llm_enabled", lambda _self: False)
    content = asyncio.run(GraphAgent().generate(center_id=center_id, center_name=name, db_path=tmp_path / "graph.db"))
    assert content.edges
    assert content.sources
    assert all(edge.source_ref < len(content.sources) for edge in content.edges)
    assert any(edge.relation_type == "药证链" for edge in content.edges)
    assert any(edge.relation_type == "证方链" for edge in content.edges)
    types = {node.type for node in content.nodes}
    assert {"herb", "syndrome", "formula"} <= types
    cached = asyncio.run(GraphAgent().generate(center_id=center_id, center_name=name, db_path=tmp_path / "graph.db"))
    assert cached == content


def test_node_detail_cache_does_not_regenerate(tmp_path: Path, monkeypatch) -> None:
    center_id, name = _center()
    upsert_node({"id": center_id, "name": name, "level": "point", "exam_frequency": 0.91}, tmp_path / "detail.db")
    import app.agents.graph_details as details
    original = details.load_herbs
    calls = 0

    def counted():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(details, "load_herbs", counted)
    first = asyncio.run(node_detail(center_id, tmp_path / "detail.db"))
    second = asyncio.run(node_detail(center_id, tmp_path / "detail.db"))
    assert first == second
    assert calls == 1
    assert first and first["关联考点"]["exam_frequency"] == 0.91


def test_graph_task_sse_order_and_no_key_rule_fallback(tmp_path: Path, monkeypatch) -> None:
    center_id, name = _center()
    db_path = tmp_path / "task.db"
    upsert_node({"id": center_id, "name": name, "level": "point"}, db_path)
    monkeypatch.setattr(GraphAgent, "_llm_enabled", lambda _self: False)
    task = resource_storage.create_task("graph-task", "u1", center_id, "graph", db_path, meta={"params": {"view": "compact"}})
    events = asyncio.run(_collect(resource_service.stream_task(task["task_id"], "u1", db_path)))
    kinds = [event["event"] for event in events]
    assert kinds[-1] == "done"
    assert "error" not in kinds
    assert kinds.count("step_start") == kinds.count("step_done") == 4
    assert all(event["step_index"] <= event["total_steps"] for event in events)
    resource = resource_storage.get_resource(events[-1]["payload"]["resource_id"], "u1", db_path)
    assert resource and resource["content"]["edges"]
    assert resource["notes"].strip()
    assert resource["meta"]["params"] == {"view": "compact"}


def test_refine_replays_original_params(tmp_path: Path) -> None:
    center_id, name = _center()
    db_path = tmp_path / "refine.db"
    upsert_node({"id": center_id, "name": name, "level": "point"}, db_path)
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "resource-user", db_path)
    resource_storage.create_resource({"id": "r1", "user_id": session["user"]["user_id"], "node_id": center_id, "type": "graph", "title": "x", "content": {}, "sources": [], "notes": "n", "status": "ready", "created_at": datetime.now(UTC).isoformat(), "meta": {"params": {"focus": "配伍"}}}, db_path)
    response = client.post("/api/resources/r1/refine", headers=session["headers"])
    assert response.status_code == 201, response.text
    assert response.json()["meta"]["params"] == {"focus": "配伍"}


def test_doc_blocks_include_rich_types() -> None:
    blocks = resource_service._doc_blocks({"sections": [{"title": "功效", "content": "正文", "claims": ["发汗解表"], "sources": [{"ref": "教材定位", "quote": "短摘录"}]}, {"title": "用量与使用注意", "content": "谨慎", "claims": [], "sources": []}]})
    assert {"heading", "paragraph", "bullet", "table", "callout", "quote"} <= {block["type"] for block in blocks}
