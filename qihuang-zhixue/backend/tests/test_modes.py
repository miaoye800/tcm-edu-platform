from pathlib import Path

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
from app.profile.nodes import diagnostic_eval_node, diagnostic_question_node, initial_state
from app.profile.store import list_evidence


client = TestClient(app)


def setup_function() -> None:
    reset_profile_sessions()
    app.dependency_overrides.clear()


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
    first = _start("practice", user_id="persist-user")
    _answer(first["session_id"], "practice", {"case": "热毒咽痛", "prescription": "金银花15g，连翘12g"})

    session = finish_profile_session(first["session_id"], db_path=db_path)
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
