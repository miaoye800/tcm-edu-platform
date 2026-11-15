"""素材工具循环：**用脚本化的 router 验行为，不打真网、不调真模型**。

这一层要证明的不是「模型选得好」（那是它在真实运行时的事），而是**边界真的挡得住**：
- 证据位绑装饰图 → 被拒，且拒绝理由回到模型手里；
- 生成图超预算 → 被拒；
- 模型不调工具 → 一轮就收工，不空转；
- 参数不是合法 JSON → 回一句错误，不让循环崩；
- 循环整体抛异常 → 返回空结果，由选型器兜底。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.llm.clients import ToolCall, ToolTurn
from app.ppt import agent_loop, materials
from app.ppt.design import DeckDesign


def _design(*slots: dict[str, Any]) -> DeckDesign:
    return DeckDesign.model_validate(
        {
            "pages": [
                {
                    "unit_id": "p1",
                    "archetype": "points",
                    "slots": list(slots),
                }
            ]
        }
    )


class ScriptedRouter:
    """按剧本一轮一轮返回 tool_calls。剧本用完就返回空轮次。"""

    def __init__(self, turns: list[ToolTurn]) -> None:
        self.turns = list(turns)
        self.seen: list[list[dict[str, Any]]] = []

    async def chat_tools(self, task_type: str, messages, **kw: Any) -> ToolTurn:
        self.seen.append([dict(item) for item in messages])
        return self.turns.pop(0) if self.turns else ToolTurn(text="收工")


class StubRouter:
    """给 look_at 用：固定返回一段 JSON 文本。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def chat(self, task_type: str, messages, **kw: Any) -> str:
        self.calls += 1
        return self.text


def _call(name: str, arguments: dict[str, Any] | None = None, *, raw: str = "", index: int = 0) -> ToolCall:
    return ToolCall(id=f"c{index}", name=name, arguments=arguments or {}, raw_arguments=raw)


def _candidate(source: str = "local.herbs", slot_class: str = "evidence") -> materials.Candidate:
    return materials.Candidate(
        id="photo-huangqi-01",
        title="黄芪",
        source=source,
        kind="photo",
        url="/assets/pictorial/photo-huangqi-01.webp",
        thumb="/assets/pictorial/photo-huangqi-01.webp",
        license="CC0",
        slot_class=slot_class,
    )


def test_asks_only_cover_image_slots() -> None:
    design = _design(
        {"slot": "figure", "subject": "黄芪"},
        {"slot": "ornament"},  # 角饰走参数化母题，不该进任务清单
        {"slot": "ground"},
    )
    asks = agent_loop.asks_of(design)
    assert len(asks) == 1
    assert asks[0].unit_id == "p1"
    assert "黄芪" in asks[0].describe()


def test_evidence_slot_rejects_decorative_source() -> None:
    loop = agent_loop.AssetLoop(router=ScriptedRouter([]), task_type="generate", asks=agent_loop.asks_of(_design({"slot": "evidence", "subject": "黄芪", "slot_class": "evidence"})))
    loop.pool["plate-x"] = _candidate(source="local.heritage", slot_class="decorative")
    result = asyncio.run(loop.execute("assign_asset", {"unit_id": "p1", "asset_id": "plate-x", "slot": "evidence"}))
    assert result["ok"] is False
    assert "证据位" in result["error"]
    assert not loop.assignments


def test_evidence_slot_rejects_nc_license() -> None:
    loop = agent_loop.AssetLoop(router=ScriptedRouter([]), task_type="generate", asks=agent_loop.asks_of(_design({"slot": "evidence", "subject": "黄芪"})))
    loop.pool["plate-nc"] = materials.Candidate(
        id="plate-nc",
        title="nc",
        source="api.wellcome",
        kind="herb",
        url="u",
        thumb="t",
        license="CC-BY-NC-4.0",
        slot_class="decorative",
    )
    result = asyncio.run(loop.execute("assign_asset", {"unit_id": "p1", "asset_id": "plate-nc", "slot": "evidence"}))
    assert result["ok"] is False
    assert "非商业" in result["error"]


def test_assign_accepts_evidence_from_local() -> None:
    loop = agent_loop.AssetLoop(router=ScriptedRouter([]), task_type="generate", asks=agent_loop.asks_of(_design({"slot": "evidence", "subject": "黄芪"})))
    loop.pool["photo-huangqi-01"] = _candidate()
    result = asyncio.run(loop.execute("assign_asset", {"unit_id": "p1", "asset_id": "photo-huangqi-01", "slot": "evidence", "why": "饮片实拍"}))
    assert result["ok"] is True
    resolution = loop.assignments[("p1", "evidence")].as_resolution()
    assert resolution["result"] == "hit"
    assert resolution["source"] == "local.herbs"


def test_assign_rejects_unknown_id_and_unknown_page() -> None:
    loop = agent_loop.AssetLoop(router=ScriptedRouter([]), task_type="generate", asks=agent_loop.asks_of(_design({"slot": "figure", "subject": "任意"})))
    assert asyncio.run(loop.execute("assign_asset", {"unit_id": "p1", "asset_id": "nope"}))["ok"] is False
    loop.pool["plate-a"] = _candidate()
    assert asyncio.run(loop.execute("assign_asset", {"unit_id": "p9", "asset_id": "plate-a"}))["ok"] is False


