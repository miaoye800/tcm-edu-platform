"""导图工坊：草案归一化、逐节点事件、整树保存的契约测试。"""

import asyncio
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.agents.resource_context import retrieve_resource_docs
from app.api.resources import get_resource_db_path
from app.config import Settings
from app.main import app
from app.mindmap import agent as agent_module
from app.mindmap.agent import MindmapAgent
from app.mindmap.draft import DraftNode, MindmapDraft, normalize_draft
from app.path.store import upsert_node
from app.resources import service as resource_service
from app.resources import storage as resource_storage
from tests.helpers import auth_session, keyless_settings

SOURCES = [
    {"name": "麻黄", "type": "教材", "ref": "p.1", "quote": "发汗解表"},
    {"name": "桂枝", "type": "教材", "ref": "p.2", "quote": "解肌发表"},
]


def _draft() -> MindmapDraft:
    return MindmapDraft(
        root_topic="麻黄",
        branches=[
            DraftNode(topic="性味归经", source_indexes=[0], children=[DraftNode(topic="辛、微苦，温", source_indexes=[0])]),
            DraftNode(topic="功效", source_indexes=[1], children=[DraftNode(topic="发汗解表", source_indexes=[1])]),
        ],
    )


def test_normalize_assigns_deterministic_ids_and_direction():
    data = normalize_draft(_draft(), topic="麻黄", sources=SOURCES)

    root = data.nodeData
    assert root.id == "root" and root.topic == "麻黄"
    assert [node.id for node in root.children] == ["b0", "b1"]
    assert [node.direction for node in root.children] == [0, 1]
    assert root.children[0].children[0].id == "b0-0"
    assert root.children[0].children[0].direction == 0
    assert root.children[1].children[0].direction == 1


def test_normalize_truncates_long_topic_at_punctuation():
    over_long = "本品具有发汗解表、宣肺平喘、利水消肿的功效，为发汗解表之要药"
    data = normalize_draft(
        MindmapDraft(root_topic="麻黄", branches=[DraftNode(topic=over_long)]),
        topic="麻黄",
        sources=SOURCES,
    )

    topic = data.nodeData.children[0].topic
    assert len(topic) <= 30
    assert not topic.endswith("、")


def test_normalize_drops_empty_and_duplicate_siblings():
    data = normalize_draft(
        MindmapDraft(
            root_topic="麻黄",
            branches=[
                DraftNode(topic="功效"),
                DraftNode(topic="功效"),
                DraftNode(topic="   "),
                DraftNode(topic="！！！"),
            ],
        ),
        topic="麻黄",
        sources=SOURCES,
    )

    assert [node.topic for node in data.nodeData.children] == ["功效"]


def test_normalize_filters_out_of_range_source_indexes():
    data = normalize_draft(
        MindmapDraft(root_topic="麻黄", branches=[DraftNode(topic="功效", source_indexes=[0, 0, 5, -1, 1])]),
        topic="麻黄",
        sources=SOURCES,
    )

    assert data.nodeData.children[0].meta.source_indexes == [0, 1]


def test_normalize_respects_requested_depth():
    draft = MindmapDraft(
        root_topic="麻黄",
        branches=[DraftNode(topic="功效", children=[DraftNode(topic="发汗解表", children=[DraftNode(topic="第三层")])])],
    )

    shallow = normalize_draft(draft, topic="麻黄", sources=SOURCES, params={"depth": 2})
    assert shallow.nodeData.children[0].children == []

    default = normalize_draft(draft, topic="麻黄", sources=SOURCES)
    assert default.nodeData.children[0].children[0].topic == "发汗解表"
    assert default.nodeData.children[0].children[0].children == []


def test_normalize_caps_total_nodes_and_branch_children():
    wide = [DraftNode(topic=f"分支{index}", children=[DraftNode(topic=f"要点{child}") for child in range(16)]) for index in range(8)]
    data = normalize_draft(MindmapDraft(root_topic="麻黄", branches=wide), topic="麻黄", sources=SOURCES)

    def count(node):
        return 1 + sum(count(child) for child in node.children)

    assert count(data.nodeData) <= 120
    assert all(len(node.children) <= 10 for node in data.nodeData.children)


