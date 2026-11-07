"""剧情链路的 finish 抽取管线。

对应的契约（计划 §9）：19 只选择无自由文本 → 抽取阶段零调用；20 提炼句不得
出现原话没有的数字；21 某一批抽取失败不得拖垮评分；22 同组同组件重复卡只算一次。
"""

import asyncio
import json
import logging
from pathlib import Path

import pytest

import app.profile.modes as modes_module
from app.profile.modes import (
    STORY_EXTRACTION_BATCHES,
    ProfileMode,
    _finish_apprentice_profile_session,
    _story_extraction_messages,
    _story_transcript,
    start_profile_session,
)
from app.profile.store import list_evidence


class _FakeRouter:
    """Stand-in for ModelRouter: records every call, replays a scripted queue."""

    calls: list[dict] = []
    queue: list[object] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def chat(self, task: str, messages: list[dict[str, str]], **kwargs: object) -> str:
        _FakeRouter.calls.append({"task": task, "messages": messages, **kwargs})
        if not _FakeRouter.queue:
            return '{"evidence": []}'
        nxt = _FakeRouter.queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if isinstance(nxt, str):
            return nxt
        return json.dumps(nxt, ensure_ascii=False)


@pytest.fixture(autouse=True)
def fake_extraction(monkeypatch: pytest.MonkeyPatch):
    _FakeRouter.calls = []
    _FakeRouter.queue = []
    monkeypatch.setattr(modes_module, "ModelRouter", _FakeRouter)
    # 关键：让"有没有 key"这道闸门放行，否则零调用是因为没 key，而不是因为没内容。
    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: True)
    yield _FakeRouter


def _self_report_card(quote: str, dimension: str = "learning_goal", component: str = "goal_specificity"):
    return modes_module._card(
        dimension,
        "序幕·叩问初心",
        quote,
        {"quote": quote, "basis": "explicit", "confidence": 0.9},
        1.0,
        component=component,
        answer_kind="self_report",
    )


def _choice_card(quote: str, dimension: str = "learning_goal", component: str = "expression_certainty"):
    return modes_module._card(
        dimension,
        "序幕·叩问初心",
        f"选择身份卡“{quote}”",
        {"quote": quote},
        1.0,
        component=component,
        answer_kind="choice",
    )


def _number_card(minutes: int = 45):
    return modes_module._card(
        "pace",
        "四幕·立下学规",
        f"计划每天 {minutes} 分钟",
        {"quote": f"每天 {minutes} 分钟", "daily_minutes": minutes},
        1.0,
        component="plan_clarity",
        answer_kind="number",
    )


def _session(tmp_path: Path, cards: list):
    db_path = tmp_path / "story.db"
    session = start_profile_session("story-user", ProfileMode.apprentice, db_path=db_path)
    session.evidence_cards = list(cards)
    return session, db_path


def _finish(session, db_path: Path):
    return asyncio.run(_finish_apprentice_profile_session(session, db_path=db_path))


def test_three_batches_cover_every_dimension_exactly_once() -> None:
    covered = [dimension for dimensions, _limit in STORY_EXTRACTION_BATCHES for dimension in dimensions]

    assert len(STORY_EXTRACTION_BATCHES) == 3
    assert len(covered) == len(set(covered)) == 7
    assert set(covered) == {
        "knowledge_base",
        "weak_points",
        "learning_goal",
        "motivation",
        "learning_style",
        "pace",
        "resource_preference",
    }


def test_finish_makes_exactly_one_call_per_batch_with_its_dimension_list(tmp_path: Path, fake_extraction) -> None:
    session, db_path = _session(tmp_path, [_self_report_card("想先把常用药性学扎实")])

    _finish(session, db_path)

    assert len(fake_extraction.calls) == 3
    assert {call["task"] for call in fake_extraction.calls} == {"profile_extract"}
    assert all(call["temperature"] == 0 for call in fake_extraction.calls)
    assert all(call["response_format"] == {"type": "json_object"} for call in fake_extraction.calls)
    for call, (dimensions, limit) in zip(fake_extraction.calls, STORY_EXTRACTION_BATCHES, strict=True):
        prompt = call["messages"][-1]["content"]
        assert f"本批只处理以下 {len(dimensions)} 个维度：{', '.join(dimensions)}" in prompt
        assert f"最多 {limit} 条" in prompt
        assert "想先把常用药性学扎实" in prompt


