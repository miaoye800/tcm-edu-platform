import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.llm.clients import ChatMessage
from app.profile.agent import cold_start_profile, export_mermaid, run_cold_start
from app.profile.jury import score_profile
from app.profile.nodes import DialogueExtraction, _dialogue_cards, _fallback_dialogue_extraction, _profile_from_scoring_results
from app.profile.evidence import StructuredEvidence
from app.profile.radar import to_radar
from app.profile.schema import DIMENSION_LABELS, WeakPoint
from app.profile.store import get_profile, upsert_profile
from app.profile.update import ProfileUpdateEvent, update_profile_from_event


def test_cold_start_builds_complete_7_dimension_profile(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    try:
        profile = asyncio.run(
            cold_start_profile(
                user_id="test-cold-start",
                first_message="我是中医学专业学生，基础一般，目标是期末考试，喜欢图表。",
                db_path=db_path,
            )
        )
    finally:
        _cleanup_db(db_path)

    assert len(DIMENSION_LABELS) == 7
    assert all(hasattr(profile, key) for key in DIMENSION_LABELS)
    assert profile.user_id == "test-cold-start"
    assert len(to_radar(profile)["dimensions"]) == 7
    assert len(to_radar(profile)["values"]) == 7


def test_unknown_dialogue_without_model_key_leaves_every_axis_dark() -> None:
    extraction = _fallback_dialogue_extraction("我完全不知道，没接触过中药")
    cards = _dialogue_cards(extraction)
    scoring = asyncio.run(score_profile(cards, settings=Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None)))
    profile = _profile_from_scoring_results(
        user_id="test-unknown-dialogue",
        scoring=scoring,
        evidence_cards=cards,
    )
    radar = to_radar(profile)
    axes = {axis["key"]: axis for axis in radar["axes"]}

    assert extraction.base_level.value == "零基础"
    assert extraction.learning_goal.value is None
    assert extraction.learning_style.value is None
    assert extraction.motivation.value is None
    assert extraction.major.value is None
    assert extraction.resource_preference.value is None
    assert extraction.base_level.evidence == ["没接触"]
    # 无 key 时两条链路都不出分：知识基础有证据也不给分，七轴全 dark。
    assert profile.knowledge_base.score is None
    assert "no_key" in profile.knowledge_base.unknown_reasons
    for key in DIMENSION_LABELS:
        assert axes[key]["lit"] is False
        assert axes[key]["status"] == "dark"


def test_unknown_dialogue_with_jury_only_lights_the_knowledge_axis() -> None:
    extraction = _fallback_dialogue_extraction("我完全不知道，没接触过中药")
    cards = _dialogue_cards(extraction)

    async def llm_call(messages: Sequence[ChatMessage]) -> str:
        first_line = str(messages[1]["content"]).split("\n", 1)[0]
        label = first_line.split("：", 1)[1].strip()
        scores = (8, 20, 12) if label == "知识基础" else (40, 40, 40)
        return json.dumps(
            {
                "dimension": label,
                "dimension_tier": "low",
                "jury": {
                    name: {"score": score, "reasoning_chain": ["逐条比对三档"], "comment": "测试"}
                    for name, score in zip(("严师", "宽师", "中立"), scores)
                },
                "label": None,
                "distribution": {},
                "weak_points": [],
                "evidence_refs": [],
                "narrative": "测试叙述",
                "component_scores": [],
            },
            ensure_ascii=False,
        )

    scoring = asyncio.run(
        score_profile(
            cards,
            settings=Settings(DEEPSEEK_API_KEY="sk-test", ZHIPU_API_KEY=None),
            llm_call=llm_call,
            force_llm=True,
        )
    )
    profile = _profile_from_scoring_results(
        user_id="test-unknown-dialogue-jury",
        scoring=scoring,
        evidence_cards=cards,
    )
    axes = {axis["key"]: axis for axis in to_radar(profile)["axes"]}

    assert profile.knowledge_base.score is not None
    assert profile.knowledge_base.score != 50
    assert axes["knowledge_base"]["status"] != "dark"
    for key in ("learning_goal", "learning_style", "weak_points", "pace", "motivation", "resource_preference"):
        assert axes[key]["lit"] is False
        assert axes[key]["status"] == "dark"


