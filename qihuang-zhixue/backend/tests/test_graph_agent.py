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
from app.kg import entities as kg_entities
from app.kg import store as kg_store
from app.kg.schema import ensure_schema
from app.kg.store import KgEdge
from app.main import app
from app.path.state import set_state
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


def test_center_graph_endpoint_serves_precomputed_kg(tmp_path: Path) -> None:
    """``/api/graph/{id}`` 读预计算的 KG 边，不再现场调 LLM 生成再缓存。

    这是「图谱作为服务」的落地：图在构建期由 ``scripts/build_kg.py`` 算好、
    带教材页码落库，请求期只做图遍历。所以第二次请求必然与第一次完全相同
    ——不是因为命中了 ``graph_cache``，而是因为数据源本来就是静态的。
    """

    db_path = tmp_path / "graph-api.db"
    ensure_schema(db_path)
    entity_list, alias_list = kg_entities.build_registry(db_path)
    kg_store.upsert_entities(entity_list, db_path)
    kg_store.upsert_aliases(alias_list, db_path)

    center = next(e for e in entity_list if e.kind == "herb" and e.status == "approved")
    partner = next(
        e for e in entity_list
        if e.kind == "herb" and e.status == "approved" and e.id != center.id
    )
    syndrome = next(e for e in entity_list if e.kind == "syndrome")
    formula = next(e for e in entity_list if e.kind == "formula")

    evidence = [{"ref": "《中药学》新世纪第四版", "page": 100, "quote": "教材原文摘录"}]
    kg_store.upsert_edges(
        [
            KgEdge(source_id=center.id, target_id=syndrome.id, relation_type="药证链", tier=2, label="主治", evidence=evidence),
            KgEdge(source_id=center.id, target_id=formula.id, relation_type="组成", tier=2, label="组成", evidence=evidence),
            KgEdge(source_id=center.id, target_id=partner.id, relation_type="相须", tier=2, label="相须", evidence=evidence),
            # T3 边就算被误写进库，也不能出现在图里——它回答不了任何一个真实问题
            KgEdge(source_id=center.id, target_id=partner.id, relation_type="功效相通", tier=3, label="功效相通", evidence=evidence),
        ],
        db_path,
    )

    app.dependency_overrides[get_graph_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "graph-api-user", db_path)

    # /default-center 必须先于 /{center_id} 匹配，否则会被当成 center_id。
    default = client.get("/api/graph/default-center", headers=session["headers"])
    assert default.status_code == 200, default.text
    assert "center_id" in default.json()

    first = client.get(f"/api/graph/{center.id}", headers=session["headers"])
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["center_id"] == center.id
    assert payload["nodes"] and payload["edges"]
    types = {edge["relation_type"] for edge in payload["edges"]}
    assert {"药证链", "组成", "相须"} <= types
    assert "功效相通" not in types

    # 红线：每条边都必须能指回一条带页码的溯源
    assert payload["sources"]
    for edge in payload["edges"]:
        assert 0 <= edge["source_ref"] < len(payload["sources"])
        assert payload["sources"][edge["source_ref"]]["原文摘录"]

    second = client.get(f"/api/graph/{center.id}", headers=session["headers"])
    assert second.json() == payload


