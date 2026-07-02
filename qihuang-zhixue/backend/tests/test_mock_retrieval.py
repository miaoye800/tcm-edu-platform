import asyncio

import pytest

from app.config import get_settings
from app.kb.retriever import search


@pytest.fixture(autouse=True)
def force_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("EMBEDDING_API_KEY", "ZHIPU_API_KEY", "ZHIPUAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


def test_mock_retrieval_is_semantic_for_clear_heat_fire() -> None:
    asyncio.run(_assert_mock_retrieval_is_semantic_for_clear_heat_fire())


async def _assert_mock_retrieval_is_semantic_for_clear_heat_fire() -> None:
    first = await search("清热泻火 高热烦渴", top_k=3)
    second = await search("清热泻火 高热烦渴", top_k=3)

    assert [hit.name for hit in first] == [hit.name for hit in second]
    assert first[0].name in {"石膏", "知母"}
    assert {"石膏", "知母"} & {hit.name for hit in first}


def test_mock_retrieval_is_semantic_for_tonify_qi_spleen() -> None:
    asyncio.run(_assert_mock_retrieval_is_semantic_for_tonify_qi_spleen())


async def _assert_mock_retrieval_is_semantic_for_tonify_qi_spleen() -> None:
    first = await search("补气健脾 乏力", top_k=3)
    second = await search("补气健脾 乏力", top_k=3)
    expected_buqi = {"人参", "党参", "太子参", "黄芪", "白术"}

    assert [hit.name for hit in first] == [hit.name for hit in second]
    assert first[0].name in expected_buqi
    assert expected_buqi & {hit.name for hit in first}


def test_mock_retrieval_is_semantic_for_promote_urination_edema() -> None:
    asyncio.run(_assert_mock_retrieval_is_semantic_for_promote_urination_edema())


async def _assert_mock_retrieval_is_semantic_for_promote_urination_edema() -> None:
    first = await search("利水消肿", top_k=3)
    second = await search("利水消肿", top_k=3)
    expected = {"麻黄", "香薷", "黄芪", "白术"}

    assert [hit.name for hit in first] == [hit.name for hit in second]
    assert first[0].name in expected
    assert expected & {hit.name for hit in first}
