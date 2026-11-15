"""总控对话 Agent 的判据测试。

守六件事，每件都对着一种真实会发生的退化：

1. **工具面干净**：不能把 ``db_path`` 这类内部字段暴露给模型，也不能暴露
   ``command`` 类与必然 501 的能力。
2. **工具名可逆**：显式映射表；字符串反解在出现三段名字时必错。
3. **错参回灌而不是吞掉**：``extra="ignore"`` 是给前端的，不是给模型的。
4. **规则兜底认得对**：产物词 → 对应工坊；导航词 → 不生成。
5. **无 key 也能跑完一轮**：受理 → 工序 → 方案卡，状态停在 ``awaiting_plan``。
6. **轮次幂等**：同一个 ``client_turn_id`` 重放不产生第二条消息。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.resources import get_resource_db_path
from app.assistant import loop as loop_module
from app.assistant import router as rules
from app.assistant import store, tools
from app.assistant.api import get_assistant_db_path
from app.assistant.events import forget_all, open_channel
from app.capabilities.http import get_capability_db_path
from app.llm.router import ModelRouter
from app.main import app
from app.path.store import upsert_node
from tests.helpers import auth_session, keyless_settings

ACTOR = SimpleNamespace(user_id="u_assistant")


@pytest.fixture(autouse=True)
def _clean_channels():
    forget_all()
    yield
    forget_all()


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "qihuang.db"
    upsert_node({"id": "general", "name": "中药学总论", "level": "domain"}, path)
    return path


# --------------------------------------------------------------------------- #
# 工具面
# --------------------------------------------------------------------------- #


def test_tool_surface_excludes_internal_fields_and_placeholders() -> None:
    schemas = {item["function"]["name"]: item["function"] for item in tools.tool_schemas()}
    assert set(schemas) == {
        "mindmap_generate",
        "ppt_generate",
        "doc_generate",
        "quiz_generate",
        "story_generate",
        "kg_ask",
        "kg_compare",
        "kg_lookup",
        "kg_path",
        "kg_review",
        "kg_attribute",
        "kb_search",
        "profile_view",
        # 规划是「学什么、多久考」这类需求的唯一出口。没有它，助手面对
        # 「我想学中药学，两年后考研」只能把用户推回「你要导图还是幻灯」。
        "plan_create",
        # 路线的只读快照（导图/文稿）与「在地图上定位」。前者让计划能带走，
        # 后者让「合谷在哪」不必强行跳页。
        "plan_present",
        "path_focus",
    }
    # command 类作用于「已打开的那张图」，对话里没有这个上下文，不该进工具面。
    assert "mindmap_present" not in schemas
    assert "mindmap_expand" not in schemas
    # 仍是 501 占位的资源生成不该出现：给模型一个必然失败的工具，等于教它重试。
    assert "resource_generate" not in schemas
    for name, schema in schemas.items():
        assert "db_path" not in (schema["parameters"].get("properties") or {}), f"{name} 泄露了内部字段"


def test_tool_name_maps_back_to_capability_explicitly() -> None:
    assert tools.capability_of("mindmap_generate") == "mindmap.generate"
    assert tools.capability_of("doc_generate") == "doc.generate"
    # 传能力名本身不该被「反解」成功——映射是单向表，不是字符串运算。
    assert tools.capability_of("mindmap.generate") is None
    assert tools.capability_of("nope_nope") is None


def test_unknown_parameter_is_returned_instead_of_swallowed() -> None:
    outcome = asyncio.run(tools.call_tool("mindmap_generate", {"query": "麻黄", "audience": "规培医师"}, ACTOR))
    assert outcome.ok is False
    assert outcome.error_code == "assistant.unknown_params"
    assert "audience" in json.dumps(outcome.payload, ensure_ascii=False)
    assert "query" in outcome.payload["accepted"]


# --------------------------------------------------------------------------- #
# 规则
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("做一份人参的讲稿，12 页", "ppt.generate"),
        ("把十八反整理成一张思维导图", "mindmap.generate"),
        ("写一篇人参的用法与禁忌", "doc.generate"),
        ("人参和藜芦能一起用吗", "kg.ask"),
        # 导航不是生成：这句话里出现了「导图」，但用户要的是打开已有的那张。
        ("打开我的导图", None),
        ("人参有什么功效", None),
    ],
)
def test_rules_pick_the_right_capability(text: str, expected: str | None) -> None:
    route = rules.route(text)
    assert (route.capability if route else None) == expected


# --------------------------------------------------------------------------- #
# 会话存储
# --------------------------------------------------------------------------- #


def test_conversation_is_scoped_to_its_owner(tmp_path: Path) -> None:
    db = _db(tmp_path)
    conversation = store.create_conversation("u_a", "第一句", db)
    assert store.get_conversation(conversation["conversation_id"], "u_a", db) is not None
    assert store.get_conversation(conversation["conversation_id"], "u_b", db) is None
    assert store.list_messages(conversation["conversation_id"], "u_b", db_path=db) == []


def test_turn_key_finds_the_same_run(tmp_path: Path) -> None:
    db = _db(tmp_path)
    conversation = store.create_conversation("u_a", "t", db)
    message = store.append_message(conversation["conversation_id"], "user", {"type": "text", "text": "hi"}, db_path=db)
    run = store.create_run(conversation["conversation_id"], message["message_id"], turn_key="turn-1", db_path=db)
    found = store.find_run_by_turn(conversation["conversation_id"], "turn-1", db)
    assert found is not None and found["run_id"] == run["run_id"]
    assert store.find_run_by_turn(conversation["conversation_id"], "", db) is None


# --------------------------------------------------------------------------- #
# 无 key 跑完一轮
# --------------------------------------------------------------------------- #


def test_keyless_generation_stops_at_the_plan_gate(tmp_path: Path) -> None:
    db = _db(tmp_path)
    conversation = store.create_conversation("u_a", "t", db)
    message = store.append_message(conversation["conversation_id"], "user", {"type": "text", "text": "写一篇人参的用法"}, db_path=db)
    run = store.create_run(conversation["conversation_id"], message["message_id"], db_path=db)
    channel = open_channel(run["run_id"])

    async def scenario() -> list[dict]:
        events: list[dict] = []

        async def drain() -> None:
            async for item in channel.stream():
                events.append(item)

        reader = asyncio.create_task(drain())
        result = await loop_module.run_turn(
            conversation_id=conversation["conversation_id"],
            run_id=run["run_id"],
            text="写一篇人参的用法",
            history=[],
            actor=ACTOR,
            channel=channel,
            db_path=db,
            model_router=ModelRouter(settings=keyless_settings()),
        )
        channel.close()
        await asyncio.wait_for(reader, timeout=10)
        assert result["status"] == "awaiting_plan"
        return events

    events = asyncio.run(scenario())
    names = [item["event"] for item in events]
    assert names[0] == "tool_start"
    assert "tool_result" in names
    assert "plan_card" in names
    assert "tool_progress" in names

    artifacts = store.list_artifacts(conversation["conversation_id"], "u_a", db)
    assert len(artifacts) == 1
    assert artifacts[0]["state"] == "awaiting_plan"
    assert artifacts[0]["kind"] == "doc"
    assert artifacts[0]["task_id"]


def test_keyless_query_answers_from_the_graph(tmp_path: Path) -> None:
    """「能一起用吗」在无 key 下也要有答案：规则路由到 kg.ask，答案来自图谱本身。"""

    db = _db(tmp_path)
    conversation = store.create_conversation("u_a", "t", db)
    message = store.append_message(conversation["conversation_id"], "user", {"type": "text", "text": "人参和藜芦能一起用吗"}, db_path=db)
    run = store.create_run(conversation["conversation_id"], message["message_id"], db_path=db)
    channel = open_channel(run["run_id"])

    async def scenario() -> dict:
        result = await loop_module.run_turn(
            conversation_id=conversation["conversation_id"],
            run_id=run["run_id"],
            text="人参和藜芦能一起用吗",
            history=[],
            actor=ACTOR,
            channel=channel,
            db_path=db,
            model_router=ModelRouter(settings=keyless_settings()),
        )
        channel.close()
        return result

    result = asyncio.run(scenario())
    assert result["status"] == "done"
    assert result["steps"], "没有执行任何工具"
    assert result["steps"][0]["capability"] == "kg.ask"
    assert result["text"].strip()


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, dict[str, str], Path]:
    db = _db(tmp_path)
    app.dependency_overrides[get_resource_db_path] = lambda: db
    app.dependency_overrides[get_capability_db_path] = lambda: db
    app.dependency_overrides[get_assistant_db_path] = lambda: db
    # 端点内部 new 一个 ModelRouter；换成无 key 的，才测得到规则兜底那条路。
    monkeypatch.setattr(loop_module, "ModelRouter", lambda *args, **kwargs: ModelRouter(settings=keyless_settings()))
    client = TestClient(app)
    session = auth_session(client, "assistant-user", db)
    return client, session["headers"], db


def _events_of(text: str) -> list[str]:
    return [line[len("event: ") :] for line in text.splitlines() if line.startswith("event: ")]


def test_http_chat_stream_persists_and_replays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, headers, _ = _client(tmp_path, monkeypatch)

    created = client.post("/api/assistant/conversations", json={"title": "试一下"}, headers=headers)
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]

    payload = {"conversation_id": conversation_id, "text": "写一篇人参的用法", "client_turn_id": "turn-http-1"}
    response = client.post("/api/assistant/chat/stream", json=payload, headers=headers)
    assert response.status_code == 200
    events = _events_of(response.text)
    assert events[0] == "message_start"
    assert "plan_card" in events
    assert events[-1] == "done"

    detail = client.get(f"/api/assistant/conversations/{conversation_id}", headers=headers).json()
    # 用户一句 + 小岐一句 + 方案卡一条（方案卡必须落库，否则刷新后没法确认）
    assert len(detail["messages"]) == 3
    assert detail["messages"][0]["role"] == "user"
    assert [m["content"]["type"] for m in detail["messages"]] == ["text", "text", "plan"]
    assert detail["messages"][2]["content"]["taskId"]
    assert detail["artifacts"] and detail["artifacts"][0]["kind"] == "doc"

    # 同一个 client_turn_id 重放：不产生第二条用户消息，也不起第二个任务。
    again = client.post("/api/assistant/chat/stream", json=payload, headers=headers)
    assert again.status_code == 200
    assert "plan_card" in _events_of(again.text)
    detail_again = client.get(f"/api/assistant/conversations/{conversation_id}", headers=headers).json()
    assert len(detail_again["messages"]) == 3


def test_http_conversation_list_is_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, headers, db = _client(tmp_path, monkeypatch)
    client.post("/api/assistant/conversations", json={"title": "甲"}, headers=headers)
    mine = client.get("/api/assistant/conversations", headers=headers).json()["items"]
    assert len(mine) == 1

    other = auth_session(client, "another-user", db)
    theirs = client.get("/api/assistant/conversations", headers=other["headers"]).json()["items"]
    assert theirs == []
