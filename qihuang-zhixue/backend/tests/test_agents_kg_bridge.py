"""主问答链路的图谱接线（M1-S4）。

查的是「检索 + 图谱」怎么合起来答一个问题，而不是图谱本身对不对（那是
``test_kg_*`` 的事）。三件事必须成立：

1. 问「为什么配」「有什么区别」「怎么扯上关系」时，答案里要出现**带页码的教材
   原文**，不是模型自己组织的一段话；
2. 问「麻黄有什么功效」这类纯属性问题时**不许**图谱插一脚——多一次查询、多一段
   无关邻域，只会让答案变长变慢；
3. 图谱挂了、超时了、库还没建好时，问答要**照常出结果**。图谱是加分项，
   不是必经之路。
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from app.agents import graph as graph_module
from app.config import get_settings
from app.kb.retriever import SearchHit
from app.llm.router import ModelRouter


def _run(question: str):
    return asyncio.run(graph_module.run_agent(question))


def _trace(state) -> str:
    return " | ".join(str(item) for item in state["trace"])


def _settings_copy(**overrides):
    return get_settings().model_copy(update=overrides)


# --------------------------------------------------------------------------- #
# 1. 该走的走通：答案里必须出现教材原文
# --------------------------------------------------------------------------- #


def test_relation_question_gets_textbook_evidence() -> None:
    """「为什么常一起用」的答案要落到原文与页码上。"""

    state = _run("麻黄和桂枝为什么常一起用")
    answer = state["answer"]

    assert "教材依据" in answer
    assert "相须" in answer
    assert "p149" in answer
    assert "图谱补" in _trace(state)
    # 两味药都要点到：以前只念 docs[0]，用户问桂枝，答案里却没有桂枝。
    assert "麻黄" in answer and "桂枝" in answer


def test_compare_question_lists_both_sides() -> None:
    state = _run("麻黄和桂枝有什么区别")
    answer = state["answer"]

    assert "教材依据" in answer
    assert "四气" in answer and "功效" in answer
    assert "麻黄温" in answer.replace(" ", "")


def test_constraint_is_rendered_before_differences() -> None:
    """问「人参和藜芦有什么区别」，第一句依据必须是十八反。

    顺序不是排版癖好：先讲四气五味，用户读到这里很可能已经去抓药了。
    """

    state = _run("人参和藜芦有什么区别")
    answer = state["answer"]

    marker = answer.index("教材依据")
    head = answer[marker : marker + 60]
    assert "禁忌" in head and "十八反" in head


def test_path_question_renders_the_chain() -> None:
    state = _run("麻黄到麻黄汤中间经过什么")
    answer = state["answer"]

    assert "关系链" in answer
    assert "组成" in answer


def test_book_only_evidence_still_gets_a_citation() -> None:
    """六成边只有书名、没有 ``ref``。这种边不能因为「没 ref」就不给引用。"""

    state = _run("人参和藜芦能一起用吗")
    answer = state["answer"]

    assert "十八反" in answer
    assert "教材 128" in answer or "p128" in answer


# --------------------------------------------------------------------------- #
# 2. 不该走的不许走
# --------------------------------------------------------------------------- #


def test_plain_attribute_question_does_not_expand() -> None:
    """纯属性问题不扩展：多查一次图谱只会多一段无关邻域。"""

    state = _run("麻黄的功效是什么")
    answer = state["answer"]

    assert "教材依据" not in answer
    assert "发汗解表" in answer
    assert "无需图谱扩展" in _trace(state)
    assert all(doc.get("kind", "herb") == "herb" for doc in state["retrieved_docs"])


def test_unknown_herb_question_does_not_expand() -> None:
    """库外的东西不许硬扩：一个实体都认不出来时，图谱只能闭嘴。"""

    state = _run("抗生素和中药一起吃有什么风险")
    assert "教材依据" not in state["answer"]


# --------------------------------------------------------------------------- #
# 3. 降级：图谱坏了，问答照常
# --------------------------------------------------------------------------- #


def test_disabled_by_config_degrades_silently(monkeypatch) -> None:
    monkeypatch.setattr(
        graph_module, "get_settings", lambda: _settings_copy(kg_agent_enabled=False)
    )
    state = _run("麻黄和桂枝为什么常一起用")

    assert "图谱扩展已关闭" in _trace(state)
    assert "教材依据" not in state["answer"]
    assert "发汗解表" in state["answer"], "关掉图谱后基础检索必须还在工作"


def test_timeout_degrades_silently(monkeypatch) -> None:
    """超时要退化成普通检索，而不是让用户对着转圈等。"""

    def slow(*_args, **_kwargs):
        import time

        time.sleep(1.5)
        return None

    monkeypatch.setattr(graph_module, "_kg_expand_sync", slow)
    monkeypatch.setattr(
        graph_module,
        "get_settings",
        lambda: _settings_copy(kg_agent_timeout_seconds=0.05),
    )
    state = _run("麻黄和桂枝为什么常一起用")

    assert "超时" in _trace(state)
    assert "发汗解表" in state["answer"]


def test_sqlite_error_degrades_silently(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("no such table: kg_edge")

    monkeypatch.setattr(graph_module, "_kg_expand_sync", boom)
    state = _run("麻黄和桂枝为什么常一起用")

    assert "尚未就绪" in _trace(state)
    assert "发汗解表" in state["answer"]


def test_unexpected_error_is_logged_not_raised(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("随便什么 bug")

    monkeypatch.setattr(graph_module, "_kg_expand_sync", boom)
    state = _run("麻黄和桂枝为什么常一起用")

    assert "异常" in _trace(state)
    assert "发汗解表" in state["answer"]


# --------------------------------------------------------------------------- #
# 4. 形状：relation doc 不能被资源链路读炸
# --------------------------------------------------------------------------- #


def test_relation_doc_carries_resource_keys() -> None:
    """``build_whitelist`` 会读 ``doc["combinations"]``，缺键是 KeyError 不是空表。"""

    async def collect():
        hits = [
            SearchHit(
                name="麻黄",
                score=0.9,
                verified=True,
                sources=[],
                id="herb_mahuang",
            )
        ]
        return await graph_module._kg_expand("麻黄和桂枝为什么常一起用", hits)

    cards, reason = asyncio.run(collect())
    assert reason == ""
    assert len(cards) == 1

    card = cards[0]
    for key in ("kind", "name", "effects", "indications", "sources", "combinations", "aliases"):
        assert key in card, f"relation doc 缺 {key}"
    assert card["kind"] == "relation"
    assert card["combinations"] == [] and card["aliases"] == []
    assert card["payload"]["relations"]


def test_hits_keep_entity_id() -> None:
    """``SearchHit.id`` 必须留在 doc 里——图谱扩展靠它，不留就只能重新解析问题。"""

    docs = graph_module._hits_to_docs(
        [SearchHit(name="麻黄", score=0.9, verified=True, sources=[], id="herb_mahuang")]
    )
    assert docs[0]["id"] == "herb_mahuang"
    assert docs[0]["kind"] == "herb"


def test_answer_length_is_capped() -> None:
    """修「只用第一本」时最容易被顺手写长，这里钉住上限。"""

    state = _run("麻黄和桂枝有什么区别")
    assert len(state["answer"]) <= 1600


def test_sources_do_not_repeat() -> None:
    state = _run("麻黄和桂枝为什么常一起用")
    block = state["answer"].split("sources:", 1)[1]
    lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
    assert 1 <= len(lines) <= 4
    assert len(set(lines)) == len(lines)


# --------------------------------------------------------------------------- #
# 5. 意图路由：图谱不能把问题带去「生成资源」
# --------------------------------------------------------------------------- #


def test_graph_word_in_a_question_is_knowledge_qa() -> None:
    router = ModelRouter()
    assert asyncio.run(router.classify("说说图谱里麻黄和桂枝为什么配")) == "knowledge_qa"


def test_generate_graph_is_still_resource_generation() -> None:
    router = ModelRouter()
    assert asyncio.run(router.classify("帮我生成一张知识图谱")) == "resource_generation"


def test_resource_chain_prefix_still_routes_to_resource_generation() -> None:
    """资源链路传的是「生成资源：<主题>」，它不该被这次改动影响。"""

    router = ModelRouter()
    assert asyncio.run(router.classify("生成资源：麻黄")) == "resource_generation"


def test_contra_question_routes_to_knowledge_qa() -> None:
    """「能一起用吗」没有药名以外的线索，也要走知识问答而不是掉进 LLM 兜底。"""

    router = ModelRouter()
    assert asyncio.run(router.classify("藜芦和细辛能一起用吗")) == "knowledge_qa"


@pytest.mark.parametrize("question", ["麻黄和桂枝为什么常一起用", "麻黄和桂枝有什么区别"])
def test_no_answer_without_sources(question: str) -> None:
    """有内容就必须有 sources——溯源断了，答案就不能出。"""

    state = _run(question)
    assert "sources:" in state["answer"]
    assert state["answer"].split("sources:", 1)[1].strip()
