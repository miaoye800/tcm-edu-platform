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
        assert item["confidence_status"] in {"待取证", "已取证"}
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

    assert view["knowledge_base"]["score"] == 70
    assert view["knowledge_base"]["items"][0]["level_name"]
    assert view["knowledge_base"]["items"][0]["anchor"]
    assert "70" in view["knowledge_base"]["display_value"]
    assert view["pace"]["score"] is not None
    assert view["pace"]["items"][0]["evidence_refs"]
    assert view["pace"]["items"][0]["daily_minutes"] == 45
    assert view["pace"]["items"][0]["target_days"] == 14
    assert view["pace"]["display_value"] == "每天45分钟 · 目标14天"
    assert view["weak_points"]["items"] == [
        {
            "label": "方剂配伍",
            "strength": 1,
            "quotes": ["这道方剂配伍题答错了"],
            "evidence_refs": ["source-weak_points#1"],
        }
    ]
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

    assert staged_goal["revealed_now"] is True
    assert delivered_goal["revealed_now"] is True
    assert repeated_goal["revealed"] is True
    assert repeated_goal["revealed_now"] is False
    assert step_data["identity_cards_revealed"]["learning_goal"] is True
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
    assert item["confidence_status"] == "待取证"
    assert item["pending_reason"]
    assert "0.50" in item["pending_reason"]


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

    assert identity_goal["provenance"] == "no_key_rule_fallback"
    assert identity_goal["revealed_now"] is True
    assert state_goal["revealed"] is True
    assert state_goal["revealed_now"] is False
    assert identity_goal["quotes"]
    assert all(quote in "我是零基础，我要准备考试提分。" for quote in identity_goal["quotes"])


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

    assert any(item["revealed_now"] for item in first_summary["identity_cards"])
    assert all(not item["revealed_now"] for item in second_summary["identity_cards"])
    assert all(not item["revealed_now"] for item in reloaded_summary["identity_cards"])
    assert all(item["revealed"] for item in reloaded_summary["identity_cards"])
    assert len(list_evidence(session.user_id, db_path=db_path)) == len(session.evidence_cards)
