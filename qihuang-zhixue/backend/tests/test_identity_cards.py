from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import app.profile.modes as modes
from app.profile.evidence import EvidenceTier
from app.profile.schema import DIMENSION_LABELS, EvidenceCard
from app.profile.store import list_evidence


def _card(
    dimension: str,
    signal: dict[str, object],
    quote: str,
    *,
    tier: EvidenceTier = EvidenceTier.MEASURED,
    extraction_method: str = "no_key_rule_fallback",
) -> EvidenceCard:
    return EvidenceCard(
        dimension=dimension,
        source=f"source-{dimension}",
        tier=tier,
        fact=f"formatted fact for {dimension}",
        objective_signal=signal,
        weight=3.0,
        quote=quote,
        extraction_confidence=0.9,
        extraction_method=extraction_method,
        timestamp=datetime.now(UTC),
    )


def _seven_dimension_cards() -> list[EvidenceCard]:
    return [
        _card("learning_goal", {"label": "应试提分"}, "我要准备考试提分"),
        _card("motivation", {"label": "内驱", "motivation_score": 80}, "我自己想弄明白"),
        _card("resource_preference", {"label": "知识图谱"}, "我喜欢看知识图谱"),
        _card("learning_style", {"label": "视觉型"}, "看图我记得更牢"),
        _card("knowledge_base", {"value": 70}, "我完成了基础测验"),
        _card("pace", {"daily_minutes": 45, "target_days": 14}, "每天45分钟，先学14天"),
        _card("weak_points", {"label": "方剂配伍", "correct": False}, "这道方剂配伍题答错了"),
    ]


def test_identity_card_contract_is_stable_for_all_seven_dimensions() -> None:
    cards = _seven_dimension_cards()
    fused = modes._dialogue_fusion(cards)
    view = modes._dialogue_identity_cards_view(cards, fusion=fused, revealed_flags={})

    assert {item["key"] for item in view} == set(DIMENSION_LABELS)
    assert {item["kind"] for item in view} == {"distribution", "level", "tags"}
    required = {
        "key",
        "label",
        "kind",
        "revealed",
        "revealable",
        "revealed_now",
        "confidence",
        "confidence_status",
        "score",
        "display_value",
        "distribution",
        "items",
        "evidence_refs",
        "quotes",
        "explanation",
        "provenance",
        "pending_reason",
    }
    for item in view:
        assert required <= item.keys()
        assert item["label"] == DIMENSION_LABELS[item["key"]]
        assert item["confidence"] == fused[item["key"]].confidence
        # 三档口径：有结论但置信度低、有材料但没到结论，都不该被写成"已取证"。
        assert item["confidence_status"] in {"待取证", "已收集到", "已取证"}
        assert item["cards"] == item["items"]
        assert item["provenance"] == "no_key_rule_fallback"


def test_distribution_items_are_exact_fusion_ratios_with_traceable_quotes() -> None:
    cards = [
        _card("learning_goal", {"distribution": {"应试提分": 0.75, "临床实用": 0.25}}, "考试和临床都重要"),
        _card("learning_goal", {"label": "临床实用"}, "我也想用于临床"),
    ]
    fused = modes._dialogue_fusion(cards)
    item = next(
        entry
        for entry in modes._dialogue_identity_cards_view(cards, fusion=fused, revealed_flags={})
        if entry["key"] == "learning_goal"
    )

    assert item["distribution"] == fused["learning_goal"].distribution
    assert {entry["canonical_label"]: entry["ratio"] for entry in item["items"]} == item["distribution"]
    for entry in item["items"]:
        assert entry["evidence_refs"]
        assert all(quote in {card.quote for card in cards} for quote in entry["quotes"])


