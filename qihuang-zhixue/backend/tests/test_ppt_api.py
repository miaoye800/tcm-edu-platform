"""幻灯工坊的接口契约：逐页事件、大纲暂停与续跑、整份存盘的乐观并发。"""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.resources import get_resource_db_path
from app.main import app
from app.path.store import upsert_node
from app.ppt import agent as ppt_agent_module
from app.resources import service as resource_service
from app.resources import storage as resource_storage
from tests.helpers import auth_headers_for_user_id, auth_session, keyless_settings


@pytest.fixture(autouse=True)
def keyless_ppt_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """服务层构造 ``PptAgent()`` 时读的是全局配置。

    开发机的 .env 里可能有 key，那样每次 stream 都会先打一次真模型再回落；
    这里统一换成无 key 配置，让「确定性地走模板」成为测试前提。
    """

    monkeypatch.setattr(ppt_agent_module, "get_settings", keyless_settings)


def _deck_payload(version: int = 1) -> dict:
    return {
        "version": version,
        "theme_id": "ink",
        "slides": [
            {"id": "s0", "kind": "cover", "title": "麻黄：发汗解表", "subtitle": "中药学讲稿"},
            {
                "id": "s1",
                "kind": "bullets",
                "title": "核心功效",
                "bullets": ["发汗解表", "宣肺平喘"],
                "source_indexes": [0],
            },
        ],
    }


def _seed_ppt(db_path: Path, user_id: str, *, resource_type: str = "ppt", status: str = "ready") -> str:
    upsert_node({"id": "mahuang", "name": "麻黄", "level": "point"}, db_path)
    resource_id = str(uuid4())
    resource_storage.create_resource(
        {
            "id": resource_id,
            "user_id": user_id,
            "node_id": "mahuang",
            "type": resource_type,
            "title": "麻黄 · 讲稿",
            "content": {
                "theme_id": "paper",
                "slides": [{"id": "s0", "kind": "cover", "title": "麻黄", "subtitle": "讲稿", "bullets": [], "columns": [], "note": "", "source_indexes": []}],
                "sources": [{"name": "麻黄", "type": "教材", "ref": "p.1", "quote": "发汗解表"}],
            },
            "sources": [{"name": "麻黄", "type": "教材", "ref": "p.1", "quote": "发汗解表"}],
            "notes": "",
            "status": status,
            "created_at": "2026-11-05T00:00:00+00:00",
            "meta": {"ppt_version": 1},
        },
        db_path,
    )
    return resource_id


def _run_stream(task_id: str, user_id: str, db_path: Path) -> list[dict]:
    async def run():
        return [item async for item in resource_service.stream_task(task_id, user_id, db_path)]

    return asyncio.run(run())


# --- 一键出整稿 -------------------------------------------------------------


def test_stream_one_shot_emits_outline_slides_and_done(tmp_path: Path):
    db_path = tmp_path / "qihuang.db"
    upsert_node({"id": "mahuang", "name": "麻黄", "level": "point"}, db_path)
    task_id = f"task-{uuid4().hex[:8]}"
    resource_storage.create_task(task_id, "u-ppt", "mahuang", "ppt", db_path, meta={"params": {"page_count": 12}})

    events = _run_stream(task_id, "u-ppt", db_path)
    names = [item["event"] for item in events]

    assert names.count("step_start") == 4
    assert names[0] == "step_start"
    assert names[-1] == "done"
    assert "pause" not in names
    # 谋篇在前、敷演在后
    assert names.index("outline") < names.index("slide")
    assert names.index("slide") < names.index("deck_verify")

    outline = next(item for item in events if item["event"] == "outline")
    assert outline["payload"]["total"] >= 6
    assert outline["payload"]["deck_title"]

    slides = [item for item in events if item["event"] == "slide"]
    assert len(slides) >= 6
    assert [item["payload"]["index"] for item in slides] == list(range(len(slides)))
    assert slides[0]["payload"]["slide"]["kind"] == "cover"
    assert slides[-1]["payload"]["slide"]["kind"] == "summary"

    verify = next(item for item in events if item["event"] == "deck_verify")
    assert verify["payload"]["stats"]["slides"] == len(slides)
    assert verify["payload"]["theme_id"] == "paper"

    done = next(item for item in events if item["event"] == "done")
    resource = resource_storage.get_resource(done["payload"]["resource_id"], "u-ppt", db_path)
    assert resource is not None
    assert resource["type"] == "ppt"
    assert resource["title"] == "麻黄 · 讲稿"
    assert resource["meta"]["ppt_version"] == 1
    assert resource["meta"]["generator"] == "ppt-agent"
    assert len(resource["content"]["slides"]) == len(slides)
    # 溯源证据固化在生成期，编辑不应改写它
    assert resource["sources"]