def test_generate_budget_is_enforced(monkeypatch) -> None:  # noqa: ANN001
    async def fake_generate(prompt: str, *, size: str = "square", asset_id: str = "", **kw: Any):  # noqa: ANN001
        return {"ok": True, "asset_id": asset_id, "local": f"/assets/generated/{asset_id}.webp", "prompt": prompt, "model": "m"}

    monkeypatch.setattr(agent_loop.wanx, "generate", fake_generate)
    loop = agent_loop.AssetLoop(router=ScriptedRouter([]), task_type="generate", asks=agent_loop.asks_of(_design({"slot": "figure", "subject": "任意"})))
    for index in range(agent_loop.GEN_BUDGET):
        assert asyncio.run(loop.execute("generate_image", {"unit_id": "p1", "prompt": f"图{index}"}))["ok"] is True
    blocked = asyncio.run(loop.execute("generate_image", {"unit_id": "p1", "prompt": "多一张"}))
    assert blocked["ok"] is False and "预算" in blocked["error"]


def test_loop_stops_when_model_stops_calling_tools() -> None:
    router = ScriptedRouter([ToolTurn(text="我看不需要配图")])
    asks = agent_loop.asks_of(_design({"slot": "figure", "subject": "任意"}))
    result = asyncio.run(agent_loop.AssetLoop(router=router, task_type="generate", asks=asks).run())
    assert result.ran is True
    assert result.rounds == 1
    assert not result.assignments
    assert result.notes, "模型的话要留一条下来，便于事后看它为什么没配图"


def test_loop_executes_full_script(monkeypatch) -> None:  # noqa: ANN001
    async def fake_search(source: str, query: str, *, kind: str = "", limit: int = 8, **kw: Any):  # noqa: ANN001
        return [_candidate()]

    monkeypatch.setattr(materials, "search", fake_search)
    # 两轮：第一轮搜，第二轮才绑定并收工。**两轮才看得出「工具结果有没有回灌」**，
    # 一轮之内模型还没机会看到结果。
    first = ToolTurn(
        tool_calls=[_call("search_assets", {"source": "local", "query": "黄芪"}, index=0)],
        message={"role": "assistant", "content": "", "tool_calls": []},
    )
    second = ToolTurn(
        tool_calls=[
            _call("assign_asset", {"unit_id": "p1", "asset_id": "photo-huangqi-01", "why": "实拍"}, index=1),
            _call("finish", {}, index=2),
        ],
        message={"role": "assistant", "content": "", "tool_calls": []},
    )
    router = ScriptedRouter([first, second])
    asks = agent_loop.asks_of(_design({"slot": "evidence", "subject": "黄芪"}))
    result = asyncio.run(agent_loop.AssetLoop(router=router, task_type="generate", asks=asks).run())
    assert result.rounds == 2
    assert result.calls == 3
    assert [item.candidate.id for item in result.assignments] == ["photo-huangqi-01"]
    # 工具结果必须回灌进对话，否则下一轮模型不知道自己刚搜到了什么。
    transcript = router.seen[-1] if router.seen else []
    assert any(item.get("role") == "tool" for item in transcript)


def test_invalid_json_arguments_go_back_to_model() -> None:
    turn = ToolTurn(
        tool_calls=[_call("search_assets", {}, raw="{query: 黄芪", index=0)],
        message={"role": "assistant", "content": "", "tool_calls": []},
    )
    router = ScriptedRouter([turn])
    asks = agent_loop.asks_of(_design({"slot": "figure", "subject": "任意"}))
    asyncio.run(agent_loop.AssetLoop(router=router, task_type="generate", asks=asks).run())
    tool_message = next(item for item in router.seen[-1] if item.get("role") == "tool")
    payload = json.loads(tool_message["content"])
    assert payload["ok"] is False and "JSON" in payload["error"]


def test_look_at_parses_json_and_tolerates_fences(monkeypatch) -> None:  # noqa: ANN001
    async def fake_read(url: str) -> str:
        return "data:image/jpeg;base64,AAAA"

    monkeypatch.setattr(agent_loop, "_read_as_data_url", fake_read)
    router = StubRouter('```json\n{"what": "黄芪饮片", "fit": "yes", "why": "形态清楚"}\n```')
    verdict = asyncio.run(agent_loop.look_at(_candidate(), router, "generate"))
    assert verdict["ok"] is True
    assert verdict["fit"] == "yes"
    assert "黄芪" in verdict["what"]


def test_look_at_reports_failure_instead_of_guessing(monkeypatch) -> None:  # noqa: ANN001
    async def fake_read(url: str) -> str:
        return ""

    monkeypatch.setattr(agent_loop, "_read_as_data_url", fake_read)
    verdict = asyncio.run(agent_loop.look_at(_candidate(), StubRouter("{}"), "generate"))
    assert verdict["ok"] is False


def test_resolve_with_tools_swallows_exceptions() -> None:
    class Boom:
        async def chat_tools(self, *a: Any, **kw: Any) -> ToolTurn:
            raise RuntimeError("boom")

    design = _design({"slot": "figure", "subject": "任意"})
    result = asyncio.run(agent_loop.resolve_with_tools(design, router=Boom(), task_type="generate"))
    assert result.ran is False
    assert result.assignments == []
    assert result.notes


def test_tool_schemas_are_well_formed() -> None:
    names = [tool["function"]["name"] for tool in agent_loop.TOOLS]
    assert names == ["search_assets", "look_at", "assign_asset", "generate_image", "finish"]
    for tool in agent_loop.TOOLS:
        schema = tool["function"]["parameters"]
        assert schema["type"] == "object"
        assert tool["function"]["description"].strip()
    search = next(tool for tool in agent_loop.TOOLS if tool["function"]["name"] == "search_assets")
    source = search["function"]["parameters"]["properties"]["source"]
    # 每个源都要能在提示词里被说明到，否则模型只会一直用 auto。
    for value in source["enum"]:
        if value == "auto":
            continue
        assert value in search["function"]["description"], f"{value} 缺说明"