def test_agent_uses_structured_draft_when_model_available(monkeypatch):
    async def fake_create(**_kwargs):
        return _draft()

    monkeypatch.setattr(agent_module, "structured_create", fake_create)

    async def run():
        docs = await retrieve_resource_docs("麻黄")
        return await MindmapAgent(settings=Settings(DEEPSEEK_API_KEY="x")).generate("麻黄", docs)

    result = asyncio.run(run())
    root = result.mindmap_data.nodeData
    topics = [node.topic for node in root.children]

    # 骨架由原型决定（单味药固定六轴），模型只被允许替换**叶子**。
    # 模型给的两支必须被采纳，且在原来的位置。
    assert topics[:2] == ["性味归经", "功效"]
    assert [child.topic for child in root.children[0].children] == ["辛、微苦，温"]
    assert [child.topic for child in root.children[1].children] == ["发汗解表"]

    # 模型没提到的分支保留骨架自己的叶子——不许因为模型没写就退化成空壳。
    assert len(root.children) >= 4
    assert all(node.children for node in root.children)
    # 采纳的叶子来自模型，但依据仍按素材下标校验过：引得到就是教材，引不到才算推断。
    assert root.children[0].children[0].meta.evidence == "textbook"


def test_agent_falls_back_when_draft_has_no_branches(monkeypatch):
    async def fake_create(**_kwargs):
        return MindmapDraft(root_topic="麻黄", branches=[])

    monkeypatch.setattr(agent_module, "structured_create", fake_create)

    async def run():
        docs = await retrieve_resource_docs("麻黄")
        return await MindmapAgent(settings=Settings(DEEPSEEK_API_KEY="x")).generate("麻黄", docs)

    result = asyncio.run(run())
    # 模型交白卷时，骨架必须原样留下——这才是「失败也有一张能看的图」。
    assert [node.topic for node in result.mindmap_data.nodeData.children] == [
        "性味归经",
        "功效",
        "主治",
        "配伍应用",
        "相似药鉴别",
        "用量与注意",
    ]
    assert all(node.children for node in result.mindmap_data.nodeData.children)