# --- 大纲暂停与续跑 ---------------------------------------------------------


def test_outline_mode_pauses_then_resumes_with_confirmed_outline(tmp_path: Path):
    db_path = tmp_path / "qihuang.db"
    upsert_node({"id": "mahuang", "name": "麻黄", "level": "point"}, db_path)
    task_id = f"task-{uuid4().hex[:8]}"
    resource_storage.create_task(task_id, "u-ppt2", "mahuang", "ppt", db_path, meta={"params": {"mode": "outline", "page_count": 12}})

    events = _run_stream(task_id, "u-ppt2", db_path)
    names = [item["event"] for item in events]

    assert names[-1] == "pause"
    assert "slide" not in names and "done" not in names
    pause = events[-1]
    assert pause["payload"]["reason"] == "await_outline"

    task = resource_storage.get_task(task_id, "u-ppt2", db_path)
    assert task is not None
    assert task["status"] == "pending"
    assert task["current_step"] == 1
    assert task["steps_done"]["awaiting_outline"] is True

    outline = next(item for item in events if item["event"] == "outline")["payload"]["outline"]
    edited = [dict(item) for item in outline]
    edited[1]["title"] = "课程导览（我改过的）"
    edited = edited[:4]

    client = TestClient(app)
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    # 任务属于 u-ppt2，接口按 user_id 过滤，这里直接给同一 user_id 签一个 token
    headers = auth_headers_for_user_id(client, "u-ppt2", db_path)

    confirmed = client.post(
        f"/api/resource/tasks/{task_id}/ppt/outline",
        json={"slides": edited},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["current_step"] == 2

    resumed = _run_stream(task_id, "u-ppt2", db_path)
    resumed_names = [item["event"] for item in resumed]
    assert resumed_names[0] == "step_start"
    assert resumed_names[-1] == "done"
    assert resumed_names.count("step_start") == 2  # 只剩敷演 + 校色
    assert "outline" not in resumed_names

    slides = [item for item in resumed if item["event"] == "slide"]
    titles = [item["payload"]["slide"]["title"] for item in slides]
    assert "课程导览（我改过的）" in titles
    assert len(slides) == 4

    done = next(item for item in resumed if item["event"] == "done")
    resource = resource_storage.get_resource(done["payload"]["resource_id"], "u-ppt2", db_path)
    assert resource is not None
    assert [slide["title"] for slide in resource["content"]["slides"]] == titles


def test_confirm_outline_rejects_non_ppt_and_finished_task(tmp_path: Path):
    db_path = tmp_path / "qihuang.db"
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "pf-guard", db_path)
    user_id = session["user"]["user_id"]
    upsert_node({"id": "mahuang", "name": "麻黄", "level": "point"}, db_path)

    mindmap_task = f"task-{uuid4().hex[:8]}"
    resource_storage.create_task(mindmap_task, user_id, "mahuang", "mindmap", db_path)
    wrong_type = client.post(
        f"/api/resource/tasks/{mindmap_task}/ppt/outline",
        json={"slides": [{"id": "s0", "title": "标题"}]},
        headers=session["headers"],
    )
    assert wrong_type.status_code == 422

    missing = client.post(
        f"/api/resource/tasks/{uuid4().hex}/ppt/outline",
        json={"slides": [{"id": "s0", "title": "标题"}]},
        headers=session["headers"],
    )
    assert missing.status_code == 404


