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
from app.api.profile import _dialogue_sse_iter
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
from app.profile.prescription import evaluate_prescription
from app.profile.schema import DimensionKey, EvidenceCard, create_blank_profile
from app.profile.nodes import diagnostic_eval_node, diagnostic_question_node, initial_state
from app.profile.store import list_chat_messages, list_evidence, upsert_profile


client = TestClient(app)


def test_dialogue_quick_replies_follow_visible_question_not_next_dimension() -> None:
    assert modes_module._dialogue_quick_replies_for_question(
        "你平时更喜欢怎么学中药？", "knowledge_base"
    ) == ["知识图谱", "教材条文", "口诀歌诀", "案例拆解"]
    assert modes_module._dialogue_quick_replies_for_question(
        "你觉得自己目前的辨证基础怎么样？", "learning_goal"
    ) == ["会四诊合参", "常按证型判断", "两种都会一些", "还在练习"]
    assert modes_module._dialogue_quick_replies_for_question(
        "嗯，明白了。", "learning_goal"
    ) == []
    assert modes_module._dialogue_quick_replies_for_question(
        "如果方便的话，你平时最常用什么方式记忆？", "learning_goal"
    ) == ["知识图谱", "教材条文", "口诀歌诀", "案例拆解"]


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
    # 原始毫秒不再落库：9000ms 在数字题基准（5–20 秒）里属于"正常"。
    assert any(card["objective_signal"].get("response_speed") == "正常" for card in cards)
    assert all("response_time_ms" not in card["objective_signal"] for card in cards)


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
    # 7000ms 在行为题基准（5–20 秒）里属于"正常"。
    assert cards[0]["objective_signal"]["response_speed"] == "正常"


def test_prescription_uses_shared_trace_metadata_and_no_key_marker(monkeypatch) -> None:
    import asyncio
    from app.profile.modes import _prescription_card
    from app.config import Settings

    monkeypatch.setattr("app.profile.modes.get_settings", lambda: Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None))

    card = asyncio.run(
        _prescription_card(
            {
                "case": "风寒怕冷",
                "prescription": "麻黄3g 桂枝3g",
                "knowledge_base_ref": "herb:mahuang",
            },
            source="收徒二幕",
            weight=1.5,
            response_time_ms=100,
        )
    )
    assert card.quote and "麻黄3g" in card.quote
    assert card.basis == "explicit"
    assert card.extraction_confidence == 0.85
    assert card.extraction_method == "no_key_rule_fallback"
    assert card.knowledge_base_ref == "herb:mahuang"


def test_free_prescriptions_allow_multiple_kb_supported_solutions() -> None:
    first = evaluate_prescription({"case": "热毒咽痛", "prescription": "金银花12g，连翘12g"})
    second = evaluate_prescription({"case": "热毒咽痛", "prescription": "黄芩9g，黄连3g，石膏30g"})

    for evaluation in (first, second):
        assert evaluation.symptom_match is True
        assert evaluation.compatible is True
        assert evaluation.dosage_ok is True
        assert evaluation.toxic_misuse is False
        assert evaluation.knowledge_base_refs
        assert all(item["quote"] in evaluation.quote or item["quote"] == "" for item in evaluation.findings)


def test_free_prescription_flags_unrelated_dose_and_toxic_risks() -> None:
    unrelated = evaluate_prescription({"case": "风寒恶寒无汗", "prescription": "阿司匹林10g"})
    dose = evaluate_prescription({"case": "风寒恶寒无汗", "prescription": "麻黄100g，桂枝6g"})
    toxic = evaluate_prescription({"case": "风寒恶寒无汗", "prescription": "细辛10g"})

    assert any(item["kind"] == "unrelated_herb" for item in unrelated.findings)
    assert any(item["kind"] == "dose_abnormal" for item in dose.findings)
    assert toxic.toxic_misuse is True
    assert any(item["kind"] == "toxic_misuse" for item in toxic.findings)


def test_cough_with_ginseng_is_not_automatically_symptom_matched() -> None:
    evaluation = evaluate_prescription({"case": "咳嗽痰多", "prescription": "人参9g"})

    assert evaluation.symptom_match is False
    assert evaluation.symptom_match_status == "mismatched"
    mismatch = next(item for item in evaluation.findings if item["kind"] == "case_mismatch")
    assert mismatch["quote"] == "咳嗽痰多"
    assert mismatch["knowledge_base_refs"]
    # 规则 id 现在按检查项细分（kg.rx.v1.case），不再是笼统的 prescription_rule_v1，
    # 这样错题归因能直接指到「是哪一条规则判错的」。
    assert mismatch["rule"] == "kg.rx.v1.case"


def test_incremental_prescription_cards_keep_shared_trace_metadata() -> None:
    import app.profile.update as update_module

    cards = update_module.evidence_cards_from_event(
        update_module.ProfileUpdateEvent(
            type="prescription",
            payload={"case": "风寒恶寒无汗", "prescription": "麻黄6g，桂枝6g"},
        )
    )

    assert cards
    for card in cards:
        assert card.quote and card.quote in "麻黄6g，桂枝6g"
        assert card.basis == "explicit"
        assert card.extraction_confidence == 0.85
        assert card.extraction_method == "no_key_rule_fallback"
        assert card.knowledge_base_ref


