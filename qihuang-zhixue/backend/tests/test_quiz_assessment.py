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


def _seed_single_questions(db_path: Path, node_id: str, name: str, count: int) -> None:
    """只造单选题，用来验证「按知识点出题」和「没有病例题就不摆病例关」。"""

    from app.quiz.storage import upsert_exam_point, upsert_question

    point_id = upsert_exam_point(
        node_id=node_id, name=name, exam_years=[], frequency=0.0,
        source=f"教材自编：测试·{name}·p1", db_path=db_path,
    )
    for index in range(count):
        upsert_question(
            {
                "exam_point_id": point_id,
                "stem": f"{name} 的第 {index + 1} 题：下列哪味药符合本知识点？",
                "options": [{"key": "A", "text": name}, {"key": "B", "text": "干扰药甲"}],
                "answer": ["A"],
                "analysis": "测试题",
                "year": None,
                "source": f"教材自编：测试·{name}·p1",
                "question_type": "single",
            },
            db_path,
        )


def test_assessment_asks_the_node_the_student_opened(tmp_path: Path) -> None:
    """点了「人参」就该考人参。

    踩过的坑：`start_assessment` 把当前知识点的题拼在 `rows` 前面，以为这样就优先，
    但 `_select_questions` 按 rank 重排，拼接顺序被完全抹平。实测请求 `d17.s1.p1`（人参）
    返回的是 `d01.s1.p1`（麻黄）的题——576 道题里每味药只有 3~4 道，不显式加权几乎选不中。
    """

    db_path = tmp_path / "scoped.db"; _seed_tree(db_path); init_quiz_db(db_path)
    _seed_single_questions(db_path, "general.p1", "人参", 4)
    _seed_single_questions(db_path, "general.p2", "麻黄", 4)
    app.dependency_overrides[quiz_api.get_quiz_db_path] = lambda: db_path
    client = TestClient(app); headers = auth_headers_for_user_id(client, "scoped-user", db_path)
    profile = Profile(user_id="scoped-user", planning_params=PlanningParams(fun_preference=0.2, assessment_strictness="严格"), goal=Goal(normalized="应试提分", confirmed_by_user=True))
    upsert_profile(profile, db_path)

    started = client.post("/api/assessment/start", json={"node_id": "general.p1"}, headers=headers)
    assert started.status_code == 200, started.text
    payload = started.json()
    ids = _question_ids(payload)
    assert ids, "一道题都没出"
    with sqlite3.connect(db_path) as conn:
        node_by_question = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT q.id, p.node_id FROM real_question q JOIN exam_point p ON p.id = q.exam_point_id"
            )
        }
    assert {node_by_question[qid] for qid in ids} == {"general.p1"}, (
        f"测评考的不是学生打开的那个知识点：{[(qid, node_by_question[qid]) for qid in ids]}"
    )


def test_gates_without_matching_question_type_are_not_shown(tmp_path: Path) -> None:
    """题库里没有病例题，就不要摆「病例情境」关装一道单选题。

    前端会把 `case` 关渲染成三页病例纸（辨证 / 选药 / 剂量）。给它塞一道
    「下列哪味药…」，学生看到的是「病例情境」标题配单选题——不是缺内容，
    是把别的东西说成病例题。同理，没有简答题就不该有「费曼关 / 小岐口试」。
    """

    db_path = tmp_path / "no_case.db"; _seed_tree(db_path); init_quiz_db(db_path)
    _seed_single_questions(db_path, "general.p1", "人参", 4)
    app.dependency_overrides[quiz_api.get_quiz_db_path] = lambda: db_path
    client = TestClient(app); headers = auth_headers_for_user_id(client, "gate-user", db_path)
    # 宽松 + 高趣味：按旧逻辑两个条件都会去挂 optional 关。
    profile = Profile(user_id="gate-user", planning_params=PlanningParams(fun_preference=0.9, assessment_strictness="宽松"), goal=Goal(normalized="兴趣科普", confirmed_by_user=True))
    upsert_profile(profile, db_path)

    payload = client.post("/api/assessment/start", json={"node_id": "general.p1"}, headers=headers).json()
    assert [gate["id"] for gate in payload["gates"]] == ["quick"], payload["gates"]