def test_dialogue_extraction_drops_fabricated_evidence_and_null_value() -> None:
    extraction = DialogueExtraction.model_validate(
        {
            "learning_goal": {
                "value": "应试提分",
                "confidence": 0.9,
                "basis": "explicit",
                "evidence": ["我准备期末考试", "编造的原话"],
            },
            "learning_style": {
                "value": "视觉型",
                "confidence": 0.4,
                "basis": "inferred",
                "evidence": ["喜欢图表"],
            },
        }
    )

    from app.profile.nodes import _validate_dialogue_evidence

    validated = _validate_dialogue_evidence(extraction, "user: 我准备期末考试，也喜欢图表")
    cards = _dialogue_cards(validated)

    assert validated.learning_goal.value is None
    assert validated.learning_goal.evidence == []
    assert validated.learning_style.value == "视觉型"
    assert [card.dimension for card in cards] == ["learning_style"]
    assert cards[0].weight == 0.4


def test_dialogue_low_confidence_becomes_low_weight_and_dashed_radar() -> None:
    extraction = DialogueExtraction.model_validate(
        {
            "learning_goal": {
                "value": "应试提分",
                "confidence": 0.2,
                "basis": "inferred",
                "evidence": ["可能要考试"],
            }
        }
    )

    cards = _dialogue_cards(extraction)
    assert cards[0].weight == 0.2

    import app.profile.modes as modes_module

    fusion = modes_module._dialogue_fusion(cards)
    profile = modes_module._profile_from_fusion("low-confidence-user", cards, fusion=fusion)
    axes = {axis["key"]: axis for axis in to_radar(profile)["axes"]}

    assert axes["learning_goal"]["status"] == "dark"
    assert axes["learning_goal"]["scoring_phase"] == "collecting"


def test_dialogue_extraction_does_not_card_unknown_statement() -> None:
    extraction = DialogueExtraction.model_validate(
        {
            "base_level": {
                "value": None,
                "confidence": 0,
                "basis": "explicit",
                "evidence": ["不知道"],
            },
            "learning_goal": {
                "value": None,
                "confidence": 0,
                "basis": "explicit",
                "evidence": ["不知道"],
            },
        }
    )

    assert _dialogue_cards(extraction) == []


def test_pure_wellness_chat_does_not_fabricate_dialogue_profile_scores() -> None:
    extraction = _fallback_dialogue_extraction("最近换季容易犯困，想喝点温水早点睡，先聊聊养生就好。")
    cards = _dialogue_cards(extraction)
    scoring = asyncio.run(score_profile(cards, settings=Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None)))
    profile = _profile_from_scoring_results(
        user_id="test-pure-wellness-dialogue",
        scoring=scoring,
        evidence_cards=cards,
    )
    axes = {axis["key"]: axis for axis in to_radar(profile)["axes"]}

    assert cards == []
    assert all(axis["confidence"] < 0.5 for axis in axes.values())
    assert all(axis["status"] == "dark" for axis in axes.values())


def test_structured_evidence_contract_and_rule_fallback_metadata() -> None:
    contract = StructuredEvidence(
        dimension="learning_goal",
        fact="目标线索",
        signal={"label": "兴趣科普"},
        quote="想先随便了解中药",
        basis="inferred",
        extraction_confidence=0.45,
        source="对话",
        extraction_method="no_key_rule_fallback",
    )
    assert contract.signal["label"] == "兴趣科普"
    cards = _dialogue_cards(_fallback_dialogue_extraction("我不为考试，想先随便了解中药"))
    assert cards
    assert all(card.extraction_method == "no_key_rule_fallback" for card in cards)
    assert all(card.quote and card.quote in "我不为考试，想先随便了解中药" for card in cards)


