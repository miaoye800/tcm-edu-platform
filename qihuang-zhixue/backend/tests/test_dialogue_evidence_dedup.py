from __future__ import annotations

from datetime import UTC, datetime

import app.profile.modes as modes
from app.profile.evidence import EvidenceTier
from app.profile.schema import EvidenceCard


def _card(
    *,
    dimension: str = "learning_goal",
    component: str = "goal_specificity",
    claim: str = "目标是考执业药师",
    quote: str = "明年考执业药师",
    evidence_id: str = "ev-1",
    group: str = "turn-1-goal",
) -> EvidenceCard:
    return EvidenceCard(
        dimension=dimension,
        component=component,
        source="对话问诊",
        tier=EvidenceTier.MEASURED,
        fact=claim,
        normalized_claim=claim,
        objective_signal={"quote": quote},
        weight=1.0,
        quote=quote,
        extraction_confidence=0.8,
        extraction_method="llm",
        collection_status="scorable",
        evidence_id=evidence_id,
        evidence_group_id=group,
        timestamp=datetime.now(UTC),
    )


def test_history_replay_is_rejected() -> None:
    """整段 transcript 每轮都会被送去抽取，历史句子必须不能再算一次。"""

    replayed = _card(quote="明年考执业药师")
    kept, rejected = modes._filter_dialogue_cards(
        [replayed],
        utterance="我每周三晚上复习两小时",
        prior_cards=[],
    )

    assert kept == []
    assert [item["reason"] for item in rejected] == ["history_replay"]
    assert rejected[0]["component"] == "goal_specificity"


def test_same_determination_is_counted_once() -> None:
    """同一表达只形成一次有效认定——哪怕这一轮又被重述了一遍。"""

    prior = _card(evidence_id="ev-prior")
    repeated = _card(evidence_id="ev-again", quote="我想考执业药师。")
    # 标点/空格不同也是同一句："想考执业药师！"
    restated = _card(evidence_id="ev-restated", quote="我想考执业药师！")

    kept, rejected = modes._filter_dialogue_cards(
        [repeated, restated],
        utterance="我想考执业药师。我想考执业药师！",
        prior_cards=[prior],
    )

    assert kept == []
    assert [item["reason"] for item in rejected] == ["duplicate_determination", "duplicate_determination"]


def test_new_claim_on_the_same_component_survives() -> None:
    prior = _card(evidence_id="ev-prior", claim="目标是考执业药师")
    fresh = _card(
        evidence_id="ev-fresh",
        claim="备考时间定在明年春天",
        quote="明年春天就要考",
    )

    kept, rejected = modes._filter_dialogue_cards(
        [fresh],
        utterance="明年春天就要考",
        prior_cards=[prior],
    )

    assert rejected == []
    assert [card.evidence_id for card in kept] == ["ev-fresh"]


def test_different_component_same_words_still_counts() -> None:
    """同一句话落在不同组件上是两次认定（"考公"既说目标也说优先级）。"""

    prior = _card(evidence_id="ev-goal", component="goal_specificity", claim="目标是考公")
    same_words = _card(
        evidence_id="ev-priority",
        component="priority_signal",
        claim="优先考公",
        quote="考公最重要",
    )

    kept, rejected = modes._filter_dialogue_cards(
        [same_words],
        utterance="考公最重要",
        prior_cards=[prior],
    )

    assert rejected == []
    assert [card.component for card in kept] == ["priority_signal"]


def test_cards_without_quote_are_judged_by_claim_only() -> None:
    """模型没给 quote 时不能整条丢：还有 claim 可以判重。"""

    first = _card(evidence_id="ev-a", quote="")
    kept, rejected = modes._filter_dialogue_cards([first], utterance="明年考执业药师", prior_cards=[])
    assert [card.evidence_id for card in kept] == ["ev-a"]

    second = _card(evidence_id="ev-b", quote="")
    kept, rejected = modes._filter_dialogue_cards(
        [second],
        utterance="换一种说法：明年考执业药师",
        prior_cards=[first],
    )
    assert kept == []
    assert [item["reason"] for item in rejected] == ["duplicate_determination"]


def test_same_quote_with_different_group_is_one_evidence() -> None:
    """组号不同、原话相同的两张卡只能算一条证据。

    实测：用户只说了一次"想学来能让我有钱赚"，模型在第 3 轮和第 5 轮各抽一次、
    各给一个 evidence_group_id，送评证据包里就出现两条一模一样的引用。
    用户看到的是"我明明只说了一遍，为什么算两条"。
    """

    from app.profile.component_model import deduplicate_component_cards

    first = _card(evidence_id="ev-3", quote="想学来能让我有钱赚", group="turn-3-goal")
    second = _card(evidence_id="ev-5", quote="想学来能让我有钱赚", group="turn-5-goal")
    # 标点/空白不同仍是同一句原话。
    third = _card(evidence_id="ev-7", quote="想学来 能让我有钱赚。", group="turn-7-goal")

    kept, conflicts, dropped = deduplicate_component_cards([first, second, third])

    assert conflicts == []
    assert len(kept) == 1
    # 三条原话归一后是同一句：只留其中一条，另外两条进 dropped。
    assert kept[0].quote.replace(" ", "").rstrip("。") == "想学来能让我有钱赚"
    assert len(dropped) == 2


def test_same_quote_on_two_components_stays_two_evidence() -> None:
    """"考公"同时是目标与优先级时，两个组件各算一条，不能互相吞掉。"""

    from app.profile.component_model import deduplicate_component_cards

    goal = _card(evidence_id="ev-goal", component="goal_specificity", quote="考公好一点")
    priority = _card(evidence_id="ev-priority", component="priority_signal", quote="考公好一点")

    kept, _, dropped = deduplicate_component_cards([goal, priority])

    assert dropped == []
    assert {card.component for card in kept} == {"goal_specificity", "priority_signal"}
