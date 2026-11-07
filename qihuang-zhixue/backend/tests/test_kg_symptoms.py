"""症状索引测试（``app/kg/symptoms.py``）。

这一层要解决的核心问题是**覆盖度**：手写词表只有 20 来个词，患者说法是开放的。
所以这里验证的重点不是「某句话能不能匹配」，而是：

1. 词表规模**跟着教材走**（≥900 条短语），不是跟着人手走；
2. 泛词不能压过专词（「热毒咽痛」要排到 热毒证，而不是随便一个含「咽痛」的证候）；
3. 匹配不上时返回空，**不猜**——空结果会交给 LLM 闭集映射兜底。
"""

from __future__ import annotations

import pytest

from app.kg.symptoms import build_index, candidates, reset_cache


@pytest.fixture(scope="module", autouse=True)
def _fresh_index() -> None:
    reset_cache()


def test_index_scale_comes_from_textbook_not_handwriting() -> None:
    """索引规模必须来自教材数据，而不是人工枚举。"""

    index = build_index()
    assert len(index.terms) >= 900, f"短语数只有 {len(index.terms)}，覆盖度不够"
    assert len(index.profiles) >= 200, "证候 profile 太少，模糊层会失效"


def test_phrase_layer_matches_textbook_symptom_words() -> None:
    """「无汗」是教材描述里的原词，应当能直接命中风寒表实证。"""

    names = [item.name for item in candidates("怕冷，无汗，头疼")]
    assert "风寒表实证" in names


def test_alias_layer_handles_colloquial_phrasing() -> None:
    """「嗓子疼」教材里没有，靠口语别名映射到咽喉肿痛。"""

    result = candidates("嗓子疼得厉害")
    assert result
    names = [item.name for item in result]
    assert "咽喉肿痛" in names
    assert any(item.layer == "alias" for item in result)


def test_specific_term_outranks_generic_term() -> None:
    """IDF 加权：专词（热毒）要压过泛词（咽痛）。

    「咽痛」在几十个证候里都出现，几乎不区分；「热毒」只挂在少数几个上。
    不加权的话，「热毒咽痛」会排到「温疫时毒」这种含咽痛但不含热毒的证候。
    """

    result = candidates("热毒咽痛", top_k=3)
    assert result
    assert result[0].name.startswith("热毒"), [item.name for item in result]


def test_name_layer_wins_when_syndrome_named_directly() -> None:
    result = candidates("咳嗽痰多")
    assert result and result[0].layer == "name"
    assert result[0].name == "咳嗽痰多"


def test_irrelevant_text_returns_empty_instead_of_guessing() -> None:
    """匹配不上就是匹配不上。乱给一个证候比给空更糟。"""

    assert candidates("今天天气不错") == []
    assert candidates("") == []


def test_llm_layer_is_skipped_without_key() -> None:
    """没有 key 时 L4 直接返回空，绝不用 mock 假装映射成功。"""

    from app.kg import symptom_llm

    import asyncio

    picks = asyncio.run(symptom_llm.map_case_to_syndromes("一咽口水就疼，还有点烧"))
    assert picks == [] or symptom_llm.available()


def test_llm_candidate_pool_is_the_whole_closed_set() -> None:
    """闭集必须真的是**全集**，不能悄悄截断。

    回归用：``MAX_SHORTLIST`` 曾是 40，候选池按名字排序后取前 40，
    「风寒表实证」「热毒证」「气虚证」「脾胃虚寒证」全被切掉——
    模型再准也选不到清单上没有的证候，闭集变成了残集，而且**没有任何报错**。
    """

    from app.kg import symptom_llm

    pool = symptom_llm.known_syndromes()
    narrowed = symptom_llm._narrow(pool, "怕冷，无汗，头疼")

    assert len(narrowed) == len(pool), "当前教材规模下不该发生任何截断"
    # 教材里的证候粒度是「气虚下陷」「脾肺气虚」，不是笼统的「气虚证」。
    for must_have in ("风寒表实证", "热毒证", "气虚下陷", "脾肺气虚"):
        assert must_have in narrowed, f"{must_have} 被挤出候选池，模型永远选不到"


def test_narrowing_keeps_lexically_relevant_candidates() -> None:
    """真的超限时，截断也要保住和原文沾边的，而不是按名字顺序瞎砍。

    注意这里只断言**字面**相关：候选池里有「风寒表实证」，原文是「风寒感冒」，
    两者共享「风寒」这一组二元组，所以能被保住。而「怕冷，无汗」和
    「风寒表实证」一个二元组都不共享——字面层救不了这种，那才是 L4 存在的理由。
    """

    from app.kg import symptom_llm

    pool = [f"占位证候{i:04d}" for i in range(symptom_llm.MAX_SHORTLIST + 50)]
    pool.append("风寒表实证")

    narrowed = symptom_llm._narrow(pool, "风寒感冒三天了")
    assert "风寒表实证" in narrowed
    assert len(narrowed) == symptom_llm.MAX_SHORTLIST
