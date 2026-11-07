"""模型投影测试（``app/kg/projection.py``）。

两条底线：

1. **不超预算**——超了就删关系，不删证据。
2. **不留空壳**——任何进入投影的关系都必须带教材原文、页码、出处。
   「A 和 B 有关系但没依据」这句话比不返回更糟。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.kg import entities as kg_entities
from app.kg import projection
from app.kg import store as kg_store
from app.kg.ask import ask
from app.kg.compare import compare, find_path
from app.kg.reverse import reverse_lookup
from app.kg.schema import ensure_schema


def _build_kg_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_kg.py"
    spec = importlib.util.spec_from_file_location("build_kg_for_projection", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kg_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    build = _build_kg_module().build
    db_path = tmp_path_factory.mktemp("projection") / "projection.db"
    ensure_schema(db_path)
    entity_list, alias_list = kg_entities.build_registry(db_path)
    kg_store.upsert_entities(entity_list, db_path)
    kg_store.upsert_aliases(alias_list, db_path)
    build(db_path)
    return db_path


def _assert_no_empty_span(payload: dict) -> None:
    def walk(value: object) -> None:
        if isinstance(value, dict):
            if "text_span" in value:
                assert value["text_span"], f"投影里出现了空 text_span：{value}"
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)


# --------------------------------------------------------------------------- #
# token 估算
# --------------------------------------------------------------------------- #


def test_estimate_tokens_is_deliberately_conservative() -> None:
    """中文按 0.75 token/字偏保守（实际约 0.6），宁可提前删也别被模型截断。"""

    assert projection.estimate_tokens("麻黄") == 2
    hundred = "药" * 100
    assert 70 <= projection.estimate_tokens(hundred) <= 80
    assert projection.estimate_tokens("") == 0


# --------------------------------------------------------------------------- #
# ask
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "question",
    [
        "麻黄和桂枝为什么常一起用",
        "人参和藜芦能一起用吗",
        "麻黄能治哪些证",
    ],
)
def test_project_ask_stays_within_budget(kg_db: Path, question: str) -> None:
    result = projection.project_ask(ask(question, db_path=kg_db))
    assert result.tokens <= projection.DEFAULT_BUDGET
    assert result.chars <= projection.DEFAULT_MAX_CHARS


def test_project_ask_relations_always_carry_evidence(kg_db: Path) -> None:
    result = projection.project_ask(ask("麻黄和桂枝为什么常一起用", db_path=kg_db))
    relations = result.payload["relations"]
    assert relations
    for item in relations:
        assert item["text_span"], item
        assert item["page"] is not None, item
        assert item["ref"], item
        assert item["edge_id"] is not None, item
    _assert_no_empty_span(result.payload)


def test_project_ask_puts_tier1_first(kg_db: Path) -> None:
    """裁的时候先丢最弱的，禁忌必须活到最后。"""

    result = projection.project_ask(ask("人参和藜芦能一起用吗", db_path=kg_db))
    tiers = [item["tier"] for item in result.payload["relations"]]
    assert tiers == sorted(tiers)
    assert tiers and tiers[0] == 1


def test_project_ask_extreme_case_drops_and_still_fits() -> None:
    """40 条关系 + 2000 字答案，必须裁到预算内并记录丢了什么。"""

    long_quote = "麻黄辛温，发汗解表，宣肺平喘，利水消肿，为发汗解表之要药。" * 4
    relations = [
        {
            "edge_id": 1000 + index,
            "source": {"id": f"a{index}", "name": f"药{index}"},
            "target": {"id": f"b{index}", "name": f"药{index + 1}"},
            "relation_type": "配伍",
            "tier": 2,
            "label": "配伍",
            "confidence": 0.7,
            "evidence": [{"quote": long_quote, "page": 100 + index, "ref": "《中药学》"}],
        }
        for index in range(40)
    ]
    fake = SimpleNamespace(
        question="极端用例",
        answer="答案" * 1000,
        seeds=[SimpleNamespace(name="麻黄", kind="herb")],
        context={
            "constraints": [],
            "relations": relations,
            "herbs": [],
            "seed_ids": ["a0"],
            "truncated": True,
        },
    )

    result = projection.project_ask(fake)
    assert result.tokens <= projection.DEFAULT_BUDGET
    assert result.chars <= projection.DEFAULT_MAX_CHARS
    assert result.dropped, "裁掉内容必须留记录"
    assert len(result.payload["relations"]) < 40
    _assert_no_empty_span(result.payload)


def test_project_ask_without_relations_keeps_answer(kg_db: Path) -> None:
    """认出了实体但没有边：给合法的空 relations，不抛异常。"""

    result = projection.project_ask(ask("麻黄", db_path=kg_db))
    assert isinstance(result.payload["relations"], list)
    assert result.payload["seeds"]


# --------------------------------------------------------------------------- #
# path / reverse / compare
# --------------------------------------------------------------------------- #


def test_project_path_keeps_every_step_sourced(kg_db: Path) -> None:
    payload = projection.project_path(find_path("麻黄", "麻黄汤", max_hops=2, db_path=kg_db))
    assert payload.payload["found"] is True
    assert payload.payload["steps"]
    for step in payload.payload["steps"]:
        assert step["text_span"]
        assert step["edge_id"] is not None
    _assert_no_empty_span(payload.payload)


def test_project_reverse_caps_syndromes_and_keeps_evidence(kg_db: Path) -> None:
    result = projection.project_reverse(reverse_lookup("咽喉肿痛", db_path=kg_db))
    assert len(result.payload["syndromes"]) <= 3
    _assert_no_empty_span(result.payload)
    assert result.tokens <= projection.DEFAULT_BUDGET


def test_project_compare_keeps_textbook_differentiation(kg_db: Path) -> None:
    result = projection.project_compare(compare("麻黄", "桂枝", db_path=kg_db))
    payload = result.payload
    assert payload["textbook_differentiation"], "教材原文鉴别不该被裁掉"
    assert payload["textbook_differentiation"]["text_span"]
    assert payload["differences"]
    assert result.tokens <= projection.DEFAULT_BUDGET
    _assert_no_empty_span(payload)


def test_projection_payload_is_json_serializable(kg_db: Path) -> None:
    result = projection.project_ask(ask("麻黄和桂枝为什么常一起用", db_path=kg_db))
    assert json.loads(json.dumps(result.payload, ensure_ascii=False))


def test_relation_order_follows_question_focus() -> None:
    """同一份邻域，「为什么配」先给配伍边，「能治什么」先给药证链。

    排序只看关系名是不够的：麻黄邻域里既有 25 条配伍、也有 6 条药证链，
    按关系名排只会答对一半问法。这条测试钉住「排序跟着问句走」。
    """

    from app.kg.contract import relation_focus

    pair = relation_focus("麻黄和桂枝为什么常一起用")
    indication = relation_focus("麻黄能治哪些证")
    formula = relation_focus("麻黄汤由哪些药组成")

    assert pair, "问配伍关系没拿到焦点"
    assert indication, "问主治没拿到焦点"
    assert formula and formula[0] == "组成", "问方剂组成时组成边应排第一"

    # 配伍题：配伍边碾过药证链；主治题：药证链碾过配伍边。
    assert projection._relation_rank("相须", pair) < projection._relation_rank("药证链", pair)
    assert projection._relation_rank("药证链", indication) < projection._relation_rank("相须", indication)
    # 没有焦点时退回默认表，且默认表里禁忌永远在最前。
    assert projection._relation_rank("十八反") == 0


def test_relation_order_follows_question_focus() -> None:
    """同一份邻域，「为什么配」先给配伍边，「能治什么」先给药证链。

    排序只看关系名是不够的：麻黄邻域里既有 25 条配伍、也有 6 条药证链，
    按关系名排只会答对一半问法。这条测试钉住「排序跟着问句走」。
    """

    from app.kg.contract import relation_focus

    pair = relation_focus("麻黄和桂枝为什么常一起用")
    indication = relation_focus("麻黄能治哪些证")
    formula = relation_focus("麻黄汤由哪些药组成")

    assert pair, "问配伍关系没拿到焦点"
    assert indication, "问主治没拿到焦点"
    assert formula and formula[0] == "组成", "问方剂组成时组成边应排第一"

    # 配伍题：配伍边碾过药证链；主治题：药证链碾过配伍边。
    assert projection._relation_rank("相须", pair) < projection._relation_rank("药证链", pair)
    assert projection._relation_rank("药证链", indication) < projection._relation_rank("相须", indication)
    # 没有焦点时退回默认表，且默认表里禁忌永远在最前。
    assert projection._relation_rank("十八反") == 0