def test_agent_falls_back_when_model_raises(monkeypatch):
    async def boom(**_kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(agent_module, "structured_create", boom)

    async def run():
        docs = await retrieve_resource_docs("麻黄")
        return await MindmapAgent(settings=Settings(DEEPSEEK_API_KEY="x")).generate("麻黄", docs)

    result = asyncio.run(run())
    assert result.mindmap_data.nodeData.children


def test_keyless_agent_keeps_two_level_template(monkeypatch):
    """无 key 时 mock_factory 回到确定性模板，与改造前结构一致。"""

    async def run():
        docs = await retrieve_resource_docs("麻黄")
        return await MindmapAgent(settings=keyless_settings()).generate("麻黄", docs)

    result = asyncio.run(run())
    assert result.template_id == "warm-gold"
    # 无 key 时直接走确定性骨架：单味药六轴，一支都不空。
    assert [node.topic for node in result.mindmap_data.nodeData.children] == [
        "性味归经",
        "功效",
        "主治",
        "配伍应用",
        "相似药鉴别",
        "用量与注意",
    ]
    assert all(node.children for node in result.mindmap_data.nodeData.children)


# --- 逐节点 SSE 事件 ---------------------------------------------------------


def _content() -> dict:
    return {
        "template_id": "warm-gold",
        "choice_reason": "层级分类内容→warm-gold",
        "mindmap_data": {
            "nodeData": {
                "id": "root",
                "topic": "解表药",
                "children": [
                    {
                        "id": "b0",
                        "topic": "分类",
                        "direction": 0,
                        "meta": {"source_indexes": [0]},
                        "children": [
                            {"id": "b0-0", "topic": "辛温解表", "direction": 0, "meta": {"source_indexes": [0]}, "children": []},
                            {"id": "b0-1", "topic": "辛凉解表", "direction": 0, "meta": {"source_indexes": [0]}, "children": []},
                        ],
                    },
                    {"id": "b1", "topic": "代表药", "direction": 1, "meta": {"source_indexes": [9]}, "children": []},
                ],
            }
        },
        "sources": [{"name": "麻黄", "type": "教材", "ref": "p.1", "quote": "发汗解表"}],
    }


def test_node_events_are_breadth_first_and_parent_first():
    events = resource_service._mindmap_node_events(_content(), 1, 4, "分梳支脉")
    payloads = [event["payload"]["node"] for event in events]

    assert [item["id"] for item in payloads] == ["root", "b0", "b1", "b0-0", "b0-1"]
    assert payloads[0]["parent_id"] is None and payloads[0]["depth"] == 0
    assert all(item["node_total"] == 5 for item in payloads)
    assert all(event["event"] == "node" and event["step_index"] == 1 for event in events)

    seen: set[str] = set()
    for item in payloads:
        assert item["parent_id"] is None or item["parent_id"] in seen
        seen.add(item["id"])


def test_mindmap_verify_trims_out_of_range_sources_without_dropping_nodes():
    content = _content()
    stats = resource_service._mindmap_verify(content, 1)

    assert stats == {"node_count": 5, "grounded": 3, "trimmed": 1}
    children = content["mindmap_data"]["nodeData"]["children"]
    assert children[1]["meta"]["source_indexes"] == []
    assert len(children) == 2


def test_stream_pushes_nodes_at_branch_step_and_titles_with_free_topic(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "qihuang.db"
    upsert_node({"id": "mahuang", "name": "麻黄", "level": "point"}, db_path)
    upsert_node({"id": "general", "name": "中药学总论", "level": "domain"}, db_path)
    task_id = f"task-{uuid4().hex[:8]}"
    resource_storage.create_task(task_id, "u-free", "general", "mindmap", db_path, meta={"params": {"topic": "解表药对比"}})

    async def fake_content(_task, _db_path):
        return _content(), [{"name": "麻黄", "type": "教材", "ref": "p.1", "quote": "发汗解表"}]

    monkeypatch.setattr(resource_service, "_generate_task_content", fake_content)

    async def run():
        return [item async for item in resource_service.stream_task(task_id, "u-free", db_path)]

    events = asyncio.run(run())
    node_events = [item for item in events if item["event"] == "node"]
    assert node_events and node_events[0]["payload"]["node"]["id"] == "root"
    assert all(item["step_index"] == 1 for item in node_events)
    assert any(item["event"] == "mindmap_verify" for item in events)

    done = next(item for item in events if item["event"] == "done")
    resource = resource_storage.get_resource(done["payload"]["resource_id"], "u-free", db_path)
    assert resource is not None
    # 标题取根节点的字，类型后缀用中文：原先的「· mindmap」是字段名，不是人话。
    assert resource["title"] == "解表药 · 思维导图"
    assert resource["meta"]["mindmap_version"] == 1
    assert resource["meta"]["generator"] == "mindmap-agent"


# --- 整树保存 ---------------------------------------------------------------


def _seed_mindmap(db_path: Path, user_id: str, *, resource_type: str = "mindmap") -> str:
    upsert_node({"id": "mahuang", "name": "麻黄", "level": "point"}, db_path)
    resource_id = str(uuid4())
    resource_storage.create_resource(
        {
            "id": resource_id,
            "user_id": user_id,
            "node_id": "mahuang",
            "type": resource_type,
            "title": "麻黄 · mindmap",
            "content": _content(),
            "sources": [{"name": "麻黄", "type": "教材", "ref": "p.1", "quote": "发汗解表"}],
            "notes": "",
            "status": "ready",
            "created_at": "2026-11-04T00:00:00+00:00",
            "meta": {"mindmap_version": 1},
        },
        db_path,
    )
    return resource_id


def _tree_root() -> dict:
    return {
        "id": "root",
        "topic": "解表药",
        "children": [
            {"id": "b0", "topic": "分类", "direction": 0, "children": [{"id": "b0-0", "topic": "辛温解表"}]},
            {"id": "b1", "topic": "代表药", "direction": 1, "meta": {"source_indexes": [0, 7]}},
        ],
    }


def test_save_mindmap_tree_increments_version_and_conflicts(tmp_path: Path):
    db_path = tmp_path / "qihuang.db"
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "mind-owner", db_path)
    resource_id = _seed_mindmap(db_path, session["user"]["user_id"])

    payload = {"version": 1, "nodeData": _tree_root()}
    first = client.put(f"/api/resources/{resource_id}/mindmap/tree", json=payload, headers=session["headers"])
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 2
    assert first.json()["node_count"] == 4
    assert first.json()["depth"] == 3

    detail = client.get(f"/api/resources/{resource_id}", headers=session["headers"]).json()
    assert detail["meta"]["mindmap_version"] == 2
    assert detail["content"]["mindmap_data"]["nodeData"]["children"][0]["children"][0]["topic"] == "辛温解表"
    # 越界依据被静默丢弃：sources 只有 1 条
    assert detail["content"]["mindmap_data"]["nodeData"]["children"][1]["meta"]["source_indexes"] == [0]
    # 生成侧的凭据不被编辑覆盖
    assert detail["content"]["choice_reason"] == "层级分类内容→warm-gold"

    replay = client.put(f"/api/resources/{resource_id}/mindmap/tree", json=payload, headers=session["headers"])
    assert replay.status_code == 409
    assert "版本" in replay.json()["detail"]


def test_save_mindmap_tree_rejects_bad_structure(tmp_path: Path):
    db_path = tmp_path / "qihuang.db"
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "mind-guard", db_path)

    doc_id = _seed_mindmap(db_path, session["user"]["user_id"], resource_type="doc")
    wrong_type = client.put(
        f"/api/resources/{doc_id}/mindmap/tree",
        json={"version": 1, "nodeData": _tree_root()},
        headers=session["headers"],
    )
    assert wrong_type.status_code == 422

    resource_id = _seed_mindmap(db_path, session["user"]["user_id"])

    def reject(node_data: dict) -> int:
        response = client.put(
            f"/api/resources/{resource_id}/mindmap/tree",
            json={"version": 1, "nodeData": node_data},
            headers=session["headers"],
        )
        return response.status_code

    assert reject({"id": "not-root", "topic": "解表药"}) == 422
    assert reject({"id": "root", "topic": "解表药", "children": [{"id": "root", "topic": "重复"}]}) == 422
    assert reject({"id": "root", "topic": "解表药", "children": [{"id": "b 0", "topic": "空格"}]}) == 422
    assert reject({"id": "root", "topic": "   "}) == 422
    assert reject({"id": "root", "topic": "解表药", "children": [{"id": "b0", "topic": "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十一"}]}) == 422

    deep: dict = {"id": "root", "topic": "根"}
    cursor = deep
    for index in range(6):
        child = {"id": f"d{index}", "topic": f"第{index}层"}
        cursor["children"] = [child]
        cursor = child
    assert reject(deep) == 422


