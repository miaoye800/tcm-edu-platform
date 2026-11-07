"""契约层测试（``app/kg/contract.py``）。

守的是三件事：

1. **六个能力都写清了「什么时候别用我」**——只写「我能干什么」的工具，
   模型不知道该在什么时候想起它。
2. **形状不漂移**——normalizer 实际输出的 key 必须在声明的 ``output_shape``
   里，否则改了实现没人发现。
3. **证据永远指得到书**——六成边只有 ``book`` 没有 ``ref``，归一化必须兜住。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from app.kg import contract
from app.kg import entities as kg_entities
from app.kg import store as kg_store
from app.kg.ask import ask
from app.kg.compare import compare, find_path
from app.kg.prescription import ReviewFinding
from app.kg.reverse import reverse_lookup
from app.kg.schema import ensure_schema
from app.kg.traverse import similar_neighbors

#: 所有能力共有的信封字段。业务字段必须逐个声明，这四个不算业务字段。
_ENVELOPE_KEYS = {"ok", "capability", "reason", "hint"}


def _build_kg_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_kg.py"
    spec = importlib.util.spec_from_file_location("build_kg_for_contract", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kg_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    build = _build_kg_module().build
    db_path = tmp_path_factory.mktemp("contract") / "contract.db"
    ensure_schema(db_path)
    entity_list, alias_list = kg_entities.build_registry(db_path)
    kg_store.upsert_entities(entity_list, db_path)
    kg_store.upsert_aliases(alias_list, db_path)
    build(db_path)
    return db_path


# --------------------------------------------------------------------------- #
# 声明本身
# --------------------------------------------------------------------------- #


def test_all_six_capabilities_are_declared() -> None:
    assert set(contract.CAPABILITIES) == set(contract.CAPABILITY_ORDER)
    assert len(contract.CAPABILITY_ORDER) == 6


def test_every_description_has_both_sections_with_examples() -> None:
    """「该用」和「别用」两段都必须有，且每段至少带一个真实问句。"""

    for name in contract.CAPABILITY_ORDER:
        text = contract.CAPABILITIES[name].description()
        assert "【什么时候该用】" in text, name
        assert "【什么时候别用】" in text, name

        use_body = text.split("【什么时候该用】", 1)[1].split("【什么时候别用】", 1)[0]
        avoid_body = text.split("【什么时候别用】", 1)[1].split("【输入】", 1)[0]
        assert "如「" in use_body, f"{name} 的「该用」没给真实问句"
        assert "如「" in avoid_body, f"{name} 的「别用」没给真实问句"


def test_tool_schema_budget() -> None:
    """六个 tool 定义要能常驻 system 段，不能吃掉整个上下文。"""

    payload = json.dumps(contract.tool_schemas(), ensure_ascii=False)
    assert len(payload) <= 2200, f"tool schema 共 {len(payload)} 字符，超预算"


def test_every_capability_declares_failure_semantics() -> None:
    for name in contract.CAPABILITY_ORDER:
        assert contract.CAPABILITIES[name].failure_note.strip(), name


# --------------------------------------------------------------------------- #
# 证据归一化
# --------------------------------------------------------------------------- #


def test_evidence_span_falls_back_to_book() -> None:
    """3337 条 evidence 只有 book。不兜底，citation 的 ref 就是空串。"""

    span = contract.evidence_span({"quote": "麻黄辛温", "book": "《中药学》", "page": 149})
    assert span["ref"] == "《中药学》"
    assert span["page"] == 149
    assert span["has_text"] is True


def test_evidence_span_flags_missing_text() -> None:
    span = contract.evidence_span({"quote": "  ", "ref": "教材 p1"})
    assert span["has_text"] is False
    assert span["text_span"] == ""


def test_provenance_two_orthogonal_axes() -> None:
    """四种组合都必须能表达，不能压成一个等级。"""

    assert contract.provenance(12, "原文") == {"has_text": True, "linkable": True}
    assert contract.provenance(None, "原文") == {"has_text": True, "linkable": False}
    assert contract.provenance(12, "") == {"has_text": False, "linkable": True}
    assert contract.provenance(None, "") == {"has_text": False, "linkable": False}


def test_book_only_edges_in_real_db_get_a_ref() -> None:
    """真库里六成 evidence 只有 ``book`` 没有 ``ref``，归一化必须兜住。

    这条针对**本地知识库**而不是测试临时库：临时库由 ``build_kg`` 现建，
    evidence 都带 ref；真实库里有 3337 条只有书名。两者都要能跑，所以库不在
    时跳过，而不是改断言迁就临时库。
    """

    from app.profile.store import DB_PATH

    if not Path(DB_PATH).exists():
        pytest.skip("本地没有知识库，跳过真数据校验")

    connection = sqlite3.connect(f"file:{Path(DB_PATH).as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT evidence_json FROM kg_edge").fetchall()
    finally:
        connection.close()

    book_only = 0
    for (raw,) in rows:
        try:
            evidence = json.loads(raw or "[]")
        except json.JSONDecodeError:
            continue
        for item in evidence if isinstance(evidence, list) else []:
            if not isinstance(item, dict) or item.get("ref") or not item.get("book"):
                continue
            book_only += 1
            span = contract.evidence_span(item)
            assert span["ref"], item
            assert span["has_text"], item

    assert book_only > 1000, f"只找到 {book_only} 条 book-only 证据，与预期量级不符"


def test_missing_page_is_not_missing_source() -> None:
    """证方链那批边没有页码，但**必须有出处**。

    「没有页码」和「没有出处」不是一回事：前者用户还能按书名去翻，后者等于编造。
    真库里这类证据（``证方链``，page 为空）有一百多条，契约层不许把它们归一成
    空 ref——那正好是「有引用但指不到书」。
    """

    from app.profile.store import DB_PATH

    if not Path(DB_PATH).exists():
        pytest.skip("本地没有知识库，跳过真数据校验")

    connection = sqlite3.connect(f"file:{Path(DB_PATH).as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT relation_type, evidence_json FROM kg_edge").fetchall()
    finally:
        connection.close()

    no_page = 0
    for relation_type, raw in rows:
        try:
            evidence = json.loads(raw or "[]")
        except json.JSONDecodeError:
            continue
        for item in evidence if isinstance(evidence, list) else []:
            if not isinstance(item, dict) or item.get("page") not in (None, ""):
                continue
            no_page += 1
            span = contract.evidence_span(item)
            assert span["page"] is None, item
            assert span["ref"], f"{relation_type} 这条证据既没有页码也没有出处"
            assert span["has_text"], item

    assert no_page > 100, f"只找到 {no_page} 条无页码证据，与预期量级不符"


# --------------------------------------------------------------------------- #
# 归一化形状
# --------------------------------------------------------------------------- #


def _assert_shape(actual: dict, declared: dict) -> None:
    extra = set(actual) - set(declared) - _ENVELOPE_KEYS
    assert not extra, f"normalizer 输出了未声明的字段：{sorted(extra)}"


def test_normalize_ask_covers_declared_shape(kg_db: Path) -> None:
    result = contract.normalize_ask(ask("麻黄和桂枝为什么常一起用", db_path=kg_db))
    _assert_shape(result, contract.CAPABILITIES["ask"].output_shape)
    assert result["ok"] is True
    assert result["relations"], "相须关系没进契约输出"
    assert all(item["evidence"] for item in result["relations"])


def test_normalize_ask_failure_when_no_seed(kg_db: Path) -> None:
    result = contract.normalize_ask(ask("阿司匹林和布洛芬能不能一起吃", db_path=kg_db))
    assert result["ok"] is False
    assert result["capability"] == "ask"
    assert result["reason"]


def test_normalize_compare_covers_declared_shape(kg_db: Path) -> None:
    result = contract.normalize_compare(compare("麻黄", "桂枝", db_path=kg_db))
    _assert_shape(result, contract.CAPABILITIES["compare"].output_shape)
    assert result["ok"] is True
    assert result["differences"]
    assert result["textbook_differentiation"]["text_span"]


def test_normalize_path_covers_declared_shape(kg_db: Path) -> None:
    result = contract.normalize_path(find_path("麻黄", "麻黄汤", max_hops=2, db_path=kg_db))
    _assert_shape(result, contract.CAPABILITIES["path"].output_shape)
    assert result["ok"] is True
    assert result["found"] is True
    assert result["path"]
    assert result["path"][0]["provenance"]["linkable"] is True


def test_normalize_path_missing_route_is_not_a_failure(kg_db: Path) -> None:
    """没路 ≠ 调用失败：ok 仍为 true，found 为 false。"""

    result = contract.normalize_path(find_path("麻黄", "人参", max_hops=1, db_path=kg_db))
    assert result["ok"] is True
    assert result["found"] is False
    assert "教材没写" in result["sentence"] or result["sentence"]


def test_normalize_reverse_covers_declared_shape(kg_db: Path) -> None:
    result = contract.normalize_reverse(reverse_lookup("咽喉肿痛", db_path=kg_db))
    _assert_shape(result, contract.CAPABILITIES["lookup"].output_shape)
    assert result["ok"] is True
    assert result["mode"] == "reverse"


def test_normalize_similar_marks_candidates_unlinkable(kg_db: Path) -> None:
    """T3 相似只给候选，没有 edge_id，不能当证据。"""

    entity, _ = kg_store.search_entities("麻黄", limit=1, db_path=kg_db)[0]
    hits = similar_neighbors(entity.id, limit=3, db_path=kg_db)
    result = contract.normalize_similar(entity.name, hits)
    _assert_shape(result, contract.CAPABILITIES["lookup"].output_shape)
    assert result["similar"]
    assert all(item["provenance"]["linkable"] is False for item in result["similar"])


def test_normalize_finding_renames_quote_to_text_span() -> None:
    """``ReviewFinding.quote`` 是用户处方原文，不是教材原文，必须改名。"""

    finding = ReviewFinding(
        kind="contra",
        severity="block",
        quote="人参、藜芦",
        reason="十八反",
        rule="v1.contra",
        knowledge_base_refs=["教材 p128"],
        edge_ids=[22132],
        evidence=[{"quote": "藜芦反人参", "ref": "教材", "page": 128}],
    )
    payload = contract.normalize_finding(finding)
    assert payload["text_span"] == "人参、藜芦"
    assert "quote" not in payload
    assert payload["provenance"] == {"has_text": True, "linkable": True}


def test_failure_shape_is_uniform() -> None:
    payload = contract.failure("ask", "没认出起点", hint="换个说法")
    assert payload == {"ok": False, "capability": "ask", "reason": "没认出起点", "hint": "换个说法"}