# --- 整份存盘 ---------------------------------------------------------------


def test_save_deck_increments_version_and_conflicts(tmp_path: Path):
    db_path = tmp_path / "qihuang.db"
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "ppt-owner", db_path)
    resource_id = _seed_ppt(db_path, session["user"]["user_id"])

    first = client.put(f"/api/resources/{resource_id}/ppt/deck", json=_deck_payload(1), headers=session["headers"])
    assert first.status_code == 200, first.text
    assert first.json() == {"version": 2, "slide_count": 2}

    detail = client.get(f"/api/resources/{resource_id}", headers=session["headers"]).json()
    assert detail["meta"]["ppt_version"] == 2
    assert detail["content"]["theme_id"] == "ink"
    assert [slide["title"] for slide in detail["content"]["slides"]] == ["麻黄：发汗解表", "核心功效"]
    # sources 是生成期固化的证据，不该被编辑请求清空
    assert detail["content"]["sources"]

    replay = client.put(f"/api/resources/{resource_id}/ppt/deck", json=_deck_payload(1), headers=session["headers"])
    assert replay.status_code == 409
    assert "版本" in replay.json()["detail"]


def test_save_deck_rejects_wrong_type_bad_payload_and_foreign_owner(tmp_path: Path):
    db_path = tmp_path / "qihuang.db"
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    client = TestClient(app)
    owner = auth_session(client, "ppt-a", db_path)
    intruder = auth_session(client, "ppt-b", db_path)

    doc_id = _seed_ppt(db_path, owner["user"]["user_id"], resource_type="doc")
    assert client.put(f"/api/resources/{doc_id}/ppt/deck", json=_deck_payload(), headers=owner["headers"]).status_code == 422

    pending_id = _seed_ppt(db_path, owner["user"]["user_id"], status="generating")
    assert client.put(f"/api/resources/{pending_id}/ppt/deck", json=_deck_payload(), headers=owner["headers"]).status_code == 409

    resource_id = _seed_ppt(db_path, owner["user"]["user_id"])
    assert client.put(f"/api/resources/{resource_id}/ppt/deck", json=_deck_payload(), headers=intruder["headers"]).status_code == 404

    broken = _deck_payload()
    broken["slides"][1]["id"] = "s0"  # 重复 id
    assert client.put(f"/api/resources/{resource_id}/ppt/deck", json=broken, headers=owner["headers"]).status_code == 422

    too_long = _deck_payload()
    too_long["slides"][0]["title"] = "一" * 41
    assert client.put(f"/api/resources/{resource_id}/ppt/deck", json=too_long, headers=owner["headers"]).status_code == 422

    extra = _deck_payload()
    extra["slides"][0]["unexpected"] = "x"
    assert client.put(f"/api/resources/{resource_id}/ppt/deck", json=extra, headers=owner["headers"]).status_code == 422


def test_create_ppt_task_and_list_filter(tmp_path: Path):
    db_path = tmp_path / "qihuang.db"
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "ppt-params", db_path)
    upsert_node({"id": "mahuang", "name": "麻黄", "level": "point"}, db_path)

    created = client.post(
        "/api/resource/tasks",
        json={"node_id": "mahuang", "resource_type": "ppt", "params": {"mode": "outline", "page_count": 99, "theme": "neon", "topic": "麻黄的配伍"}},
        headers=session["headers"],
    )
    assert created.status_code == 201, created.text
    assert created.json()["resource_type"] == "ppt"

    listing = client.get("/api/resources", params={"type": "ppt"}, headers=session["headers"])
    assert listing.status_code == 200
    assert listing.json()["total"] == 0
