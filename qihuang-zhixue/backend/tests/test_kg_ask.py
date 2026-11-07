"""GraphRAG 问答测试（``app/kg/ask.py`` + ``POST /api/kg/ask``）。

验证的是「查到的」而不是「猜到的」：

- 种子里认出的实体必须是问题原文里真出现的，且不能把「麻黄汤」拆成「麻黄」；
- 上下文里的每条关系都必须带教材原文；
- 摘要里出现的每个药名/证名都必须能在 ``context`` 里找到；
- 认不出实体时要说「没找到起点」，而不是编一段像模像样的答案。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.kg import get_kg_db_path
from app.kg import entities as kg_entities
from app.kg import store as kg_store
from app.kg.ask import ask, detect_seeds
from app.kg.constants import Tier
from app.kg.schema import ensure_schema
from app.main import app
from tests.helpers import auth_session


def _build_kg_module():
    """按文件路径加载构建脚本。

    不要 ``sys.path.insert(scripts)``——那会把 ``backend/scripts`` 塞进整个
    pytest 会话的导入路径，可能给后续模块的导入埋雷。
    """

    path = Path(__file__).resolve().parents[1] / "scripts" / "build_kg.py"
    spec = importlib.util.spec_from_file_location("build_kg_for_ask", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kg_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """真建一次图，不手搓边——扩展能不能命中取决于边到底在不在。"""

    build = _build_kg_module().build

    db_path = tmp_path_factory.mktemp("ask") / "ask.db"
    ensure_schema(db_path)
    entity_list, alias_list = kg_entities.build_registry(db_path)
    kg_store.upsert_entities(entity_list, db_path)
    kg_store.upsert_aliases(alias_list, db_path)
    build(db_path)
    return db_path


def test_seeds_come_from_question_text(kg_db: Path) -> None:
    result = ask("麻黄和桂枝为什么常一起用", db_path=kg_db)
    names = [seed.name for seed in result.seeds]
    assert "麻黄" in names and "桂枝" in names
    assert all(seed.matched_by == "mention" for seed in result.seeds)


def test_formula_name_is_not_split_into_herb(kg_db: Path) -> None:
    """问「麻黄汤」不该只认出「麻黄」——最长匹配优先，且区间不重叠。"""

    result = ask("麻黄汤由哪些药组成", db_path=kg_db)
    names = [seed.name for seed in result.seeds]
    assert "麻黄汤" in names
    assert "麻黄" not in names


def test_expansion_returns_textbook_evidence(kg_db: Path) -> None:
    result = ask("麻黄和桂枝", db_path=kg_db)
    assert result.subgraph.edges
    assert result.citations
    assert all(item["quote"] for item in result.citations)
    # 相须是教材明文，必须能被查到并带上原文
    xiangxu = [item for item in result.context["relations"] if item["relation_type"] == "相须"]
    assert xiangxu, "麻黄-桂枝 的相须关系没有进上下文"
    assert xiangxu[0]["evidence"][0]["quote"]


def test_tier3_similarity_never_enters_context(kg_db: Path) -> None:
    """相似边回答不了「为什么」，不许混进上下文。"""

    result = ask("麻黄", db_path=kg_db)
    tiers = {item["tier"] for item in result.context["relations"]}
    assert int(Tier.SIMILARITY) not in tiers


def test_answer_only_mentions_facts_from_context(kg_db: Path) -> None:
    result = ask("麻黄能治什么", db_path=kg_db)
    known = {item["name"] for item in result.context["herbs"]}
    known |= {item["name"] for item in result.context["syndromes"]}
    known |= {item["name"] for item in result.context["formulas"]}
    # 摘要里提到的每个实体名都必须能在 context 里找到出处
    mentioned = [name for name in known if name in result.answer]
    assert mentioned, result.answer
    assert "麻黄" in result.answer


def test_answer_keeps_textbook_and_manual_indications_apart(kg_db: Path) -> None:
    """教材原文主治和人工归纳的通用证名必须分开列。

    两套都留着是为了覆盖不同措辞，但拼成一句「教材记载主治：A；B；C；D」
    会读成「这味药能治十几个证」，后半段其实是前半段的粗粒度归纳。
    """

    result = ask("黄芪能治什么", db_path=kg_db)
    answer = result.answer

    assert "教材原文主治：" in answer
    assert "通用证名（归纳，非原文）：" in answer
    # 教材那条原文必须在，且只能出现在「教材原文主治」这一句里
    textbook_line = answer.split("教材原文主治：", 1)[1].split("。", 1)[0]
    assert "气虚乏力，食少便溏" in textbook_line
    assert "脾气虚证" not in textbook_line, "人工证名混进了教材原文那句"


def test_unknown_question_says_no_starting_point(kg_db: Path) -> None:
    result = ask("阿司匹林和布洛芬能不能一起吃", db_path=kg_db)
    assert result.seeds == []
    assert "没找到起点" in result.answer


def test_expansion_can_start_from_retrieval_ids(kg_db: Path) -> None:
    """上游向量检索已经给出实体 id 时，不该再回头解析一遍问题文本。

    这里故意把问题写成**认不出实体名**的句子，只靠 ``seed_ids`` 起步：
    如果实现又去解析文本，就会认不出种子、返回「没找到起点」。
    """

    seeds = kg_store.search_entities("麻黄", limit=1, db_path=kg_db)
    assert seeds, "库里应该有麻黄"
    entity, _matched_by = seeds[0]

    result = ask("这两个为什么配在一起", db_path=kg_db, seed_ids=[entity.id])

    assert result.seeds, "按 id 起步失败"
    assert result.seeds[0].entity_id == entity.id
    assert result.seeds[0].matched_by == "retrieval"
    assert result.subgraph.edges


def test_unknown_seed_id_is_skipped_not_guessed(kg_db: Path) -> None:
    """给一个图里不存在的 id：跳过，**不按名字猜**，也不能凭空造种子。"""

    result = ask("随便问问", db_path=kg_db, seed_ids=["herb_不存在"])
    assert result.seeds == []
    assert "没找到起点" in result.answer


def test_detect_seeds_strict_mode_only_accepts_literal_mentions(kg_db: Path) -> None:
    """严格模式：文本里没原样写出的实体，一律不算。

    路由判定与图谱扩展门槛都必须用 ``fuzzy=False``。模糊回落会把
    「阿司匹林和布洛芬」硬凑出一个实体，于是本该走普通 RAG 的问题被扩成
    一张无关的邻域图——用户问西药，回答里冒出中药关系。
    """

    seeds = detect_seeds("麻黄和桂枝", db_path=kg_db, fuzzy=False)
    assert {seed.name for seed in seeds} == {"麻黄", "桂枝"}
    assert all(seed.matched_by == "mention" for seed in seeds)

    assert detect_seeds("阿司匹林和布洛芬", db_path=kg_db, fuzzy=False) == []


def test_detect_seeds_strict_mode_does_not_split_formula(kg_db: Path) -> None:
    """最长匹配优先在严格模式下同样成立：不把「桂枝汤」拆出「桂枝」。"""

    names = {seed.name for seed in detect_seeds("麻黄和桂枝汤", db_path=kg_db, fuzzy=False)}
    assert "桂枝汤" in names
    assert "桂枝" not in names


def test_ask_endpoint_accepts_seed_ids(kg_db: Path) -> None:
    """接口层也要能「带着检索结果来」，不用把问题再解析一遍。"""

    entity, _ = kg_store.search_entities("麻黄", limit=1, db_path=kg_db)[0]

    app.dependency_overrides[get_kg_db_path] = lambda: kg_db
    try:
        client = TestClient(app)
        session = auth_session(client, "ask-seed-user", kg_db)
        response = client.post(
            "/api/kg/ask",
            json={"question": "这两味为什么配在一起", "seed_ids": [entity.id]},
            headers=session["headers"],
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert [seed["id"] for seed in payload["seeds"]] == [entity.id]
        assert payload["seeds"][0]["matched_by"] == "retrieval"
        assert payload["graph"]["edges"]
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)


def test_ask_endpoint_returns_context_and_citations(kg_db: Path) -> None:
    app.dependency_overrides[get_kg_db_path] = lambda: kg_db
    try:
        client = TestClient(app)
        session = auth_session(client, "ask-user", kg_db)
        response = client.post(
            "/api/kg/ask",
            json={"question": "麻黄和桂枝为什么常一起用"},
            headers=session["headers"],
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["seeds"]
        assert payload["answer"]
        assert payload["citations"]
        assert payload["graph"]["edges"]
        assert payload["method"] == "kg_expand_v1"
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)
