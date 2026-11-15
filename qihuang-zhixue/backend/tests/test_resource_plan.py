"""「需求 → 方案 → 反馈 → 修订 → 确认 → 生成」这条流程的契约测试。

这些断言针对的是**产品语义**，不是 JSON 形状：

- 不同任务得到不同方案，而不是同一套药材字段；
- 没有确认就不生成；确认后生成必须读那一版方案；
- 依据只有三档，声明了知识库却拿不出引用的会被降级；
- 修订失败要说「没改」，而不是假装改过；
- 旧任务（pipeline_version=1）仍按原流程跑完。
"""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.resources import get_resource_db_path
from app.main import app
from app.path.store import upsert_node
from app.resources import planning, service, storage
from tests.helpers import auth_session, keyless_settings


@pytest.fixture(autouse=True)
def keyless_writers(monkeypatch: pytest.MonkeyPatch) -> None:
    """三种工坊都换到无 key 配置，让落笔走确定性兜底而不是 mock 回声。

    服务层构造 ``DocAgent()`` / ``PptAgent()`` 时读的是全局配置；开发机的
    .env 里往往配了 key，测试会变成「拿 mock 回声当正文」。
    """

    monkeypatch.setattr("app.docs.agent.get_settings", keyless_settings)
    monkeypatch.setattr("app.ppt.agent.get_settings", keyless_settings)
    monkeypatch.setattr("app.mindmap.agent.get_settings", keyless_settings)


# ── 夹具 ────────────────────────────────────────────────────────────────────


def _db(tmp_path: Path, node: str = "renshen", name: str = "人参") -> Path:
    db_path = tmp_path / "qihuang.db"
    upsert_node({"id": node, "name": name, "level": "point"}, db_path)
    upsert_node({"id": "general", "name": "中药学总论", "level": "domain"}, db_path)
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    return db_path


def _task(db_path: Path, kind: str, params: dict | None = None, version: int = 2) -> dict:
    return storage.create_task(
        f"task-{kind}-{version}",
        "u-plan",
        "renshen",
        kind,
        db_path,
        meta={"params": params or {}, "pipeline_version": version},
    )


def _collect(iterator):
    async def run():
        return [event async for event in iterator]

    return asyncio.run(run())


def _stream(task_id: str, db_path: Path, user: str = "u-plan") -> list[dict]:
    return _collect(service.stream_task(task_id, user, db_path))


def _plan_from(events: list[dict]) -> dict:
    return next(event for event in events if event["event"] == "plan")["payload"]["plan"]


def _confirm(task_id: str, plan: dict, db_path: Path, user: str = "u-plan") -> None:
    """直接走存储层确认：等价于 POST /plan/confirm 的写入部分。"""

    task = storage.get_task(task_id, user, db_path)
    steps = dict(task["steps_done"])
    steps["plan"] = plan
    steps["plan_confirmed"] = True
    steps.pop("awaiting_plan", None)
    steps.pop("active_step", None)
    storage.update_task(task_id, user, status="pending", current_step=1, steps_done=steps, meta={"confirmed_plan": plan}, db_path=db_path)
    service.reset_progress(task_id)


# ── 方案是否体现「用户要做什么」 ─────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["ppt", "doc", "mindmap"])
def test_plan_follows_the_task_not_a_herb_template(tmp_path: Path, kind: str) -> None:
    """「人参的发展历史」要按时间讲，不能套性味归经。"""

    db_path = _db(tmp_path)

    async def run():
        return await planning.propose(kind, "人参的发展历史", {"page_count": 10})

    plan = asyncio.run(run())
    labels = [unit["label"] for unit in plan["units"]]
    organization = plan["organization"]

    assert any(word in organization for word in ("时间", "阶段", "沿")), organization
    assert not any("性味归经" in label for label in labels), labels
    assert any(word in "".join(labels) for word in ("起源", "源流", "阶段", "演变", "背景")), labels
    assert plan["kind"] == kind
    # 教材没有按「历史阶段」编排：这些单元必须老老实实标成模型补充。
    assert all(unit["source"] == "model" for unit in plan["units"])
    assert any("教材" in gap or "模型" in gap for gap in plan["gaps"])


