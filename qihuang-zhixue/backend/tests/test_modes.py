import asyncio
import inspect
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.profile.modes as modes_module
from app.api.profile import get_profile_db_path
from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.config import Settings
from app.main import app
from app.profile.modes import (
    APPRENTICE_STEPS,
    extract_prescription_signal,
    finish_profile_session,
    load_diagnostic_questions,
    reset_profile_sessions,
)
from app.profile.fusion import ALLOWED_LABELS
from app.profile.schema import DimensionKey, EvidenceCard, create_blank_profile
from app.profile.nodes import diagnostic_eval_node, diagnostic_question_node, initial_state
from app.profile.store import list_evidence, upsert_profile


client = TestClient(app)


def setup_function() -> None:
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def isolated_profile_session_db(tmp_path: Path):
    db_path = tmp_path / "profile-sessions.db"
    reset_profile_sessions(db_path=db_path)
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    yield


def test_apprentice_flow_emits_cards_for_all_acts_without_scores(tmp_path: Path) -> None:
    db_path = tmp_path / "apprentice.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    first = _start("apprentice")
    session_id = first["session_id"]

    response = _answer(session_id, APPRENTICE_STEPS[0], {"identity_card": "临床学徒", "response_time_ms": 1200})
    assert response["step"]["id"] == APPRENTICE_STEPS[1]

    questions = response["step"]["payload"]["questions"]
    assert len(questions) >= 2
    assert "answer" not in questions[0]

    _answer(
        session_id,
        APPRENTICE_STEPS[1],
        {
            "answers": [
                {"question_id": questions[0]["id"], "selected": questions[0]["options"][0], "response_time_ms": 3000},
                {"question_id": questions[1]["id"], "selected": "故意选错", "response_time_ms": 4100},
            ]
        },
    )
    _answer(
        session_id,
        APPRENTICE_STEPS[2],
        {
            "case": "风寒表证，恶寒无汗",
            "herbs": [{"name": "麻黄", "dose": 6}, {"name": "桂枝", "dose": 6}],
            "response_time_ms": 9000,
        },
    )
    _answer(session_id, APPRENTICE_STEPS[3], {"resource_type": "医案文选", "response_time_ms": 1500})
    last = _answer(session_id, APPRENTICE_STEPS[4], {"daily_minutes": 45, "target_days": 14, "accept_challenge": True, "response_time_ms": 1800})

    finished = client.post("/api/profile/session/finish", json={"session_id": session_id}).json()
    cards = finished["evidence_cards"]
    stored = list_evidence("student-001", db_path=db_path)

    assert last["finished"] is True
    assert finished["finished"] is True
    assert len(stored) == len(cards)
    assert {card["source"] for card in cards} >= {
        "序幕·叩问初心",
        "一幕·药圃初认",
        "二幕·初次抓药",
        "三幕·师父药宝",
        "四幕·立下学规",
    }
    assert any(card["dimension"] == "resource_preference" for card in cards)
    assert all("score" not in card["objective_signal"] for card in cards)
    assert any(card["objective_signal"].get("response_time_ms") == 9000 for card in cards)


def test_quickcard_emits_identity_and_timed_judgement_cards() -> None:
    first = _start("quickcard")
    session_id = first["session_id"]
    questions = first["step"]["payload"]["questions"]

    payload = _answer(
        session_id,
        "quickcard",
        {
            "identity_card": "考前冲刺",
            "elapsed_seconds": 28,
            "answers": [{"question_id": questions[0]["id"], "selected": "故意选错", "response_time_ms": 28000}],
        },
    )

    assert payload["finished"] is True
    assert any(card["dimension"] == "learning_goal" for card in payload["evidence_cards"])
    assert any(card["objective_signal"].get("elapsed_seconds") == 28 for card in payload["evidence_cards"])
    assert any(card["objective_signal"].get("correct") is False for card in payload["evidence_cards"])


def test_practice_uses_high_weight_prescription_evidence() -> None:
    first = _start("practice")

    payload = _answer(
        first["session_id"],
        "practice",
        {"case": "热毒咽痛", "prescription": "金银花15g，连翘12g", "response_time_ms": 7000},
    )
    cards = payload["evidence_cards"]

    assert len(cards) == 1
    assert cards[0]["source"] == "开药实战"
    assert cards[0]["weight"] == 1.5
    assert cards[0]["objective_signal"]["对症"] is True
    assert cards[0]["objective_signal"]["配伍"] is True
    assert cards[0]["objective_signal"]["response_time_ms"] == 7000


def test_passive_events_convert_to_evidence_cards() -> None:
    first = _start("passive")

    payload = _answer(
        first["session_id"],
        "passive",
        {
            "events": [
                {"event_type": "click", "resource_type": "知识图谱", "topic": "麻黄", "duration_ms": 2000},
                {"event_type": "skip", "topic": "剂量换算", "response_time_ms": 300},
            ]
        },
    )
    cards = payload["evidence_cards"]

    assert [card["dimension"] for card in cards] == ["resource_preference", "weak_points"]
    assert cards[1]["objective_signal"]["event_type"] == "skip"
    assert cards[1]["objective_signal"]["response_time_ms"] == 300