def test_default_center_never_opens_a_blank_canvas(tmp_path: Path) -> None:
    """学习路径停在总论节点时，默认中心必须退到真能出图的那味药。

    实测缺陷：用户当前节点是 ``general.p1``（性味归经）——它是学习路径上的
    合法节点，但不是药材。``/default-center`` 原样把它当中心，前端再拿它请求
    ``/api/graph/general.p1``，回来的是「1 个节点 / 0 条关系」，打开 /atlas
    就是一张空白画布。原来的测试只断言 ``"center_id" in json()``，所以这个
    缺陷一直没被挡住。
    """

    db_path = tmp_path / "graph-default-center.db"
    herbs = [herb for herb in load_herbs() if herb.combinations and herb.indications]
    center_herb, partner_herb = herbs[0], herbs[1]

    # 只有 center_herb 在知识树里有 point 节点，兜底入口因此唯一确定。
    upsert_node({"id": "general.p1", "name": "性味归经", "level": "point"}, db_path)
    upsert_node({"id": "d01.s1.p1", "name": center_herb.name, "level": "point"}, db_path)

    ensure_schema(db_path)
    entity_list, alias_list = kg_entities.build_registry(db_path)
    kg_store.upsert_entities(entity_list, db_path)
    kg_store.upsert_aliases(alias_list, db_path)

    center = next(entity for entity in entity_list if entity.id == center_herb.id)
    partner = next(entity for entity in entity_list if entity.id == partner_herb.id)
    assert center.knowledge_node_id == "d01.s1.p1"
    assert partner.knowledge_node_id is None
    kg_store.upsert_edges(
        [
            KgEdge(
                source_id=center.id,
                target_id=partner.id,
                relation_type="同类鉴别",
                tier=2,
                label="同类鉴别",
                evidence=[{"ref": "《中药学》", "page": 100, "quote": "教材原文摘录"}],
            )
        ],
        db_path,
    )

    app.dependency_overrides[get_graph_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "graph-default-center-user", db_path)
    set_state(session["user"]["user_id"], "general.p1", "current", db_path=db_path)

    default = client.get("/api/graph/default-center", headers=session["headers"])
    assert default.status_code == 200, default.text
    center_id = default.json()["center_id"]
    assert center_id == "d01.s1.p1", "当前节点出不了图时，不能把它当中心"

    payload = client.get(f"/api/graph/{center_id}", headers=session["headers"]).json()
    assert payload["nodes"], "默认中心必须能出节点"
    assert payload["edges"], "默认中心必须能出关系，否则就是空白画布"


def test_center_graph_endpoint_marks_orphan_instead_of_faking(tmp_path: Path) -> None:
    """孤儿药材（只在别处被引用、没有自己的条目）返回显式的「教材未收录」。"""

    db_path = tmp_path / "graph-api-orphan.db"
    ensure_schema(db_path)
    entity_list, alias_list = kg_entities.build_registry(db_path)
    kg_store.upsert_entities(entity_list, db_path)
    kg_store.upsert_aliases(alias_list, db_path)

    orphan = next(e for e in entity_list if e.kind == "herb" and e.status == "orphan")
    app.dependency_overrides[get_graph_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "graph-api-orphan-user", db_path)

    response = client.get(f"/api/graph/{orphan.id}", headers=session["headers"])
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["edges"] == []
    assert payload["nodes"][0]["name"] == orphan.name
    assert payload["nodes"][0]["brief"] == "教材未收录"


def test_center_graph_endpoint_returns_placeholder_for_unknown_node(tmp_path: Path) -> None:
    """未知节点不再 404。

    旧行为是 404，前端 ``stores/graph.ts`` 收到失败后回落 ``mockMahuangGraph``
    （15 个硬编码节点），用户看到的「麻黄图」就是这么来的。现在返回一个显式的
    「教材未收录」单节点，让缺失可见而不是被假数据掩盖。
    """

    db_path = tmp_path / "graph-api-404.db"
    app.dependency_overrides[get_graph_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "graph-api-404-user", db_path)
    response = client.get("/api/graph/not-a-real-node", headers=session["headers"])
    assert response.status_code == 200
    payload = response.json()
    assert payload["center_id"] == "not-a-real-node"
    assert payload["edges"] == []
    assert payload["sources"] == []
    assert payload["nodes"][0]["brief"] == "教材未收录"


def test_doc_blocks_include_rich_types() -> None:
    blocks = resource_service._doc_blocks({"sections": [{"title": "功效", "content": "正文", "claims": ["发汗解表"], "sources": [{"ref": "教材定位", "quote": "短摘录"}]}, {"title": "用量与使用注意", "content": "谨慎", "claims": [], "sources": []}]})
    assert {"heading", "paragraph", "bullet", "table", "callout", "quote"} <= {block["type"] for block in blocks}
