"""端到端验收：两条链路都跑真实会话流程，看能不能真的出分。

计划 §11-12/13 的验收条件在这里落成断言。测试不打真实模型：
抽取与判分都被替换掉，跑的是会话状态机、卡构造、覆盖度与统一算分公式本身。
真正的三师数值由 `test_scoring_contract.py` 用精确数字保证。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

import app.profile.modes as modes_module
from app.profile.component_model import COMPONENTS, COVERAGE_THRESHOLD, dimension_score
from app.profile.modes import answer_profile_session, get_profile_session, start_profile_session
from app.profile.schema import EvidenceCard

USER = "e2e-user"


def _drive(session, payload):
    session, _cards = asyncio.run(
        answer_profile_session(
            session.session_id,
            USER,
            session.current_step.id,
            payload,
            db_path=session.persisted_db_path or _DB["path"],
        )
    )
    return session


_DB: dict[str, Path] = {}


def _start(mode: str, tmp_path: Path):
    db_path = tmp_path / f"{mode}.db"
    _DB["path"] = db_path
    return start_profile_session(USER, mode, db_path=db_path)


def _collected(session, dimension: str) -> list[str]:
    return sorted(
        {
            card.component
            for card in session.evidence_cards
            if card.dimension == dimension and card.collection_status == "scorable"
        }
    )


def _run_story(tmp_path: Path, *, all_correct: bool = True):
    """五幕全流程，走的是真实会话状态机。"""

    session = _start("apprentice", tmp_path)
    session = _drive(
        session,
        {
            "identity_card": "临床学徒",
            "utterance": "想跟门诊抄方，明年春天能上手",
            "story_answers": {
                "extra_goal_scene": "想跟门诊抄方，明年春天上手",
                "extra_goal_priority": "配伍剂量",
            },
            "response_time_ms": 6000,
        },
    )

    # 题目必须来自服务端已存下的那一套：重载后重新抽题会让 correct 永远为 False。
    questions = get_profile_session(session.session_id, USER, db_path=_DB["path"]).step_data["questions"]
    answers = []
    for index, question in enumerate(questions):
        right = question.get("answer") or (question.get("options") or [""])[0]
        picked = right if all_correct else (question.get("options") or [""])[-1]
        item = {"question_id": question["id"], "selected": picked, "response_time_ms": 5000}
        if index == 0:
            item["free_text"] = "补气药都差不多，具体记不清"
        answers.append(item)
    session = _drive(
        session,
        {
            "answers": answers,
            "response_time_ms": 5000,
            "story_answers": {"extra_weak_cross_context": "换成病例里的说法我会犹豫，比如黄芪和党参分不清"},
        },
    )

    session = _drive(
        session,
        {
            "case": "热毒咽痛",
            "prescription": "金银花15g，连翘12g",
            "preference_note": "我更喜欢按证型推",
            "response_time_ms": 7000,
        },
    )
    session = _drive(
        session,
        {
            "resource_type": "医案文选",
            "story_answers": {
                "extra_style_memory": "背口诀",
                "extra_style_compare": "知识图谱",
                "extra_resource_reason": "病例更接近真实门诊",
                "extra_resource_constraint": "手机",
            },
            "response_time_ms": 4000,
        },
    )
    session = _drive(
        session,
        {
            "daily_minutes": 30,
            "target_days": 45,
            "accept_challenge": False,
            "story_answers": {
                "extra_pace_window": "晚上",
                "extra_pace_recover": "我会从上次停下的那味药接着看",
                "extra_motivation_conflict": "先把考试顶过去，学习往后挪两天",
                "extra_weak_recover": "我会把记错的药抄一遍再找相似药对比",
            },
            "response_time_ms": 9000,
        },
    )
    return session


@pytest.mark.parametrize("all_correct", [True, False])
def test_story_flow_reaches_the_gate_on_every_dimension(tmp_path: Path, all_correct: bool) -> None:
    """验收 1：剧情五幕跑完，7 维都能过 60% 门槛。"""

    session = _run_story(tmp_path, all_correct=all_correct)

    assert session.finished is True
    assert _collected(session, "knowledge_base") == sorted(COMPONENTS["knowledge_base"])
    for dimension in COMPONENTS:
        collected = _collected(session, dimension)
        coverage = len(collected) / len(COMPONENTS[dimension])
        assert coverage >= COVERAGE_THRESHOLD, f"{dimension} 只有 {len(collected)}/5：{collected}"
        outcome = dimension_score(dimension, {component: 70.0 for component in collected})
        assert outcome["gate_ok"] is True, dimension
        assert outcome["score"] is not None, dimension


def test_story_flow_knowledge_base_is_full_and_the_rest_is_sixty_percent(tmp_path: Path) -> None:
    """验收 1（精确值）：知识基础 5/5，其余 6 维 3/5。"""

    session = _run_story(tmp_path)
    coverage = {
        dimension: len(_collected(session, dimension)) / len(COMPONENTS[dimension])
        for dimension in COMPONENTS
    }

    assert coverage["knowledge_base"] == 1.0
    for dimension in COMPONENTS:
        if dimension == "knowledge_base":
            continue
        assert coverage[dimension] == pytest.approx(0.6), f"{dimension} 覆盖度不是 0.6"


def test_the_quiz_paper_is_drawn_once_and_survives_a_reload(tmp_path: Path) -> None:
    """题目是随机抽的，必须只抽一次：否则客户端答 A 卷、服务端判 B 卷。"""

    session = _start("apprentice", tmp_path)
    session = _drive(session, {"identity_card": "临床学徒", "utterance": "x", "response_time_ms": 6000})
    first = [question["id"] for question in session.step_data["questions"]]

    reloaded = get_profile_session(session.session_id, USER, db_path=_DB["path"])
    second = [question["id"] for question in reloaded.step_data["questions"]]

    assert first == second
    assert len(first) == 8


def test_story_answers_are_judged_against_the_served_paper(tmp_path: Path) -> None:
    """答对就要记成答对：题目错位时 correct 会恒为 False，实测表现永远是错的。"""

    session = _start("apprentice", tmp_path)
    session = _drive(session, {"identity_card": "临床学徒", "utterance": "x", "response_time_ms": 6000})
    questions = session.step_data["questions"]
    answers = [
        {"question_id": question["id"], "selected": question.get("answer"), "response_time_ms": 5000}
        for question in questions
    ]
    session = _drive(session, {"answers": answers, "response_time_ms": 5000})

    measured = [
        card
        for card in session.evidence_cards
        if card.dimension == "knowledge_base" and card.component == "measured_performance"
    ]
    assert measured
    assert all(card.objective_signal.get("correct") is True for card in measured)


def _dialogue_card(dimension: str, component: str) -> EvidenceCard:
    return EvidenceCard(
        dimension=dimension,  # type: ignore[arg-type]
        source="对话",
        fact="test",
        objective_signal={"quote": "test"},
        weight=1.0,
        timestamp=datetime.now(UTC),
        component=component,
        collection_status="scorable",
    )


def test_dialogue_flow_reaches_full_coverage_on_the_learning_goal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验收 2：对话 8 轮后学习目标 5/5 并出分。"""

    goal_components = list(COMPONENTS["learning_goal"])
    turn = {"value": 0}

    async def fake_extract_and_question(**kwargs):
        turn["value"] = int(kwargs["turn_index"])
        return {}, f"follow up {turn['value']}"

    def fake_cards_from_extraction(_extraction):
        component = goal_components[min(max(turn["value"] - 1, 0), len(goal_components) - 1)]
        return [_dialogue_card("learning_goal", component)]

    monkeypatch.setattr(modes_module, "_dialogue_extract_and_question", fake_extract_and_question)
    monkeypatch.setattr(modes_module, "_dialogue_cards_from_extraction", fake_cards_from_extraction)

    session = _start("dialogue", tmp_path)
    for _ in range(8):
        assert session.current_step is not None
        session = _drive(session, {"utterance": "我想明年考过执业药师，每天能学半小时"})

    collected = _collected(session, "learning_goal")
    assert len(collected) == 5
    outcome = dimension_score("learning_goal", {component: 70.0 for component in collected})
    assert outcome["coverage"] == 1.0
    assert outcome["score"] is not None


def test_story_coverage_numbers_reproduce_the_plan_arithmetic(tmp_path: Path) -> None:
    """验收 3（可复算）：剧情链路拿到的 3/5 组件，代进公式要得到计划里的数。

    两条链路同分本身由 `test_scoring_contract.py::test_4` 用同一组组件分保证；
    这里验证的是真实流程产出的覆盖度确实能复现那组手算值。
    """

    session = _run_story(tmp_path)
    collected = _collected(session, "knowledge_base")
    assert len(collected) == 5

    # 知识基础 3/5 的手算样例：55 / 22.33 / 52，权重 .25/.20/.15。
    components = {"concept_foundation": 55.0, "knowledge_scope": 22.33, "measured_performance": 52.0}
    outcome = dimension_score("knowledge_base", components)

    assert outcome["coverage"] == pytest.approx(0.6)
    assert outcome["weighted_mean"] == pytest.approx(43.36, abs=0.01)
    assert outcome["score"] == pytest.approx(46.02, abs=0.01)
