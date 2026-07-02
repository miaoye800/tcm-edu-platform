import asyncio
from pathlib import Path

from app.agents import graph as graph_module
from app.profile.agent import run_cold_start as run_profile_cold_start


def test_knowledge_qa_routes_to_rag_and_answers_with_sources() -> None:
    asyncio.run(_assert_knowledge_qa_routes_to_rag_and_answers_with_sources())


async def _assert_knowledge_qa_routes_to_rag_and_answers_with_sources() -> None:
    state = await graph_module.run_agent("麻黄的功效是什么")

    assert state["intent"] == "knowledge_qa"
    assert any(item.startswith("rag_agent:") for item in state["trace"])
    assert "发汗解表" in state["answer"]
    assert "sources:" in state["answer"]
    assert "[1]" in state["answer"]
    assert "紫苏叶" not in state["answer"]
    assert "牛蒡子" not in state["answer"]


def test_main_graph_export_contains_runtime_router_topology(tmp_path: Path) -> None:
    path = graph_module.export_mermaid(tmp_path / "graph_main.mmd")
    text = path.read_text(encoding="utf-8")

    assert path.name == "graph_main.mmd"
    assert "router" in text
    assert "profile_agent" in text
    assert "rag_agent" in text
    assert "explain_agent" in text
    assert "profile_router" not in text


def test_profile_setup_routes_to_profile_subgraph(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / ".data" / "profile_graph.db"

    async def run_cold_start_in_tmp(*args, **kwargs):
        kwargs["db_path"] = db_path
        return await run_profile_cold_start(*args, **kwargs)

    monkeypatch.setattr(graph_module, "run_cold_start", run_cold_start_in_tmp)

    state = asyncio.run(
        graph_module.run_agent("请根据我的学习目标和基础建立画像，我是中医学专业学生。", user_id="c_check")
    )

    assert state["user_id"] == "c_check"
    assert state["intent"] == "profile_setup"
    assert any(item.startswith("profile_agent:") for item in state["trace"])
    assert any(item.startswith("profile_subgraph.dialogue_extract_agent:") for item in state["trace"])
    assert state["profile"]