def test_transcript_keeps_prose_and_drops_option_labels_and_bare_numbers() -> None:
    session, _ = _session(Path("."), [
        _self_report_card("想先把常用药性学扎实"),
        _choice_card("临床学徒"),
        _number_card(45),
    ])

    transcript = _story_transcript(session)

    assert "想先把常用药性学扎实" in transcript
    assert "临床学徒" not in transcript
    assert "45" not in transcript
    # 自述题带上题型与语速档，供 cross_turn_stability 这类组件判断。
    assert "（自述" in transcript


def test_choice_only_session_never_calls_the_extractor(tmp_path: Path, fake_extraction) -> None:
    """断言 19：全是选项题的会话，抽取阶段一次模型调用都不发。"""

    session, db_path = _session(tmp_path, [_choice_card("临床学徒"), _number_card(30)])

    finished = _finish(session, db_path)

    assert fake_extraction.calls == []
    assert finished.finished is True
    # 规则卡照常落库，评分链路不受影响。
    assert len(list_evidence("story-user", db_path=db_path)) == 2


def test_claim_with_a_number_absent_from_the_quote_degrades_to_the_quote(tmp_path: Path, fake_extraction) -> None:
    """断言 20：模型编出原话没有的数字时，宁可退回原话也不采用编造的句子。"""

    quote = "我每天大概能学一个小时"
    session, db_path = _session(tmp_path, [_self_report_card(quote, dimension="pace", component="plan_clarity")])
    fake_extraction.queue = [
        {"evidence": []},
        {"evidence": []},
        {
            "evidence": [
                {
                    "dimension": "pace",
                    "component": "plan_clarity",
                    "quote": quote,
                    "normalized_claim": "每天稳定学习90分钟",
                    "evidence_type": "self_report",
                    "quality_factors": {
                        "source_reliability": 0.8,
                        "expression_clarity": 0.8,
                        "content_specificity": 0.7,
                        "freshness": 1.0,
                        "consistency": 0.8,
                    },
                }
            ]
        },
    ]

    _finish(session, db_path)

    extracted = [card for card in session.evidence_cards if card.extraction_method == "llm"]
    assert len(extracted) == 1
    assert extracted[0].fact == quote
    assert extracted[0].normalized_claim == quote


def test_extractor_cannot_attach_evidence_to_an_unknown_component(tmp_path: Path, fake_extraction) -> None:
    quote = "背方剂老是记不住君臣佐使"
    session, db_path = _session(tmp_path, [_self_report_card(quote, dimension="weak_points", component="error_recovery")])
    fake_extraction.queue = [
        {
            "evidence": [
                {"dimension": "weak_points", "component": "made_up_component", "quote": quote},
                # 维度不在本批范围内，同样要丢掉。
                {"dimension": "pace", "component": "plan_clarity", "quote": quote},
            ]
        },
        {"evidence": []},
        {"evidence": []},
    ]

    _finish(session, db_path)

    assert not [card for card in session.evidence_cards if card.extraction_method == "llm"]


def test_a_failed_batch_never_blocks_scoring(tmp_path: Path, fake_extraction, caplog: pytest.LogCaptureFixture) -> None:
    """断言 21：三批全失败，finish 仍要完成，规则卡仍是评分依据。"""

    session, db_path = _session(tmp_path, [_self_report_card("想先把常用药性学扎实")])
    fake_extraction.queue = [RuntimeError("upstream 502"), RuntimeError("upstream 502"), RuntimeError("upstream 502")]

    with caplog.at_level(logging.WARNING, logger="app.profile.modes"):
        finished = _finish(session, db_path)

    assert finished.finished is True
    assert finished.current_step is None
    assert len(list_evidence("story-user", db_path=db_path)) == 1
    assert not [card for card in session.evidence_cards if card.extraction_method == "llm"]
    assert "story extraction batch failed" in caplog.text
    assert session.step_data["extraction_provenance"] == "rule_only"


