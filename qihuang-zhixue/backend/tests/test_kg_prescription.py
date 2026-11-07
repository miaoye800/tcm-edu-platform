"""KG 处方审查器测试（``app/kg/prescription.py`` + ``POST /api/kg/prescription/review``）。

重点验证旧实现结构上做不到的三件事：

1. **十八反双向命中**。「藜芦反诸参辛芍」这条规则写在藜芦名下，旧实现只查
   每味药自己的 ``incompatibility.shibafan`` 字段，所以「人参 + 藜芦」能不能
   检出来取决于书写顺序，甚至取决于哪味药有 monograph。
2. **孤儿药材也能查**。藜芦、甘遂、大戟、芫花、巴豆都没有结构化条目，
   但它们在十八反/十九畏/妊娠禁忌里全是主角。
3. **妊娠禁忌分级**。教材 p131 分「禁用」「慎用」两级，旧实现完全没有。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.kg import get_kg_db_path
from app.kg import entities as kg_entities
from app.kg import store as kg_store
from app.kg.prescription import review
from app.kg.schema import ensure_schema
from app.main import app
from tests.helpers import auth_session


def _build_kg_module():
    """按文件路径加载构建脚本，避免把 ``backend/scripts`` 塞进全局导入路径。"""

    path = Path(__file__).resolve().parents[1] / "scripts" / "build_kg.py"
    spec = importlib.util.spec_from_file_location("build_kg_for_rx", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kg_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """建一个**真的**图谱库：实体 + T1/T2 边，直接复用构建脚本。

    不用手搓几条边糊弄测试——证候层能不能命中，取决于 ``药证链`` 到底有没有
    落库，这正好是要验证的东西。
    """

    build = _build_kg_module().build

    db_path = tmp_path_factory.mktemp("rx") / "rx.db"
    ensure_schema(db_path)
    entity_list, alias_list = kg_entities.build_registry(db_path)
    kg_store.upsert_entities(entity_list, db_path)
    kg_store.upsert_aliases(alias_list, db_path)
    build(db_path)
    assert kg_store.stats(db_path)["edges_total"] > 0
    return db_path


def test_shibafan_detected_regardless_of_writing_order(kg_db: Path) -> None:
    """人参 + 藜芦：两个方向都必须 block，且指到 p128 那条边。"""

    for prescription in ("人参9g，藜芦3g", "藜芦3g，人参9g"):
        result = review({"case": "气虚乏力", "prescription": prescription}, kg_db)
        assert result.verdict == "block", prescription
        conflicts = [item for item in result.findings if item.kind == "incompatible_pair"]
        assert conflicts, prescription
        pair = conflicts[0]
        assert {pair.reason.count("人参"), pair.reason.count("藜芦")} == {1, 1}
        assert pair.rule.startswith("kg.rx.v1.t1.")
        assert pair.evidence and pair.evidence[0]["page"] == 128


def test_orphan_herbs_are_checked_for_t1_conflicts(kg_db: Path) -> None:
    """草乌没有结构化条目，但十八反必须查得出来。

    P4 之后 100 味孤儿只剩 14 味，草乌是其中之一——教材只在目录标注
    「附药：草乌」，正文无独立条目。但它仍是十八反的主角（乌头反半夏），
    所以 T1 检查不能因为「没有 monograph」就跳过它。
    """

    result = review({"case": "风寒湿痹", "prescription": "半夏9g，草乌3g"}, kg_db)
    assert result.verdict == "block"
    kinds = {item.kind for item in result.findings}
    assert "incompatible_pair" in kinds
    # 认不出属性的药必须显式说「没校验」，不能看起来像校验通过
    assert "herb_not_structured" in kinds


def test_unstructured_herb_reports_unknown_not_ok(kg_db: Path) -> None:
    """草乌没有条目 → 剂量/毒性必须是 ``unknown``，不能是「合规」。

    这是布尔值表达不了的一态。旧版 ``dosage_ok=True`` 让前端把草乌30g
    画成绿色的「剂量 合规」「毒性 未发现」——一味大毒药，界面告诉学生
    「没问题」。同一张卡片上写着「尚无结构化条目」，自相矛盾。
    """

    result = review({"prescription": "草乌30g"}, kg_db)

    assert result.checks["dosage"] == "unknown"
    assert result.checks["toxicity"] == "unknown"
    # 配伍走 T1 硬约束表，孤儿药也查得到，所以这一项可以是 pass
    assert result.checks["compatible"] == "pass"
    assert result.verdict == "warn"


def test_structured_herb_still_reports_pass(kg_db: Path) -> None:
    """有教材数据的药不能被「未知」污染，该 pass 就是 pass。"""

    result = review({"prescription": "金银花15g，连翘15g"}, kg_db)

    assert result.checks["dosage"] == "pass"
    assert result.checks["toxicity"] == "pass"
    assert result.checks["compatible"] == "pass"


def test_herb_name_substring_does_not_create_phantom_herb(kg_db: Path) -> None:
    """「牵牛子」不该同时匹配出「牵牛」，否则会凭空多出一味药。"""

    result = review({"case": "积滞", "prescription": "巴豆3g，牵牛子6g"}, kg_db)
    names = [line.name for line in result.herbs]
    assert names.count("牵牛子") == 1
    assert "牵牛" not in names


def test_pregnancy_forbidden_and_caution_are_separated(kg_db: Path) -> None:
    """教材 p131 分两级：巴豆禁用（block），桃仁慎用（warn）。"""

    result = review({"case": "妊娠恶阻", "prescription": "巴豆3g，桃仁9g"}, kg_db)
    by_kind = {item.kind: item for item in result.findings}
    assert by_kind["pregnancy_forbidden"].severity == "block"
    assert by_kind["pregnancy_forbidden"].evidence[0]["page"] == 131
    assert by_kind["pregnancy_caution"].severity == "warn"
    assert result.verdict == "block"


def test_pregnancy_check_is_silent_without_pregnancy_context(kg_db: Path) -> None:
    """没提妊娠就不该报「孕妇禁用」——那是噪音。"""

    result = review({"case": "积滞", "prescription": "巴豆3g"}, kg_db)
    assert not any(item.kind.startswith("pregnancy") for item in result.findings)


def test_syndrome_layer_cites_textbook_edge(kg_db: Path) -> None:
    """麻黄治风寒表实证：必须走证候层，并带回教材原文。

    证名断言放宽到「含『风寒』」而不是写死「风寒表实证」：P4 把教材证名
    （风寒感冒）补进图谱之后，命中的是**教材原文里的证名**，比人工归纳的
    「风寒表实证」更可溯源。写死某个证名会让每次证候主数据重建都要改测试。
    """

    result = review({"case": "风寒恶寒无汗", "prescription": "麻黄6g，桂枝6g"}, kg_db)
    assert result.symptom_match is True
    assert result.trace["layer"] == "syndrome"
    assert result.trace["edges"], "证候层命中必须给出具体边"
    assert any("风寒" in item["syndrome"] for item in result.trace["edges"])
    # 边必须带教材原文——这是「可溯源」的底线
    assert all(item["quote"] for item in result.trace["edges"])


def test_coverage_layer_is_always_disclosed(kg_db: Path) -> None:
    """对症命中必须标明走的是哪一层，且给出可核对的依据。

    金银花在教材里对「热毒血痢」有明确主治，而「热毒咽痛」的候选里含「热毒」，
    所以走的是证候层而不是弱一些的功效层。**层名必须如实反映这一点**——
    用户要能看见系统凭的是哪条教材关系，而不是只得到一个「对症」的结论。
    """

    result = review({"case": "热毒咽痛", "prescription": "金银花12g，连翘12g"}, kg_db)
    assert result.symptom_match is True
    assert result.trace["layer"] in {"syndrome", "effect_overlap", "effect_tag"}
    if result.trace["layer"] == "syndrome":
        # 证候层必须交代清楚是哪个证、哪条边
        assert result.trace["edges"]
        assert result.trace["edges"][0]["quote"]


def test_alternatives_point_at_the_textbook_correct_herb(kg_db: Path) -> None:
    """即使用户开的药「间接对症」，也要告诉他教材里真正主治该证的是哪味。

    咽喉肿痛在教材里的主治药是射干（"热毒痰火壅结咽喉，咽痛肿胀"）。
    金银花/连翘是靠清热解毒间接覆盖，这是图谱比关键词匹配多出来的东西。

    注意这里必须用「咽喉肿痛」而不是「热毒咽痛」：后者和证候名之间差一个
    **子序列**关系（咽-喉-肿-痛 vs 咽-痛），字面层怎么都够不到，
    那属于 L4 闭集映射要解决的问题，不该在这里假装字面能覆盖。
    """

    result = review({"case": "咽喉肿痛，发热", "prescription": "金银花12g，连翘12g"}, kg_db)
    alternatives = {
        item["syndrome"]: item["herbs"]
        for item in result.trace.get("syndrome_alternatives") or []
    }
    assert "射干" in alternatives.get("咽喉肿痛", [])


def test_effect_overlap_layer_needs_no_handwritten_terms(kg_db: Path) -> None:
    """「嗓子疼还有点头疼发烧」里没有任何手写关键词，仍要判为对症。

    走的路径是：口语别名 → 发热咽痛 → 教材主治药板蓝根（清热解毒）→
    处方里的金银花/连翘也是清热解毒 → 共享同一条功效项。
    整条链路没有一个手写映射。
    """

    result = review(
        {"case": "嗓子疼，还有点头疼发烧", "prescription": "金银花12g，连翘12g"}, kg_db
    )
    assert result.symptom_match is True
    assert result.trace["layer"] == "effect_overlap"
    assert result.trace["matched_herbs"]
    assert "清热解毒" in result.trace["shared_effects"]["金银花"]


def test_review_endpoint_returns_traceable_findings(kg_db: Path) -> None:
    app.dependency_overrides[get_kg_db_path] = lambda: kg_db
    try:
        client = TestClient(app)
        session = auth_session(client, "rx-user", kg_db)
        response = client.post(
            "/api/kg/prescription/review",
            json={"case": "气虚乏力", "prescription": "人参9g，藜芦3g"},
            headers=session["headers"],
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["verdict"] == "block"
        assert payload["blocking"]
        assert payload["blocking"][0]["kind"] == "incompatible_pair"
        assert payload["blocking"][0]["evidence"][0]["quote"]
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)


def test_every_block_finding_carries_evidence(kg_db: Path) -> None:
    """block 级结论**必须**带教材原文。这是「所有断言可溯源」红线的直接落地。

    以前 ``toxic_misuse`` 只有一句 ``reason``，没有任何原文——用户没法核对
    「3g 到底超没超」。现在剂量类结论引【用法用量】，毒性类结论再补【使用注意】。
    """

    result = review({"prescription": "人参9g，藜芦3g"}, kg_db)
    blocking = [item for item in result.findings if item.severity == "block"]
    assert blocking
    for item in blocking:
        assert item.evidence, f"{item.kind} 是 block 却没有 evidence"
        assert item.evidence[0]["quote"], f"{item.kind} 的 evidence 没有原文"


def test_herbs_carry_dose_range_and_status(kg_db: Path) -> None:
    """端点要给出「写了多少 / 教材允许多少」，前端才能画出对照。

    只回一句「超出范围」而不给区间，等于让用户自己去翻书。
    """

    result = review({"prescription": "人参9g，藜芦3g"}, kg_db)
    by_name = {line.name: line for line in result.herbs}
    assert by_name["人参"].dose_status == "ok"
    assert by_name["人参"].dose_range == [3.0, 9.0]
    assert by_name["藜芦"].dose_status == "above"
    assert by_name["藜芦"].dose_range == [0.3, 0.6]


def test_unknown_toxicity_neither_blocks_nor_stays_silent() -> None:
    """``Toxicity.UNKNOWN`` 不能被判成有毒。

    教材没写毒性 ≠ 有毒，也 ≠ 无毒。判成有毒会凭空 block 一张方子；
    判成无毒则是把「没查到」说成「安全」。正确做法是单独报一条 warn。
    """

    from app.kb.ingest import load_herbs
    from app.kb.models import Toxicity
    from app.kg.prescription import _is_toxic, _toxicity_unknown

    herb = load_herbs()[0].model_copy(update={"toxicity": Toxicity.UNKNOWN})
    assert _is_toxic(herb) is False
    assert _toxicity_unknown(herb) is True
    assert _is_toxic(herb.model_copy(update={"toxicity": Toxicity.TOXIC})) is True
    assert _is_toxic(herb.model_copy(update={"toxicity": Toxicity.NONE})) is False


def test_no_case_text_does_not_emit_case_noise(kg_db: Path) -> None:
    """只查配伍禁忌、没写病例时，不该冒出一条空 quote 的「证候无法映射」。"""

    result = review({"prescription": "人参9g，藜芦3g"}, kg_db)
    assert not any(item.kind == "case_unresolved" for item in result.findings)
