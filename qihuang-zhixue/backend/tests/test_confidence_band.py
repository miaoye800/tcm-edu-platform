"""置信度上限必须随独立确认增长，不能永远卡在"单句清晰自述"。

对话抽取路径从不写 ``combination_type``，融合层于是把所有维度都回退成
"单句清晰自述"（区间 20%–38%）。真实 8 轮里 6 条证据、4 个独立来源组的
维度仍然是 38%，证据涨到 7 条置信度一动不动——看起来像饱和曲线太平，
根因却是档位根本没升过。
"""

from app.profile.component_model import COMBINATION_INTERVALS, COMPONENTS
from app.profile.modes import _dialogue_fusion
from app.profile.schema import EvidenceCard, EvidenceTier


def _card(
    *,
    group: str,
    turn: int,
    component: str = "goal_specificity",
    quality: float = 0.8,
    relation: str = "new",
) -> EvidenceCard:
    return EvidenceCard(
        dimension="learning_goal",
        source="dialogue",
        tier=EvidenceTier.SELF_REPORT,
        fact="我想考研，目标北中医",
        quote="我想考研，目标北中医",
        objective_signal={"evidence_type": "self_report"},
        weight=1.0,
        component=component,
        evidence_group_id=group,
        source_turn_ids=[turn],
        normalized_claim="想考研，目标北中医",
        collection_status="scorable",
        semantic_relation=relation,
        quality_factors={
            "source_reliability": 0.85,
            "expression_clarity": quality,
            "content_specificity": quality,
            "consistency": quality,
            "freshness": 1.0,
            "independence_note": "置信度档位回归",
        },
    )


def _band(cards: list[EvidenceCard]) -> tuple[str, float]:
    result = _dialogue_fusion(cards)["learning_goal"]
    audit = result.metadata.get("component_confidence") or {}
    return str(audit.get("combination_type")), float(result.confidence)


def test_band_rises_when_one_component_is_confirmed_by_several_sources() -> None:
    """同一个组件被 4 个独立来源确认 → 进"多轮清晰且一致"档，不再是单句。"""

    band_single, confidence_single = _band([_card(group="turn-1", turn=1)])
    band_many, confidence_many = _band([_card(group=f"turn-{index}", turn=index) for index in range(1, 5)])

    assert band_single == "single_clear_self_report"
    assert band_many == "multi_turn_clear_consistent"
    assert confidence_many > confidence_single + 0.2


def test_different_components_do_not_count_as_mutual_confirmation() -> None:
    """4 个组件各说一件事只是"多个侧面"，不能当成互相印证抬一档。"""

    keys = COMPONENTS["learning_goal"][:4]
    cards = [_card(group=f"turn-{index}", turn=index, component=key) for index, key in enumerate(keys, start=1)]

    band, confidence = _band(cards)

    assert band == "multi_turn_clear_pending_crosscheck"
    lower, upper = COMBINATION_INTERVALS[band]
    assert lower <= confidence <= upper


def test_low_quality_evidence_never_leaves_the_vague_band() -> None:
    """含糊说三遍还是含糊：清晰度是进档的硬门槛。"""

    cards = [_card(group=f"turn-{index}", turn=index, quality=0.45) for index in range(1, 4)]

    band, confidence = _band(cards)

    assert band == "single_vague_self_report"
    assert confidence <= COMBINATION_INTERVALS["single_vague_self_report"][1]


def test_declared_band_is_never_downgraded() -> None:
    """模型在卡片上明确写的档只可升不可降。"""

    card = _card(group="turn-1", turn=1)
    card.objective_signal = {**card.objective_signal, "combination_type": "dialogue_behavior_consistent"}

    band, _confidence = _band([card])

    assert band == "dialogue_behavior_consistent"


def test_confidence_is_monotonic_in_independent_groups() -> None:
    """独立来源组越多，置信度只能持平或上升，不能倒退。"""

    values = []
    for count in (1, 2, 3, 4, 6):
        cards = [_card(group=f"turn-{index}", turn=index) for index in range(1, count + 1)]
        values.append(_band(cards)[1])

    assert values == sorted(values)
