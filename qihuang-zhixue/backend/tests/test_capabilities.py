"""能力契约的判据测试。

守四件事，每件都对应一种真实会发生的退化：

1. **目录完整**：新增能力必须写清「什么时候别用」。缺这一句，模型就会在不该
   用它的时候用它——这不是文档洁癖，是行为约束。
2. **文档不过期**：文档从注册表渲染，一旦有人改了 spec 忘了生成，这里就红。
3. **幂等真的幂等**：同一个请求号重放必须拿到同一个 task，且不建第二张图。
4. **降级诚实**：占位能力返回 501 而不是假装成功；域外主题整棵树标推断。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.resources import get_resource_db_path
from app.capabilities import dispatch, docs, registry
from app.capabilities.bootstrap import install
from app.capabilities.http import get_capability_db_path
from app.main import app
from app.mindmap.params import MAX_DEPTH
from app.path.store import upsert_node
from app.profile.store import DB_PATH
from app.resources import storage as resource_storage
from tests.helpers import auth_session

ACTOR = SimpleNamespace(user_id="u_cap")


# --------------------------------------------------------------------------- #
# 目录与文档
# --------------------------------------------------------------------------- #


def test_registry_is_installed_and_has_the_core_capabilities() -> None:
    install()
    names = {spec.name for spec in registry.all_specs()}
    for expected in ("mindmap.generate", "mindmap.get", "mindmap.present", "mindmap.expand"):
        assert expected in names


def test_every_capability_explains_when_not_to_use_it() -> None:
    install()
    for spec in registry.all_specs():
        assert spec.use_when, f"{spec.name} 没写什么时候该用"
        assert spec.avoid_when, f"{spec.name} 没写什么时候别用"
        # 每条都要带一个真实问句或明确指令，否则对模型没有约束力。
        assert all(len(item) >= 6 for item in spec.use_when), f"{spec.name} 的 use_when 太抽象"
        assert spec.one_line and len(spec.one_line) <= 60, f"{spec.name} 的一句话说明过长"


def test_job_capabilities_declare_artifact_and_pipeline() -> None:
    install()
    for spec in registry.all_specs():
        if spec.kind != "job":
            continue
        assert spec.artifact is not None, f"{spec.name} 是 job 却没写产物"
        assert spec.artifact.open_url_template, f"{spec.name} 的产物没有打开地址"
        assert spec.idempotency, f"{spec.name} 是 job，必须支持幂等"


def test_capability_names_are_domain_dot_verb() -> None:
    install()
    for spec in registry.all_specs():
        head, _, tail = spec.name.partition(".")
        assert head and tail, f"能力名要写成 <domain>.<verb>：{spec.name}"
        assert head.islower() and tail.islower()


def test_generated_docs_match_the_registry() -> None:
    install()
    stale = docs.check()
    assert stale == [], f"能力文档已过期：{stale}；跑 python -m app.capabilities.docs"


def test_placeholder_capabilities_say_so() -> None:
    """仍然只声明不转发的，必须把 501 写在契约里。

    ``kb.search`` 已经接上真实检索（原来在这里），所以它不该再有 501——
    能力实装了却还挂着一行「本期只占位」，调用方会按占位处理，等于白实装。
    """

    install()
    for name in ("resource.generate",):
        entry = registry.get(name)
        assert entry is not None
        assert any(item.http_status == 501 for item in entry.spec.errors), f"{name} 没声明 501"
    kb = registry.get("kb.search")
    assert kb is not None
    assert not any(item.http_status == 501 for item in kb.spec.errors), "kb.search 已实装，不该再声明 501"


# --------------------------------------------------------------------------- #
# HTTP：目录
# --------------------------------------------------------------------------- #


def test_catalog_endpoint_is_public_and_lists_everything() -> None:
    install()
    client = TestClient(app)
    index = client.get("/api/capabilities")
    assert index.status_code == 200
    payload = index.json()
    assert payload["count"] == len(registry.all_specs())
    assert {item["name"] for item in payload["capabilities"]} >= {"mindmap.generate", "kg.compare"}

    one = client.get("/api/capabilities/mindmap.generate")
    assert one.status_code == 200
    assert one.json()["artifact"]["kind"] == "mindmap"
    assert one.json()["input_schema"]["properties"]["query"]

    missing = client.get("/api/capabilities/nope.nope")
    assert missing.status_code == 404
    assert missing.json()["code"] == "capability.unknown"


# --------------------------------------------------------------------------- #
# HTTP：生成与幂等
# --------------------------------------------------------------------------- #


def _setup(tmp_path: Path):
    db_path = tmp_path / "qihuang.db"
    upsert_node({"id": "general", "name": "中药学总论", "level": "domain"}, db_path)
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    app.dependency_overrides[get_capability_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "cap-user", db_path)
    return client, session["headers"], db_path


def test_generate_returns_202_with_stream_and_artifact(tmp_path: Path) -> None:
    client, headers, _ = _setup(tmp_path)
    response = client.post(
        "/api/capabilities/mindmap.generate",
        json={"query": "麻黄", "client_invocation_id": "t-1", "params": {"depth": 3}},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["ok"] and body["capability"] == "mindmap.generate"
    assert body["task_id"]
    assert body["stream_url"] == f"/api/resource/tasks/{body['task_id']}/stream"
    assert body["replayed"] is False
    assert body["artifact"]["structure"] == "auto"
    assert body["artifact"]["resource_id"] is None


def test_same_invocation_id_replays_the_same_task(tmp_path: Path) -> None:
    client, headers, db_path = _setup(tmp_path)
    payload = {"query": "解表药的分类", "client_invocation_id": "t-2"}
    first = client.post("/api/capabilities/mindmap.generate", json=payload, headers=headers).json()
    second = client.post("/api/capabilities/mindmap.generate", json=payload, headers=headers)
    assert second.status_code == 202
    body = second.json()
    assert body["replayed"] is True
    assert body["task_id"] == first["task_id"]
    assert body["invocation_id"] == first["invocation_id"]

    # 只建了一个任务：重放不该在库里留第二条。
    tasks = [row for row in _all_tasks(db_path)]
    assert len(tasks) == 1


def _all_tasks(db_path: Path) -> list[dict]:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM resource_task").fetchall()]


def test_same_invocation_id_with_different_params_is_rejected(tmp_path: Path) -> None:
    client, headers, _ = _setup(tmp_path)
    client.post("/api/capabilities/mindmap.generate", json={"query": "麻黄", "client_invocation_id": "t-3"}, headers=headers)
    clash = client.post(
        "/api/capabilities/mindmap.generate",
        json={"query": "桂枝", "client_invocation_id": "t-3"},
        headers=headers,
    )
    assert clash.status_code == 409
    assert clash.json()["code"] == "capability.idempotency_mismatch"


def test_out_of_range_depth_is_rejected(tmp_path: Path) -> None:
    client, headers, _ = _setup(tmp_path)
    response = client.post(
        "/api/capabilities/mindmap.generate",
        json={"query": "麻黄", "params": {"depth": MAX_DEPTH + 3}},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "capability.invalid_input"


def test_empty_query_is_rejected(tmp_path: Path) -> None:
    client, headers, _ = _setup(tmp_path)
    response = client.post("/api/capabilities/mindmap.generate", json={"query": "   "}, headers=headers)
    assert response.status_code == 422
    assert response.json()["code"] == "mindmap.topic_empty"


def test_unknown_capability_invocation_is_404(tmp_path: Path) -> None:
    client, headers, _ = _setup(tmp_path)
    response = client.post("/api/capabilities/does.not_exist", json={}, headers=headers)
    assert response.status_code == 404
    assert response.json()["code"] == "capability.unknown"


def test_cancel_stops_before_the_next_step(tmp_path: Path) -> None:
    client, headers, db_path = _setup(tmp_path)
    accepted = client.post(
        "/api/capabilities/mindmap.generate",
        json={"query": "细辛", "client_invocation_id": "t-4"},
        headers=headers,
    ).json()

    cancelled = client.post(f"/api/capability-invocations/{accepted['invocation_id']}/cancel", headers=headers)
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "cancelled"

    again = client.post(f"/api/capability-invocations/{accepted['invocation_id']}/cancel", headers=headers)
    assert again.status_code == 409
    assert again.json()["code"] == "invocation.already_finished"

    # 流不用跑完：下一个工序边界就该停，并说明是谁停的。
    from app.resources import service as resource_service

    events = asyncio.run(_collect(resource_service.stream_task(accepted["task_id"], _user_id(headers, db_path), db_path)))
    assert events[-1]["event"] == "error"
    assert "取消" in events[-1]["payload"]["message"]


def _user_id(headers: dict[str, str], db_path: Path) -> str:
    from app.auth.tokens import decode_token

    return decode_token(headers["Authorization"].split(" ", 1)[1])


async def _collect(stream) -> list[dict]:
    out: list[dict] = []
    async for event in stream:
        out.append(event)
    return out


def test_invocation_polling_works_without_the_stream(tmp_path: Path) -> None:
    client, headers, _ = _setup(tmp_path)
    accepted = client.post(
        "/api/capabilities/mindmap.generate",
        json={"query": "黄芪的用量", "client_invocation_id": "t-5"},
        headers=headers,
    ).json()
    polled = client.get(f"/api/capability-invocations/{accepted['invocation_id']}", headers=headers)
    assert polled.status_code == 200
    body = polled.json()
    assert body["task_id"] == accepted["task_id"]
    assert body["status"] == "accepted"
    assert client.get("/api/capability-invocations/inv_unknown", headers=headers).status_code == 404


# --------------------------------------------------------------------------- #
# HTTP：读与改
# --------------------------------------------------------------------------- #


def _seed_resource(db_path: Path, user_id: str, resource_id: str = "res_1") -> str:
    content = {
        "template_id": "indigo",
        "mindmap_data": {
            "nodeData": {
                "id": "root",
                "topic": "麻黄",
                "meta": {"evidence": "textbook"},
                "children": [
                    {
                        "id": "b0",
                        "topic": "功效",
                        "meta": {"evidence": "textbook"},
                        "children": [{"id": "b0-0", "topic": "发汗解表", "meta": {"evidence": "textbook"}}],
                    },
                    {"id": "b1", "topic": "用量", "meta": {"evidence": "model"}, "children": []},
                ],
            }
        },
    }
    resource_storage.create_resource(
        {
            "id": resource_id,
            "user_id": user_id,
            "node_id": "general",
            "type": "mindmap",
            "title": "麻黄 · 思维导图",
            "content": content,
            "sources": [{"name": "麻黄", "ref": "p.1"}],
            "notes": "",
            "status": "ready",
            "created_at": resource_storage.now(),
            "meta": {"mindmap_version": 1, "mindmap_structure": "logic-right", "mindmap_theme": "indigo", "intent": "monograph", "intent_label": "单味药", "intent_note": "按「单味药」组织", "degraded": False},
        },
        db_path,
    )
    return resource_id


def test_mindmap_get_reports_size_and_evidence(tmp_path: Path) -> None:
    client, headers, db_path = _setup(tmp_path)
    user_id = _user_id(headers, db_path)
    _seed_resource(db_path, user_id)

    response = client.post("/api/capabilities/mindmap.get", json={"resource_id": "res_1"}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["nodes"] == 4
    assert body["evidence"] == {"textbook": 3, "graph": 0, "model": 1}
    assert body["depth"] == 3
    assert body["intent"]["kind"] == "monograph"
    assert body["open_url"].endswith("res_1")


def test_mindmap_present_changes_appearance_only(tmp_path: Path) -> None:
    client, headers, db_path = _setup(tmp_path)
    user_id = _user_id(headers, db_path)
    _seed_resource(db_path, user_id, "res_2")

    changed = client.post("/api/capabilities/mindmap.present", json={"resource_id": "res_2", "structure": "fishbone", "theme_id": "verdant"}, headers=headers)
    assert changed.status_code == 200
    assert changed.json()["structure"] == "fishbone"
    assert set(changed.json()["changed"]) == {"structure", "theme_id"}

    stored = resource_storage.get_resource("res_2", user_id, db_path)
    assert stored["meta"]["mindmap_structure"] == "fishbone"
    assert stored["meta"]["mindmap_theme"] == "verdant"
    # 只改样子：版本号不动，树也不动。
    assert stored["meta"]["mindmap_version"] == 1
    assert stored["content"]["mindmap_data"]["nodeData"]["id"] == "root"

    # 同样的命令再发一次：没有变化，但仍返回 200——命令本来就该幂等。
    again = client.post("/api/capabilities/mindmap.present", json={"resource_id": "res_2", "structure": "fishbone"}, headers=headers)
    assert again.status_code == 200
    assert again.json()["changed"] == []

    bad = client.post("/api/capabilities/mindmap.present", json={"resource_id": "res_2", "structure": "spiral"}, headers=headers)
    assert bad.status_code == 422
    assert bad.json()["code"] == "mindmap.structure_unknown"


def test_mindmap_get_on_missing_resource_is_404(tmp_path: Path) -> None:
    client, headers, _ = _setup(tmp_path)
    response = client.post("/api/capabilities/mindmap.get", json={"resource_id": "nope"}, headers=headers)
    assert response.status_code == 404
    assert response.json()["code"] == "mindmap.resource_not_found"


# --------------------------------------------------------------------------- #
# 图谱桥接
# --------------------------------------------------------------------------- #


def test_kg_bridge_exposes_all_six() -> None:
    install()
    names = {spec.name for spec in registry.all_specs() if spec.name.startswith("kg.")}
    assert names == {"kg.ask", "kg.lookup", "kg.compare", "kg.path", "kg.review", "kg.attribute"}


def test_kg_lookup_returns_the_entity() -> None:
    install()
    result = asyncio.run(dispatch.invoke("kg.lookup", {"query": "甘草"}, ACTOR, db_path=DB_PATH))
    assert result.http_status == 200
    assert result.body["capability"] == "kg.lookup"
    assert any(item["name"] == "甘草" for item in result.body["entities"])


def test_kg_capability_reports_its_full_name() -> None:
    """归一化函数会塞短名（lookup），对外必须统一成 kg.lookup。"""

    install()
    result = asyncio.run(dispatch.invoke("kg.lookup", {"query": "桂枝"}, ACTOR, db_path=DB_PATH))
    assert result.body["capability"] == "kg.lookup"


@pytest.mark.parametrize("body", [{"question": ""}, {}])
def test_kg_ask_requires_a_question(body: dict) -> None:
    install()
    with pytest.raises(Exception) as excinfo:
        asyncio.run(dispatch.invoke("kg.ask", body, ACTOR, db_path=DB_PATH))
    assert "参数" in str(excinfo.value) or "question" in str(excinfo.value)


def test_replay_after_completion_points_at_the_artifact(tmp_path: Path) -> None:
    """跑完之后再重放：同一个 task_id，且 artifact 已经能打开。

    这是 ``artifact.resolve_on = done`` 的意义——受理时没有 resource_id，
    跑完必须补上，否则调用方永远拿不到「点开看」的地址。
    """

    client, headers, db_path = _setup(tmp_path)
    accepted = client.post(
        "/api/capabilities/mindmap.generate",
        json={"query": "麻黄", "client_invocation_id": "t-6"},
        headers=headers,
    ).json()

    from app.resources import service as resource_service

    # 新流程：先出方案、停下来等确认；确认之后才真正生成。
    first = asyncio.run(_collect(resource_service.stream_task(accepted["task_id"], _user_id(headers, db_path), db_path)))
    assert first[-1]["event"] == "pause"
    plan = next(event for event in first if event["event"] == "plan")["payload"]["plan"]
    confirmed = client.post(
        f"/api/resource/tasks/{accepted['task_id']}/plan/confirm",
        json={"plan": plan},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text

    events = asyncio.run(_collect(resource_service.stream_task(accepted["task_id"], _user_id(headers, db_path), db_path)))
    assert events[-1]["event"] == "done"

    replay = client.post(
        "/api/capabilities/mindmap.generate",
        json={"query": "麻黄", "client_invocation_id": "t-6"},
        headers=headers,
    ).json()
    assert replay["replayed"] is True
    assert replay["task_id"] == accepted["task_id"]
    assert replay["status"] == "done"
    assert replay["artifact"]["resource_id"]
    assert replay["artifact"]["open_url"].endswith(replay["artifact"]["resource_id"])