def test_extraction_provenance_separates_not_attempted_mixed_and_llm(tmp_path: Path, fake_extraction) -> None:
    quote = "想先把常用药性学扎实"

    idle, idle_db = _session(tmp_path / "idle", [_choice_card("临床学徒")])
    _finish(idle, idle_db)
    assert idle.step_data["extraction_provenance"] == "not_attempted"

    mixed, mixed_db = _session(tmp_path / "mixed", [_self_report_card(quote)])
    fake_extraction.queue = [
        {"evidence": [{"dimension": "knowledge_base", "component": "concept_foundation", "quote": quote}]},
        {"evidence": []},
        {"evidence": []},
    ]
    _finish(mixed, mixed_db)
    assert mixed.step_data["extraction_provenance"] == "mixed"

    full, full_db = _session(tmp_path / "full", [_self_report_card(quote)])
    per_batch = [
        {"dimension": "knowledge_base", "component": "concept_foundation", "quote": quote},
        {"dimension": "learning_goal", "component": "goal_specificity", "quote": quote},
        {"dimension": "learning_style", "component": "preference_clarity", "quote": quote},
    ]
    fake_extraction.queue = [{"evidence": [item]} for item in per_batch]
    _finish(full, full_db)
    assert full.step_data["extraction_provenance"] == "llm"


def test_same_component_and_group_is_counted_once(tmp_path: Path, fake_extraction) -> None:
    """断言 22：同一组、同一组件的重复卡只算一次，否则 coverage 会被虚高。"""

    quote = "背方剂老是记不住君臣佐使"
    session, db_path = _session(tmp_path, [_self_report_card(quote, dimension="weak_points", component="error_recovery")])
    duplicated = {
        "dimension": "weak_points",
        "component": "error_recovery",
        "quote": quote,
        "normalized_claim": "背方剂记不住君臣佐使",
        "evidence_type": "self_report",
        "quality_factors": {
            "source_reliability": 0.8,
            "expression_clarity": 0.8,
            "content_specificity": 0.8,
            "freshness": 1.0,
            "consistency": 0.8,
        },
    }
    fake_extraction.queue = [{"evidence": [duplicated, dict(duplicated)]}, {"evidence": []}, {"evidence": []}]

    _finish(session, db_path)

    extracted = [card for card in session.evidence_cards if card.extraction_method == "llm"]
    assert len(extracted) == 1
    assert extracted[0].component == "error_recovery"
    assert extracted[0].collection_status == "scorable"
    assert extracted[0].quality_factors


def test_extraction_prompt_forbids_scoring_and_offers_no_numeric_anchor() -> None:
    messages = _story_extraction_messages("【序幕·叩问初心】用户：想学药性（自述）", ("learning_goal", "motivation"), 10)
    system, user = messages[0]["content"], messages[1]["content"]

    assert "绝对不评分" in system
    assert "连续子串" in system
    assert "不超过30字" in system
    # 抽取阶段一旦出现分数或档位锚点，就会把评分提前做掉。
    assert not any(token in system + user for token in ("0-100", "1-5分", "档位", "score"))
    assert "learning_goal" in user and "motivation" in user


def test_story_flow_without_a_key_scores_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """断言 26（剧情侧）：没有 key 时剧情链路和对话链路一样不出分。"""

    import asyncio as _asyncio

    from app.profile.jury import score_dimension

    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)
    session, db_path = _session(tmp_path, [_self_report_card("想先把常用药性学扎实")])

    finished = _finish(session, db_path)

    assert finished.step_data["extraction_provenance"] == "not_attempted"
    assert not [card for card in finished.evidence_cards if card.extraction_method == "llm"]
    result = _asyncio.run(score_dimension("learning_goal", finished.evidence_cards))
    assert result.aggregated_score is None
    assert result.provenance == "no_key"