def test_rule_fallback_handles_mixed_goals_and_negation() -> None:
    extraction = _fallback_dialogue_extraction("不是为了考试，既想做临床实践，也想考研深造")
    assert isinstance(extraction.learning_goal.value, dict)
    assert "应试提分" not in extraction.learning_goal.value
    assert {"临床实用", "学术深造"} <= set(extraction.learning_goal.value)
    assert all(quote in "不是为了考试，既想做临床实践，也想考研深造" for quote in extraction.learning_goal.evidence)


def test_rule_fallback_deduplicates_repeated_self_report_and_ignores_idle_chat() -> None:
    repeated = _fallback_dialogue_extraction("我喜欢看图谱，我喜欢看图谱")
    assert repeated.learning_style.evidence == ["图谱"]
    idle = _fallback_dialogue_extraction("weather is nice, what should I eat for dinner?")
    assert _dialogue_cards(idle) == []


def test_wrong_differentiation_event_adds_weak_point_and_updates_scores(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    try:
        profile = get_profile("test-update", db_path=db_path)
        upsert_profile(profile, db_path=db_path)

        result = asyncio.run(
            update_profile_from_event(
                "test-update",
                ProfileUpdateEvent(
                    type="quiz",
                    payload={
                        "question_id": "q1",
                        "correct": False,
                        "topic": "麻黄-桂枝鉴别",
                        "difficulty": "hard",
                    },
                ),
                db_path=db_path,
                jury_weight_threshold=99,
            )
        )
    finally:
        _cleanup_db(db_path)

    labels = [item.label for item in result.profile.weak_points.items]
    assert "麻黄-桂枝鉴别" in labels
    assert result.profile.knowledge_base.score == 0
    assert result.affected_dimensions == ["knowledge_base", "weak_points"]


def test_profile_graph_routes_with_trace_and_exports_mermaid(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    try:
        state = asyncio.run(
            run_cold_start(
                user_id="test-trace",
                first_message="请根据我的基础和学习目标建立画像，我是中医学专业，喜欢图表。",
                db_path=db_path,
            )
        )
    finally:
        _cleanup_db(db_path)
    nodes = [item["node"] for item in state["trace"]]

    assert nodes[0] == "profile_router"
    assert "dialogue_extract_agent" in nodes
    assert "diagnostic_question_agent" in nodes
    assert "diagnostic_eval_agent" in nodes
    assert "profile_synthesis_agent" in nodes
    assert export_mermaid(tmp_path / "graph_profile.mmd").exists()


def test_profile_persistence_round_trips_weak_points(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    try:
        profile = get_profile("verify_user", db_path=db_path)
        profile.knowledge_base.score = 99
        profile.weak_points.items = [WeakPoint(label="麻黄vs桂枝鉴别", strength=2)]

        upsert_profile(profile, db_path=db_path)
        loaded = get_profile("verify_user", db_path=db_path)
    finally:
        _cleanup_db(db_path)

    assert loaded.knowledge_base.score == 99
    assert loaded.weak_points.items[0].label == "麻黄vs桂枝鉴别"
    assert loaded.weak_points.items[0].strength == 2


def test_profile_dimension_assignment_validates_score_bounds(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    try:
        profile = get_profile("verify_user_score", db_path=db_path)
        with pytest.raises(ValidationError):
            profile.knowledge_base.score = 10086
        with pytest.raises(ValidationError):
            profile.pace.score = -10
    finally:
        _cleanup_db(db_path)


def _test_db_path(tmp_path: Path) -> Path:
    root = tmp_path / ".data"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"profile_test_{uuid4().hex}.db"


def _cleanup_db(db_path: Path) -> None:
    for path in [db_path, db_path.with_suffix(".db-wal"), db_path.with_suffix(".db-shm")]:
        if path.exists():
            path.unlink()
