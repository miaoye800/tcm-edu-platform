import asyncio

import pytest

from app.config import get_settings
from app.kb.ingest import load_herbs
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

    # 期望集直接取自教材：**功效项里含「利水消肿」四个字**的药材。
    # 原来是 {麻黄, 香薷, 黄芪, 白术}——那是药材还只有 76 味时写的；
    # 教材抽全 442 味之后变成 14 味（白术的功效里其实没有「利水消肿」）。
    # 这 14 味同分并列，谁排第一由同分次序决定，**没有医学含义**——所以断言
    # 「第一名确实是带这条功效的药」，而不是某个特定药名。
    expected = {
        "黄芪",
        "麻黄",
        "香薷",
        "海藻",
        "桑白皮",
        "鸭跖草",
        "防己",
        "五加皮",
        "玉米须",
        "葫芦",
        "香加皮",
        "枳椇子",
        "泽兰",
        "昆布",
    }

    assert [hit.name for hit in first] == [hit.name for hit in second]
    assert first[0].name in expected
    assert {hit.name for hit in first} <= expected


def test_hits_carry_graph_entity_id() -> None:
    """检索结果必须带图谱实体 id——没有它就没法沿图扩展。

    按名字反查是旧做法：同名不同物（炮制品、地方用药）一出现就串条目。
    """

    asyncio.run(_assert_hits_carry_graph_entity_id())


async def _assert_hits_carry_graph_entity_id() -> None:
    hits = await search("麻黄", top_k=3)

    assert hits
    assert all(hit.id for hit in hits), f"有命中没带 id：{[hit.name for hit in hits]}"
    assert all(hit.id.startswith("herb_") for hit in hits), [hit.id for hit in hits]

    by_name = {herb.name: herb.id for herb in load_herbs()}
    for hit in hits:
        assert by_name[hit.name] == hit.id, f"{hit.name} 的 id 对不上：{hit.id}"