def test_revealed_card_without_model_distribution_still_shows_composition() -> None:
    """翻开牌却四张全空——因为组成只认模型顺手给的 distribution。

    现在翻开的牌必须自带组成：先按用户说过的标签算比例，没有标签就按已收集
    的组件算，并且一定要报出牌名与占比摘要。
    """

    cards = [
        _card("learning_goal", {"label": "应试提分", "component_score": 78}, "明年考药师，先把药性补上")
        .model_copy(update={"component": "goal_specificity"}),
        _card("learning_goal", {"label": "临床实用", "component_score": 66}, "也想看懂门诊的方子")
        .model_copy(update={"component": "action_orientation"}),
        _card("learning_goal", {"label": "应试提分", "component_score": 70}, "先把考试顶过去")
        .model_copy(update={"component": "priority_signal"}),
    ]
    fused = modes._dialogue_fusion(cards)
    item = next(
        entry
        for entry in modes._dialogue_identity_cards_view(
            cards, fusion=fused, revealed_flags={"learning_goal": True}
        )
        if entry["key"] == "learning_goal"
    )

    assert item["revealed"] is True, "这份证据已经够翻牌"
    assert item["distribution"], "翻开的牌不能没有组成"
    assert abs(sum(item["distribution"].values()) - 1.0) < 0.01, "占比要归一"
    assert len(item["items"]) >= 1 and all(entry.get("ratio") for entry in item["items"])
    assert item["composition_basis"] == "labels"
    assert item["card_name"], "预设之外的组合也要有牌名"
    assert "%" in item["composition_summary"]


def test_revealed_card_falls_back_to_collected_components() -> None:
    """一条标签都没有时，用"哪些组件已经收集"当组成，牌面不留白。"""

    cards = [
        _card("motivation", {"component_score": 72}, "我自己想把这事弄明白")
        .model_copy(update={"component": "intrinsic_value"}),
        _card("motivation", {"component_score": 55}, "还是想弄明白")
        .model_copy(update={"component": "actual_investment"}),
        _card("motivation", {"component_score": 60}, "先做完手上的事")
        .model_copy(update={"component": "external_commitment"}),
    ]
    fused = modes._dialogue_fusion(cards)
    item = next(
        entry
        for entry in modes._dialogue_identity_cards_view(
            cards, fusion=fused, revealed_flags={"motivation": True}
        )
        if entry["key"] == "motivation"
    )

    assert item["revealed"] is True
    assert item["composition_basis"] == "components", "没有标签时必须退回组件构成"
    assert item["distribution"], "组件回退也要给比例"
    assert abs(sum(item["distribution"].values()) - 1.0) < 0.01
    assert item["card_name"], "组件构成也要能报出牌名"
    assert "已收集组件构成" in item["composition_summary"]
    assert len(item["items"]) >= 2, "翻开的牌至少要有两张非空牌面"


def test_revealed_card_with_single_component_is_not_blank() -> None:
    """只收到一个组件、又没有标签时，翻开必须有一张 100% 的牌。

    旧实现要求"至少两个组件"才回退到组件构成，于是这类维度翻开后是四张
    "未成形"——用户看到的是"你说点亮了，牌是空的"。
    """

    from app.profile.fusion import FusionResult

    cards = [
        _card("learning_goal", {}, "我想考公务员").model_copy(
            update={"component": "goal_specificity", "collection_status": "scorable"}
        )
    ]
    result = FusionResult(
        dimension="learning_goal",
        dimension_label="学习目标",
        score=60,
        label=None,
        distribution={},
        confidence=0.66,
        variance=0.0,
        evidence_refs=[],
        narrative="",
    )
    item = modes._identity_distribution_item(
        "learning_goal", result, cards, {}, set(), keep_revealed=True
    )

    assert item["revealed"] is True
    assert item["composition_basis"] == "components"
    assert item["distribution"] == {"目标具体度": 1.0}
    assert len(item["items"]) == 1
    assert item["items"][0]["ratio"] == 1.0
    assert item["card_name"], "单组件也要报得出牌名"


def test_goal_answer_can_form_multiple_short_named_cards() -> None:
    extraction = modes._validate_dialogue_extraction(
        __import__("app.profile.nodes", fromlist=["_fallback_dialogue_extraction"])._fallback_dialogue_extraction(
            "我想考研，并且想掌握基础"
        ),
        "我想考研，并且想掌握基础",
    )
    cards = __import__("app.profile.nodes", fromlist=["_dialogue_cards"])._dialogue_cards(extraction)
    goal = next(card for card in cards if card.dimension == "learning_goal")
    view = next(
        item
        for item in modes._dialogue_identity_cards_view(cards, revealed_flags={})
        if item["key"] == "learning_goal"
    )

    assert goal.objective_signal["distribution"] == {"学术深造": 1.0, "基础巩固": 1.0}
    assert view["score"] is None
    assert view["revealed"] is False
    assert view["items"] == []


