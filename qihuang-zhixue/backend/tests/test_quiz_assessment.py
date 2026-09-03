from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import quiz as quiz_api
from app.main import app
from app.profile.schema import Goal, PlanningParams, Profile
from app.profile.store import upsert_profile
from app.quiz.schemas import QuizQuestion
from app.quiz.seed import seed_question_bank
from app.quiz.storage import get_question, init_quiz_db, list_all_questions
from tests.helpers import auth_headers_for_user_id


def _seed_tree(db_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "seed_knowledge_tree.py"
    spec = importlib.util.spec_from_file_location("quiz_seed_tree", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); module.seed(db_path)


def _setup(tmp_path: Path, *, fun: float = 0.2, goal: str = "应试提分", strictness: str = "宽松") -> tuple[TestClient, dict[str, str], Path]:
    db_path = tmp_path / "quiz.db"; _seed_tree(db_path); init_quiz_db(db_path); seed_question_bank(db_path, 36)
    app.dependency_overrides[quiz_api.get_quiz_db_path] = lambda: db_path
    client = TestClient(app); headers = auth_headers_for_user_id(client, "quiz-user", db_path)
    profile = Profile(user_id="quiz-user", planning_params=PlanningParams(fun_preference=fun, assessment_strictness=strictness), goal=Goal(normalized=goal, confirmed_by_user=True))
    upsert_profile(profile, db_path)
    return client, headers, db_path


def _question_ids(start: dict) -> list[str]:
    return [question["id"] for gate in start["gates"] for question in gate["questions"]]


def test_source_claims_and_public_questions_are_safe(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        QuizQuestion(id="bad", stem="x", options=[], answer=["x"], analysis="x", question_type="short", exam_point_id="p", source_level="real", source_detail={})
    client, headers, _ = _setup(tmp_path)
    response = client.post("/api/quiz/generate?node_id=general.p1", headers=headers)
    assert response.status_code == 200
    question = response.json()["questions"][0]
    assert "answer" not in question and "analysis" not in question


def test_profiles_change_composition_and_recent_question_is_downweighted(tmp_path: Path) -> None:
    _, _, db_path = _setup(tmp_path)
    from app.quiz.service import compose_quiz, grade_question
    import asyncio
    exam = asyncio.run(compose_quiz(user_id="quiz-user", node_id="general.p1", scenario="practice", db_path=db_path))
    first = exam.questions[0]
    asyncio.run(grade_question(user_id="quiz-user", question_id=first.id, raw_answer=first.answer, elapsed_seconds=None, db_path=db_path))
    next_group = asyncio.run(compose_quiz(user_id="quiz-user", node_id="general.p1", scenario="practice", db_path=db_path))
    assert next_group.questions[0].id != first.id
    profile = Profile(user_id="interest-user", planning_params=PlanningParams(fun_preference=0.9, assessment_strictness="宽松"), goal=Goal(normalized="兴趣科普", confirmed_by_user=True))
    upsert_profile(profile, db_path)
    interest = asyncio.run(compose_quiz(user_id="interest-user", node_id="general.p1", scenario="practice", db_path=db_path))
    assert [item.id for item in exam.questions] != [item.id for item in interest.questions]


def test_grade_semantic_fallback_records_mistake_remediation_and_profile_event(tmp_path: Path) -> None:
    client, headers, db_path = _setup(tmp_path)
    question = next(item for item in list_all_questions(db_path) if item["question_type"] == "short")
    response = client.post("/api/quiz/grade", json={"question_id":question["id"],"answer":"不相关"}, headers=headers)
    assert response.status_code == 200
    result = response.json()
    assert result["missing_points"] and result["remediation"] and "analysis" in result
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM mistake_record").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM mastery_events WHERE event_type='quiz'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM evidence WHERE user_id='quiz-user'").fetchone()[0] >= 1


def test_assessment_full_flow_marks_existing_completed_state(tmp_path: Path) -> None:
    client, headers, db_path = _setup(tmp_path, fun=0.95, strictness="宽松")
    started = client.post("/api/assessment/start", json={"node_id":"general.p1"}, headers=headers)
    assert started.status_code == 200
    payload = started.json(); assert {gate["id"] for gate in payload["gates"]} == {"quick","case","optional"}
    assert all("answer" not in question and "analysis" not in question for gate in payload["gates"] for question in gate["questions"])
    for question_id in _question_ids(payload):
        question = get_question(question_id, db_path); assert question
        response = client.post("/api/assessment/answer", json={"assessment_id":payload["assessment_id"],"question_id":question_id,"answer":question["answer"]}, headers=headers)
        assert response.status_code == 200
    finished = client.post("/api/assessment/finish", json={"assessment_id":payload["assessment_id"]}, headers=headers)
    assert finished.status_code == 200 and finished.json()["passed"] is True
    with sqlite3.connect(db_path) as conn:
        status, meta = conn.execute("SELECT status,meta FROM user_path_state WHERE user_id=? AND node_id=?", ("quiz-user","general.p1")).fetchone()
    assert status == "completed" and payload["assessment_id"] in meta


def test_build_script_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "script.db"; _seed_tree(db_path); seed_question_bank(db_path, 36)
    first = len(list_all_questions(db_path)); seed_question_bank(db_path, 36)
    assert first == len(list_all_questions(db_path)) == 36