@pytest.mark.parametrize(
    "topic,expect",
    [
        ("麻黄与桂枝的区别", "比较"),
        ("帮我做一个中药学复习计划", "阶段"),
        ("如何煎煮中药", "操作"),
    ],
)
def test_plan_shapes_comparison_planning_and_procedure(tmp_path: Path, topic: str, expect: str) -> None:
    db_path = _db(tmp_path)

    async def run():
        return await planning.propose("doc", topic, {})

    plan = asyncio.run(run())
    assert expect in plan["organization"], plan["organization"]


def test_herb_plan_keeps_knowledge_base_units_grounded(tmp_path: Path) -> None:
    """单味药主题：教材真的覆盖的章节要标成知识库依据，并带上具体出处。"""

    db_path = _db(tmp_path)

    async def run():
        return await planning.propose("doc", "人参", {})

    plan = asyncio.run(run())
    grounded = [unit for unit in plan["units"] if unit["source"] == "knowledge_base"]
    assert grounded, plan["units"]
    assert all(unit["evidence"] for unit in grounded)
    assert plan["evidence"]["knowledge_base"]


def test_plan_accepts_user_material_as_an_independent_source(tmp_path: Path) -> None:
    db_path = _db(tmp_path)

    async def run():
        return await planning.propose("doc", "我自己的课堂讲义", {"materials": ["本讲重点讲人参的配伍禁忌与临床案例。"]})

    plan = asyncio.run(run())
    assert plan["evidence"]["user_material"], plan["evidence"]
    assert set(plan["evidence"]) == {"knowledge_base", "user_material", "model"}


# ── 修订 ────────────────────────────────────────────────────────────────────


def test_revision_without_model_keeps_plan_and_says_so(tmp_path: Path) -> None:
    """没有可用模型时不许「假装修订成功」：方案一字不动，并说明原因。"""

    db_path = _db(tmp_path)
    current = asyncio.run(planning.propose("doc", "人参的发展历史", {}))

    async def run():
        return await planning.revise("doc", "人参的发展历史", current, "把历代演变拆成两节")

    revised = asyncio.run(run())
    assert revised["applied"] is False
    assert revised["units"] == current["units"]
    assert revised["notice"]