def test_other_goal_uses_valid_generated_short_name_and_keeps_quote_in_evidence() -> None:
    card = _card(
        "learning_goal",
        {"label": "其他", "card_names": {"其他": "跨界求索"}},
        "我想把心理学和中医结合起来",
    )
    item = next(
        entry
        for entry in modes._dialogue_identity_cards_view([card], revealed_flags={})
        if entry["key"] == "learning_goal"
    )

    assert item["items"] == []
    assert item["revealed"] is False
    assert item["score"] is None


def test_level_and_verified_weak_point_structures_do_not_fabricate_quotes() -> None:
    cards = _seven_dimension_cards()
    cards.append(
        _card(
            "weak_points",
            {"label": "仅自述弱项"},
            "我猜这里可能薄弱",
            tier=EvidenceTier.SELF_REPORT,
        )
    )
    view = {item["key"]: item for item in modes._dialogue_identity_cards_view(cards, revealed_flags={})}

    assert view["knowledge_base"]["score"] is None
    assert view["knowledge_base"]["revealed"] is False
    assert view["pace"]["score"] is None
    assert view["pace"]["items"][0]["evidence_refs"]
    assert view["pace"]["items"][0]["daily_minutes"] == 45
    assert view["pace"]["items"][0]["target_days"] == 14
    assert view["pace"]["display_value"] == "待取证"
    assert view["weak_points"]["items"] == []
    assert "仅自述弱项" not in view["weak_points"]["distribution"]
    raw_quotes = {card.quote for card in cards}
    assert all(quote in raw_quotes for item in view.values() for quote in item["quotes"])
    assert all("formatted fact" not in quote for item in view.values() for quote in item["quotes"])


def test_revealed_now_is_persisted_then_consumed_exactly_once() -> None:
    card = _card("learning_goal", {"label": "应试提分"}, "我要明确准备考试")
    step_data: dict[str, object] = {}

    staged = modes._dialogue_identity_cards_view(
        [card],
        step_data=step_data,
        consume_revealed_now=False,
    )
    delivered = modes._dialogue_identity_cards_view([card], step_data=step_data)
    repeated = modes._dialogue_identity_cards_view([card], step_data=step_data)
    staged_goal = next(item for item in staged if item["key"] == "learning_goal")
    delivered_goal = next(item for item in delivered if item["key"] == "learning_goal")
    repeated_goal = next(item for item in repeated if item["key"] == "learning_goal")

    assert staged_goal["revealed_now"] is False
    assert delivered_goal["revealed_now"] is False
    assert repeated_goal["revealed"] is False
    assert repeated_goal["revealed_now"] is False
    assert step_data.get("identity_cards_revealed", {}).get("learning_goal") is not True
    assert step_data["identity_cards_revealed_now"] == []


def test_low_confidence_card_stays_pending_with_an_explanation() -> None:
    card = EvidenceCard(
        dimension="learning_goal",
        source="dialogue",
        tier=EvidenceTier.SELF_REPORT,
        fact="uncertain fact",
        objective_signal={"label": "应试提分"},
        weight=0.1,
        quote="可能会准备考试",
        extraction_confidence=0.4,
        extraction_method="no_key_rule_fallback",
    )
    item = next(
        entry
        for entry in modes._dialogue_identity_cards_view([card], revealed_flags={})
        if entry["key"] == "learning_goal"
    )

    assert item["revealable"] is False
    assert item["revealed"] is False
    assert item["revealed_now"] is False
    assert item["confidence_status"] == "已收集到"
    assert item["pending_reason"]
    assert "0/5" in item["pending_reason"]
    assert "评分暂缓" in item["pending_reason"]


