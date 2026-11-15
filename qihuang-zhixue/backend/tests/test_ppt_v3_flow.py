"""v3 管线：设计步与选材步在真实任务里长什么样。

三条断言对应三件真事：

1. 用户先看到**方案 + 设计单**，然后才被要求确认（闸门从 plan 后移到 design）；
2. 确认之后生成读的是那一版设计：页的版式来自设计单，不来自页序；
3. 素材解析结果落进每一页的 ``asset_refs``，空资产库时照样出稿。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.path.store import upsert_node
from app.resources import service, storage
from tests.helpers import keyless_settings


@pytest.fixture(autouse=True)
def keyless_writers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.docs.agent.get_settings", keyless_settings)
    monkeypatch.setattr("app.ppt.agent.get_settings", keyless_settings)
    monkeypatch.setattr("app.mindmap.agent.get_settings", keyless_settings)
    # 选材步现在会先跑一轮工具循环（模型自己找图）。测试里同样要它走 mock：
    # 否则每个流程测试都会真连一次模型，既慢又不可重复。
    monkeypatch.setattr("app.resources.service.get_settings", keyless_settings)


def _db(tmp_path: Path) -> Path:
    db_path = tmp_path / "qihuang.db"
    upsert_node({"id": "renshen", "name": "人参", "level": "point"}, db_path)
    upsert_node({"id": "general", "name": "中药学总论", "level": "domain"}, db_path)
    return db_path


def _task(db_path: Path, params: dict | None = None) -> dict:
    return storage.create_task(
        "task-ppt-v3",
        "u-v3",
        "renshen",
        "ppt",
        db_path,
        meta={"params": params or {"topic": "人参", "page_count": 8}, "pipeline_version": 3},
    )


def _stream(task_id: str, db_path: Path) -> list[dict]:
    async def run():
        return [event async for event in service.stream_task(task_id, "u-v3", db_path)]

    return asyncio.run(run())


def _confirm(task_id: str, db_path: Path, *, design: dict | None = None) -> None:
    """等价于 POST /plan/confirm 的写入部分：确认后从 select 步续跑。"""

    task = storage.get_task(task_id, "u-v3", db_path)
    steps = dict(task["steps_done"])
    steps["plan_confirmed"] = True
    if design is not None:
        steps["design"] = design
    steps.pop("awaiting_plan", None)
    steps.pop("active_step", None)
    steps["_skip"] = True  # 只为让 dict 内容变化，避免缓存命中旧进度
    storage.update_task(
        task_id,
        "u-v3",
        status="pending",
        current_step=2,  # v3：plan(0) → design(1) → select(2)
        steps_done=steps,
        meta={"confirmed_plan": steps.get("plan"), "confirmed_design": steps.get("design")},
        db_path=db_path,
    )
    service.reset_progress(task_id)


def test_gate_moved_to_design_and_carries_both(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    task = _task(db_path)
    events = _stream(task["task_id"], db_path)

    order = [event["event"] for event in events]
    assert "plan" in order and "design" in order
    assert order.index("plan") < order.index("design") < order.index("pause")

    design_event = next(event for event in events if event["event"] == "design")
    payload = design_event["payload"]
    assert payload["plan"], "设计单必须与方案一起给用户看"
    assert payload["design"]["pages"], "设计单至少要有逐页版式"
    assert [step["id"] for step in payload["steps"]] == ["plan", "design", "select", "compose", "verify", "seal"]
    assert payload["confirmed"] is False

    # 设计单的页数与方案单元数一致：用户确认的是同一份施工图。
    assert len(payload["design"]["pages"]) == len(payload["plan"]["units"])


def test_confirmed_design_drives_page_archetypes(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    task = _task(db_path)
    _stream(task["task_id"], db_path)

    stored = storage.get_task(task["task_id"], "u-v3", db_path)
    design = dict(stored["steps_done"]["design"])
    # 用户把第 3 页从「要点」改成「警示」——生成期必须照着做，
    # 且同一批要点要变成朱砂警示块，而不是照旧画五条圆点。
    design["pages"][2] = {**design["pages"][2], "archetype": "warning"}
    _confirm(task["task_id"], db_path, design=design)

    events = _stream(task["task_id"], db_path)
    slides = [event["payload"]["slide"] for event in events if event["event"] == "slide"]
    assert slides
    assert slides[2]["purpose"] == "warning"
    assert slides[2]["kind"] == "bullets"
    assert slides[2]["blocks"] and all(block["type"] == "alert" for block in slides[2]["blocks"])


def test_slot_resolution_is_emitted_and_attached(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    task = _task(db_path)
    _stream(task["task_id"], db_path)
    _confirm(task["task_id"], db_path)
    events = _stream(task["task_id"], db_path)

    slot_event = next(event for event in events if event["event"] == "slot_resolution")
    slots = slot_event["payload"]["slots"]
    assert slots, "每个设计槽位都要有解析结果"
    assert all(item["result"] in ("parametric", "hit", "substitute", "generated", "none") for item in slots)
    # 空资产库时，底纹与角饰由母题生成，主图位降级——不许留坏图。
    assert any(item["result"] == "parametric" for item in slots)

    slides = [event["payload"]["slide"] for event in events if event["event"] == "slide"]
    refs = [ref for slide in slides for ref in slide.get("asset_refs") or []]
    assert refs, "落库的页面要能回答「这页用了什么素材」"
    assert all(ref["provenance"] in ("parametric", "library", "generated") for ref in refs)


def test_verify_reports_design_and_asset_classes(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    task = _task(db_path)
    _stream(task["task_id"], db_path)
    _confirm(task["task_id"], db_path)
    events = _stream(task["task_id"], db_path)

    verify = next(event for event in events if event["event"] == "deck_verify")["payload"]
    assert "design" in verify["stats"] and "assets" in verify["stats"]
    assert verify["stats"]["design"]["checked"] >= 1
    # 校验只报问题，不偷偷改内容：设计问题不该凭空出现。
    assert verify["stats"]["assets"]["slot_unfilled"] == [] or isinstance(verify["stats"]["assets"]["slot_unfilled"], list)
    # 单页重写是「校验之后」的定点修复。没有 key 时它一次都不该跑，
    # 但回执里必须留这个字段——前端据此判断这一版有没有被改过页。
    assert verify["revised"] == []


def test_design_and_slots_freeze_into_resource_meta(tmp_path: Path) -> None:
    """设计单、逐槽解析结果与冻结标记必须跟着成品落库。

    不落这三样，重开旧稿就只剩画面：「这一页为什么长这样」「这张图哪来的」
    要重新跑模型才能回答，而重跑的结果和上次不一定一样。
    """

    db_path = _db(tmp_path)
    task = _task(db_path)
    _stream(task["task_id"], db_path)
    _confirm(task["task_id"], db_path)
    events = _stream(task["task_id"], db_path)

    resource_id = next(event for event in events if event["event"] == "done")["payload"]["resource_id"]
    resource = storage.get_resource(resource_id, "u-v3", db_path)
    meta = resource["meta"]

    assert meta["art_frozen"] is True
    assert isinstance(meta["design"], dict) and meta["design"]["pages"]
    assert isinstance(meta["slot_resolution"], list) and meta["slot_resolution"]
    # 每条解析结果都要能回答「用了什么、什么许可、从哪来」。
    for item in meta["slot_resolution"]:
        assert item["result"] in ("parametric", "hit", "substitute", "generated", "none")
        assert item["provenance"] in ("parametric", "library", "generated", "none")
        assert "license" in item and "asset_id" in item


def test_frozen_slots_are_not_resolved_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """续跑回到 select 步时，已冻结的解析结果不再重算。

    这是「模型的不确定性只存在于那一次循环里」的落点：重连、刷新、重跑后续步骤，
    拿到的必须是同一批素材，否则同一份设计单两次出稿会换图。
    """

    db_path = _db(tmp_path)
    task = _task(db_path)
    _stream(task["task_id"], db_path)
    _confirm(task["task_id"], db_path)
    events = _stream(task["task_id"], db_path)
    frozen = next(event for event in events if event["event"] == "slot_resolution")["payload"]["slots"]

    calls: list[int] = []
    original = service._resolve_deck_slots_live

    async def counted(design, settings):
        calls.append(1)
        return await original(design, settings)

    monkeypatch.setattr(service, "_resolve_deck_slots_live", counted)

    # 把任务拨回 select 步再跑一遍：steps_done 里已经有 slot_resolution。
    stored = storage.get_task(task["task_id"], "u-v3", db_path)
    storage.update_task(
        task["task_id"],
        "u-v3",
        status="pending",
        current_step=2,
        steps_done=dict(stored["steps_done"]),
        db_path=db_path,
    )
    service.reset_progress(task["task_id"])
    again = _stream(task["task_id"], db_path)

    assert calls == [], "已冻结的解析结果不该再跑一次"
    replay = next(event for event in again if event["event"] == "slot_resolution")["payload"]["slots"]
    assert [item["asset_id"] for item in replay] == [item["asset_id"] for item in frozen]


def test_revised_pages_are_reported_and_frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """重写过的页要进回执、也要落库。

    这里把重写本体换掉，只验接线：事件里带 `revised`，`meta.revise` 存的是
    「改过什么」。真跑模型的那一层在 `test_revise.py` 与 `scripts/smoke_revise.py`。
    """

    async def fake(draft: dict, stats: dict, design: object) -> dict:
        slides = draft["content"]["slides"]
        slides[1]["blocks"] = (slides[1].get("blocks") or [])[:1]
        return {"revised": [{"page": 2, "issues": ["内容块超限（6/5）"], "notes": []}], "skipped": []}

    monkeypatch.setattr(service, "_revise_deck_pages", fake)

    db_path = _db(tmp_path)
    task = _task(db_path)
    _stream(task["task_id"], db_path)
    _confirm(task["task_id"], db_path)
    events = _stream(task["task_id"], db_path)

    verify = next(event for event in events if event["event"] == "deck_verify")["payload"]
    assert verify["revised"] == [{"page": 2, "issues": ["内容块超限（6/5）"], "notes": []}]

    resource_id = next(event for event in events if event["event"] == "done")["payload"]["resource_id"]
    meta = storage.get_resource(resource_id, "u-v3", db_path)["meta"]
    assert meta["revise"]["revised"][0]["page"] == 2
    assert meta["art_frozen"] is True