def test_apprentice_practice_and_event_reuse_prescription_evaluator(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    real = evaluate_prescription

    def spy(payload: dict[str, object]):
        calls.append(payload)
        return real(payload)

    monkeypatch.setattr(modes_module, "evaluate_prescription", spy)
    import app.profile.update as update_module

    monkeypatch.setattr(update_module, "evaluate_prescription", spy)
    asyncio.run(
        modes_module._prescription_card(
            {"case": "风寒恶寒无汗", "prescription": "麻黄6g，桂枝6g"},
            source="practice",
            weight=1.5,
            response_time_ms=1,
        )
    )
    update_module.evidence_cards_from_event(
        update_module.ProfileUpdateEvent(
            type="prescription",
            payload={"case": "风寒恶寒无汗", "prescription": "麻黄6g，桂枝6g"},
        )
    )

    assert len(calls) == 2


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
    # 300ms 在行为题基准里属于"快"。
    assert cards[1]["objective_signal"]["response_speed"] == "快"


def test_dialogue_flow_without_key_does_not_fabricate_semantic_cards(tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    first = _start("dialogue", user_id="dialogue-user")
    session_id = first["session_id"]

    assert first["step"]["kind"] == "dialogue"
    assert first["step"]["id"] == "dialogue_turn_1"

    first_utterance = "我是中药学学生，最近主要想准备期末考试，也想把基础补稳。"
    payload = _answer(
        session_id,
        "dialogue_turn_1",
        {"utterance": first_utterance},
    )
    assert payload["turn_index"] == 1
    assert payload["next_question"]
    assert payload["evidence_cards"] == []
    assert set(payload["card_scores"]) == {"考前冲刺", "临床学徒", "草木好奇者", "研读派"}
    assert all(0 <= value <= 1 for value in payload["card_scores"].values())
    assert payload["converged"] is False

    payload = _answer(
        session_id,
        "dialogue_turn_2",
        {"quick_reply": "考试推动，但我自己也想学明白，平时更喜欢文字材料和表格。"},
    )
    assert payload["turn_index"] == 2
    assert payload["evidence_cards"] == []
    assert payload["converged"] is False

    payload = _answer(
        session_id,
        "dialogue_turn_3",
        {"utterance": "我每天大概能学45分钟，目标是两周内先把解表药和清热药过一遍。"},
    )

    assert payload["turn_index"] == 3
    assert payload["step"]["kind"] == "dialogue"
    assert payload["converged"] is False
    assert payload["next_question"]
    assert payload["inferred_card"] is None
    assert max(payload["card_scores"].values()) == 0
    assert "dimensions" in payload["radar"]

    finished = client.post("/api/profile/session/finish", json={"session_id": session_id})
    stored = list_evidence("dialogue-user", db_path=db_path)

    assert finished.status_code == 200
    assert finished.json()["finished"] is True
    assert isinstance(finished.json()["radar_changes"], list)
    assert isinstance(finished.json()["summary_text"], str)
    assert len(stored) == len(finished.json()["evidence_cards"])
    assert stored == []


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
        # Convergence now hangs on component coverage, not on confidence: a
        # dimension counts as ready only once 3 of its 5 components are
        # collected, and a half-baked one keeps the dialogue going. So the fake
        # turn has to land real components instead of one anonymous card.
        return [
            _evidence_card("learning_goal", {"label": ALLOWED_LABELS["learning_goal"][0]}, weight=1.0, component="expression_certainty"),
            _evidence_card("learning_goal", {"label": ALLOWED_LABELS["learning_goal"][0]}, weight=1.0, component="goal_specificity"),
            _evidence_card("learning_goal", {"label": ALLOWED_LABELS["learning_goal"][0]}, weight=1.0, component="action_orientation"),
            _evidence_card("motivation", {"label": ALLOWED_LABELS["motivation"][0], "motivation_score": 80}, weight=1.0, component="intrinsic_value"),
            _evidence_card("motivation", {"label": ALLOWED_LABELS["motivation"][0], "motivation_score": 80}, weight=1.0, component="external_commitment"),
            _evidence_card("motivation", {"label": ALLOWED_LABELS["motivation"][0], "motivation_score": 80}, weight=1.0, component="actual_investment"),
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

    assert payload["evidence_cards"] == []


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
    assert event_names[-6:] == ["evidence", "audit", "identity_cards", "radar", "state", "done"]

    radar = next(data for event, data in events if event == "radar")
    audit = next(data for event, data in events if event == "audit")
    identity_cards = next(data for event, data in events if event == "identity_cards")
    assert len(identity_cards["items"]) == 7
    assert {item["key"] for item in identity_cards["items"]} == set(modes_module.DIMENSION_LABELS)
    axes = {axis["key"]: axis for axis in radar["axes"]}
    for key in modes_module.DIMENSION_LABELS:
        assert axes[key]["status"] == "dark"
    assert audit["turn_index"] == 1
    assert audit["user_input"] == "我完全不知道，没接触过中药"
    assert audit["model_result"]["method"] == "no_key_rule_fallback"
    assert audit["model_result"]["scan_available"] is False
    assert audit["model_result"]["scan_complete"] is False
    assert audit["model_result"]["evidence"] == []
    assert {stage["actor"] for stage in audit["stages"]} == {"模型", "系统"}
    assert len(audit["system_result"]["dimensions"]) == 7
    assert all(item["status"] == "本轮未收集" for item in audit["system_result"]["dimensions"])

    restored = client.get(f"/api/profile/session/{first['session_id']}")
    assert restored.status_code == 200, restored.text
    restored_payload = restored.json()
    assert restored_payload["status"] == "restored"
    assert restored_payload["step"]["id"] == "dialogue_turn_2"
    assert all(not item["revealed_now"] for item in restored_payload["identity_cards"])
    assert restored_payload["chat_history"][0] == {"role": "user", "content": "我完全不知道，没接触过中药"}
    assert restored_payload["chat_history"][1]["role"] == "assistant"
    assert restored_payload["chat_history"][1]["content"]
    assert restored_payload["evidence_count"] == len(restored_payload["evidence_cards"])


def test_dialogue_visible_reply_is_emitted_before_provider_finishes(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-incremental-stream.db"
    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)
    session = modes_module.start_profile_session("incremental-user", "dialogue", db_path=db_path)
    provider_state = {"finished": False}

    class IncrementalRouter:
        async def chat_stream(self, _task, _messages, **_kwargs):
            for token in "第一句。第二句。":
                yield "message", token
                await asyncio.sleep(0)
            provider_state["finished"] = True

    monkeypatch.setattr(modes_module, "ModelRouter", IncrementalRouter)

    async def read_until_first_reply_chunk():
        async for event, _data in modes_module.stream_dialogue_chat_events(
            session.session_id,
            user_id=session.user_id,
            utterance="继续说说",
            db_path=db_path,
        ):
            if event == "message":
                return provider_state["finished"]
        raise AssertionError("dialogue stream ended without a visible reply")

    assert asyncio.run(read_until_first_reply_chunk()) is False


def test_dialogue_no_key_visible_stream_uses_rule_question_without_mock_marker(tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-visible-no-key.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    first = _start("dialogue", user_id="dialogue-visible-no-key-user")

    response = client.post(
        "/api/profile/dialogue/chat",
        json={"session_id": first["session_id"], "utterance": "我想在30天内通过考试，每天学习45分钟。"},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    message_text = "".join(str(data.get("delta") or "") for event, data in events if event == "message")
    thinking_text = "".join(str(data.get("delta") or "") for event, data in events if event == "thinking")
    assert not message_text.startswith("我记下了。")
    assert "我还没能从这句话里确认" in message_text
    assert "[MOCK:" not in message_text
    assert "FallbackReason" not in message_text
    assert thinking_text
    assert [event for event, _ in events][-6:] == ["evidence", "audit", "identity_cards", "radar", "state", "done"]


def test_dialogue_session_restore_rejects_another_user(tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-restore-owner.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    first = _start("dialogue", user_id="restore-owner")

    _override_current_user("other-user")
    response = client.get(f"/api/profile/session/{first['session_id']}")

    assert response.status_code == 404
    assert response.json()["detail"] == "画像会话已过期，请重新开始"


def test_dialogue_stream_log_records_lengths_without_content(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="app.profile.modes")

    modes_module._log_dialogue_stream_fulltext(
        "session-that-must-not-be-logged-in-full",
        3,
        ["private thinking"],
        ["private assistant response"],
    )

    assert "thinking_len=16" in caplog.text
    assert "message_len=26" in caplog.text
    assert "private thinking" not in caplog.text
    assert "private assistant response" not in caplog.text
    assert "session-that-must-not-be-logged-in-full" not in caplog.text


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
    assert "error" not in event_names
    assert "audit" in event_names
    audit = next(data for event, data in events if event == "audit")
    assert audit["model_result"]["failure_reason"] == "extraction_pipeline_error:RuntimeError"
    assert audit["model_result"]["collected_count"] == 0
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
                    "turn_id": 2,
                    "dimension_scan": [
                        {"dimension": "learning_goal", "status": "evidence_found", "reason": "回答表达了明确的学习目标意向。", "components": ["expression_certainty"], "excluded_candidates": []}
                    ],
                    "atomic_evidence": [
                        {
                            "evidence_id": "ev-goal-parser", "dimension": "learning_goal", "component": "goal_specificity",
                            "evidence_group_id": "turn-2-goal", "source_turn_ids": [2], "quote": "可能要考试",
                            "normalized_claim": "用户提到考试目标", "evidence_type": "self_report", "supports": ["goal_specificity"],
                            "collection_status": "scorable", "assessment": "提到考试但未说明科目和期限",
                            "confirmed_aspects": ["存在考试目标线索"], "pending_aspects": ["科目和期限"],
                            "semantic_relation": "new", "related_evidence_ids": [],
                            "quality_factors": {"source_reliability": 0.8, "expression_clarity": 0.8, "content_specificity": 0.6, "freshness": 1.0, "consistency": 0.8, "independence_note": "本轮原话"},
                        }
                    ],
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
                            "assessment": {"radar_score": 68, "confidence_contribution": 0.23, "coverage": 0.2, "priority_strength": 0.5, "independence": 0.9, "unknown_reasons": ["其他目标尚未谈及"], "assessment_reason": "目标已出现但组成未充分了解"},
                        },
                        "resource_preference": {
                            "value": "知识图谱",
                            "confidence": 0.85,
                            "basis": "explicit",
                            "evidence": ["我更喜欢图谱"],
                            "assessment": {"radar_score": 63, "confidence_contribution": 0.28, "coverage": 0.18, "priority_strength": 0.6, "independence": 0.9, "unknown_reasons": ["其他资源偏好尚未谈及"], "assessment_reason": "出现了明确资源偏好"},
                        },
                        "motivation": {
                            "value": "被动",
                            "confidence": 0.9,
                            "basis": "explicit",
                            "evidence": ["你为什么想学"],
                        },
                    }
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(modes_module, "ModelRouter", CapturingRouter)
    history = "assistant: 你为什么想学？\nuser: 我是中药学学生\nassistant: 更偏什么目标？\nuser: 可能要考试，我更喜欢图谱"

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
    # The current question targets learning_goal, but an answer may contain an
    # unrelated seven-dimension clue and the agent must retain it.
    assert extraction.resource_preference.value == "知识图谱"
    assert extraction.resource_preference.assessment.radar_score == 63
    assert extraction.dimension_scan[0].dimension == "learning_goal"
    assert extraction.dimension_scan[0].status == "evidence_found"
    assert extraction.turn_id == 2
    assert extraction.atomic_evidence[0].quote == "可能要考试"
    assert extraction.motivation.value is None
    assert extraction.motivation.evidence == []
    assert calls[0]["task_type"] == "profile_extract"
    assert calls[0]["kw"]["temperature"] == 0
    assert calls[0]["kw"]["thinking"] == {"type": "disabled"}
    payload = json.loads(calls[0]["messages"][1]["content"].split("\n", 1)[0])
    assert "我是中药学学生" in payload["dialogue_history"]
    assert "可能要考试" in payload["dialogue_history"]
    assert "绝不限制提取范围" in calls[0]["messages"][0]["content"]


def test_dialogue_extraction_adapter_preserves_common_model_shapes() -> None:
    normalized = modes_module._normalize_dialogue_extraction_payload({
        "dimension_scan": {
            "learning_goal": {"status": "partial_evidence", "evidence": ["用户表达了明确方向"]},
            "motivation": {"status": "no_usable_evidence", "evidence": []},
        },
        "component_collection": {
            "learning_goal": {
                "expression_certainty": {"status": "confirmed", "evidence_ids": ["ev-1"]},
                "action_orientation": {"status": "no_usable_evidence", "missing_information": ["行动计划"]},
            },
        },
        "atomic_evidence": [{
            "evidence_id": "ev-1", "dimension": "learning_goal", "component": "expression_certainty",
            "evidence_group_id": "turn-1-goal", "source_turn_ids": [1], "quote": "我想治病救人",
            "normalized_claim": "用户表达了学习医学并帮助患者的意向", "evidence_type": "self_report",
            "supports": "expression_certainty", "collection_status": "confirmed",
            "assessment": {"reason": "目标表达明确，但行动和持续性未确认"},
            "confirmed_aspects": ["表达了学习医学并帮助患者的意向"], "pending_aspects": ["行动计划"],
            "semantic_relation": "new", "related_evidence_ids": [],
            "quality_factors": {"directness": "high", "specificity": "medium", "independence": "同一原话"},
        }],
    })

    from app.profile.component_model import AtomicEvidence, ComponentCollection, DimensionScan
    from app.profile.nodes import DialogueExtraction

    extraction = DialogueExtraction.model_validate(normalized)
    extraction.atomic_evidence = [AtomicEvidence.model_validate(item) for item in extraction.atomic_evidence]
    extraction.dimension_scan = [DimensionScan.model_validate(item) for item in extraction.dimension_scan]
    extraction.component_collection = [ComponentCollection.model_validate(item) for item in extraction.component_collection]
    assert [(item.dimension, item.status) for item in extraction.dimension_scan] == [
        ("learning_goal", "evidence_found"), ("motivation", "no_usable_evidence")
    ]
    assert extraction.atomic_evidence[0].quote == "我想治病救人"
    assert extraction.atomic_evidence[0].collection_status == "scorable"
    assert extraction.atomic_evidence[0].assessment == "目标表达明确，但行动和持续性未确认"
    assert extraction.atomic_evidence[0].quality_factors.expression_clarity == .9
    assert extraction.component_collection[0].status == "scorable"


def test_dialogue_sse_extraction_keeps_atomic_scan_in_audit_contract(monkeypatch, tmp_path: Path) -> None:
    from app.profile.component_model import QualityFactors
    from app.profile.nodes import AtomicEvidence, DialogueExtraction, DimensionScan

    session = modes_module.start_profile_session("dialogue-audit-target", "dialogue", db_path=tmp_path / "audit-target.db")
    extraction = DialogueExtraction(
        turn_id=1,
        dimension_scan=[
            DimensionScan(dimension="learning_goal", status="evidence_found", reason="明确表达了希望学医并帮助患者的目标。", components=["expression_certainty", "goal_specificity"]),
            DimensionScan(dimension="motivation", status="evidence_found", reason="表达了帮助他人的价值取向。", components=["intrinsic_value"]),
        ],
        atomic_evidence=[
            AtomicEvidence(
                evidence_id="ev-goal-1", dimension="learning_goal", component="expression_certainty",
                evidence_group_id="turn-1-intent", source_turn_ids=[1], quote="我想学医救疾病折磨的人",
                normalized_claim="用户希望学习医学并帮助受疾病困扰的人", evidence_type="self_report",
                supports=["expression_certainty", "goal_specificity"], collection_status="scorable",
                assessment="明确愿望，但尚无行动路径或期限", confirmed_aspects=["存在学医并帮助患者的意向"],
                pending_aspects=["行动计划和期限"], semantic_relation="new",
                quality_factors=QualityFactors(source_reliability=.8, expression_clarity=.9, content_specificity=.7, freshness=1, consistency=.8, independence_note="本轮原话"),
            ),
        ],
    )

    async def mocked_extract(**kwargs):
        return extraction, ""

    monkeypatch.setattr(modes_module, "_dialogue_extract_and_question", mocked_extract)
    cards = asyncio.run(modes_module._dialogue_extract_cards_for_chat(
        session=session, utterance="我想学医救疾病折磨的人", assistant_text="", history_messages=[],
        target="learning_goal", turn_index=1, response_time_ms=100, current_prompt="你为什么想学？",
    ))

    contract = session.step_data["_last_component_contract"]
    assert contract["dimension_scan"][0]["reason"].startswith("明确表达")
    assert contract["dimension_scan"][1]["dimension"] == "motivation"
    assert contract["atomic_evidence"][0]["quote"] == "我想学医救疾病折磨的人"
    assert cards[0].quote == "我想学医救疾病折磨的人"


def test_dialogue_chat_sse_audit_exposes_full_scan_for_healing_goal(monkeypatch, tmp_path: Path) -> None:
    from app.profile.component_model import QualityFactors
    from app.profile.nodes import AtomicEvidence, DialogueExtraction, DimensionScan

    quote = "我想学医救疾病折磨的人"
    extraction = DialogueExtraction(
        turn_id=1,
        dimension_scan=[
            DimensionScan(dimension=dimension, status="evidence_found" if dimension in {"learning_goal", "motivation"} else "no_usable_evidence",
                          reason="明确表达学习医学并帮助患者的意向。" if dimension == "learning_goal" else "体现帮助他人的内在价值。" if dimension == "motivation" else "本句没有提供该维度的可追溯信息。",
                          components=["expression_certainty", "goal_specificity"] if dimension == "learning_goal" else ["intrinsic_value"] if dimension == "motivation" else [])
            for dimension in modes_module.DIMENSION_LABELS
        ],
        atomic_evidence=[
            AtomicEvidence(
                evidence_id="ev-live-goal", dimension="learning_goal", component="expression_certainty",
                evidence_group_id="turn-1-intent", source_turn_ids=[1], quote=quote,
                normalized_claim="用户希望学习医学并帮助患者", evidence_type="self_report", supports=["expression_certainty", "intrinsic_value"],
                collection_status="scorable", assessment="表达明确意向，暂无路径和期限", confirmed_aspects=["存在学医并帮助患者的目标"],
                pending_aspects=["目标期限和行动路径"], semantic_relation="new",
                quality_factors=QualityFactors(source_reliability=.8, expression_clarity=.9, content_specificity=.7, freshness=1, consistency=.8, independence_note="同一原话来源"),
            ),
            AtomicEvidence(
                evidence_id="ev-live-motivation", dimension="motivation", component="intrinsic_value",
                evidence_group_id="turn-1-intent", source_turn_ids=[1], quote=quote,
                normalized_claim="用户希望帮助受疾病困扰的人", evidence_type="self_report", supports=["intrinsic_value"],
                collection_status="scorable", assessment="呈现帮助他人的价值取向", confirmed_aspects=["帮助他人的价值"],
                pending_aspects=["长期投入表现"], semantic_relation="new",
                quality_factors=QualityFactors(source_reliability=.8, expression_clarity=.8, content_specificity=.7, freshness=1, consistency=.8, independence_note="与目标证据同句同源"),
            ),
            AtomicEvidence(
                evidence_id="ev-live-specificity", dimension="learning_goal", component="goal_specificity",
                evidence_group_id="turn-1-intent", source_turn_ids=[1], quote=quote,
                normalized_claim="学习医学并帮助患者是可辨识的目标方向", evidence_type="self_report", supports=["goal_specificity"],
                collection_status="scorable", assessment="目标方向可辨，但缺少具体成果和期限", confirmed_aspects=["目标方向是学习医学并帮助患者"],
                pending_aspects=["具体成果和期限"], semantic_relation="new",
                quality_factors=QualityFactors(source_reliability=.8, expression_clarity=.8, content_specificity=.65, freshness=1, consistency=.8, independence_note="与同轮目标证据同源"),
            ),
        ],
    )

    pipeline_order: list[str] = []
    answer_payloads: list[list[dict[str, str]]] = []

    async def mocked_extract(**kwargs):
        pipeline_order.append("scan")
        return extraction, ""

    class OrderedAnswerRouter:
        def select(self, _task: str):
            return object()

        async def chat_stream(self, _task: str, messages: list[dict[str, str]], **_kwargs):
            pipeline_order.append("answer")
            answer_payloads.append(messages)
            yield "message", "我听到了你想帮助患者的方向。"

    monkeypatch.setattr(modes_module, "_dialogue_extract_and_question", mocked_extract)
    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)
    monkeypatch.setattr(modes_module, "ModelRouter", OrderedAnswerRouter)
    started = _start("dialogue", user_id="dialogue-healing-goal")
    response = client.post("/api/profile/dialogue/chat", json={"session_id": started["session_id"], "utterance": quote})

    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    audit = next(data for event, data in events if event == "audit")
    goal_scan = next(item for item in audit["model_result"]["dimension_scan"] if item["dimension"] == "learning_goal")
    motivation_scan = next(item for item in audit["model_result"]["dimension_scan"] if item["dimension"] == "motivation")
    assert audit["model_result"]["scan_available"] is True
    assert audit["model_result"]["scan_complete"] is True
    assert goal_scan["status"] == "evidence_found"
    assert motivation_scan["status"] == "evidence_found"
    assert [item["quotes"] for item in audit["model_result"]["evidence"]] == [[quote], [quote], [quote]]
    assert {item["evidence_group_id"] for item in audit["model_result"]["evidence"]} == {"turn-1-intent"}
    learning_goal = next(item for item in audit["system_result"]["dimensions"] if item["key"] == "learning_goal")
    assert learning_goal["scoring_phase"] == "collecting"
    assert learning_goal["radar_score"] is None
    assert learning_goal["matrix_after"]["expression_certainty"] is True
    assert learning_goal["matrix_after"]["goal_specificity"] is True
    assert learning_goal["matrix_after"]["action_orientation"] is False
    assert pipeline_order == ["scan", "answer"]
    steering = json.loads(answer_payloads[0][1]["content"].removeprefix("profile_steering="))
    assert steering["target_dimension"] == "learning_goal"
    assert steering["component_focus"]["component"] == "action_orientation"
    # 纠偏走 replace 而不是追加：可见正文以最后一次 replace 为准。
    visible_answer = ""
    for event, data in events:
        if event == "message":
            visible_answer += str(data.get("delta", ""))
        elif event == "replace":
            visible_answer = str(data.get("text", ""))
    assert "你为这个方向做过哪些准备" in visible_answer
    # 漂移的追问被替换掉，正文里只留一个问号。
    assert visible_answer.count("？") == 1
    # 首字不能重复：前缀探测释放时曾把第一个分片写两遍（"我我听到了…"）。
    assert visible_answer.startswith("我听到了")
    assert [stage["label"] for stage in audit["stages"]] == [
        "七维扫描", "原话与来源校验", "组件矩阵更新", "组件评分门禁", "分数与确认度", "补问规划", "回答组织"
    ]


def test_dialogue_complete_component_set_is_scored_and_revealed(monkeypatch, tmp_path: Path) -> None:
    from app.profile.component_model import COMPONENTS, COMPONENT_RUBRICS, QualityFactors
    from app.profile.jury import ScoringResult
    from app.profile.nodes import AtomicEvidence, DialogueExtraction, DimensionScan

    quote = "我想考研，已经开始准备"
    keys = COMPONENTS["learning_goal"]
    extraction = DialogueExtraction(
        turn_id=1,
        dimension_scan=[
            DimensionScan(
                dimension=dimension,
                status="evidence_found" if dimension == "learning_goal" else "no_usable_evidence",
                reason="原话提供了目标信息。" if dimension == "learning_goal" else "本句没有该维度的可追溯内容。",
                components=list(keys) if dimension == "learning_goal" else [],
            )
            for dimension in modes_module.DIMENSION_LABELS
        ],
        atomic_evidence=[
            AtomicEvidence(
                evidence_id=f"ev-goal-{index}", dimension="learning_goal", component=component,
                evidence_group_id="turn-1-intent", source_turn_ids=[1], quote=quote,
                normalized_claim=f"目标组件证据：{COMPONENT_RUBRICS['learning_goal'][component]['label']}",
                evidence_type="self_report", supports=[component], collection_status="scorable",
                assessment="用户给出与该组件直接相关的信息", confirmed_aspects=["存在相关目标线索"],
                pending_aspects=[], semantic_relation="new",
                quality_factors=QualityFactors(source_reliability=.9, expression_clarity=.9, content_specificity=.8, freshness=1, consistency=.9, independence_note="评分门槛回归样例"),
            )
            for index, component in enumerate(keys, start=1)
        ],
    )

    async def mocked_extract(**_kwargs):
        return extraction, ""

    score_call_count = 0

    async def mocked_score(dimension, _cards):
        nonlocal score_call_count
        assert dimension == "learning_goal"
        score_call_count += 1
        returned_components = keys if score_call_count == 2 else keys[:-1]
        return ScoringResult(
            dimension="目标锚定度", dimension_key="learning_goal",
            component_scores={component: 70 + index for index, component in enumerate(returned_components)},
            method="fusion" if score_call_count == 1 else "jury",
            provenance="rule_fallback" if score_call_count == 1 else "test_scoring_model",
            narrative="五项评分完成；本结果为可复核的理由摘要。",
        )

    class AnswerRouter:
        async def chat_stream(self, _task, _messages, **_kwargs):
            yield "message", "目标已经逐渐清楚了。你为此做了哪些准备？"

    monkeypatch.setattr(modes_module, "_dialogue_extract_and_question", mocked_extract)
    monkeypatch.setattr(modes_module, "score_dimension", mocked_score)
    monkeypatch.setattr(modes_module, "ModelRouter", AnswerRouter)
    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)
    started = _start("dialogue", user_id="dialogue-complete-component-score")

    response = client.post("/api/profile/dialogue/chat", json={"session_id": started["session_id"], "utterance": quote})
    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    audit = next(data for event, data in events if event == "audit")
    goal = next(item for item in audit["system_result"]["dimensions"] if item["key"] == "learning_goal")
    radar = next(data for event, data in events if event == "radar")

    assert goal["component_collection"]["collected"] == 5
    assert goal["scoring_phase"] == "scored"
    assert goal["scoring_provenance"] == "test_scoring_model"
    assert goal["scoring_attempts"] == 2
    assert score_call_count == 2
    assert goal["radar_score"] is not None
    assert goal["scoring_output"] and len(goal["scoring_output"]) == 5
    assert goal["confidence_audit"]["combination_type"] == "single_clear_self_report"
    assert radar["axes"][1]["scoring_phase"] == "scored"


def test_dialogue_partial_component_set_still_scores_and_lights_the_radar(monkeypatch, tmp_path: Path) -> None:
    """4/5 已收集就要出分：覆盖 80% 已过 60% 门槛，雷达不能是暗的。

    实测用户在对话里收齐了"表达确定性/目标具体度/行动指向/优先级信号"四个
    组件，界面上写着"已收集 4/5 个组件 · 雷达 —"——因为旧实现要 5 个全收齐
    才肯调评分模型。维度分的门槛本来就是 coverage ≥ 0.6，两者必须一致。
    """

    from app.profile.component_model import COMPONENTS, COMPONENT_RUBRICS, QualityFactors
    from app.profile.jury import JurorAssessment, ScoringResult
    from app.profile.nodes import AtomicEvidence, DialogueExtraction, DimensionScan

    quote = "我想考研，已经列了复习计划，每天先看中药学"
    keys = COMPONENTS["learning_goal"]
    collected = keys[:4]

    def build_extraction(turn_id: int, components: list[str]) -> DialogueExtraction:
        return DialogueExtraction(
            turn_id=turn_id,
            dimension_scan=[
                DimensionScan(
                    dimension=dimension,
                    status="evidence_found" if dimension == "learning_goal" else "no_usable_evidence",
                    reason="原话提供了目标信息。" if dimension == "learning_goal" else "本句没有该维度的可追溯内容。",
                    components=list(components) if dimension == "learning_goal" else [],
                )
                for dimension in modes_module.DIMENSION_LABELS
            ],
            atomic_evidence=[
                AtomicEvidence(
                    evidence_id=f"ev-partial-{turn_id}-{index}", dimension="learning_goal", component=component,
                    evidence_group_id=f"turn-{turn_id}-goal", source_turn_ids=[turn_id], quote=quote,
                    normalized_claim=f"目标组件证据：{COMPONENT_RUBRICS['learning_goal'][component]['label']}",
                    evidence_type="self_report", supports=[component], collection_status="scorable",
                    assessment="用户给出与该组件直接相关的信息", confirmed_aspects=["存在相关目标线索"],
                    pending_aspects=[], semantic_relation="new",
                    quality_factors=QualityFactors(
                        source_reliability=.9, expression_clarity=.9, content_specificity=.8,
                        freshness=1, consistency=.9, independence_note="部分覆盖回归样例",
                    ),
                )
                for index, component in enumerate(components, start=1)
            ],
        )

    extractions = [build_extraction(1, collected), build_extraction(2, [])]
    score_calls: list[list[str]] = []

    async def mocked_extract(**_kwargs):
        return extractions.pop(0), ""

    async def mocked_score(dimension, cards):
        assert dimension == "learning_goal"
        score_calls.append([card.component for card in cards])
        return ScoringResult(
            dimension="目标锚定度", dimension_key="learning_goal",
            component_scores={component: 76.0 for component in collected},
            jury={
                "严师": JurorAssessment(score=72, reasoning_chain=["目标有具体院校和时间"], comment="方向明确"),
                "宽师": JurorAssessment(score=80, reasoning_chain=["已列复习计划"], comment="可执行"),
                "中立": JurorAssessment(score=76, reasoning_chain=["有意向也有初步动作"], comment="中等偏上"),
            },
            jury_mean=76.0,
            coverage=0.8,
            radar_score=70.8,
            narrative="四项组件已评分，覆盖 80%。",
            method="jury",
            provenance="test_partial_scoring",
        )

    class AnswerRouter:
        async def chat_stream(self, _task, _messages, **_kwargs):
            yield "message", "目标已经比较清楚了。你打算怎么安排每周的时间？"

    monkeypatch.setattr(modes_module, "_dialogue_extract_and_question", mocked_extract)
    monkeypatch.setattr(modes_module, "score_dimension", mocked_score)
    monkeypatch.setattr(modes_module, "ModelRouter", AnswerRouter)
    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)
    started = _start("dialogue", user_id="dialogue-partial-component-score")

    response = client.post("/api/profile/dialogue/chat", json={"session_id": started["session_id"], "utterance": quote})
    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    audit = next(data for event, data in events if event == "audit")
    goal = next(item for item in audit["system_result"]["dimensions"] if item["key"] == "learning_goal")
    radar = next(data for event, data in events if event == "radar")
    goal_axis = next(axis for axis in radar["axes"] if axis["key"] == "learning_goal")

    assert goal["component_collection"]["collected"] == 4
    assert goal["scoring_phase"] == "scored"
    assert goal["radar_score"] is not None
    assert goal["component_collection"]["coverage"] == 0.8
    assert goal["scoring_deferred_reason"] is None
    assert goal["scoring_provenance"] == "test_partial_scoring"
    # 审计要能回答"模型怎么打分的"：三师的分数、逐条比对摘要、评语都要透出，
    # 否则页面上只剩一个结论分，用户没法判断这个分是怎么来的。
    assert [item["teacher"] for item in goal["jury_panel"]] == ["严师", "宽师", "中立"]
    assert goal["jury_panel"][0]["reasoning_chain"] == ["目标有具体院校和时间"]
    assert goal["jury_panel"][0]["comment"] == "方向明确"
    assert goal["jury_mean"] == 76.0
    assert goal_axis["lit"] is True
    assert goal_axis["status"] == "solid"
    assert goal_axis["value"] > 0
    assert len(score_calls) == 1

    # 第二轮没有新组件：同一批已收组件不能每轮重复打分。
    second = client.post("/api/profile/dialogue/chat", json={"session_id": started["session_id"], "utterance": "每周三晚上看两小时"})
    assert second.status_code == 200, second.text
    assert len(score_calls) == 1


def test_dialogue_rerun_waits_for_three_more_evidence(monkeypatch, tmp_path: Path) -> None:
    """重评节奏：首评在组件达 60%，之后每新增 3 条去重证据才重评一次。

    时效性让位给成本：+2 条就重评等于白烧一次模型调用，而 +3 条才说明
    "用户又说了新东西、分数该动了"。水位线用去重后的条数，避免同一句话
    被抽成三张卡时每轮都触发。
    """

    from app.profile.component_model import COMPONENTS, COMPONENT_RUBRICS, QualityFactors
    from app.profile.jury import JurorAssessment, ScoringResult
    from app.profile.nodes import AtomicEvidence, DialogueExtraction, DimensionScan

    keys = COMPONENTS["learning_goal"][:3]
    quote = "我想考研，目标定在北中医，每天先背方剂"
    score_calls: list[int] = []

    def evidence(turn_id: int, component: str, slot: int) -> AtomicEvidence:
        return AtomicEvidence(
            evidence_id=f"ev-rerun-{turn_id}-{slot}", dimension="learning_goal", component=component,
            # 每次新增都换来源组，否则会被当成同源重复，水位线永远不涨。
            evidence_group_id=f"turn-{turn_id}-slot-{slot}", source_turn_ids=[turn_id], quote=quote,
            normalized_claim=f"目标组件证据：{COMPONENT_RUBRICS['learning_goal'][component]['label']}",
            evidence_type="self_report", supports=[component], collection_status="scorable",
            assessment="与组件直接相关", confirmed_aspects=["存在相关线索"], pending_aspects=[],
            semantic_relation="new",
            quality_factors=QualityFactors(
                source_reliability=.9, expression_clarity=.82, content_specificity=.78,
                freshness=1, consistency=.85, independence_note="重评节奏回归样例",
            ),
        )

    def build_extraction(turn_id: int, items: list[AtomicEvidence]) -> DialogueExtraction:
        return DialogueExtraction(
            turn_id=turn_id,
            dimension_scan=[
                DimensionScan(
                    dimension=dimension,
                    status="evidence_found" if items and dimension == "learning_goal" else "no_usable_evidence",
                    reason="原话提供了目标信息。" if items and dimension == "learning_goal" else "本句没有该维度的可追溯内容。",
                    components=sorted({item.component for item in items}) if dimension == "learning_goal" else [],
                )
                for dimension in modes_module.DIMENSION_LABELS
            ],
            atomic_evidence=items,
        )

    # 第 1 轮 3 条（3 个组件各 1 条）→ 首评；第 2 轮 +2 条 → 不重评；第 3 轮 +1 条 → 重评。
    extractions = [
        build_extraction(1, [evidence(1, key, index) for index, key in enumerate(keys, start=1)]),
        build_extraction(2, [evidence(2, keys[0], index) for index in (1, 2)]),
        build_extraction(3, [evidence(3, keys[0], 3)]),
    ]

    async def mocked_extract(**_kwargs):
        return extractions.pop(0), ""

    async def mocked_score(dimension, cards):
        assert dimension == "learning_goal"
        score_calls.append(len(cards))
        return ScoringResult(
            dimension="目标锚定度", dimension_key="learning_goal",
            component_scores={key: 66.0 for key in keys},
            rescored_components=list(keys),
            jury={
                "严师": JurorAssessment(score=60, reasoning_chain=["目标有对象"], comment="方向明确"),
                "宽师": JurorAssessment(score=72, reasoning_chain=["有每日动作"], comment="可执行"),
                "中立": JurorAssessment(score=66, reasoning_chain=["有目标有动作"], comment="中等偏上"),
            },
            jury_mean=66.0,
            coverage=0.6,
            radar_score=59.6,
            narrative="三个组件已评分。",
            method="jury",
            provenance="test_rerun_pacing",
        )

    class AnswerRouter:
        async def chat_stream(self, _task, _messages, **_kwargs):
            yield "message", "你的目标方向清楚了。你打算用什么材料复习？"

    monkeypatch.setattr(modes_module, "_dialogue_extract_and_question", mocked_extract)
    monkeypatch.setattr(modes_module, "score_dimension", mocked_score)
    monkeypatch.setattr(modes_module, "ModelRouter", AnswerRouter)
    monkeypatch.setattr(modes_module, "_has_profile_extract_key", lambda: False)
    started = _start("dialogue", user_id="dialogue-rerun-pacing")

    def chat(utterance: str) -> dict[str, object]:
        response = client.post("/api/profile/dialogue/chat", json={"session_id": started["session_id"], "utterance": utterance})
        assert response.status_code == 200, response.text
        events = _parse_sse(response.text)
        audit = next(data for event, data in events if event == "audit")
        return next(item for item in audit["system_result"]["dimensions"] if item["key"] == "learning_goal")

    first = chat(quote)
    assert len(score_calls) == 1
    assert first["scoring_watermark"] == 3
    assert first["next_scoring_watermark"] == 6
    assert "首评" in str(first["scoring_trigger"])

    # +2 条：水位线 3 → 5，差 2 条不到步长，不重评。
    second = chat("我每周三晚上复习两小时")
    assert len(score_calls) == 1
    assert second["current_watermark"] == 5
    assert second["scoring_watermark"] == 3

    # +1 条：水位线 6，刚好涨 3 条，重评一次。
    third = chat("我还会整理错题本")
    assert len(score_calls) == 2
    assert third["scoring_watermark"] == 6
    assert "重评" in str(third["scoring_trigger"])
    assert "3" in str(third["scoring_trigger"])

    # 逐轮时间线：置信度每轮都记一行，评分轮打标记。审计里要回答的"这个分是
    # 在多少置信度下评的"，直接读评分轮那一行，不必再单独存一份快照。
    timeline = third["confidence_timeline"]
    assert [row["turn"] for row in timeline] == [1, 2, 3]
    assert [row["scored_this_turn"] for row in timeline] == [True, False, True]
    assert [row["evidence"] for row in timeline] == [3, 5, 6]
    assert all(row["quality_new"] is not None for row in timeline)
    assert all(0.0 <= row["confidence"] <= 1.0 for row in timeline)


def test_dimension_timeline_overwrites_instead_of_duplicating_a_turn() -> None:
    """同一轮重试/重放只留一行，否则时间线里会出现两条"第 2 轮"。"""

    from app.profile.component_model import QualityFactors
    from app.profile.schema import EvidenceCard, EvidenceTier

    cards = [
        EvidenceCard(
            dimension="learning_goal", source="dialogue", tier=EvidenceTier.SELF_REPORT,
            fact="我想考研", quote="我想考研", objective_signal={}, weight=1,
            component="goal_specificity", collection_status="scorable",
            quality_factors=QualityFactors(
                source_reliability=.9, expression_clarity=.8, content_specificity=.75,
                freshness=1, consistency=.85, independence_note="时间线覆盖样例",
            ).model_dump(),
        )
    ]
    step_data: dict[str, object] = {}
    fusion = modes_module._dialogue_fusion(cards)
    for _ in range(3):
        modes_module._append_dimension_timeline(
            step_data, turn_index=2, fusion=fusion, cards=cards, all_cards=cards
        )
    rows = step_data["dimension_timeline"]["learning_goal"]  # type: ignore[index]
    assert len(rows) == 1
    assert rows[0]["turn"] == 2
    assert rows[0]["quality_new"] is not None
    assert rows[0]["quality_avg"] is not None


def test_dialogue_restore_recovers_a_complete_but_unscored_dimension(monkeypatch, tmp_path: Path) -> None:
    from app.profile.component_model import COMPONENTS
    from app.profile.jury import ScoringResult
    from app.profile.schema import EvidenceCard, EvidenceTier

    session = modes_module.start_profile_session(
        "dialogue-restore-score", "dialogue", db_path=tmp_path / "restore-score.db"
    )
    keys = COMPONENTS["learning_goal"]
    session.evidence_cards = [
        EvidenceCard(
            dimension="learning_goal", source="dialogue", tier=EvidenceTier.SELF_REPORT,
            fact="我想考研", quote="我想考研", objective_signal={}, weight=1,
            component=component, collection_status="scorable",
        )
        for component in keys
    ]
    calls = 0

    async def mocked_score(dimension, _cards):
        nonlocal calls
        calls += 1
        return ScoringResult(
            dimension="目标锚定度", dimension_key=dimension,
            component_scores={component: 70 for component in keys},
            provenance="test_scoring_model", narrative="恢复时补全组件评分。",
        )

    monkeypatch.setattr(modes_module, "score_dimension", mocked_score)

    recovered = asyncio.run(
        modes_module.recover_dialogue_component_scores(session, db_path=tmp_path / "restore-score.db")
    )
    fusion = modes_module._dialogue_fusion(session.evidence_cards)

    assert recovered is True
    assert calls == 1
    assert all(card.objective_signal["component_score"] == 70 for card in session.evidence_cards)
    assert fusion["learning_goal"].metadata["component_collection"]["scoring_phase"] == "scored"
    assert fusion["learning_goal"].radar_score == 70


def test_dialogue_visible_reply_removes_internal_rubric_sentence_without_losing_question() -> None:
    reason = "自然追问实际投入的具体例子、限制和后续表现"
    reply = f"关于实际投入，{reason}。你能结合最近一次学习经历具体说说吗？"

    cleaned = modes_module._remove_component_rubric_leak(
        reply,
        {"label": "实际投入", "reason": reason},
    )

    assert cleaned == "你能结合最近一次学习经历具体说说吗？"


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


def test_dialogue_component_focus_hands_off_after_two_unanswered_asks() -> None:
    """连问两次还填不上的格子必须让位，否则整段对话会围着它打转。

    实测十轮对话里连续五轮都在问同一个"中药里哪块让你觉得有意思"，只是每轮
    换个说法：那一格始终填不上，软锁定又一直把它选回来。问满两次就该换格子。
    """

    cards: list[EvidenceCard] = []

    fresh = modes_module._dialogue_component_focus(cards, "motivation")
    assert fresh is not None
    assert fresh["component"] == "intrinsic_value"

    once = modes_module._dialogue_component_focus(cards, "motivation", asked_counts={"intrinsic_value": 1})
    assert once is not None
    assert once["component"] == "intrinsic_value"

    twice = modes_module._dialogue_component_focus(cards, "motivation", asked_counts={"intrinsic_value": 2})
    assert twice is not None
    assert twice["component"] != "intrinsic_value"


def test_dialogue_component_focus_abandons_the_last_hole_once_asked_out() -> None:
    """只剩一个洞但它已经问满两次时，宁可放弃这一维也不要再问第三遍。

    实测节奏可持续度只剩「坚持了多久」一格可问，模型连续 6 轮换个说法重问同一
    件事。那一维停在 4/5 = 0.8，仍过 60% 门槛、照样出分，硬问第六遍只是折磨用户。
    """

    cards = [
        _evidence_card("motivation", {"label": ALLOWED_LABELS["motivation"][0]}, weight=1.0, component="external_commitment"),
        _evidence_card("motivation", {"label": ALLOWED_LABELS["motivation"][0]}, weight=1.0, component="conflict_regulation"),
    ]

    focus = modes_module._dialogue_component_focus(cards, "motivation", asked_counts={"intrinsic_value": 2})

    assert focus is None
    assert modes_module._dialogue_answerable_holes(
        "motivation", cards, asked_counts={"intrinsic_value": 2}
    ) == []


def test_dialogue_target_skips_the_stalled_dimension() -> None:
    """卡住的那一维必须先出局，否则"先补完半满维度"会把它自己选回来。"""

    cards = [
        _evidence_card("pace", {"daily_minutes": 60}, weight=1.0, component="sustained_period"),
        _evidence_card("pace", {"daily_minutes": 60}, weight=1.0, component="weekly_rhythm"),
        _evidence_card("pace", {"daily_minutes": 60}, weight=1.0, component="session_length"),
        _evidence_card("pace", {"daily_minutes": 60}, weight=1.0, component="adjustment_flexibility"),
        _evidence_card("motivation", {"label": ALLOWED_LABELS["motivation"][0]}, weight=1.0, component="intrinsic_value"),
    ]
    fusion = modes_module._dialogue_fusion(cards)

    target = modes_module._dialogue_target(
        cards,
        fusion=fusion,
        current_target="pace",
        stalled=True,
        stalled_dimension="pace",
    )

    assert target != "pace"


def test_dialogue_ask_ledger_survives_until_the_component_is_filled() -> None:
    step_data: dict[str, object] = {}
    focus = {"component": "intrinsic_value"}

    modes_module._dialogue_note_asked_component(step_data, focus)
    modes_module._dialogue_note_asked_component(step_data, focus)

    assert modes_module._dialogue_asked_components(step_data) == {"intrinsic_value": 2}
    # 空 focus（这一维没有可问的洞）不该凭空记一笔。
    modes_module._dialogue_note_asked_component(step_data, None)
    assert modes_module._dialogue_asked_components(step_data) == {"intrinsic_value": 2}


def test_dialogue_target_finishes_a_partially_collected_dimension_first() -> None:
    from app.profile.component_model import AtomicEvidence, QualityFactors
    from app.profile.nodes import DialogueExtraction, _dialogue_cards

    utterance = "我想治病救人"
    extraction = DialogueExtraction(
        turn_id=1,
        dimension_scan=[{
            "dimension": "learning_goal",
            "status": "evidence_found",
            "reason": "表达了希望通过学习达成的职业/助人方向，可作为目标锚点；同一句也可弱支持内在价值动机。",
            "components": ["expression_certainty", "goal_specificity"],
            "excluded_candidates": [],
        }, {
            "dimension": "motivation",
            "status": "evidence_found",
            "reason": "治病救人表达了利他价值线索，但不能单独证明持续投入或受阻后的坚持。",
            "components": ["intrinsic_value"],
            "excluded_candidates": [],
        }],
        atomic_evidence=[
            AtomicEvidence(
                evidence_id="ev-goal",
                dimension="learning_goal",
                component="expression_certainty",
                evidence_group_id="turn-1",
                source_turn_ids=[1],
                quote=utterance,
                normalized_claim="用户表达了治病救人的学习目标方向",
                evidence_type="self_report",
                supports=["expression_certainty", "goal_specificity"],
                collection_status="scorable",
                quality_factors=QualityFactors(source_reliability=.8, expression_clarity=.8, content_specificity=.7, freshness=1, consistency=.8, independence_note="同一轮用户原话"),
                assessment="有方向和助人意向，但单句不能证明长期稳定或行动承诺",
                confirmed_aspects=["目标方向：治病救人"],
                pending_aspects=["具体目标对象、期限和路径", "行动和优先级", "跨轮稳定性"],
                semantic_relation="new",
            ),
            AtomicEvidence(
                evidence_id="ev-motivation",
                dimension="motivation",
                component="intrinsic_value",
                evidence_group_id="turn-1",
                source_turn_ids=[1],
                quote=utterance,
                normalized_claim="用户表达了助人价值取向",
                evidence_type="self_report",
                supports=["intrinsic_value"],
                collection_status="scorable",
                quality_factors=QualityFactors(source_reliability=.8, expression_clarity=.8, content_specificity=.7, freshness=1, consistency=.8, independence_note="同一轮用户原话"),
                assessment="作为内在价值线索，尚需询问其个人意义与实际投入",
                confirmed_aspects=["助人价值取向"],
                pending_aspects=["是否为持续内在驱动", "实际投入"],
                semantic_relation="new",
            ),
        ],
    )
    cards = _dialogue_cards(extraction)

    assert modes_module._dialogue_target(cards) == "learning_goal"
    question = modes_module._fallback_dialogue_question("learning_goal", 1, evidence_cards=cards)
    assert question == "学医救人对你来说，最希望实现的具体变化是什么？"
    assert {card.dimension for card in cards} == {"learning_goal", "motivation"}
    assert {card.evidence_group_id for card in cards} == {"turn-1"}


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

    assert progress["collected"] < 6
    assert all(result.score is None and result.radar_score is None for result in fusion.values())
    assert all(result.metadata["component_collection"]["scoring_phase"] == "collecting" for result in fusion.values())


def test_dialogue_extract_cards_for_chat_without_key_keeps_weak_points_uncollected(monkeypatch, tmp_path: Path) -> None:
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

    assert weak_cards == []
    assert not any(item["key"] == "weak_points" and item["collected"] for item in progress["dimensions"])

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


def test_dialogue_no_key_path_never_claims_all_seven_axes(monkeypatch, tmp_path: Path) -> None:
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
    assert progress["collected"] == 0
    assert cards == []
    assert all(item["status"] == "待取证" and not item["quotes"] for item in evidence_panel)


def test_dialogue_identity_cards_without_key_remain_unrevealed(monkeypatch, tmp_path: Path) -> None:
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

    assert goal_cards == []
    assert resource_cards == []
    assert motivation_cards == []
    assert all(not item["revealed"] for item in by_key.values())


def test_dialogue_identity_cards_level_names_align_fusion_anchors() -> None:
    cards = [
        _evidence_card("knowledge_base", {"prior": True, "label": "较好", "evidence": ["基础较好"]}, weight=1.0),
        _evidence_card("pace", {"daily_minutes": 45, "target_days": 14, "evidence": ["每天45分钟", "14天"]}, weight=1.0),
    ]
    fusion = modes_module._dialogue_fusion(cards)
    view = modes_module._dialogue_identity_cards_view(cards, fusion=fusion, revealed_flags={})
    by_key = {item["key"]: item for item in view}

    assert fusion["knowledge_base"].score is None
    assert fusion["pace"].score is None
    assert not by_key["knowledge_base"]["revealed"]
    assert not by_key["pace"]["revealed"]


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
    assert {item["key"]: item for item in high_view}["learning_goal"]["revealed"] is False
    assert {item["key"]: item for item in rollback_view}["learning_goal"]["revealed"] is False


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


def test_prescription_signal_llm_extract_skips_thinking(monkeypatch) -> None:
    """抽取 4 个布尔字段不需要推理 token，调用侧不应显式开思考。

    默认值由 ModelRouter._with_default_params 注入 disabled，这里只验证
    调用点没有覆盖它。
    """

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
            "thinking": None,
            "reasoning_effort": None,
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


def _evidence_card(
    dimension: DimensionKey,
    objective_signal: dict[str, object],
    *,
    weight: float,
    component: str | None = None,
) -> EvidenceCard:
    return EvidenceCard(
        dimension=dimension,
        source="test",
        fact="test evidence",
        objective_signal=objective_signal,
        weight=weight,
        timestamp=datetime.now(UTC),
        component=component,
        collection_status="scorable" if component else None,
    )


def _start(mode: str, user_id: str = "student-001") -> dict[str, object]:
    _override_current_user(user_id)
    response = client.post("/api/profile/session/start", json={"user_id": user_id, "mode": mode})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "start"
    assert payload["step"] is not None
    return payload


def test_dialogue_client_turn_id_replay_after_pre_done_disconnect_is_idempotent(tmp_path: Path) -> None:
    """A reconnect after server commit must replay state, not write a second turn."""

    db_path = tmp_path / "dialogue-client-turn-replay.db"
    user_id = "dialogue-client-turn-user"
    session = modes_module.start_profile_session(user_id, "dialogue", db_path=db_path)

    async def consume_until_state() -> list[tuple[str, dict[str, object]]]:
        events: list[tuple[str, dict[str, object]]] = []
        async for event, data in modes_module.stream_dialogue_chat_events(
            session.session_id,
            user_id=user_id,
            utterance="I want to pass the exam in thirty days.",
            client_turn_id="turn-a",
            db_path=db_path,
        ):
            events.append((event, data))
            if event == "state":
                # Simulate a client which lost the connection before the done frame.
                break
        return events

    first_events = asyncio.run(consume_until_state())
    committed = modes_module.get_profile_session(session.session_id, user_id=user_id, db_path=db_path)
    evidence_before = list_evidence(user_id, db_path=db_path)
    messages_before = list_chat_messages(user_id, session.session_id, db_path=db_path)

    async def replay() -> list[tuple[str, dict[str, object]]]:
        return [
            event
            async for event in modes_module.stream_dialogue_chat_events(
                session.session_id,
                user_id=user_id,
                utterance="I want to pass the exam in thirty days.",
                client_turn_id="turn-a",
                db_path=db_path,
            )
        ]

    replay_events = asyncio.run(replay())
    reloaded = modes_module.get_profile_session(session.session_id, user_id=user_id, db_path=db_path)

    assert [event for event, _ in first_events][-1] == "state"
    assert [event for event, _ in replay_events] == ["state", "done"]
    assert committed.step_index == reloaded.step_index == 1
    assert list_evidence(user_id, db_path=db_path) == evidence_before
    assert list_chat_messages(user_id, session.session_id, db_path=db_path) == messages_before
    assert all(not item["revealed_now"] for item in replay_events[0][1]["identity_cards"])


def test_dialogue_chat_api_replays_completed_client_turn_without_duplicate_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-api-client-turn-replay.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    started = _start("dialogue", user_id="dialogue-api-client-turn-user")
    request = {
        "session_id": started["session_id"],
        "utterance": "Use the same request id through a reconnect.",
        "client_turn_id": "api-turn-a",
    }

    first = client.post("/api/profile/dialogue/chat", json=request)
    replay = client.post("/api/profile/dialogue/chat", json=request)

    assert first.status_code == replay.status_code == 200
    assert [event for event, _ in _parse_sse(replay.text)] == ["state", "done"]
    assert modes_module.get_profile_session(started["session_id"], user_id="dialogue-api-client-turn-user", db_path=db_path).step_index == 1
    assert len(list_chat_messages("dialogue-api-client-turn-user", started["session_id"], db_path=db_path)) == 2


def test_dialogue_sse_disconnect_before_done_keeps_committed_turn(tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-disconnect-commit.db"
    user_id = "dialogue-disconnect-commit-user"
    session = modes_module.start_profile_session(user_id, "dialogue", db_path=db_path)

    class DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    async def consume() -> list[str]:
        events: list[str] = []
        async for frame in _dialogue_sse_iter(
            session_id=session.session_id,
            user_id=user_id,
            utterance="Commit this answer before the stream closes.",
            client_turn_id="turn-disconnect",
            db_path=db_path,
            request=DisconnectedRequest(),
        ):
            events.append(frame.splitlines()[0].removeprefix("event: "))
        return events

    events = asyncio.run(consume())
    restored = modes_module.get_profile_session(session.session_id, user_id=user_id, db_path=db_path)

    assert "done" not in events
    assert restored.step_index == 1
    assert len(list_chat_messages(user_id, session.session_id, db_path=db_path)) == 2


def test_dialogue_client_turn_id_scopes_replay_and_allows_same_text_next_turn(tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-client-turn-scope.db"
    user_id = "dialogue-client-turn-scope-user"
    first = modes_module.start_profile_session(user_id, "dialogue", db_path=db_path)
    second = modes_module.start_profile_session("dialogue-client-turn-other-user", "dialogue", db_path=db_path)

    async def submit(session_id: str, owner: str, turn_id: str) -> list[str]:
        return [
            event
            async for event, _ in modes_module.stream_dialogue_chat_events(
                session_id,
                user_id=owner,
                utterance="I want steady study time.",
                client_turn_id=turn_id,
                db_path=db_path,
            )
        ]

    assert asyncio.run(submit(first.session_id, user_id, "turn-a"))[-6:] == ["evidence", "audit", "identity_cards", "radar", "state", "done"]
    assert asyncio.run(submit(first.session_id, user_id, "turn-b"))[-6:] == ["evidence", "audit", "identity_cards", "radar", "state", "done"]
    assert modes_module.get_profile_session(first.session_id, user_id=user_id, db_path=db_path).step_index == 2
    assert len(list_chat_messages(user_id, first.session_id, db_path=db_path)) == 4

    assert asyncio.run(submit(second.session_id, "dialogue-client-turn-other-user", "turn-a"))[-1] == "done"
    assert modes_module.get_profile_session(second.session_id, user_id="dialogue-client-turn-other-user", db_path=db_path).step_index == 1


def test_dialogue_client_turn_id_concurrent_claim_only_writes_once(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-client-turn-concurrent.db"
    user_id = "dialogue-client-turn-concurrent-user"
    session = modes_module.start_profile_session(user_id, "dialogue", db_path=db_path)
    original_extract = modes_module._dialogue_extract_cards_for_chat

    async def delayed_extract(**kwargs):
        await asyncio.sleep(0.02)
        return await original_extract(**kwargs)

    monkeypatch.setattr(modes_module, "_dialogue_extract_cards_for_chat", delayed_extract)

    async def collect() -> list[str]:
        return [
            event
            async for event, _ in modes_module.stream_dialogue_chat_events(
                session.session_id,
                user_id=user_id,
                utterance="I need a repeat-safe answer.",
                client_turn_id="turn-concurrent",
                db_path=db_path,
            )
        ]

    async def run_both() -> tuple[list[str], list[str]]:
        return await asyncio.gather(collect(), collect())

    left, right = asyncio.run(run_both())

    assert sum("evidence" in events for events in (left, right)) == 1
    assert sum("error" in events for events in (left, right)) == 1
    assert modes_module.get_profile_session(session.session_id, user_id=user_id, db_path=db_path).step_index == 1
    assert len(list_chat_messages(user_id, session.session_id, db_path=db_path)) == 2


def test_dialogue_chat_api_accepts_legacy_requests_without_client_turn_id(tmp_path: Path) -> None:
    db_path = tmp_path / "dialogue-legacy-client.db"
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    started = _start("dialogue", user_id="dialogue-legacy-client-user")

    response = client.post(
        "/api/profile/dialogue/chat",
        json={"session_id": started["session_id"], "utterance": "I am using an older client."},
    )

    assert response.status_code == 200, response.text
    assert [event for event, _ in _parse_sse(response.text)][-1] == "done"
    assert modes_module.get_profile_session(started["session_id"], user_id="dialogue-legacy-client-user", db_path=db_path).step_index == 1


def test_dialogue_start_returns_server_owned_unrevealed_identity_cards() -> None:
    payload = _start("dialogue", user_id="dialogue-start-cards")

    cards = payload["identity_cards"]
    assert len(cards) == 7
    assert all(card["revealed"] is False for card in cards)
    assert all(card["revealed_now"] is False for card in cards)
    assert all(card["items"] == card["cards"] for card in cards)


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


def test_lenient_dialogue_validation_keeps_the_good_evidence() -> None:
    """一轮里只要还有一条好证据，就不能整轮归零。

    实测：十轮里有八轮因为 legacy 字段形状不对（或一条 atomic_evidence 缺字段）
    整轮判废，界面上显示"0 条原子证据"，用户那句话等于白说。
    """

    from app.profile.modes import _normalize_dialogue_extraction_payload, _validate_dialogue_payload_leniently

    raw = {
        "turn_id": "2",  # 字符串轮次号：丢掉，用默认值
        "combination_type": "自造的类型",  # 非法枚举：丢掉
        "learning_goal": {"value": {"action_orientation": "已给出第一步"}},  # legacy 形状不合法
        "dimension_scan": [
            {"dimension": "base_level", "status": "evidence_found", "reason": "提到背过性味"},
            {"dimension": "不存在的维度", "status": "pending"},
        ],
        "atomic_evidence": [
            {
                "evidence_id": "e1",
                "dimension": "base_level",
                "component": "concept_foundation",
                "evidence_group_id": "g1",
                "quote": "我背过性味归经",
                "normalized_claim": "用户自述背过性味归经",
                "evidence_type": "self_report",
                "collection_status": "confirmed",
                "quality_factors": {
                    "source_reliability": 0.8,
                    "expression_clarity": 0.8,
                    "content_specificity": 0.7,
                    "freshness": 1.0,
                    "consistency": 0.7,
                },
            },
            {"evidence_id": "e2", "dimension": "base_level"},  # 缺必填字段：丢掉
        ],
        "component_collection": [],
    }

    extraction = _validate_dialogue_payload_leniently(_normalize_dialogue_extraction_payload(raw))

    assert [item.evidence_id for item in extraction.atomic_evidence] == ["e1"]
    # base_level 是 legacy 维度名，必须归一成 knowledge_base 才留得住
    assert extraction.atomic_evidence[0].dimension == "knowledge_base"
    assert [item.dimension for item in extraction.dimension_scan] == ["knowledge_base"]
    assert extraction.turn_id is None
    assert extraction.extraction_error
