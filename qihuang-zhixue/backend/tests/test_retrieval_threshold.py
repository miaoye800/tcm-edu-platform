import asyncio

import pytest

from app.agents.graph import NO_KB_HIT_ANSWER, run_agent
from app.config import get_settings
from app.kb.retriever import search


@pytest.fixture(autouse=True)
def force_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DEEPSEEK_API_KEY", "EMBEDDING_API_KEY", "ZHIPU_API_KEY", "ZHIPUAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


def test_uncovered_query_returns_empty_hits_and_agent_fallback() -> None:
    asyncio.run(_assert_uncovered_query_returns_empty_hits_and_agent_fallback())


async def _assert_uncovered_query_returns_empty_hits_and_agent_fallback() -> None:
    hits = await search("抗生素用法", top_k=3)
    state = await run_agent("抗生素用法 中药")

    assert hits == []
    assert state["intent"] == "knowledge_qa"
    assert state["answer"] == NO_KB_HIT_ANSWER
    assert "清热泻火" not in state["answer"]
    assert "补气健脾" not in state["answer"]
    assert "发汗解表" not in state["answer"]


def test_covered_query_returns_hits_above_threshold() -> None:
    asyncio.run(_assert_covered_query_returns_hits_above_threshold())


async def _assert_covered_query_returns_hits_above_threshold() -> None:
    threshold = get_settings().retrieval_score_threshold
    hits = await search("清热泻火 高热烦渴", top_k=3)

    assert hits
    assert hits[0].score >= threshold
    assert {"石膏", "知母"} & {hit.name for hit in hits}