def test_save_mindmap_tree_is_scoped_to_owner(tmp_path: Path):
    db_path = tmp_path / "qihuang.db"
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    client = TestClient(app)
    owner = auth_session(client, "mind-a", db_path)
    intruder = auth_session(client, "mind-b", db_path)
    resource_id = _seed_mindmap(db_path, owner["user"]["user_id"])

    response = client.put(
        f"/api/resources/{resource_id}/mindmap/tree",
        json={"version": 1, "nodeData": _tree_root()},
        headers=intruder["headers"],
    )
    assert response.status_code == 404


def test_create_task_validates_mindmap_params(tmp_path: Path):
    db_path = tmp_path / "qihuang.db"
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "mind-params", db_path)
    upsert_node({"id": "mahuang", "name": "麻黄", "level": "point"}, db_path)

    bad = client.post(
        "/api/resource/tasks",
        json={"node_id": "mahuang", "resource_type": "mindmap", "params": {"depth": 9}},
        headers=session["headers"],
    )
    assert bad.status_code == 422
    assert "depth" in bad.json()["detail"]

    good = client.post(
        "/api/resource/tasks",
        json={"node_id": "mahuang", "resource_type": "mindmap", "params": {"depth": 4, "topic": "桂枝汤方解"}},
        headers=session["headers"],
    )
    assert good.status_code == 201
    assert good.json()["resource_type"] == "mindmap"


def test_saved_tree_keeps_the_evidence_tier() -> None:
    """整树保存不能把依据档位洗掉。

    前端 ``toSaveTree`` 把 evidence 放在**节点顶层**（不在 meta 里）。保存接口
    若只读 meta，每自动保存一次「教材实证」就被洗成 None——重开这张图时
    「推断」角标全部消失，底栏三档只剩兜底值。
    """

    from app.mindmap.tree_schema import TreeInNode, validate_tree

    raw = TreeInNode(
        id="root",
        topic="人参与麻黄鉴别",
        evidence="textbook",
        meta={"source_indexes": [0]},
        children=[
            TreeInNode(
                id="b1",
                topic="主治差异",
                evidence="textbook",
                children=[
                    TreeInNode(id="b1-0", topic="人参：气虚欲脱", evidence="textbook"),
                    TreeInNode(id="b1-1", topic="麻黄：咳喘水肿"),
                    TreeInNode(id="b1-2", topic="麻黄：风寒表实", meta={"evidence": "graph"}),
                ],
            )
        ],
    )
    node = validate_tree(raw, source_count=1)
    assert node.meta.evidence == "textbook"
    leaves = node.children[0].children
    assert leaves[0].meta.evidence == "textbook"
    # 顶层没写就退回 meta 里那份，两条路都要认。
    assert leaves[1].meta.evidence is None
    assert leaves[2].meta.evidence == "graph"


def test_unknown_evidence_value_is_not_persisted() -> None:
    """只认三档。自造的第四种「依据」不许进存储结构。"""

    from app.mindmap.tree_schema import TreeInNode, validate_tree

    node = validate_tree(TreeInNode(id="root", topic="测试", evidence="猜测"), source_count=0)
    assert node.meta.evidence is None
