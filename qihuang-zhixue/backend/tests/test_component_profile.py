import json

from app.profile.component_model import (
    COMPONENTS,
    COMPONENT_RUBRICS,
    DIMENSION_COMPONENT_LABELS,
    AtomicEvidence,
    DimensionScoreOutput,
    ComponentScore,
    QualityFactors,
    apply_dimension_scores,
    collect_component_results,
    confidence_from_evidence,
    deduplicate_evidence,
    quote_is_traceable,
)
from app.profile.nodes import DialogueExtraction, _dialogue_cards
from app.profile.modes import _dialogue_fusion
from app.profile.evidence import EvidenceCard, EvidenceTier
from app.profile.jury import _parse_jury_output


def _ev(*, evidence_id: str, component: str, group: str = "g1", relation: str = "new", status: str = "scorable", quote: str = "我想通过线上老师一对一教学学习我们的中医学内容") -> AtomicEvidence:
    return AtomicEvidence(
        evidence_id=evidence_id,
        dimension="resource_preference",
        component=component,
        evidence_group_id=group,
        source_turn_ids=[1],
        quote=quote,
        normalized_claim="用户表达了资源选择偏好",
        evidence_type="self_report",
        collection_status=status,
        semantic_relation=relation,
        quality_factors=QualityFactors(
            source_reliability=.8,
            expression_clarity=.9,
            content_specificity=.8,
            freshness=1,
            consistency=.9,
            independence_note="首次独立表达",
        ),
    )


def test_all_seven_dimensions_have_five_rubric_components_and_weights():
    assert sum(len(items) for items in COMPONENTS.values()) == 35
    assert set(COMPONENT_RUBRICS) == set(COMPONENTS)
    for dimension, components in COMPONENTS.items():
        assert set(COMPONENT_RUBRICS[dimension]) == set(components)
        assert sum(COMPONENT_RUBRICS[dimension][key]["weight"] for key in components) == 1
        for key in components:
            rubric = COMPONENT_RUBRICS[dimension][key]
            assert rubric["minimum_evidence"]
            assert rubric["not_scorable_when"]
            assert rubric["low"] and rubric["medium"] and rubric["high"]
            assert rubric["accepted_evidence_types"]
            assert rubric["conflict_principle"] and rubric["followup_direction"]


def test_quote_must_be_a_verifiable_original_substring():
    source = "我想通过线上老师一对一教学学习我们的中医学内容。"
    assert quote_is_traceable("线上老师一对一教学", source)
    assert not quote_is_traceable("用户偏好线上老师", source)


def test_same_group_is_deduplicated_but_conflict_is_retained():
    first = _ev(evidence_id="ev-1", component="preference_specificity")
    duplicate = _ev(evidence_id="ev-2", component="preference_specificity", relation="semantic_duplicate")
    conflict = _ev(evidence_id="ev-3", component="preference_specificity", group="g2", relation="conflict")
    selected, conflicts = deduplicate_evidence([first, duplicate, conflict])
    assert len(selected) == 1
    assert selected[0].evidence_id == "ev-1"
    assert [item.evidence_id for item in conflicts] == ["ev-3"]


def test_incomplete_dimension_is_pending_and_not_scoreable():
    evidence = [_ev(evidence_id="ev-1", component="preference_specificity")]
    result = collect_component_results(evidence)["resource_preference"]
    assert result.collected == 1
    assert result.total == 5
    assert result.dimension_score is None
    assert "task_match_reason" in result.pending_components


def test_dimension_score_uses_public_weights_and_5_to_95_bounds():
    evidence = [_ev(evidence_id=f"ev-{index}", component=component, group=f"g-{index}") for index, component in enumerate(COMPONENTS["resource_preference"])]
    output = DimensionScoreOutput(
        component_scores=[
            ComponentScore(component=component, score=score, evidence_refs=[f"ev-{index}"], reason="证据充分", conflict_handling="无冲突")
            for index, (component, score) in enumerate(zip(COMPONENTS["resource_preference"], [5, 95, 50, 80, 70]))
        ],
        dimension_summary="完整证据包评分",
    )
    result = apply_dimension_scores(evidence, output)
    assert result.complete
    assert result.dimension_score == 57.5
    assert 5 <= result.dimension_score <= 95


def test_jury_component_scores_accept_rubric_labels_and_normalize_to_keys():
    components = COMPONENTS["learning_goal"]
    cards = [
        EvidenceCard(
            dimension="learning_goal", source="dialogue", tier=EvidenceTier.SELF_REPORT,
            fact="我想考研", quote="我想考研", objective_signal={}, weight=1,
            component=component, collection_status="scorable",
        )
        for component in components
    ]
    teachers = {
        name: {"score": 65, "reasoning_chain": ["根据可追溯证据评分"], "comment": "审慎判断"}
        for name in ("严师", "宽师", "中立")
    }
    raw = {
        "jury": teachers,
        "narrative": "学习目标组件评分。",
        "component_scores": [
            {
                "component": DIMENSION_COMPONENT_LABELS["learning_goal"][component],
                "score": 65 + index,
                "evidence_refs": [],
                "reason": "原话支持该判断",
                "conflict_handling": "未见冲突",
            }
            for index, component in enumerate(components)
        ],
    }

    result = _parse_jury_output("learning_goal", cards, json.dumps(raw, ensure_ascii=False))

    assert set(result.component_scores) == set(components)
    assert [card.objective_signal["component_score"] for card in cards] == [65, 66, 67, 68, 69]


def test_single_clear_self_report_cannot_exceed_038_even_with_duplicate_cards():
    evidence = [_ev(evidence_id=f"ev-{index}", component="preference_specificity", group="same") for index in range(3)]
    info = confidence_from_evidence(evidence, "single_clear_self_report")
    assert info["confidence"] <= .38
    assert len(info["independent_source_groups"]) == 1


def test_atomic_contract_preserves_cross_dimension_source_and_acceptance_quote():
    source = "我想通过线上老师一对一教学学习我们的中医学内容。"
    extraction = DialogueExtraction(
        turn_id=1,
        combination_type="single_clear_self_report",
        atomic_evidence=[
            _ev(evidence_id="ev-resource", component="preference_specificity", quote=source),
            _ev(evidence_id="ev-style", component="preference_clarity", quote="一对一教学"),
        ],
    )
    extraction.atomic_evidence[1].dimension = "learning_style"
    cards = _dialogue_cards(extraction)
    assert {card.dimension for card in cards} == {"resource_preference", "learning_style"}
    assert all(card.quote in source for card in cards)
    assert {card.evidence_group_id for card in cards} == {"g1"}
    assert all(card.collection_status == "scorable" for card in cards)


def test_dialogue_legacy_dimension_signal_cannot_bypass_component_score_gate():
    card = EvidenceCard(
        dimension="learning_goal",
        source="dialogue",
        tier=EvidenceTier.SELF_REPORT,
        fact="我想治病救人",
        quote="我想治病救人",
        objective_signal={"radar_score": 88, "label": "考研升学"},
        weight=1,
        component="expression_certainty",
        collection_status="scorable",
    )

    result = _dialogue_fusion([card])["learning_goal"]

    assert result.score is None
    assert result.radar_score is None
    assert result.label is None
    gate = result.metadata["component_collection"]
    assert gate["collected"] == 1
    assert gate["total"] == 5
    assert "goal_specificity" in gate["missing_components"]
    assert gate["scoring_phase"] == "collecting"
