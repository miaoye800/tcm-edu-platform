"""Contract tests for the unified scoring system.

Every assertion here is a number the plan predicted by hand.  If one of them
drifts, the two collection flows have started to disagree again -- which is the
whole reason this file exists.

Assertion numbers in the docstrings map to the plan's acceptance list.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.profile.anchors import resolve_band
from app.profile.component_model import (
    COMPONENTS,
    COMPONENT_RUBRICS,
    COMPONENT_WEIGHTS,
    COVERAGE_THRESHOLD,
    DIMENSION_COMPONENT_LABELS,
    AtomicEvidence,
    QUALITY_FACTOR_GUIDE,
    QUALITY_FACTOR_TIERS,
    QualityFactors,
    apply_dimension_scores,
    clamp_to_band,
    confidence_from_evidence,
    dimension_score,
    infer_combination_type,
    quality_tier_of,
    score_interval,
)
from app.profile.fusion import fuse_dimension
from app.profile.jury import _parse_jury_output, build_jury_messages
from app.profile.modes import _dialogue_fusion
from app.profile.schema import DIMENSION_LABELS, EvidenceCard, EvidenceTier

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _card(
    dimension: str,
    component: str | None,
    signal: dict,
    *,
    weight: float = 1.0,
    status: str = "scorable",
    quality: dict | None = None,
    group: str | None = None,
) -> EvidenceCard:
    return EvidenceCard(
        evidence_id=f"ev-{component or 'none'}-{abs(hash((dimension, component, tuple(signal.items())))) % 10000}",
        dimension=dimension,
        source="测试",
        tier=EvidenceTier.SELF_REPORT,
        fact="测试事实",
        objective_signal=dict(signal),
        weight=weight,
        quote="用户原话",
        component=component,
        collection_status=status,
        evidence_group_id=group,
        quality_factors=quality or {},
        created_at=datetime(2026, 7, 2, tzinfo=UTC),
    )


def _atomic(
    evidence_id: str,
    group: str,
    q: float,
    *,
    dimension: str = "knowledge_base",
    component: str = "concept_foundation",
) -> AtomicEvidence:
    factors = QualityFactors(
        source_reliability=q,
        expression_clarity=q,
        content_specificity=q,
        freshness=q,
        consistency=q,
    )
    return AtomicEvidence(
        evidence_id=evidence_id,
        dimension=dimension,
        component=component,
        evidence_group_id=group,
        quote="原话",
        normalized_claim="提炼句",
        evidence_type="self_report",
        collection_status="scorable",
        quality_factors=factors,
    )


def _jury_payload(component_scores: list[dict], *, tier: str = "medium") -> str:
    return json.dumps(
        {
            "dimension": "知识基础",
            "dimension_tier": tier,
            "jury": {
                "严师": {"score": 50, "reasoning_chain": ["严"], "comment": "严"},
                "宽师": {"score": 70, "reasoning_chain": ["宽"], "comment": "宽"},
                "中立": {"score": 60, "reasoning_chain": ["中"], "comment": "中"},
            },
            "label": None,
            "distribution": {},
            "weak_points": [],
            "evidence_refs": [],
            "narrative": "契约测试",
            "component_scores": component_scores,
        },
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------
# 1-7: the single dimension formula
# --------------------------------------------------------------------------


def test_1_clamp_to_band_pulls_into_the_chosen_tier() -> None:
    assert clamp_to_band("low", 88) == 40.0
    assert clamp_to_band("high", 12) == 70.0
    assert clamp_to_band("medium", 55) == 55.0


def test_2_full_coverage_equals_the_weighted_mean() -> None:
    components = COMPONENTS["knowledge_base"]
    scores = {key: float(value) for key, value in zip(components, (60, 70, 80, 90, 50))}

    outcome = dimension_score("knowledge_base", scores)

    weights = COMPONENT_WEIGHTS["knowledge_base"]
    expected = sum(scores[key] * weights[key] for key in components)
    assert outcome["coverage"] == 1.0
    assert outcome["weighted_mean"] == round(expected, 2)
    assert outcome["score"] == round(expected, 2)


def test_3_normalisation_is_applied_to_three_of_five_components() -> None:
    """55/22.33/52 on weights .25/.20/.15 must not collapse to a plain sum."""

    outcome = dimension_score(
        "knowledge_base",
        {"concept_foundation": 55, "knowledge_scope": 22.33, "measured_performance": 52},
    )

    assert outcome["coverage"] == 0.6
    assert outcome["weight_sum"] == 0.6
    assert outcome["weighted_mean"] == 43.36
    assert outcome["score"] == 46.02
    # The un-normalised sum would have produced 35.61; that value must never
    # come back.
    assert outcome["score"] != 35.61


def test_4_both_flows_produce_the_same_dimension_score() -> None:
    scores = {"concept_foundation": 55, "knowledge_scope": 22.33, "measured_performance": 52}
    cards = [
        _card("knowledge_base", component, {"component_score": score})
        for component, score in scores.items()
    ]

    via_fusion = fuse_dimension("knowledge_base", cards)
    via_dialogue = _dialogue_fusion(cards)["knowledge_base"]
    via_component_model = apply_dimension_scores(
        [_atomic(f"a{i}", f"g{i}", 0.8, component=component) for i, component in enumerate(scores)],
        _score_output(scores),
        dimension="knowledge_base",
    )

    assert via_fusion.score == 46.02
    assert via_dialogue.score == 46.02
    assert via_component_model.dimension_score == 46.02
    assert via_dialogue.coverage == via_fusion.coverage


def _score_output(scores: dict[str, float]):
    from app.profile.component_model import ComponentScore, DimensionScoreOutput

    return DimensionScoreOutput(
        component_scores=[
            ComponentScore(component=key, tier="medium", score=value, reason="测试")
            for key, value in scores.items()
        ]
    )


def test_5_the_gate_reads_coverage_not_collected() -> None:
    five = {"concept_foundation": 70, "knowledge_scope": 70, "retrieval_application": 70, "boundary_awareness": 70, "measured_performance": 70}
    two = {"concept_foundation": 70, "knowledge_scope": 70}
    four = {**two, "retrieval_application": 70, "boundary_awareness": 70}

    assert dimension_score("knowledge_base", five)["score"] is not None
    assert dimension_score("knowledge_base", two)["coverage"] == 0.4
    assert dimension_score("knowledge_base", two)["score"] is None
    assert dimension_score("knowledge_base", four)["coverage"] == 0.8
    assert dimension_score("knowledge_base", four)["score"] is not None
    assert COVERAGE_THRESHOLD == 0.6


def test_6_interval_is_clamped_to_the_public_band() -> None:
    # half = 25 * (0.6 * (1 - 0.4) + 0.4 * (1 - 0.15)) = 17.5
    low, high = score_interval(90, 0.4, 0.15)

    assert low == 72.5
    assert high == 95.0


def test_7_the_three_entry_points_agree_exactly() -> None:
    scores = {"plan_clarity": 80, "time_feasibility": 60, "actual_execution": 70}
    cards = [_card("pace", component, {"component_score": score}) for component, score in scores.items()]

    fusion = fuse_dimension("pace", cards)
    dialogue = _dialogue_fusion(cards)["pace"]
    model = apply_dimension_scores(
        [_atomic(f"p{i}", f"pg{i}", 0.8, dimension="pace", component=component) for i, component in enumerate(scores)],
        _score_output(scores),
        dimension="pace",
    )

    assert fusion.score == dialogue.score == model.dimension_score
    assert round(fusion.coverage, 4) == round(dialogue.coverage, 4) == round(model.coverage, 4)


# --------------------------------------------------------------------------
# 8-12: confidence band and anchor intersection
# --------------------------------------------------------------------------


def test_8_combination_type_follows_countable_facts() -> None:
    assert infer_combination_type(group_count=1, is_clear=True) == "single_clear_self_report"
    assert infer_combination_type(group_count=2, is_clear=True, consistent=True) == "multi_turn_clear_consistent"
    assert infer_combination_type(group_count=1, is_clear=False) == "single_vague_self_report"
    assert (
        infer_combination_type(group_count=1, is_clear=True, has_long_term_measurement=True)
        == "dialogue_behavior_measurement_long_term"
    )


def test_9_confidence_ceiling_is_chosen_by_combination_type() -> None:
    evidence = [_atomic("e1", "g1", 0.5743), _atomic("e2", "g2", 0.5743)]

    consistent = confidence_from_evidence(evidence, "multi_turn_clear_consistent")
    measured = confidence_from_evidence(evidence, "dialogue_behavior_measurement_long_term")

    assert consistent["confidence"] == 0.6383
    assert measured["confidence"] == 0.9228
    assert consistent["interval"] == {"lower": 0.45, "upper": 0.68}


def test_10_teacher_mean_becomes_the_component_score() -> None:
    cards = [_card("knowledge_base", "concept_foundation", {})]

    result = _parse_jury_output(
        "knowledge_base",
        cards,
        _jury_payload([{"component": "concept_foundation", "tier": "medium", "score": 70, "reason": "均值"}]),
    )

    assert result.jury_mean == 60.0
    assert result.component_scores == {"concept_foundation": 70.0}


def test_11_component_scores_are_clamped_to_their_own_tier() -> None:
    cards = [_card("knowledge_base", "concept_foundation", {})]

    low_tier = _parse_jury_output(
        "knowledge_base",
        cards,
        _jury_payload([{"component": "concept_foundation", "tier": "medium", "score": 30, "reason": "低于中档"}]),
    )
    high_tier = _parse_jury_output(
        "knowledge_base",
        cards,
        _jury_payload([{"component": "concept_foundation", "tier": "high", "score": 30, "reason": "档内过低"}]),
    )

    assert low_tier.component_scores == {"concept_foundation": 40.0}
    assert high_tier.component_scores == {"concept_foundation": 70.0}


def test_12_anchor_interval_and_tier_band_intersect() -> None:
    card = _card("pace", "time_feasibility", {"daily_minutes": 30})

    tier_high, reasons_high = resolve_band("high", "pace", [card])
    tier_medium, reasons_medium = resolve_band("medium", "pace", [card])

    assert tier_high == (70.0, 95.0)
    assert reasons_high == ["anchor_tier_conflict"]
    # 30 分钟/天的客观锚点只允许 40-65，中档 40-70 与之求交后仍是 40-65。
    assert tier_medium == (40.0, 65.0)
    assert reasons_medium == []


# --------------------------------------------------------------------------
# 13-15: the prompt never hands the model a number it could copy
# --------------------------------------------------------------------------


def _prompt_text(dimension: str = "knowledge_base") -> str:
    cards = [
        _card(dimension, COMPONENTS[dimension][0], {"topic": "解表药", "correct": True, "response_speed": "快"}),
    ]
    return "\n".join(str(message["content"]) for message in build_jury_messages(dimension, cards))


def test_13_prompt_contains_no_hardcoded_scores() -> None:
    text = _prompt_text()

    for banned in ('"score": 62', '"score": 72', '"score": 66', "aggregated_score", "variance", "answer_quality"):
        assert banned not in text


def test_14_prompt_only_exposes_whitelisted_card_fields() -> None:
    text = _prompt_text()

    for banned in ("weight", "difficulty", "response_time_ms"):
        assert f'"{banned}"' not in text
    assert "response_speed" in text


def test_15_dimension_scale_is_written_as_three_aligned_bands() -> None:
    text = _prompt_text()

    assert "低档（5–40）" in text
    assert "中档（40–70）" in text
    assert "高档（70–95）" in text
    assert "0-20 药名认不全" not in text


# --------------------------------------------------------------------------
# 27-35: robustness of the contract
# --------------------------------------------------------------------------


def test_27_structured_assessment_no_longer_crashes() -> None:
    item = AtomicEvidence(
        evidence_id="x",
        dimension="knowledge_base",
        component="concept_foundation",
        evidence_group_id="g",
        quote="原话",
        normalized_claim="提炼句",
        evidence_type="self_report",
        assessment={"reason": "结构化理由"},
        quality_factors=QualityFactors(
            source_reliability=0.9,
            expression_clarity=0.9,
            content_specificity=0.9,
            freshness=1.0,
            consistency=0.9,
        ),
    )

    assert item.assessment_text() == "结构化理由"


def test_30_all_thirty_five_rubrics_are_distinct() -> None:
    total = 0
    for dimension, components in COMPONENTS.items():
        triples = set()
        for component in components:
            rubric = COMPONENT_RUBRICS[dimension][component]
            triples.add((rubric["low"], rubric["medium"], rubric["high"]))
            # A component must not borrow another component's label in its body.
            for other in components:
                if other == component:
                    continue
                label = DIMENSION_COMPONENT_LABELS[dimension][other]
                body = f"{rubric['definition']}{rubric['low']}{rubric['medium']}{rubric['high']}"
                assert label not in body, f"{dimension}.{component} 正文里出现了 {label}"
            total += 1
        assert len(triples) == len(components), f"{dimension} 的三档描述有重复"
    assert total == 35


def test_31_prompt_blocks_each_component_once() -> None:
    text = _prompt_text()

    for component in COMPONENTS["knowledge_base"]:
        assert DIMENSION_COMPONENT_LABELS["knowledge_base"][component] in text
    # The 实测表现 rubric must not leak into the 概念基础 block.
    concept = COMPONENT_RUBRICS["knowledge_base"]["concept_foundation"]["medium"]
    measured = COMPONENT_RUBRICS["knowledge_base"]["measured_performance"]["medium"]
    assert text.count(concept) == 1
    assert text.count(measured) == 1
    assert measured not in text.split("概念基础")[1].split("知识范围")[0]


def test_32_unscored_components_stay_null_and_block_the_score() -> None:
    cards = [
        _card("knowledge_base", "concept_foundation", {}),
        _card("knowledge_base", "knowledge_scope", {}),
    ]
    payload = _jury_payload(
        [
            {"component": "concept_foundation", "tier": "medium", "score": 60, "reason": "有证据"},
            {"component": "knowledge_scope", "tier": "medium", "score": 50, "reason": "有证据"},
            {"component": "retrieval_application", "tier": "medium", "score": None, "reason": "缺证据"},
            {"component": "boundary_awareness", "tier": "medium", "score": None, "reason": "缺证据"},
            {"component": "measured_performance", "tier": "medium", "score": None, "reason": "缺证据"},
        ]
    )

    result = _parse_jury_output("knowledge_base", cards, payload)

    assert len(result.component_scores) == 2
    assert result.coverage == 0.4
    assert result.aggregated_score is None


def test_33_component_tiers_do_not_constrain_each_other() -> None:
    cards = [
        _card("knowledge_base", "concept_foundation", {}),
        _card("knowledge_base", "measured_performance", {}),
    ]
    payload = _jury_payload(
        [
            {"component": "concept_foundation", "tier": "high", "score": 88, "reason": "概念扎实"},
            {"component": "measured_performance", "tier": "low", "score": 12, "reason": "实测很差"},
        ]
    )

    result = _parse_jury_output("knowledge_base", cards, payload)

    assert 70 <= result.component_scores["concept_foundation"] <= 95
    assert 5 <= result.component_scores["measured_performance"] <= 40


def test_34_coverage_counts_scored_components_not_collected_ones() -> None:
    cards = [
        _card("knowledge_base", "concept_foundation", {}, status="scorable"),
        _card("knowledge_base", "knowledge_scope", {}, status="scorable"),
        _card("knowledge_base", "retrieval_application", {}, status="scorable"),
    ]
    payload = _jury_payload(
        [
            {"component": "concept_foundation", "tier": "medium", "score": 60, "reason": "有证据"},
            {"component": "knowledge_scope", "tier": "medium", "score": None, "reason": "模型未判"},
            {"component": "retrieval_application", "tier": "medium", "score": None, "reason": "模型未判"},
        ]
    )

    result = _parse_jury_output("knowledge_base", cards, payload)

    # Three scorable cards, but only one score: coverage is 1/5, not 3/5.
    assert result.coverage == 0.2
    assert result.aggregated_score is None


def test_35_shrink_pulls_three_of_five_high_components_toward_the_middle() -> None:
    outcome = dimension_score(
        "motivation",
        {"intrinsic_value": 95, "external_commitment": 95, "actual_investment": 95},
    )

    assert outcome["coverage"] == 0.6
    assert outcome["weighted_mean"] == 95.0
    assert outcome["score"] == 77.0
    assert outcome["score"] != 95.0


def test_16_story_rule_cards_satisfy_the_evidence_contract() -> None:
    from app.profile.modes import _learning_rule_cards, _quiz_cards

    questions = [{"id": "q1", "answer": "补气升阳", "topic": "黄芪功效"}]
    cards = _quiz_cards(
        {"answers": [{"question_id": "q1", "selected": "补气升阳", "response_time_ms": 3000}]},
        questions,
        "一幕·药圃初认",
        3000,
    )
    cards += _learning_rule_cards({"daily_minutes": 30, "target_days": 45, "response_time_ms": 9000}, "四幕·立下学规", 9000)

    assert cards
    for card in cards:
        assert card.quote, "每张卡都必须带用户原话"
        assert card.evidence_group_id, "来源组不能为空，否则置信度无法分组"
        assert card.component in COMPONENTS[card.dimension]
        assert set(card.quality_factors) >= {
            "source_reliability",
            "expression_clarity",
            "content_specificity",
            "freshness",
            "consistency",
        }
        assert card.collection_status == "scorable"


def test_23_speed_is_bucketed_per_question_type() -> None:
    from app.profile.modes import _response_speed

    assert _response_speed(3_000, "选择") == "快"
    assert _response_speed(9_000, "选择") == "正常"
    assert _response_speed(20_000, "选择") == "慢"
    # 18 秒对自述题是正常，用选择题基准会误判成"慢"。
    assert _response_speed(18_000, "自述") == "正常"
    assert _response_speed(70_000, "自述") == "慢"
    assert _response_speed(4_000, "数字") == "快"


def test_24_cards_never_store_raw_milliseconds() -> None:
    from app.profile.modes import _learning_rule_cards

    cards = _learning_rule_cards({"daily_minutes": 30, "target_days": 45, "response_time_ms": 9000}, "四幕·立下学规", 9000)

    for card in cards:
        assert "response_time_ms" not in card.objective_signal
        assert card.objective_signal["response_speed"] in {"快", "正常", "慢"}


def test_28_dialogue_prompt_no_longer_forces_verbatim_reuse() -> None:
    from app.profile.modes import _dialogue_companion_messages

    messages = _dialogue_companion_messages(
        history_messages=[],
        utterance="我想考研，但记不住药性",
        target="knowledge_base",
        evidence_cards=[],
        fusion=_dialogue_fusion([]),
        current_confidences={},
        component_focus={"component": "concept_foundation", "label": "概念基础", "reason": "缺少自述"},
        component_question="你平时怎么记药性？",
    )
    text = "\n".join(str(message["content"]) for message in messages)

    assert "逐字放在回答末尾" not in text
    assert "不许照抄" in text
    assert "must_cover" in text


def test_29_sanitize_dialogue_reply_applies_four_local_checks() -> None:
    from app.profile.modes import sanitize_dialogue_reply

    fallback = "你平时每天能挤出多少时间？"
    assert sanitize_dialogue_reply("你好呀？今天想学点什么？要不要聊聊？") == "今天想学点什么？"
    assert "关于实际投入" not in sanitize_dialogue_reply(
        "关于实际投入这块我记下了。你觉得每天能学多久？", fallback_question=fallback
    )
    assert sanitize_dialogue_reply("嗯嗯", fallback_question=fallback) == f"嗯嗯{fallback}"
    clean = "黄芪确实常用。你平时记药性更靠图还是靠口诀？"
    assert sanitize_dialogue_reply(clean, fallback_question=fallback) == clean


def test_17_story_five_acts_collect_at_least_three_components_per_dimension() -> None:
    """剧情五幕跑完，每个维度都要够 60% 覆盖度所需的 3 个组件。"""

    import asyncio

    from app.profile.modes import (
        _card,
        _identity_cards,
        _learning_rule_cards,
        _prescription_card,
        _quiz_cards,
        _resource_card,
        _story_extra_cards,
        _vary_herb_questions,
        load_diagnostic_questions,
    )

    cards: list[EvidenceCard] = []
    cards += _identity_cards({"identity_card": "临床学徒"}, source="序幕·叩问初心", response_time_ms=6000, prior_weight=0.3)
    cards += _story_extra_cards(
        "apprentice_prologue",
        {"story_answers": {"extra_goal_scene": "想跟门诊抄方，明年春天上手", "extra_goal_priority": "配伍剂量"}},
        source="序幕·叩问初心",
    )
    # 识药就是五味有图的药，题型轮换（功效 / 性味 / 使用注意 / 相似药）。
    quiz = _vary_herb_questions(load_diagnostic_questions(limit=5, photo_ready_only=True))
    assert len(quiz) == 5, "识药题固定五味药，不再追加派生题"
    assert {question.get("question_type") for question in quiz if question.get("question_type")} >= {
        "nature_flavor", "caution", "similar_herb",
    }, "五题要覆盖性味 / 使用注意 / 相似药，不能只考功效"
    answers = []
    for index, question in enumerate(quiz):
        picked = question["answer"] if index < 2 else question["options"][0]
        if picked == question["answer"]:
            picked = question["options"][1]
        answers.append({"question_id": question["id"], "selected": picked, "response_time_ms": 5000})
    answers[0]["free_text"] = "补气药都差不多，具体记不清"
    cards += _quiz_cards({"answers": answers}, quiz, source="一幕·药圃初认", response_time_ms=5000)
    cards += [
        asyncio.run(
            _prescription_card(
                {"case": "热毒咽痛", "prescription": "金银花15g，连翘12g"},
                source="二幕·初次抓药",
                weight=1.5,
                response_time_ms=7000,
            )
        )
    ]
    cards += [
        _card(
            "learning_style",
            "二幕·初次抓药",
            "偏好的辨证法门：按证型推",
            {"preference_note": "我更喜欢按证型推", "quote": "我更喜欢按证型推", "basis": "explicit", "confidence": 0.8},
            1.0,
            component="strategy_execution",
            answer_kind="self_report",
        )
    ]
    cards += [_resource_card({"resource_type": "医案文选"}, source="三幕·师父药宝", response_time_ms=4000)]
    cards += _story_extra_cards(
        "apprentice_act3_resource",
        {
            "story_answers": {
                "extra_style_memory": "背口诀",
                "extra_style_compare": "知识图谱",
                "extra_resource_reason": "病例更接近真实门诊",
                "extra_resource_constraint": "手机",
            }
        },
        source="三幕·师父药宝",
    )
    cards += _learning_rule_cards(
        {"daily_minutes": 30, "target_days": 45, "accept_challenge": False},
        source="四幕·立下学规",
        response_time_ms=9000,
    )
    cards += _story_extra_cards(
        "apprentice_act4_rule",
        {
            "story_answers": {
                "extra_pace_window": "晚上",
                "extra_pace_recover": "我会从上次停下的那味药接着看",
                "extra_motivation_conflict": "先把考试顶过去，学习往后挪两天",
                "extra_weak_recover": "我会把记错的药抄一遍再找相似药对比",
            }
        },
        source="四幕·立下学规",
    )

    for dimension, components in COMPONENTS.items():
        collected = {
            card.component for card in cards
            if card.dimension == dimension and card.collection_status == "scorable"
        }
        assert len(collected) >= 3, f"{dimension} 只收集到 {len(collected)} 个组件：{sorted(collected)}"
        assert collected <= set(components)


def test_18_choice_answer_quote_is_traceable() -> None:
    from app.profile.component_model import quote_is_traceable
    from app.profile.modes import _quiz_cards

    question = {"id": "q1", "answer": "补气升阳", "topic": "黄芪功效", "options": ["补气升阳", "清热解毒"]}
    cards = _quiz_cards(
        {"answers": [{"question_id": "q1", "selected": "补气升阳", "response_time_ms": 3000}]},
        [question],
        "一幕·药圃初认",
        3000,
    )
    transcript = "一幕·药圃初认 黄芪 补气升阳"

    assert quote_is_traceable(cards[0].quote or "", transcript)


def test_dimension_score_rejects_unknown_components() -> None:
    outcome = dimension_score("pace", {"not_a_component": 80, "plan_clarity": 60})

    assert outcome["coverage"] == 0.2
    assert "not_a_component" not in outcome["component_scores"]
    assert outcome["score"] is None


@pytest.mark.parametrize("dimension", list(DIMENSION_LABELS))
def test_every_dimension_has_five_components_with_weights_summing_to_one(dimension: str) -> None:
    components = COMPONENTS[dimension]
    weights = COMPONENT_WEIGHTS[dimension]

    assert len(components) == 5
    assert round(sum(weights.values()), 6) == 1.0


# --------------------------------------------------------------------------
# 对话收敛（计划 §5.5.3-5）
# --------------------------------------------------------------------------


def _component_card(dimension: str, component: str) -> EvidenceCard:
    return EvidenceCard(
        dimension=dimension,  # type: ignore[arg-type]
        source="对话第 1 轮",
        fact="test",
        objective_signal={"quote": "test"},
        weight=1.0,
        timestamp=datetime.now(UTC),
        component=component,
        collection_status="scorable",
    )


def test_dialogue_coverage_counts_collected_components_only() -> None:
    from app.profile.modes import _dialogue_coverage

    cards = [
        _component_card("learning_goal", "expression_certainty"),
        _component_card("learning_goal", "goal_specificity"),
        # Same component twice must not inflate coverage.
        _component_card("learning_goal", "goal_specificity"),
        # A component-less card cannot reach the scoring path, so it counts for nothing.
        EvidenceCard(dimension="pace", source="对话第 1 轮", fact="x", objective_signal={}, weight=1.0, timestamp=datetime.now(UTC)),
    ]

    coverage = _dialogue_coverage(cards)

    assert coverage["learning_goal"] == pytest.approx(0.4)
    assert coverage["pace"] == 0.0


def test_dialogue_converges_only_when_nothing_is_half_baked() -> None:
    from app.profile.modes import DIALOGUE_MIN_TURNS, _dialogue_converged

    ready = [
        _component_card("learning_goal", "expression_certainty"),
        _component_card("learning_goal", "goal_specificity"),
        _component_card("learning_goal", "action_orientation"),
    ]
    half_baked = [_component_card("motivation", "intrinsic_value")]

    assert _dialogue_converged(DIALOGUE_MIN_TURNS - 1, ready) is False
    assert _dialogue_converged(DIALOGUE_MIN_TURNS, ready) is True
    # 1/5 motivation would end up unscored, so the dialogue keeps going.
    assert _dialogue_converged(DIALOGUE_MIN_TURNS, ready + half_baked) is False
    # An untouched dimension is empty, not half-baked, and does not block.
    assert _dialogue_converged(12, ready) is True
    # Nothing collected at all means nothing can be scored yet.
    assert _dialogue_converged(12, []) is False


# --------------------------------------------------------------------------
# 组件锚点（计划 §5.6.4）
# --------------------------------------------------------------------------


def test_every_component_has_an_anchor_and_only_measurement_ones_are_unaskable() -> None:
    from app.profile.component_model import REQUIRES_MEASUREMENT
    from app.profile.modes import COMPONENT_ANCHORS

    unaskable = {
        (dimension, component)
        for dimension, components in REQUIRES_MEASUREMENT.items()
        for component in components
    }
    declared_unaskable = {
        (dimension, component)
        for dimension, anchors in COMPONENT_ANCHORS.items()
        for component, anchor in anchors.items()
        if not anchor.get("askable", True)
    }

    assert set(COMPONENT_ANCHORS) == set(COMPONENTS)
    for dimension, components in COMPONENTS.items():
        assert set(COMPONENT_ANCHORS[dimension]) == set(components), dimension
    assert sum(len(anchors) for anchors in COMPONENT_ANCHORS.values()) == 35
    assert declared_unaskable == unaskable


def test_every_askable_anchor_carries_angles_and_guard_words() -> None:
    from app.profile.modes import COMPONENT_ANCHORS

    for dimension, anchors in COMPONENT_ANCHORS.items():
        for component, anchor in anchors.items():
            if not anchor.get("askable", True):
                continue
            where = f"{dimension}.{component}"
            assert anchor["must_cover"], where
            assert len(anchor["angles"]) >= 2, where
            assert anchor["extractable"], where
            assert len(anchor["guard_keywords"]) >= 2, where
            # The anchor must not hand the model a number to copy.
            assert not any(char.isdigit() for char in anchor["must_cover"]), where


def test_focus_never_asks_about_a_measurement_only_component() -> None:
    from app.profile.modes import _dialogue_component_focus

    for _ in range(5):
        focus = _dialogue_component_focus([], "knowledge_base")
        assert focus is not None
        assert focus["component"] != "measured_performance"
        # 概念基础权重最高，空盘时先问它。
        assert focus["component"] == "concept_foundation"
        assert focus["guard_keywords"]

    empty = _dialogue_component_focus([], "weak_points")
    assert empty is not None and empty["component"] != "measured_validation"


def test_focus_returns_none_once_every_answerable_component_is_filled() -> None:
    from app.profile.modes import _dialogue_component_focus

    cards = [
        _component_card("knowledge_base", component)
        for component in ("concept_foundation", "knowledge_scope", "retrieval_application", "boundary_awareness")
    ]

    assert _dialogue_component_focus(cards, "knowledge_base") is None


def test_reply_that_drifts_off_the_component_is_steered_back() -> None:
    from app.profile.modes import sanitize_dialogue_reply

    anchor = {"guard_keywords": ["性味", "归经"]}
    fallback = "你回忆一下这味药的性味归经？"

    drifted = sanitize_dialogue_reply("好的，我记下了。你平时喜欢看视频吗？", fallback_question=fallback, anchor=anchor)
    on_target = sanitize_dialogue_reply("好的。你还记得它的性味吗？", fallback_question=fallback, anchor=anchor)

    # 问偏了就把那句问句换掉，不是叠成两个问题。
    assert drifted == fallback
    assert on_target == "好的。你还记得它的性味吗？"


# --------------------------------------------------------------------------
# 软锁定换维（计划 §5.5.3-4）
# --------------------------------------------------------------------------


def test_soft_lock_keeps_the_current_dimension_while_it_has_a_hole() -> None:
    from app.profile.modes import _dialogue_target

    cards = [_component_card("learning_goal", "expression_certainty")]
    # 另一个维度更"满"，但当前维还有可问的空格 → 不换。
    others = [_component_card("pace", "plan_clarity"), _component_card("pace", "time_feasibility")]

    assert _dialogue_target(cards + others, current_target="learning_goal") == "learning_goal"


def test_soft_lock_releases_when_another_dimension_is_two_components_ahead() -> None:
    from app.profile.modes import _dialogue_target

    mine = [_component_card("learning_goal", "expression_certainty")]
    ahead = [
        _component_card("pace", "plan_clarity"),
        _component_card("pace", "time_feasibility"),
        _component_card("pace", "sustained_period"),
    ]

    assert _dialogue_target(mine + ahead, current_target="learning_goal") != "learning_goal"


def test_soft_lock_releases_when_the_dimension_has_no_answerable_hole_left() -> None:
    from app.profile.modes import _dialogue_target

    # learning_goal 的 5 个组件全部可对话；填满 5 个才没有空位。
    cards = [
        _component_card("learning_goal", component)
        for component in COMPONENTS["learning_goal"]
    ]

    assert _dialogue_target(cards, current_target="learning_goal") != "learning_goal"


def test_soft_lock_releases_after_two_stalled_turns() -> None:
    from app.profile.modes import _dialogue_target

    # learning_goal 只填了 1 格、pace 填了 2 格：没卡住时软锁定压住不换，
    # 连卡两轮之后才放行给填充率更高的 pace。
    cards = [
        _component_card("learning_goal", "expression_certainty"),
        _component_card("pace", "plan_clarity"),
        _component_card("pace", "time_feasibility"),
    ]

    assert _dialogue_target(cards, current_target="learning_goal", stalled=False) == "learning_goal"
    assert _dialogue_target(cards, current_target="learning_goal", stalled=True) == "pace"


# --------------------------------------------------------------------------
# 36-37: 真实模型输出的两个坑（都是实测发现的，不是设想出来的）
# --------------------------------------------------------------------------


def test_36_jury_prompt_caps_the_output_length() -> None:
    """五个组件各带三师落点，不设上限就会被 max_tokens 截成半截 JSON。

    实测 knowledge_base / weak_points / pace / motivation 四个维度都在
    2700–4500 字符处 EOF，整份结果作废。
    """

    text = _prompt_text()

    assert "篇幅上限" in text
    assert "每位老师的 reason 不超过 25 字" in text
    assert "narrative 不超过 120 字" in text


def test_36b_every_component_carries_three_teacher_scores() -> None:
    """三师必须落到每个组件上，而不是只给维度一个整体印象分。

    旧提示词只有维度级 ``jury``，组件只出一个综合分：于是"严师觉得这个组件
    只有 40、宽师觉得能到 65"的分歧在审计里完全看不到。
    """

    text = _prompt_text()

    assert "逐组件独立判档" in text
    assert "每个组件都要严师、宽师、中立三个视角分别给分" in text
    assert '"jury": {' in text
    assert "系统先对每个组件取三师落点平均，再按组件固定权重合成维度分" in text


def test_36c_component_score_is_the_mean_of_the_three_teachers() -> None:
    """组件分 = 三师落点平均；维度分再由组件分按固定权重合成。

    这是"每个组件都要三师分别评分"的可算证据：三位老师对同一个组件的分歧
    被保留下来，取平均后才是这一格的分数，再进维度分的加权和。
    """

    keys = COMPONENTS["knowledge_base"][:3]
    cards = [_card("knowledge_base", key, {"topic": "解表药", "correct": True}) for key in keys]
    payload = {
        "dimension": "知识基础",
        "dimension_tier": "medium",
        "narrative": "测试。",
        "component_scores": [
            {
                "component": "concept_foundation",
                "tier": "medium",
                "jury": {
                    "严师": {"score": 44, "reason": "只认原话"},
                    "宽师": {"score": 66, "reason": "意图明确"},
                    "中立": {"score": 55, "reason": "居中"},
                },
            },
            {
                "component": "knowledge_scope",
                "tier": "medium",
                "jury": {
                    "严师": {"score": 40, "reason": "范围窄"},
                    "宽师": {"score": 60, "reason": "够用"},
                    "中立": {"score": 50, "reason": "居中"},
                },
            },
            {
                "component": "retrieval_application",
                "tier": "medium",
                "jury": {
                    "严师": {"score": 50, "reason": "能提取"},
                    "宽师": {"score": 70, "reason": "会用"},
                    "中立": {"score": 60, "reason": "居中"},
                },
            },
        ],
    }

    result = _parse_jury_output("knowledge_base", cards, json.dumps(payload, ensure_ascii=False))

    assert result.component_scores == {
        "concept_foundation": 55.0,
        "knowledge_scope": 50.0,
        "retrieval_application": 60.0,
    }
    assert result.component_jury["concept_foundation"]["严师"].score == 44.0
    # 每位老师那一列按组件权重合成维度分，再取三人均值。
    weights = [COMPONENT_WEIGHTS["knowledge_base"][key] for key in result.component_scores]
    for teacher, expected in {"严师": (44, 40, 50), "宽师": (66, 60, 70), "中立": (55, 50, 60)}.items():
        assert result.teacher_dimension_scores[teacher] == pytest.approx(
            sum(score * weight for score, weight in zip(expected, weights)) / sum(weights),
            abs=0.01,
        )
    # 维度级三师不再由模型另给，而是从组件推导，均值仍落在 5–95。
    assert set(result.jury) == {"严师", "宽师", "中立"}
    assert result.jury_mean == pytest.approx(
        sum(result.teacher_dimension_scores.values()) / 3, abs=0.01
    )
    assert result.radar_score is not None


def test_36d_component_without_teacher_scores_falls_back_to_its_single_score() -> None:
    """旧记录（组件只有一个综合分）必须照旧可读，不能因为缺三师就整维作废。"""

    cards = [_card("knowledge_base", "concept_foundation", {"topic": "解表药", "correct": True})]
    payload = {
        "dimension": "知识基础",
        "dimension_tier": "medium",
        "narrative": "旧格式。",
        "component_scores": [{"component": "concept_foundation", "tier": "medium", "score": 52}],
    }

    result = _parse_jury_output("knowledge_base", cards, json.dumps(payload, ensure_ascii=False))

    assert result.component_scores == {"concept_foundation": 52.0}
    assert result.component_jury == {}


def test_36e_components_the_model_skipped_keep_their_previous_score() -> None:
    """未涉及的组件沿用上一轮分数，维度分按"本轮重评 + 上轮沿用"一起算。

    重评只覆盖证据变了的组件。如果拿"本轮返回的组件"单独算维度分，每轮
    分数都会因为分母变小而乱跳——用户会看到同一维度在 40 和 70 之间来回。
    """

    keys = COMPONENTS["knowledge_base"][:3]
    cards = [_card("knowledge_base", key, {"topic": "解表药", "correct": True}) for key in keys]
    cards[0].objective_signal = {**cards[0].objective_signal, "component_score": 50.0}
    cards[1].objective_signal = {**cards[1].objective_signal, "component_score": 60.0}
    payload = {
        "dimension": "知识基础",
        "dimension_tier": "medium",
        "narrative": "只重评了第三个组件。",
        "component_scores": [
            {
                "component": keys[2],
                "tier": "medium",
                "jury": {
                    "严师": {"score": 50, "reason": "能提取"},
                    "宽师": {"score": 70, "reason": "会用"},
                    "中立": {"score": 60, "reason": "居中"},
                },
            },
        ],
    }

    result = _parse_jury_output("knowledge_base", cards, json.dumps(payload, ensure_ascii=False))

    assert result.rescored_components == [keys[2]]
    assert sorted(result.carried_components) == sorted(keys[:2])
    # 有效分三格齐全，沿用的两格数值不变。
    assert result.component_scores[keys[0]] == 50.0
    assert result.component_scores[keys[1]] == 60.0
    assert result.component_scores[keys[2]] == 60.0
    # 覆盖度按 3/5 算，不是 1/5。
    assert result.coverage == pytest.approx(0.6, abs=0.01)


def test_36f_quality_tiers_are_ranges_not_three_fixed_values() -> None:
    """质量分三项给的是"档内区间"，模型在区间里选点。

    只给 0.9/0.6/0.3 三个固定值会把"能懂但略省略"和"能懂但严重指代不明"
    压成同一个数，几何平均后所有证据的置信度趋同，置信度就失去分辨力。
    """

    assert QUALITY_FACTOR_TIERS["low"] == (0.20, 0.40)
    assert QUALITY_FACTOR_TIERS["medium"] == (0.45, 0.65)
    assert QUALITY_FACTOR_TIERS["high"] == (0.70, 0.95)
    # 落点反推档位：区间中点之内归该档，不会出现"档位说高、数值在中档"的矛盾。
    assert quality_tier_of(0.30) == "low"
    assert quality_tier_of(0.55) == "medium"
    assert quality_tier_of(0.82) == "high"

    guide = QUALITY_FACTOR_GUIDE
    assert "0.70–0.95" in guide
    assert "先判档" in guide
    assert "quality_thinking" in guide
    # 三档区间必须都写进抽取提示词，否则模型只能照旧猜 0.9/0.6/0.3。
    assert "0.20–0.40" in guide and "0.45–0.65" in guide


def test_36g_evidence_sent_to_the_jury_carries_turn_order_and_quality() -> None:
    """送评证据按轮次排列，并带轮次标注与质量分。

    "第 1 轮含糊、第 5 轮具体"应该往上走分；把五条证据当成并列的一次性
    陈述，老师只能看到一堆句子，看不出演化。
    """

    keys = COMPONENTS["knowledge_base"][:2]
    early = _card("knowledge_base", keys[0], {"topic": "解表药", "correct": True})
    early.quote = "好像学过一点"
    early.source_turn_ids = [5]
    late = _card("knowledge_base", keys[0], {"topic": "解表药", "correct": True})
    late.quote = "麻黄发汗解表，桂枝调和营卫"
    late.source_turn_ids = [1]
    for card in (early, late):
        card.quality_factors = {
            "source_reliability": 0.85, "expression_clarity": 0.8,
            "content_specificity": 0.8, "freshness": 1.0, "consistency": 0.8,
        }

    text = _prompt_text(dimension="knowledge_base")
    messages = build_jury_messages("knowledge_base", [early, late])
    body = "\n".join(str(message["content"]) for message in messages)

    assert "turn_label" in body
    assert "第1轮" in body
    assert "第5轮" in body
    assert "quality_score" in body
    # 第 1 轮的证据排在前面：送评顺序是时间顺序，不是抽取顺序。
    assert body.index("麻黄发汗解表") < body.index("好像学过一点")
    assert "按轮次先后排列" in body
    assert text  # 提示词本身可构造


def test_37_null_optional_text_fields_do_not_void_the_whole_batch() -> None:
    """模型把“不适用”写成 null 时，只有那一条该丢，不能连累整批。

    实测 learning_goal / resource_preference 等维度就是因为
    ``component_scores[0].missing == null`` 整批判废，维度退回不出分。
    """

    cards = [_card("knowledge_base", "concept_foundation", {"topic": "解表药", "correct": True})]
    raw = json.dumps(
        {
            "dimension": "知识基础",
            "dimension_tier": "medium",
            "jury": {
                "严师": {"score": 45, "reasoning_chain": ["证据偏少"], "comment": "偏弱"},
                "宽师": {"score": 55, "reasoning_chain": ["能对上"], "comment": "尚可"},
                "中立": {"score": 50, "reasoning_chain": ["居中"], "comment": "中档"},
            },
            "narrative": "测试叙述。",
            "component_scores": [
                {
                    "component": "concept_foundation",
                    "tier": "medium",
                    "score": 52,
                    "missing": None,
                    "reason": None,
                    "conflict_handling": None,
                },
                {"component": None, "score": None, "missing": None},
            ],
        },
        ensure_ascii=False,
    )

    result = _parse_jury_output("knowledge_base", cards, raw)

    assert result.method == "jury"
    assert result.component_scores == {"concept_foundation": 52.0}
    assert result.coverage == pytest.approx(0.2)
    # 没有 component 的那条被丢掉，而不是让整批抛异常
    assert result.aggregated_score is None