def test_dialogue_flow_collects_cards_scores_converges_and_finishes_without_key(tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    first = _start("dialogue", user_id="dialogue-user")
    session_id = first["session_id"]

    assert first["step"]["kind"] == "dialogue"
    assert first["step"]["id"] == "dialogue_turn_1"

    payload = _answer(
        session_id,
        "dialogue_turn_1",
        {"utterance": "我是中药学学生，最近主要想准备期末考试，也想把基础补稳。"},
    )
    assert payload["turn_index"] == 1
    assert payload["next_question"]
    assert len(payload["evidence_cards"]) > 0
    assert set(payload["card_scores"]) == {"考前冲刺", "临床学徒", "草木好奇者", "研读派"}
    assert all(0 <= value <= 1 for value in payload["card_scores"].values())
    assert payload["converged"] is False

    payload = _answer(
        session_id,
        "dialogue_turn_2",
        {"quick_reply": "考试推动，但我自己也想学明白，平时更喜欢文字材料和表格。"},
    )
    assert payload["turn_index"] == 2
    assert len(payload["evidence_cards"]) > 0
    assert payload["converged"] is False

    payload = _answer(
        session_id,
        "dialogue_turn_3",
        {"utterance": "我每天大概能学45分钟，目标是两周内先把解表药和清热药过一遍。"},
    )

    assert payload["turn_index"] == 3
    assert payload["step"]["kind"] == "dialogue"
    assert payload["converged"] is True
    assert payload["next_question"]
    assert payload["inferred_card"] in payload["card_scores"]
    assert max(payload["card_scores"].values()) > 0
    assert "dimensions" in payload["radar"]

    finished = client.post("/api/profile/session/finish", json={"session_id": session_id})
    stored = list_evidence("dialogue-user", db_path=db_path)

    assert finished.status_code == 200
    assert finished.json()["finished"] is True
    assert isinstance(finished.json()["radar_changes"], list)
    assert isinstance(finished.json()["summary_text"], str)
    assert len(stored) == len(finished.json()["evidence_cards"])
    assert len(stored) >= 3


def test_dialogue_reload_preserves_step_data_and_finished_semantics(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-reload.db"
    user_id = "dialogue-reload-user"
    generated_questions = {
        1: "LLM follow-up: learning goal detail?",
        2: "LLM follow-up: motivation detail?",
    }
    current_turn = {"value": 0}

    async def fake_extract_and_question(**kwargs):
        turn_index = int(kwargs["turn_index"])
        current_turn["value"] = turn_index
        return {}, generated_questions.get(turn_index, "")

    def fake_cards_from_extraction(_extraction):
        turn_index = current_turn["value"]
        return [
            _evidence_card("learning_goal", {"label": ALLOWED_LABELS["learning_goal"][0], "turn": turn_index}, weight=1.0),
            _evidence_card(
                "motivation",
                {"label": ALLOWED_LABELS["motivation"][0], "motivation_score": 100, "turn": turn_index},
                weight=1.0,
            ),
        ]

    monkeypatch.setattr(modes_module, "_dialogue_extract_and_question", fake_extract_and_question)
    monkeypatch.setattr(modes_module, "_dialogue_cards_from_extraction", fake_cards_from_extraction)

    session = modes_module.start_profile_session(user_id, "dialogue", db_path=db_path)
    session, _ = asyncio.run(
        modes_module.answer_profile_session(
            session.session_id,
            user_id=user_id,
            step_id="dialogue_turn_1",
            payload={"utterance": "first answer"},
            db_path=db_path,
        )
    )

    reloaded = modes_module.get_profile_session(session.session_id, user_id=user_id, db_path=db_path)
    assert reloaded.current_step is not None
    assert reloaded.current_step.id == "dialogue_turn_2"
    assert reloaded.current_step.prompt == generated_questions[1]
    assert generated_questions[1] in reloaded.step_data["asked_questions"]
    assert reloaded.finished is False
    assert reloaded.persisted is False

    session, _ = asyncio.run(
        modes_module.answer_profile_session(
            session.session_id,
            user_id=user_id,
            step_id=reloaded.current_step.id,
            payload={"utterance": "second answer"},
            db_path=db_path,
        )
    )
    session, _ = asyncio.run(
        modes_module.answer_profile_session(
            session.session_id,
            user_id=user_id,
            step_id=session.current_step.id,
            payload={"utterance": "third answer"},
            db_path=db_path,
        )
    )
    confirmed = modes_module.get_profile_session(session.session_id, user_id=user_id, db_path=db_path)

    assert confirmed.current_step is not None
    assert confirmed.current_step.kind == "dialogue"
    assert generated_questions[1] in confirmed.step_data["asked_questions"]
    assert generated_questions[2] in confirmed.step_data["asked_questions"]
    assert confirmed.finished is False
    assert confirmed.persisted is False
    assert "_dialogue_fusion" not in confirmed.step_data

    with sqlite3.connect(db_path) as conn:
        stored = conn.execute(
            """
            SELECT finished, persisted, step_data_json
            FROM profile_sessions
            WHERE session_id = ?
            """,
            (session.session_id,),
        ).fetchone()

    assert stored is not None
    assert stored[0] == 0
    assert stored[1] == 0
    assert generated_questions[1] in stored[2]


def test_dialogue_fallback_question_does_not_repeat_consecutively() -> None:
    asked_questions: list[str] = []

    first = modes_module._fallback_dialogue_question("knowledge_base", 0, asked_questions=asked_questions)
    second = modes_module._fallback_dialogue_question("knowledge_base", 0, asked_questions=asked_questions)

    assert second != first
    assert asked_questions == [first, second]


def test_parse_json_object_returns_none_on_mock_or_garbage() -> None:
    assert modes_module._parse_json_object("[MOCK:deepseek-chat] not structured JSON") is None
    assert modes_module._parse_json_object("not structured JSON") is None


def test_dialogue_mock_mode_next_question_not_empty(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-mock.db"
    user_id = "dialogue-mock-user"
    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)

    session = modes_module.start_profile_session(user_id, "dialogue", db_path=db_path)
    first_prompt = session.current_step.prompt
    assert first_prompt

    session, _ = asyncio.run(
        modes_module.answer_profile_session(
            session.session_id,
            user_id=user_id,
            step_id=session.current_step.id,
            payload={"utterance": "I want to prepare for exams and fix my basics."},
            db_path=db_path,
        )
    )
    second_prompt = session.current_step.prompt
    assert second_prompt
    assert second_prompt != first_prompt
    assert first_prompt in session.step_data["asked_questions"]
    assert second_prompt in session.step_data["asked_questions"]

    session, _ = asyncio.run(
        modes_module.answer_profile_session(
            session.session_id,
            user_id=user_id,
            step_id=session.current_step.id,
            payload={"utterance": "External exams push me, but I also want to understand the material."},
            db_path=db_path,
        )
    )
    third_prompt = session.current_step.prompt
    assert third_prompt
    assert third_prompt != second_prompt
    assert third_prompt in session.step_data["asked_questions"]


def test_dialogue_exceeds_legacy_max_turns_without_finishing(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-ten-turns.db"
    user_id = "dialogue-ten-turn-user"
    current_turn = {"value": 0}

    async def fake_extract_and_question(**kwargs):
        current_turn["value"] = int(kwargs["turn_index"])
        return {}, f"follow up {current_turn['value']}"

    def fake_cards_from_extraction(_extraction):
        return [
            _evidence_card("learning_goal", {"label": ALLOWED_LABELS["learning_goal"][0]}, weight=1.0),
            _evidence_card("motivation", {"label": ALLOWED_LABELS["motivation"][0], "motivation_score": 80}, weight=1.0),
        ]

    monkeypatch.setattr(modes_module, "_dialogue_extract_and_question", fake_extract_and_question)
    monkeypatch.setattr(modes_module, "_dialogue_cards_from_extraction", fake_cards_from_extraction)

    session = modes_module.start_profile_session(user_id, "dialogue", db_path=db_path)
    for index in range(10):
        assert session.current_step is not None
        session, _ = asyncio.run(
            modes_module.answer_profile_session(
                session.session_id,
                user_id=user_id,
                step_id=session.current_step.id,
                payload={"utterance": f"answer {index}"},
                db_path=db_path,
            )
        )

    assert session.step_index == 10
    assert session.current_step is not None
    assert session.current_step.kind == "dialogue"
    assert session.finished is False
    assert modes_module.dialogue_session_summary(session)["converged"] is True


def test_dialogue_reload_next_question_recorded(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-reload-missing-question.db"
    user_id = "dialogue-reload-missing-user"
    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)

    session = modes_module.start_profile_session(user_id, "dialogue", db_path=db_path)
    session, _ = asyncio.run(
        modes_module.answer_profile_session(
            session.session_id,
            user_id=user_id,
            step_id=session.current_step.id,
            payload={"utterance": "I am just starting and want a clear study path."},
            db_path=db_path,
        )
    )
    broken_step_data = dict(session.step_data)
    broken_step_data.pop("next_question", None)
    broken_step_data.pop("_dialogue_fusion", None)
    broken_step_data["asked_questions"] = []
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE profile_sessions
            SET step_data_json = ?
            WHERE session_id = ?
            """,
            (json.dumps(broken_step_data, ensure_ascii=False), session.session_id),
        )

    reloaded = modes_module.get_profile_session(session.session_id, user_id=user_id, db_path=db_path)

    assert reloaded.current_step is not None
    assert reloaded.current_step.prompt
    assert reloaded.current_step.prompt in reloaded.step_data["asked_questions"]


def test_dialogue_mock_mode_does_not_store_utterance_in_objective_signal(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-clean.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)
    first = _start("dialogue", user_id="dialogue-clean-user")

    payload = _answer(
        first["session_id"],
        "dialogue_turn_1",
        {"utterance": "I am preparing for exams and want to strengthen the basics."},
    )

    assert payload["evidence_cards"]
    assert all("utterance" not in card["objective_signal"] for card in payload["evidence_cards"])


def test_dialogue_chat_sse_event_order_and_dark_axes(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-sse.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)
    monkeypatch.setattr(modes_module, "ModelRouter", _streaming_router_factory())
    first = _start("dialogue", user_id="dialogue-sse-user")

    response = client.post(
        "/api/profile/dialogue/chat",
        json={"session_id": first["session_id"], "utterance": "我完全不知道，没接触过中药"},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    event_names = [event for event, _ in events]
    assert event_names[0] == "thinking"
    assert "message" in event_names
    assert event_names.index("message") < event_names.index("evidence")
    assert event_names.index("message_done") < event_names.index("evidence")
    assert event_names[-5:] == ["evidence", "identity_cards", "radar", "state", "done"]

    radar = next(data for event, data in events if event == "radar")
    identity_cards = next(data for event, data in events if event == "identity_cards")
    assert len(identity_cards["items"]) == 7
    assert {item["key"] for item in identity_cards["items"]} == set(modes_module.DIMENSION_LABELS)
    axes = {axis["key"]: axis for axis in radar["axes"]}
    assert axes["knowledge_base"]["status"] != "dark"
    for key in ("learning_goal", "learning_style", "weak_points", "pace", "motivation", "resource_preference"):
        assert axes[key]["status"] == "dark"


def test_dialogue_chat_extraction_error_still_emits_visible_done(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-sse-error.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path

    async def broken_extract(**kwargs):
        raise RuntimeError("extract boom")

    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)
    monkeypatch.setattr(modes_module, "ModelRouter", _streaming_router_factory())
    monkeypatch.setattr(modes_module, "_dialogue_extract_cards_for_chat", broken_extract)
    first = _start("dialogue", user_id="dialogue-error-user")

    response = client.post(
        "/api/profile/dialogue/chat",
        json={"session_id": first["session_id"], "utterance": "I want to learn slowly."},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    event_names = [event for event, _ in events]
    assert "message_done" in event_names
    assert "error" in event_names
    assert event_names.index("message_done") < event_names.index("error")
    assert event_names[-1] == "done"
    state = next(data for event, data in events if event == "state")
    assert state["finished"] is False


def test_dialogue_finish_uses_score_profile_jury_path(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-sse-jury.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    calls = {"score_profile": 0}
    real_score_profile = modes_module.score_profile

    async def spy_score_profile(cards, **kwargs):
        calls["score_profile"] += 1
        return await real_score_profile(cards, settings=Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None))

    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)
    monkeypatch.setattr(modes_module, "_dialogue_converged", lambda *args, **kwargs: True)
    monkeypatch.setattr(modes_module, "ModelRouter", _streaming_router_factory())
    monkeypatch.setattr(modes_module, "score_profile", spy_score_profile)
    first = _start("dialogue", user_id="dialogue-jury-user")

    response = client.post(
        "/api/profile/dialogue/chat",
        json={"session_id": first["session_id"], "utterance": "我完全不知道，没接触过中药"},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    state = next(data for event, data in events if event == "state")
    assert state["converged"] is True
    assert state["finished"] is False
    assert calls["score_profile"] == 0

    finish = client.post("/api/profile/session/finish", json={"session_id": first["session_id"]})

    assert finish.status_code == 200, finish.text
    assert calls["score_profile"] == 1
    assert finish.json()["finished"] is True
    assert "radar_changes" in finish.json()
    assert "summary_text" in finish.json()


def test_dialogue_finish_radar_changes_from_blank_baseline(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-radar-new.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)
    monkeypatch.setattr(modes_module, "ModelRouter", _streaming_router_factory())

    async def final_profile(user_id: str, evidence_cards: list[EvidenceCard]):
        profile = create_blank_profile(user_id)
        profile.knowledge_base.score = 30
        profile.knowledge_base.confidence = 0.8
        profile.knowledge_base.evidence = [card for card in evidence_cards if card.dimension == "knowledge_base"]
        return profile

    monkeypatch.setattr(modes_module, "_profile_from_dialogue_scoring", final_profile)
    first = _start("dialogue", user_id="dialogue-radar-new-user")
    response = client.post(
        "/api/profile/dialogue/chat",
        json={"session_id": first["session_id"], "utterance": "I have no foundation yet."},
    )
    assert response.status_code == 200, response.text

    finish = client.post("/api/profile/session/finish", json={"session_id": first["session_id"]})

    changes = {change["key"]: change for change in finish.json()["radar_changes"]}
    assert changes["knowledge_base"]["newly_lit"] is True
    assert changes["knowledge_base"]["from_score"] == 0
    assert changes["knowledge_base"]["to_score"] == 30
    assert changes["knowledge_base"]["delta"] == 30


def test_dialogue_finish_radar_changes_from_existing_baseline(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-radar-existing.db"
    user_id = "dialogue-radar-existing-user"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    existing = create_blank_profile(user_id)
    existing.knowledge_base.score = 20
    existing.knowledge_base.confidence = 0.8
    upsert_profile(existing, db_path=db_path)
    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)
    monkeypatch.setattr(modes_module, "ModelRouter", _streaming_router_factory())

    async def final_profile(user_id: str, evidence_cards: list[EvidenceCard]):
        profile = create_blank_profile(user_id)
        profile.knowledge_base.score = 45
        profile.knowledge_base.confidence = 0.8
        profile.knowledge_base.evidence = [card for card in evidence_cards if card.dimension == "knowledge_base"]
        return profile

    monkeypatch.setattr(modes_module, "_profile_from_dialogue_scoring", final_profile)
    first = _start("dialogue", user_id=user_id)
    response = client.post(
        "/api/profile/dialogue/chat",
        json={"session_id": first["session_id"], "utterance": "I want a stronger foundation."},
    )
    assert response.status_code == 200, response.text

    finish = client.post("/api/profile/session/finish", json={"session_id": first["session_id"]})

    changes = {change["key"]: change for change in finish.json()["radar_changes"]}
    assert changes["knowledge_base"]["newly_lit"] is False
    assert changes["knowledge_base"]["from_score"] == 20
    assert changes["knowledge_base"]["to_score"] == 45
    assert changes["knowledge_base"]["delta"] == 25


def test_dialogue_llm_extractor_reads_full_history_and_disables_thinking(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class CapturingRouter:
        async def chat(self, task_type: str, messages: list[dict[str, str]], **kw: object) -> str:
            calls.append({"task_type": task_type, "messages": messages, "kw": kw})
            return json.dumps(
                {
                    "extraction": {
                        "major": {
                            "value": "中药学",
                            "confidence": 0.9,
                            "basis": "explicit",
                            "evidence": ["我是中药学学生"],
                        },
                        "learning_goal": {
                            "value": {"应试提分": 0.6, "兴趣科普": 0.4},
                            "confidence": 0.55,
                            "basis": "inferred",
                            "evidence": ["可能要考试"],
                        },
                    }
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(modes_module, "ModelRouter", CapturingRouter)
    history = "assistant: 你为什么想学？\nuser: 我是中药学学生\nassistant: 更偏什么目标？\nuser: 可能要考试"

    extraction, next_question = asyncio.run(
        modes_module._try_llm_dialogue_turn(
            "可能要考试",
            "learning_goal",
            2,
            [],
            {},
            dialogue_history=history,
        )
    )

    assert next_question == ""
    assert extraction.major.value == "中药学"
    assert extraction.learning_goal.value == {"应试提分": 0.6, "兴趣科普": 0.4}
    assert calls[0]["task_type"] == "profile_extract"
    assert calls[0]["kw"]["temperature"] == 0
    assert calls[0]["kw"]["thinking"] == {"type": "disabled"}
    payload = json.loads(calls[0]["messages"][1]["content"].split("\n", 1)[0])
    assert "我是中药学学生" in payload["dialogue_history"]
    assert "可能要考试" in payload["dialogue_history"]


def test_json_object_rejects_mock_prefix() -> None:
    from app.profile.nodes import _json_object

    with pytest.raises(ValueError):
        _json_object("[MOCK:deepseek-chat] not structured JSON")


def test_dialogue_target_rotates_to_supplemental_dimensions_after_thresholds() -> None:
    cards = [
        _evidence_card("learning_goal", {"label": ALLOWED_LABELS["learning_goal"][0]}, weight=1.0),
        _evidence_card("motivation", {"label": ALLOWED_LABELS["motivation"][0], "motivation_score": 75}, weight=1.0),
        _evidence_card("knowledge_base", {"prior": True, "base_level": "较好"}, weight=1.0),
    ]
    fusion = modes_module._dialogue_fusion(cards)

    target = modes_module._dialogue_target(
        cards,
        fusion=fusion,
        asked_dimensions=["learning_goal", "motivation", "knowledge_base"],
    )
    next_target = modes_module._dialogue_target(
        cards,
        fusion=fusion,
        asked_dimensions=["learning_goal", "motivation", "knowledge_base", target],
    )

    assert target == "learning_style"
    assert next_target == "weak_points"


def test_dialogue_target_rotates_after_repeated_unfilled_dimension() -> None:
    cards = [
        _evidence_card("knowledge_base", {"prior": True, "base_level": "较好"}, weight=1.0),
        _evidence_card("learning_goal", {"label": ALLOWED_LABELS["learning_goal"][0]}, weight=1.0),
        _evidence_card("learning_style", {"label": ALLOWED_LABELS["learning_style"][0]}, weight=1.0),
        _evidence_card("pace", {"daily_minutes": 30, "target_days": 14}, weight=1.0),
        _evidence_card("motivation", {"label": ALLOWED_LABELS["motivation"][0], "motivation_score": 75}, weight=1.0),
        _evidence_card("resource_preference", {"label": ALLOWED_LABELS["resource_preference"][0]}, weight=1.0),
    ]
    fusion = modes_module._dialogue_fusion(cards)

    target = modes_module._dialogue_target(
        cards,
        fusion=fusion,
        asked_dimensions=["weak_points", "weak_points", "weak_points"],
    )

    assert target != "weak_points"


def test_dialogue_companion_steering_prompt_receives_current_target_and_confidences() -> None:
    messages = modes_module._dialogue_companion_messages(
        history_messages=[],
        utterance="最近换季总是犯困，想先聊聊养生。",
        target="pace",
        evidence_cards=[],
        fusion=modes_module._dialogue_fusion([]),
        current_confidences={"pace": 0.0, "learning_goal": 0.6},
        pure_chat_streak=2,
    )

    steering = json.loads(messages[1]["content"].removeprefix("profile_steering="))
    assert steering["target_dimension"] == "pace"
    assert steering["target_label"] == "学习节奏"
    assert steering["current_confidences"]["pace"] == 0.0
    assert "连续纯寒暄/纯养生闲聊不得超过 2 轮" in messages[0]["content"]
    assert "每天学习分钟数/目标天数" in messages[0]["content"]


def test_dialogue_visible_reply_dimension_classifier_handles_rephrased_target_questions() -> None:
    assert (
        modes_module._dialogue_visible_reply_asked_dimension(
            "如果按你的日常安排，多久能稳定留一小段给学习？", "pace"
        )
        == "pace"
    )
    assert (
        modes_module._dialogue_visible_reply_asked_dimension(
            "哪种呈现方式最能帮你理解，一张关系图、案例拆解还是顺口的记忆句？",
            "learning_style",
        )
        == "learning_style"
    )


def test_dialogue_visible_stream_defers_unhit_target_without_second_question(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-steering.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    captured_messages: list[list[dict[str, str]]] = []

    class PureChatRouter:
        async def chat_stream(self, task_type: str, messages: list[dict[str, str]], **kw: object):
            captured_messages.append(messages)
            yield "message", "换季犯困确实会让人提不起劲，先把睡眠和饮食稳一稳。"

    monkeypatch.setattr(modes_module, "ModelRouter", PureChatRouter)
    first = _start("dialogue", user_id="dialogue-steering-user")
    _override_current_user("dialogue-steering-user")

    response = client.post(
        "/api/profile/dialogue/chat",
        json={"session_id": first["session_id"], "utterance": "最近换季总犯困，想聊聊养生。"},
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    message_text = "".join(str(data.get("delta") or "") for event, data in events if event == "message")
    assert "中医药基础大概自评到哪" not in message_text
    state = next(data for event, data in events if event == "state")
    assert state["target"] == "knowledge_base"
    steering = json.loads(captured_messages[0][1]["content"].removeprefix("profile_steering="))
    assert steering["target_dimension"] == "knowledge_base"


def test_dialogue_visible_stream_answers_evidence_requests(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-evidence-request.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path

    class VagueRouter:
        async def chat_stream(self, task_type: str, messages: list[dict[str, str]], **kw: object):
            yield "message", "这个问题我们可以慢慢看，我先陪你把思路理顺。"

    monkeypatch.setattr(modes_module, "ModelRouter", VagueRouter)
    first = _start("dialogue", user_id="dialogue-evidence-user")
    _override_current_user("dialogue-evidence-user")

    response = client.post(
        "/api/profile/dialogue/chat",
        json={"session_id": first["session_id"], "utterance": "请列出画像评分证据，凭什么这么打分？"},
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    message_text = "".join(str(data.get("delta") or "") for event, data in events if event == "message")
    assert "证据清单" in message_text
    assert "原文引用" in message_text


def test_dialogue_scripted_answers_collect_six_axes_with_traceable_quotes() -> None:
    from app.profile.nodes import _dialogue_cards, _fallback_dialogue_extraction

    utterances = [
        "我是中药学学生，基础一般，目标是期末考试，喜欢图表。",
        "我还是想考试提分，平时看图表会更快。",
        "这件事主要是考试推动，但自己也想学明白。",
        "考研也有压力，所以现在确实是考试推动。",
        "资源上我最想先看知识图谱，知识图谱能帮我理结构。",
        "如果继续补材料，我还是会优先打开知识图谱。",
        "接下来每天30分钟，先坚持14天。",
    ]
    cards = []
    for utterance in utterances:
        cards.extend(_dialogue_cards(_fallback_dialogue_extraction(utterance)))

    fusion = modes_module._dialogue_fusion(cards)
    progress = modes_module._dialogue_profile_progress(cards, fusion=fusion)
    evidence_panel = modes_module._dialogue_evidence_panel(cards, fusion=fusion)
    source_text = "\n".join(utterances)

    assert progress["collected"] >= 6
    for item in evidence_panel:
        if item["status"] != "已取证":
            continue
        assert item["value"] != "待取证"
        assert item["quotes"]
        assert any(quote["text"] in source_text for quote in item["quotes"])


def test_dialogue_extract_cards_for_chat_collects_weak_points_with_traceable_quote(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(modes_module, "get_settings", lambda: Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None))
    session = modes_module.start_profile_session("dialogue-weak-user", "dialogue", db_path=tmp_path / "weak.db")
    utterance = "我总记不住归经，分不清相似药，方剂配伍也老错。"

    cards = asyncio.run(
        modes_module._dialogue_extract_cards_for_chat(
            session=session,
            utterance=utterance,
            assistant_text="我听到了几个卡点，我们慢慢拆。",
            history_messages=[],
            target="weak_points",
            turn_index=1,
            response_time_ms=1200,
            current_prompt="最近哪个知识点最常让你不确定？",
        )
    )
    weak_cards = [card for card in cards if card.dimension == "weak_points"]
    fusion = modes_module._dialogue_fusion(cards)
    progress = modes_module._dialogue_profile_progress(cards, fusion=fusion)

    assert len(weak_cards) == 1
    assert weak_cards[0].objective_signal["label"] in utterance
    assert all(quote in utterance for quote in weak_cards[0].objective_signal["evidence"])
    assert any(item["key"] == "weak_points" and item["collected"] for item in progress["dimensions"])

    no_cards = asyncio.run(
        modes_module._dialogue_extract_cards_for_chat(
            session=session,
            utterance="最近换季容易犯困，想喝点温水早点睡，先聊聊养生就好。",
            assistant_text="先把作息稳住也很好。",
            history_messages=[],
            target="weak_points",
            turn_index=2,
            response_time_ms=900,
            current_prompt="最近哪个知识点最常让你不确定？",
        )
    )
    assert [card for card in no_cards if card.dimension == "weak_points"] == []


def test_dialogue_real_extract_path_collects_all_seven_axes_with_quotes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(modes_module, "get_settings", lambda: Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None))
    session = modes_module.start_profile_session("dialogue-seven-user", "dialogue", db_path=tmp_path / "seven.db")
    utterances = [
        "我是中药学学生，基础一般，目标是期末考试，喜欢图表。",
        "这件事主要是考试推动，但自己也想学明白。",
        "资源上我最想先看知识图谱，知识图谱能帮我理结构。",
        "如果继续补材料，我还是会优先打开知识图谱。",
        "接下来每天30分钟，先坚持14天。",
        "我总记不住归经，分不清相似药，方剂配伍也老错。",
    ]

    cards: list[EvidenceCard] = []
    for index, utterance in enumerate(utterances, start=1):
        session.evidence_cards = cards
        cards.extend(
            asyncio.run(
                modes_module._dialogue_extract_cards_for_chat(
                    session=session,
                    utterance=utterance,
                    assistant_text="我记下了，继续顺着你的情况看。",
                    history_messages=[],
                    target=modes_module._dialogue_target(cards),
                    turn_index=index,
                    response_time_ms=1000,
                    current_prompt="画像问题",
                )
            )
        )

    fusion = modes_module._dialogue_fusion(cards)
    progress = modes_module._dialogue_profile_progress(cards, fusion=fusion)
    evidence_panel = modes_module._dialogue_evidence_panel(cards, fusion=fusion)
    source_text = "\n".join(utterances)

    assert progress["collected"] == 7
    for item in evidence_panel:
        assert item["status"] == "已取证"
        assert item["quotes"]
        assert any(quote["text"] in source_text for quote in item["quotes"])


def test_dialogue_identity_cards_distribution_ratios_and_skins_from_real_cards(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(modes_module, "get_settings", lambda: Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None))
    session = modes_module.start_profile_session("dialogue-cards-user", "dialogue", db_path=tmp_path / "cards.db")
    utterances = [
        "我还是想考试提分，平时看图表会更快。",
        "我也希望把中药讲给家里老人听，先学会调理脾胃。",
        "这件事主要是考试推动，但自己也想学明白。",
        "资源上我最想先看知识图谱，知识图谱能帮我理结构。",
        "接下来每天30分钟，先坚持14天。",
        "我总记不住归经，分不清相似药，方剂配伍也老错。",
    ]

    cards: list[EvidenceCard] = []
    for index, utterance in enumerate(utterances, start=1):
        session.evidence_cards = cards
        cards.extend(
            asyncio.run(
                modes_module._dialogue_extract_cards_for_chat(
                    session=session,
                    utterance=utterance,
                    assistant_text="我记下了。",
                    history_messages=[],
                    target=modes_module._dialogue_target(cards),
                    turn_index=index,
                    response_time_ms=1000,
                    current_prompt="画像问题",
                )
            )
        )

    fusion = modes_module._dialogue_fusion(cards)
    view = modes_module._dialogue_identity_cards_view(cards, fusion=fusion, revealed_flags={})
    by_key = {item["key"]: item for item in view}
    goal_cards = by_key["learning_goal"]["cards"]
    resource_cards = by_key["resource_preference"]["cards"]
    motivation_cards = by_key["motivation"]["cards"]

    assert round(sum(card["ratio"] for card in goal_cards), 4) == 1.0
    assert any(card["canonical_label"] == "其他" for card in goal_cards)
    other = next(card for card in goal_cards if card["canonical_label"] == "其他")
    assert other["display_name"] in utterances[1]
    assert other["quotes"] and all(quote in "\n".join(utterances) for quote in other["quotes"])
    assert any(card["display_name"] == "观脉者" for card in resource_cards)
    assert any(card["display_name"] in {"赶考人", "双修者"} for card in motivation_cards)


def test_dialogue_identity_cards_level_names_align_fusion_anchors() -> None:
    cards = [
        _evidence_card("knowledge_base", {"prior": True, "label": "较好", "evidence": ["基础较好"]}, weight=1.0),
        _evidence_card("pace", {"daily_minutes": 45, "target_days": 14, "evidence": ["每天45分钟", "14天"]}, weight=1.0),
    ]
    fusion = modes_module._dialogue_fusion(cards)
    view = modes_module._dialogue_identity_cards_view(cards, fusion=fusion, revealed_flags={})
    by_key = {item["key"]: item for item in view}

    assert fusion["knowledge_base"].score == 70
    assert by_key["knowledge_base"]["cards"][0]["level_name"] == "杏林手"
    assert fusion["pace"].score == 85
    assert by_key["pace"]["cards"][0]["level_name"] == "武火急煎"


def test_dialogue_identity_cards_reveal_threshold_and_no_rollback() -> None:
    flags: dict[str, bool] = {}
    low_cards = [
        _evidence_card("learning_goal", {"label": "应试提分", "evidence": ["可能要考试"]}, weight=0.2),
    ]
    high_cards = [
        _evidence_card("learning_goal", {"label": "应试提分", "evidence": ["明确要考试"]}, weight=1.0),
    ]

    low_view = modes_module._dialogue_identity_cards_view(
        low_cards,
        fusion=modes_module._dialogue_fusion(low_cards),
        revealed_flags=flags,
    )
    high_view = modes_module._dialogue_identity_cards_view(
        high_cards,
        fusion=modes_module._dialogue_fusion(high_cards),
        revealed_flags=flags,
    )
    rollback_view = modes_module._dialogue_identity_cards_view(
        low_cards,
        fusion=modes_module._dialogue_fusion(low_cards),
        revealed_flags=flags,
    )

    assert {item["key"]: item for item in low_view}["learning_goal"]["revealed"] is False
    assert {item["key"]: item for item in high_view}["learning_goal"]["revealed"] is True
    assert {item["key"]: item for item in rollback_view}["learning_goal"]["revealed"] is True


def test_dialogue_evidence_panel_marks_pending_dimensions_without_fabricating_quotes() -> None:
    panel = modes_module._dialogue_evidence_panel([], fusion=modes_module._dialogue_fusion([]))
    progress = modes_module._dialogue_profile_progress([], fusion=modes_module._dialogue_fusion([]))

    assert progress["collected"] == 0
    assert progress["total"] == 7
    assert all(item["status"] == "待取证" for item in panel)
    assert all(item["quotes"] == [] for item in panel)


def test_session_survives_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "restart.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    first = _start("apprentice", user_id="restart-user")
    session_id = first["session_id"]

    advanced = _answer(
        session_id,
        APPRENTICE_STEPS[0],
        {"identity_card": "临床学徒", "response_time_ms": 1200},
    )
    assert advanced["step"]["id"] == APPRENTICE_STEPS[1]

    with sqlite3.connect(db_path) as conn:
        stored = conn.execute(
            """
            SELECT user_id, mode, current_step, step_index, finished, evidence_cards_json
            FROM profile_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    assert stored is not None
    assert stored[0] == "restart-user"
    assert stored[1] == "apprentice"
    assert stored[2] == 1
    assert stored[3] == 1
    assert stored[4] == 0
    assert stored[5] != "[]"

    questions = advanced["step"]["payload"]["questions"]
    resumed = _answer(
        session_id,
        APPRENTICE_STEPS[1],
        {
            "answers": [
                {"question_id": questions[0]["id"], "selected": questions[0]["options"][0], "response_time_ms": 3000}
            ]
        },
    )
    finished = client.post("/api/profile/session/finish", json={"session_id": session_id})
    stored_evidence = list_evidence("restart-user", db_path=db_path)

    assert resumed["step"]["id"] == APPRENTICE_STEPS[2]
    assert finished.status_code == 200
    assert finished.json()["finished"] is True
    assert len(stored_evidence) == len(finished.json()["evidence_cards"])
    assert len(stored_evidence) >= 4


def test_session_cross_user_404(tmp_path: Path) -> None:
    db_path = tmp_path / "cross-user.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    first = _start("practice", user_id="owner-user")
    _override_current_user("intruder-user")

    answer = client.post(
        "/api/profile/session/answer",
        json={
            "session_id": first["session_id"],
            "step_id": "practice",
            "payload": {"case": "热毒咽痛", "prescription": "金银花15g"},
        },
    )
    finish = client.post("/api/profile/session/finish", json={"session_id": first["session_id"]})

    assert answer.status_code == 404
    assert answer.json()["detail"] == "profile session not found"
    assert finish.status_code == 404
    assert finish.json()["detail"] == "profile session not found"


def test_session_not_in_memory() -> None:
    source = inspect.getsource(modes_module)

    assert not hasattr(modes_module, "_SESSIONS")
    assert "_SESSIONS" not in source
    assert "dict[str, ProfileSession]" not in source


def test_prescription_signal_falls_back_to_rule_parser_without_key() -> None:
    import asyncio

    signal = asyncio.run(
        extract_prescription_signal(
            {"case": "风寒表证，恶寒无汗", "prescription": "麻黄6g，桂枝6g"}
        )
    )

    assert signal == {"对症": True, "配伍": True, "剂量": True, "误用毒烈": False}


def test_prescription_signal_llm_extract_uses_flash_with_thinking(monkeypatch) -> None:
    import asyncio

    settings = Settings(DEEPSEEK_API_KEY="test-key", ZHIPU_API_KEY=None, MOCK_STREAM_DELAY=0.0)
    calls: list[dict[str, object]] = []

    class CapturingRouter:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def chat(self, task_type: str, messages: list[dict[str, str]], **kw: object) -> str:
            calls.append(
                {
                    "task_type": task_type,
                    "thinking": kw.get("thinking"),
                    "reasoning_effort": kw.get("reasoning_effort"),
                }
            )
            return '{"对症": true, "配伍": true, "剂量": false, "误用毒烈": false}'

    monkeypatch.setattr(modes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(modes_module, "ModelRouter", CapturingRouter)

    signal = asyncio.run(
        modes_module.extract_prescription_signal(
            {"case": "风寒表证，恶寒无汗", "prescription": "麻黄6g，桂枝0.5g"}
        )
    )

    assert signal == {"对症": True, "配伍": True, "剂量": False, "误用毒烈": False}
    assert calls == [
        {
            "task_type": "profile_extract",
            "thinking": {"type": "enabled"},
            "reasoning_effort": "medium",
        }
    ]


def test_finish_defaults_to_persisting_cards_and_reads_back_from_temp_db(tmp_path: Path) -> None:
    db_path = tmp_path / "finish-default.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    first = _start("practice", user_id="persist-user")
    _answer(first["session_id"], "practice", {"case": "热毒咽痛", "prescription": "金银花15g，连翘12g"})

    session = finish_profile_session(first["session_id"], user_id="persist-user", db_path=db_path)
    stored = list_evidence("persist-user", db_path=db_path)

    assert session.persisted is True
    assert len(stored) == len(session.evidence_cards) == 1
    assert stored[0] == session.evidence_cards[0]


def test_repeated_finish_does_not_duplicate_evidence_cards(tmp_path: Path) -> None:
    db_path = tmp_path / "idempotent.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    first = _start("quickcard", user_id="retry-user")
    question = first["step"]["payload"]["questions"][0]
    _answer(
        first["session_id"],
        "quickcard",
        {
            "identity_card": "考前冲刺",
            "answers": [{"question_id": question["id"], "selected": "故意选错"}],
        },
    )

    first_finish = client.post("/api/profile/session/finish", json={"session_id": first["session_id"]})
    second_finish = client.post("/api/profile/session/finish", json={"session_id": first["session_id"]})
    stored = list_evidence("retry-user", db_path=db_path)

    assert first_finish.status_code == 200
    assert second_finish.status_code == 200
    assert first_finish.json()["evidence_cards"] == second_finish.json()["evidence_cards"]
    assert len(stored) == len(first_finish.json()["evidence_cards"])


def test_diagnostic_questions_come_from_kb_and_fallback_when_empty(tmp_path: Path) -> None:
    questions = load_diagnostic_questions(limit=5)
    assert questions
    assert any(str(question["id"]).startswith("kb_") for question in questions)

    empty_dir = tmp_path / "empty-herbs"
    empty_dir.mkdir()
    fallback = load_diagnostic_questions(limit=2, data_dir=empty_dir)
    assert len(fallback) == 2
    assert all(str(question["id"]).startswith("fallback_") for question in fallback)


def test_diagnostic_eval_no_longer_defaults_to_all_correct() -> None:
    import asyncio

    state = initial_state("student-no-default", "我要建立画像")
    assert state["diagnostic_answers"] == {}

    state = asyncio.run(diagnostic_question_node(state))
    evaluated = asyncio.run(diagnostic_eval_node(state))

    assert evaluated["quiz_features"]["correct_count"] == 0
    assert evaluated["quiz_features"]["score"] == 0
    assert len(evaluated["quiz_features"]["weak_points"]) == len(evaluated["questions"])


def _evidence_card(dimension: DimensionKey, objective_signal: dict[str, object], *, weight: float) -> EvidenceCard:
    return EvidenceCard(
        dimension=dimension,
        source="test",
        fact="test evidence",
        objective_signal=objective_signal,
        weight=weight,
        timestamp=datetime.now(UTC),
    )


def _start(mode: str, user_id: str = "student-001") -> dict[str, object]:
    _override_current_user(user_id)
    response = client.post("/api/profile/session/start", json={"user_id": user_id, "mode": mode})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "start"
    assert payload["step"] is not None
    return payload


def _answer(session_id: str, step_id: str, payload: dict[str, object]) -> dict[str, object]:
    response = client.post(
        "/api/profile/session/answer",
        json={"session_id": session_id, "step_id": step_id, "payload": payload},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _override_current_user(user_id: str) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        user_id=user_id,
        username=user_id,
        display_name=user_id,
        is_guest=False,
        created_at="2026-07-06T00:00:00+00:00",
    )


def _parse_sse(text: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in text.strip().split("\n\n"):
        event = ""
        data = "{}"
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        if event:
            events.append((event, json.loads(data)))
    return events


def _streaming_router_factory():
    class StreamingRouter:
        async def chat_stream(self, task_type: str, messages: list[dict[str, str]], **kw: object):
            yield ("thinking", "thinking")
            for token in "好的，我们慢慢来。":
                yield ("message", token)

    return StreamingRouter