def test_sse_identity_cards_expose_no_key_provenance_and_one_shot_reveal(monkeypatch, tmp_path: Path) -> None:
    class FakeRouter:
        async def chat_stream(self, *_args, **_kwargs):
            yield "message", "你主要想为考试提分，还是用于临床？"

    monkeypatch.setattr(modes, "ModelRouter", FakeRouter)
    monkeypatch.setattr(modes, "_has_profile_extract_key", lambda: False)
    db_path = tmp_path / "identity-sse.db"
    session = modes.start_profile_session("identity-sse-user", "dialogue", db_path=db_path)

    async def collect() -> list[tuple[str, dict[str, object]]]:
        return [
            event
            async for event in modes.stream_dialogue_chat_events(
                session.session_id,
                user_id=session.user_id,
                utterance="我是零基础，我要准备考试提分。",
                db_path=db_path,
            )
        ]

    events = asyncio.run(collect())
    identity_event = next(data for event, data in events if event == "identity_cards")
    state_event = next(data for event, data in events if event == "state")
    identity_goal = next(item for item in identity_event["items"] if item["key"] == "learning_goal")
    state_goal = next(item for item in state_event["identity_cards"] if item["key"] == "learning_goal")

    assert identity_goal["provenance"] == "fusion"
    assert identity_goal["revealed_now"] is False
    assert state_goal["revealed"] is False
    assert state_goal["revealed_now"] is False
    assert identity_goal["quotes"] == []


def test_finish_repeat_and_reload_do_not_repeat_revealed_now(tmp_path: Path) -> None:
    db_path = tmp_path / "identity-finish.db"
    session = modes.start_profile_session("identity-finish-user", "dialogue", db_path=db_path)
    session.evidence_cards.extend(_seven_dimension_cards())
    modes._save_profile_session(session, db_path=db_path)

    first_finished = asyncio.run(
        modes.finish_profile_session_async(session.session_id, user_id=session.user_id, db_path=db_path)
    )
    first_summary = modes.dialogue_session_summary(first_finished, db_path=db_path)
    second_finished = asyncio.run(
        modes.finish_profile_session_async(session.session_id, user_id=session.user_id, db_path=db_path)
    )
    second_summary = modes.dialogue_session_summary(second_finished, db_path=db_path)
    reloaded = modes.get_profile_session(session.session_id, user_id=session.user_id, db_path=db_path)
    reloaded_summary = modes.dialogue_session_summary(reloaded, db_path=db_path)

    assert all(not item["revealed_now"] for item in first_summary["identity_cards"])
    assert all(not item["revealed_now"] for item in second_summary["identity_cards"])
    assert all(not item["revealed_now"] for item in reloaded_summary["identity_cards"])
    assert all(not item["revealed"] for item in reloaded_summary["identity_cards"])
    assert len(list_evidence(session.user_id, db_path=db_path)) == len(session.evidence_cards)


def test_dialogue_session_inherits_persisted_evidence_without_duplicating_it(tmp_path: Path) -> None:
    """对话要接住用户已经攒下的证据，收尾时又不能把它们再插一遍。

    以前对话会话从零开始：先走完收徒剧情、再开对话，牌阵是空的，弟子档案
    却是亮的——同一份证据给了两个答案。证据本来就都在同一个库里，缺的只是
    "读回来"。
    """

    db = tmp_path / "inherit.db"
    from app.profile.store import add_evidence, list_evidence

    story_card = _card("motivation", {"label": "内驱"}, "我自己想把这事弄明白").model_copy(
        update={"component": "intrinsic_value", "collection_status": "scorable", "evidence_id": "ev-story-1"}
    )
    add_evidence("u-inherit", story_card, db_path=db)

    session = modes.start_profile_session("u-inherit", "dialogue", db_path=db)
    assert [card.evidence_id for card in session.evidence_cards] == ["ev-story-1"]

    # 牌阵要认得出剧情留下的动机证据：置信度不再是从零，且没翻开时说明缺什么。
    identity = modes._dialogue_identity_cards_view(session.evidence_cards)
    motivation = next(item for item in identity if item["key"] == "motivation")
    assert motivation["confidence"] > 0
    assert motivation["revealed"] is False
    assert motivation["pending_reason"], "没翻开的牌必须说明还差什么，而不是空白"

    # 本轮新证据正常入库，继承来的那条不重复插入。
    fresh = _card("learning_goal", {"label": "应试提分"}, "我想考公务员").model_copy(
        update={"component": "goal_specificity", "collection_status": "scorable", "evidence_id": "ev-new-1"}
    )
    session.evidence_cards.append(fresh)
    modes._finish_profile_session(session, db_path=db)

    stored = list_evidence("u-inherit", db_path=db)
    assert sorted(card.evidence_id for card in stored) == ["ev-new-1", "ev-story-1"]