def test_revision_merges_feedback_and_bumps_revision(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    current = asyncio.run(planning.propose("doc", "人参的发展历史", {}))

    async def fake_llm(messages):
        payload = json.loads(messages[-1]["content"])
        plan = payload["current_plan"]
        plan["units"] = plan["units"][:3]
        plan["units"][0]["label"] = "神农本草经里的记载"
        plan["goal"] = payload["feedback"]
        return json.dumps({"plan": plan, "clarification": ""}, ensure_ascii=False)

    revised = asyncio.run(planning.revise("doc", "人参的发展历史", current, "第一节只讲本草经", llm_call=fake_llm))
    assert revised["applied"] is True
    assert revised["units"][0]["label"] == "神农本草经里的记载"
    assert len(revised["units"]) == 3
    assert revised["revision"] == int(current.get("revision") or 0) + 1
    assert revised["clarification"] == ""


def test_revision_surfaces_clarification_only_when_the_model_flags_it(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    current = asyncio.run(planning.propose("doc", "人参的发展历史", {}))

    async def ambiguous(messages):
        payload = json.loads(messages[-1]["content"])
        return json.dumps({"plan": payload["current_plan"], "clarification": "这份文稿是给本科生还是临床医师？"}, ensure_ascii=False)

    revised = asyncio.run(planning.revise("doc", "人参的发展历史", current, "再专业一点", llm_call=ambiguous))
    assert "本科生还是临床医师" in revised["clarification"]

    async def clear(messages):
        payload = json.loads(messages[-1]["content"])
        return json.dumps({"plan": payload["current_plan"], "clarification": ""}, ensure_ascii=False)

    plain = asyncio.run(planning.revise("doc", "人参的发展历史", current, "第二章改名为历代演变", llm_call=clear))
    assert plain["clarification"] == ""


def test_revision_overturns_the_goal_and_rebuilds_the_plan(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    current = asyncio.run(planning.propose("doc", "人参的发展历史", {}))

    async def run():
        return await planning.revise("doc", "人参的发展历史", current, "其实我要的是两味药的比较", goal="人参与党参的区别")

    rebuilt = asyncio.run(run())
    assert rebuilt["overturned"] is True
    assert "比较" in rebuilt["organization"]
    assert rebuilt["revision"] == int(current.get("revision") or 0) + 1


# ── 确认门禁与旧任务兼容 ─────────────────────────────────────────────────────


def test_no_generation_before_confirmation(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    task = _task(db_path, "doc", {"topic": "人参的发展历史"})

    events = _stream(task["task_id"], db_path)
    names = [event["event"] for event in events]
    assert names == ["step_start", "plan", "pause"]
    assert next(event for event in events if event["event"] == "pause")["payload"]["reason"] == "await_plan"

    state = storage.get_task(task["task_id"], "u-plan", db_path)
    assert state["status"] == "pending"
    assert state["steps_done"]["plan"]["units"]
    assert "draft" not in state["steps_done"]


def test_cancelled_plan_does_not_generate(tmp_path: Path) -> None:
    """退出协商 = 不生成；任务停在等待态，之后仍可回来确认。"""

    db_path = _db(tmp_path)
    task = _task(db_path, "mindmap", {"topic": "人参"})
    _stream(task["task_id"], db_path)

    state = storage.get_task(task["task_id"], "u-plan", db_path)
    assert state["status"] == "pending"
    assert state["resource_id"] is None
    assert storage.list_resources("u-plan", node_id=None, resource_type=None, favorited=None, query=None, offset=0, limit=10, db_path=db_path)[1] == 0


def test_in_flight_v1_task_keeps_the_old_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """升级前落库的任务没有 pipeline_version，不能突然被要求先确认方案。"""

    db_path = _db(tmp_path)
    task = _task(db_path, "doc", {"topic": "人参"}, version=1)

    async def stub(resource_type: str, topic: str, **_kwargs):
        return ({"blocks": [{"type": "heading", "text": "人参"}]}, [])

    monkeypatch.setattr(service, "generate_content", stub)
    events = _stream(task["task_id"], db_path)
    names = [event["event"] for event in events]
    assert "plan" not in names
    assert names.count("step_start") == 5
    assert names[-1] == "done"


def test_confirmed_plan_drives_mindmap_branches_and_evidence(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    task = _task(db_path, "mindmap", {"topic": "人参"})
    events = _stream(task["task_id"], db_path)
    plan = _plan_from(events)

    edited = {
        **plan,
        "units": [
            {"id": "u0", "label": "功效", "intent": "", "detail": "", "source": "knowledge_base", "evidence": plan["evidence"]["knowledge_base"][:1], "gap": ""},
            {"id": "u1", "label": "源流", "intent": "", "detail": "", "source": "model", "evidence": [], "gap": ""},
        ],
    }
    _confirm(task["task_id"], edited, db_path)

    resumed = _stream(task["task_id"], db_path)
    branches = [event["payload"]["node"] for event in resumed if event["event"] == "node" and event["payload"]["node"]["depth"] == 1]
    assert [node["topic"] for node in branches] == ["功效", "源流"], "用户删改的分支必须原样落地"
    tiers = {node["topic"]: node["evidence"] for node in branches}
    assert tiers["功效"] in ("textbook", "graph"), "教材覆盖的分支要有依据"
    assert tiers["源流"] == "model", "教材没覆盖的分支要标成模型补充"

    verify = next(event for event in resumed if event["event"] == "mindmap_verify")["payload"]
    assert verify["plan_coverage"]["planned"] == 2
    assert verify["plan_coverage"]["matched"] == 2


def test_confirmed_plan_drives_doc_sections(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    task = _task(db_path, "doc", {"topic": "人参的发展历史"})
    plan = _plan_from(_stream(task["task_id"], db_path))

    edited = {**plan, "units": [plan["units"][0], plan["units"][2]]}
    edited["units"][0]["label"] = "上编：源流"
    _confirm(task["task_id"], edited, db_path)

    resumed = _stream(task["task_id"], db_path)
    headings = [
        event["payload"]["block"]["text"]
        for event in resumed
        if event["event"] == "block" and str(event["payload"]["block"].get("type", "")).startswith("heading")
    ]
    assert headings[:2] == ["上编：源流", plan["units"][2]["label"]]

    verify = next(event for event in resumed if event["event"] == "doc_verify")["payload"]
    assert verify["stats"]["planned"] == 2
    assert verify["stats"]["matched"] == 2
    assert verify["stats"]["bad_citations"] == 0


def test_confirmed_plan_drives_ppt_pages(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    task = _task(db_path, "ppt", {"topic": "人参的发展历史", "page_count": 10})
    plan = _plan_from(_stream(task["task_id"], db_path))

    edited = {**plan, "units": plan["units"][:3]}
    _confirm(task["task_id"], edited, db_path)

    resumed = _stream(task["task_id"], db_path)
    titles = [event["payload"]["slide"]["title"] for event in resumed if event["event"] == "slide"]
    assert titles == [unit["label"] for unit in edited["units"]], "页数与页序由方案决定，不补模板页"

    verify = next(event for event in resumed if event["event"] == "deck_verify")["payload"]
    assert verify["stats"]["planned"] == len(edited["units"])
    assert verify["stats"]["missing"] == []


# ── 接口 ────────────────────────────────────────────────────────────────────


def test_plan_api_round_trip_and_resume(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    client = TestClient(app)
    session = auth_session(client, "plan-api", db_path)

    created = client.post(
        "/api/resource/tasks",
        json={"node_id": "renshen", "resource_type": "doc", "params": {"topic": "人参的发展历史"}},
        headers=session["headers"],
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]

    user_id = session["user"]["user_id"]
    paused = _stream(task_id, db_path, user_id)
    plan = _plan_from(paused)

    snapshot = client.get(f"/api/resource/tasks/{task_id}", headers=session["headers"])
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["awaiting_plan"] is True and body["plan_confirmed"] is False
    assert body["plan"]["units"] == plan["units"]
    # v3：方案之后多了「定体例」，且闸门后移到 design——方案与设计同批确认。
    assert [step["id"] for step in body["steps"]] == ["plan", "design", "compose", "verify", "seal"]
    assert body["design"]["genre"], "设计单必须与方案一起回到前端"

    # 空方案必须被拦住
    empty = client.post(f"/api/resource/tasks/{task_id}/plan/confirm", json={"plan": {**plan, "units": []}}, headers=session["headers"])
    assert empty.status_code == 422

    edited = {**plan, "units": plan["units"][:2]}
    confirmed = client.post(f"/api/resource/tasks/{task_id}/plan/confirm", json={"plan": edited}, headers=session["headers"])
    assert confirmed.status_code == 200, confirmed.text

    after = client.get(f"/api/resource/tasks/{task_id}", headers=session["headers"]).json()
    assert after["plan_confirmed"] is True and after["awaiting_plan"] is False
    assert after["plan"]["units"][0]["label"] == plan["units"][0]["label"]

    # 已确认的任务不能再走到修订入口
    again = client.post(f"/api/resource/tasks/{task_id}/plan/revise", json={"feedback": "再改一次"}, headers=session["headers"])
    assert again.status_code == 409


def test_ppt_deck_verify_reports_missing_and_duplicate_pages(tmp_path: Path) -> None:
    plan = {
        "kind": "ppt",
        "units": [
            {"label": "开场", "source": "model"},
            {"label": "关键阶段", "source": "model"},
            {"label": "当代形态", "source": "model"},
        ],
    }
    content = {
        "slides": [
            {"title": "开场", "kind": "cover", "source_indexes": []},
            {"title": "关键阶段", "kind": "bullets", "bullets": ["甲"], "source_indexes": [0]},
            {"title": "关键阶段", "kind": "bullets", "bullets": [], "source_indexes": [9]},
        ]
    }
    stats = service._deck_verify(content, plan, source_count=2)
    assert stats["planned"] == 3
    assert stats["matched"] == 2
    assert stats["missing"] == ["当代形态"]
    assert stats["duplicates"] == ["关键阶段"]
    assert stats["empty"] == 1
    assert stats["bad_sources"] == 1
    pages = {page["title"]: page for page in stats["pages"]}
    assert pages["关键阶段"]["source"] in ("knowledge_base", "model")
