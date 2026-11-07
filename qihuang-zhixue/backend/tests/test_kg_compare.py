"""两药对比 / 关系路径测试（``app/kg/compare.py`` + ``/api/kg/compare``、``/api/kg/path``）。

对比表**不调模型**：差异是可枚举的，教材本身也有现成的鉴别句。
这里验证的就是「摘要用的是教材原文，而不是模型重新组织的说法」。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.kg import get_kg_db_path
from app.kg import entities as kg_entities
from app.kg import store as kg_store
from app.kg.compare import compare, find_path
from app.kg.schema import ensure_schema
from app.main import app
from tests.helpers import auth_session


def _build_kg_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_kg.py"
    spec = importlib.util.spec_from_file_location("build_kg_for_cmp", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kg_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    build = _build_kg_module().build
    db_path = tmp_path_factory.mktemp("cmp") / "cmp.db"
    ensure_schema(db_path)
    entity_list, alias_list = kg_entities.build_registry(db_path)
    kg_store.upsert_entities(entity_list, db_path)
    kg_store.upsert_aliases(alias_list, db_path)
    build(db_path)
    return db_path


def test_compare_uses_textbook_sentence_as_summary(kg_db: Path) -> None:
    """摘要必须是教材原文，不是模型生成的说法。"""

    result = compare("麻黄", "桂枝", db_path=kg_db)
    assert result["ok"] is True
    textbook = result["textbook_differentiation"]
    assert textbook and "麻黄发汗力强" in textbook["quote"]
    assert result["summary"] == textbook["quote"]


def test_compare_lists_structural_differences(kg_db: Path) -> None:
    result = compare("麻黄", "桂枝", db_path=kg_db)
    by_field = {item["field"]: item for item in result["differences"]}
    assert set(by_field) == {"nature", "flavor", "meridians", "effects", "indications", "dosage", "toxicity"}
    # 功效没有交集，两边各有自己的
    assert "发汗解表" in by_field["effects"]["only_left"]
    assert "发汗解肌" in by_field["effects"]["only_right"]
    assert by_field["meridians"]["shared"]


def test_compare_carries_source_per_side(kg_db: Path) -> None:
    """差异表每一行的值都来自该药的教材条目，所以要能说清「出自哪一条」。

    出处挂在**一侧**而不是逐行重复：同一味药的字段集只有一个来源，
    逐行贴同一句出处只会让表变脏。
    """

    result = compare("麻黄", "桂枝", db_path=kg_db)
    for side in ("left", "right"):
        source = result[side].get("source")
        assert source, f"{side} 缺教材出处"
        assert "中药学" in source["ref"]
        assert source["quote"]


def test_compare_refuses_to_fabricate_for_orphan_herb(kg_db: Path) -> None:
    """草乌没有结构化条目，宁可说「无法对比」，也不要凑一张空表。

    P4 之后 100 味孤儿只剩 14 味，草乌是其中之一——教材只在目录标注
    「附药：草乌」，正文没有独立条目，所以它永远不该有性味功效。
    藜芦原先被当作孤儿例子，但教材有完整条目，现在已转成 approved。
    """

    result = compare("麻黄", "草乌", db_path=kg_db)
    assert result["ok"] is False
    assert "无法" in result["reason"] or "缺" in result["reason"]


def test_compare_surfaces_t1_constraint(kg_db: Path) -> None:
    """对比页必须把「能不能一起用」放在最前面。

    草乌是孤儿（没有性味功效），逐项对比做不了——但十八反不依赖 monograph，
    这条约束必须照样报出来。否则最要命的信息会因为「数据不全」被吞掉。
    """

    result = compare("半夏", "草乌", db_path=kg_db)
    assert result["ok"] is False
    assert "草乌" in result["reason"]
    assert result["constraints"], "孤儿药材不能成为漏掉十八反的理由"
    assert result["constraints"][0]["relation_type"] == "十八反"
    assert result["constraints"][0]["page"] == 128


def test_path_between_herb_and_syndrome(kg_db: Path) -> None:
    result = find_path("麻黄", "风寒表实证", db_path=kg_db)
    assert result["ok"] is True and result["found"] is True
    assert result["hops"] == 1
    assert result["path"][0]["relation_type"] == "药证链"
    assert "风寒表实证" in result["sentence"]


def test_path_reports_honest_miss(kg_db: Path) -> None:
    """找不到路径时要说「教材没写这条关系」，而不是「没关系」。"""

    result = find_path("麻黄", "人参", max_hops=1, db_path=kg_db)
    assert result["ok"] is True
    if not result["found"]:
        assert "教材没写" in result["reason"]


def test_compare_and_path_endpoints(kg_db: Path) -> None:
    app.dependency_overrides[get_kg_db_path] = lambda: kg_db
    try:
        client = TestClient(app)
        session = auth_session(client, "cmp-user", kg_db)

        response = client.get(
            "/api/kg/compare", params={"left": "麻黄", "right": "桂枝"}, headers=session["headers"]
        )
        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True

        response = client.get(
            "/api/kg/path",
            params={"from_id": "麻黄", "to_id": "风寒表实证"},
            headers=session["headers"],
        )
        assert response.status_code == 200, response.text
        assert response.json()["found"] is True
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)
