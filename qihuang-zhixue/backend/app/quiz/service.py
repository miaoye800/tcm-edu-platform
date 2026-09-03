"""QuizAgent selection, secure grading, and assessment orchestration."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.llm.clients import MockClient
from app.llm.router import ModelRouter
from app.mastery.bkt import get_mastery, record_mastery_event
from app.path.state import set_state
from app.profile.update import ProfileUpdateEvent, get_latest_profile, update_profile_from_event
from app.quiz.constants import (
    ASSESSMENT_GATE_WEIGHTS, ASSESSMENT_PASS_THRESHOLDS, CASE_RECOMMENDED_SECONDS,
    HIGH_FUN_PREFERENCE, OPTIONAL_RECOMMENDED_SECONDS, QUICK_QUESTION_COUNT,
    QUICK_RECOMMENDED_SECONDS, RECENT_QUESTION_LIMIT,
)
from app.quiz.prompts import SEMANTIC_GRADING_SYSTEM
from app.quiz.schemas import GradeResult, QuizGroup, QuizQuestion, QuizQuestionPublic
from app.quiz.storage import (
    create_assessment, finish_assessment, get_assessment, get_question, list_all_questions,
    list_questions_for_node, recent_history, save_assessment_answer, save_attempt, save_mistake,
)


def ensure_seed_questions(db_path: Path) -> None:
    """Lazy import avoids startup coupling while keeping an offline path usable."""
    if list_all_questions(db_path):
        return
    from app.quiz.seed import seed_question_bank
    seed_question_bank(db_path, target_count=36)


async def compose_quiz(*, user_id: str, node_id: str, scenario: str, db_path: Path) -> QuizGroup:
    ensure_seed_questions(db_path)
    profile = await get_latest_profile(user_id, db_path=db_path)
    targeted = list_questions_for_node(node_id, db_path)
    all_questions = list_all_questions(db_path)
    questions = targeted + [row for row in all_questions if row["id"] not in {item["id"] for item in targeted}]
    selected = _select_questions(questions, user_id=user_id, profile=profile, db_path=db_path, count=QUICK_QUESTION_COUNT)
    return QuizGroup(scenario="assessment" if scenario == "assessment" else "practice", questions=[_to_internal(row) for row in selected])


def public_question(question: QuizQuestion) -> dict[str, Any]:
    return QuizQuestionPublic.model_validate(question.model_dump(exclude={"answer", "analysis"})).model_dump()


def _select_questions(rows: list[dict[str, Any]], *, user_id: str, profile: Any, db_path: Path, count: int, question_type: str | None = None) -> list[dict[str, Any]]:
    recent_qids, recent_points = recent_history(user_id, db_path, RECENT_QUESTION_LIMIT)
    mastery = get_mastery(user_id, db_path=db_path)
    goal = str(profile.learning_goal.label or profile.goal.normalized or "")
    base_score = profile.knowledge_base.score if profile.knowledge_base.score is not None else 50
    candidates = [row for row in rows if question_type is None or row["question_type"] == question_type] or rows

    def rank(row: dict[str, Any]) -> tuple[float, str]:
        if goal == "应试提分":
            value = row["frequency"] * 2.0
        elif goal == "兴趣科普":
            # Interest learners start from broader, less exam-skewed coverage.
            value = 1.0 - row["frequency"]
        else:
            value = row["frequency"] * 0.7
        snapshot = mastery.get(row["node_id"])
        current_mastery = snapshot.get("p_mastery", 0.35) if isinstance(snapshot, dict) else (snapshot.p_mastery if snapshot else 0.35)
        value += max(0.0, 1.0 - current_mastery)
        if base_score < 45 and row["question_type"] in {"single", "short"}: value += 0.5
        if row["id"] in recent_qids: value -= 10
        if row["exam_point_id"] in recent_points: value -= 2
        return (-value, row["id"])

    ordered = sorted(candidates, key=rank)
    picked: list[dict[str, Any]] = []
    point_ids: set[str] = set()
    for item in ordered:
        if len(picked) >= count: break
        # Avoid repeating a point in one group whenever the bank offers alternatives.
        if item["exam_point_id"] in point_ids and len({x["exam_point_id"] for x in ordered}) >= count: continue
        picked.append(item); point_ids.add(item["exam_point_id"])
    return picked or ordered[:count]


def _source_level(row: dict[str, Any]) -> str:
    source = str(row["source"])
    if source.startswith("original:"): return "original"
    if source.startswith("adapted:"): return "adapted"
    return "real"


def _to_internal(row: dict[str, Any]) -> QuizQuestion:
    level = _source_level(row)
    if level == "real":
        detail: dict[str, Any] = {"year": row["year"], "source": row["source"], "copyright_notice": "仅限学习使用"}
    elif level == "adapted":
        detail = {"adapted_from_question_id": str(row["source"]).split(":", 2)[1], "changes": str(row["source"]).split(":", 2)[-1]}
    else:
        detail = {"knowledge_node_id": row["node_id"], "source": row["source"]}
    return QuizQuestion(id=row["id"], stem=row["stem"], options=row["options"], answer=row["answer"], analysis=row["analysis"], question_type=row["question_type"], exam_point_id=row["exam_point_id"], source_level=level, source_detail=detail)


async def grade_question(*, user_id: str, question_id: str, raw_answer: str | list[str], elapsed_seconds: float | None, db_path: Path) -> GradeResult:
    ensure_seed_questions(db_path)
    row = get_question(question_id, db_path)
    if row is None: raise ValueError("question not found")
    question = _to_internal(row)  # Validates source provenance again before grading.
    answer = _answer_list(raw_answer)
    if question.question_type in {"single", "multi"}:
        correct = {_norm(x) for x in answer} == {_norm(x) for x in question.answer}
        score, missing, comment = (100, [], "回答正确。") if correct else (0, list(question.answer), "答案与标准答案不一致。")
    else:
        score, missing, comment = await _semantic_grade(answer, question.answer)
        correct = score >= 60
    remediation = None if correct else _remediation(row)
    result = GradeResult(question_id=question.id, correct=correct, score=score, missing_points=missing, comment=comment, analysis=question.analysis, remediation=remediation)
    save_attempt(user_id=user_id, question_id=question.id, exam_point_id=question.exam_point_id, correct=correct, score=score, answer=answer, db_path=db_path)
    record_mastery_event(user_id=user_id, kc_id=row["node_id"], correct=correct, event_type="quiz", elapsed_seconds=elapsed_seconds, evidence={"question_id": question.id, "exam_point_id": question.exam_point_id, "score": score, "source_level": question.source_level}, db_path=db_path)
    if not correct: save_mistake(user_id=user_id, question=row, answer=answer, remediation=remediation or {}, db_path=db_path)
    await update_profile_from_event(user_id, ProfileUpdateEvent(type="quiz", payload={"answers":[{"question_id":question.id,"knowledge_point":row["exam_point_name"],"difficulty":"hard" if question.question_type in {"case","short"} else "easy","correct":correct}]}), db_path=db_path)
    return result


async def _semantic_grade(answer: list[str], expected: list[str]) -> tuple[int, list[str], str]:
    client = ModelRouter().select("generate")
    if not isinstance(client, MockClient):
        try:
            raw = await ModelRouter().chat("generate", [{"role":"system","content":SEMANTIC_GRADING_SYSTEM},{"role":"user","content":json.dumps({"answer":" ".join(answer),"expected":expected},ensure_ascii=False)}], response_format={"type":"json_object"}, temperature=0)
            parsed = json.loads(raw[raw.find("{"):raw.rfind("}")+1])
            score = max(0, min(100, int(parsed["score"])))
            return score, [str(x) for x in parsed.get("missing_points", [])], str(parsed.get("comment") or "已完成语义判分。")
        except Exception:
            pass
    joined = " ".join(_norm(item) for item in answer)
    missing = [item for item in expected if _norm(item) not in joined]
    covered = len(expected) - len(missing)
    score = round(100 * covered / max(1, len(expected)))
    return score, missing, "已按关键术语覆盖度判分。" if missing else "关键要点覆盖完整。"


def _remediation(row: dict[str, Any]) -> dict[str, Any]:
    return {"explain": {"resource_type":"explain","topic":row["exam_point_name"],"entry":"/api/resources/generate"}, "graph": {"center_id":row["node_id"],"entry":f"/api/graph/node/{row['node_id']}/detail"}}


async def start_assessment(*, user_id: str, node_id: str, db_path: Path) -> dict[str, Any]:
    ensure_seed_questions(db_path); profile = await get_latest_profile(user_id, db_path=db_path)
    targeted = list_questions_for_node(node_id, db_path)
    all_rows = list_all_questions(db_path)
    rows = targeted + [row for row in all_rows if row["id"] not in {item["id"] for item in targeted}]
    case = _select_questions(rows, user_id=user_id, profile=profile, db_path=db_path, count=1, question_type="case")
    quick_rows = [row for row in rows if row["question_type"] in {"single", "multi"} and row["id"] not in {item["id"] for item in case}]
    quick = _select_questions(quick_rows or rows, user_id=user_id, profile=profile, db_path=db_path, count=QUICK_QUESTION_COUNT)
    optional: list[dict[str, Any]] = []
    optional_name = None
    if profile.planning_params.fun_preference >= HIGH_FUN_PREFERENCE:
        optional, optional_name = _select_questions([row for row in rows if row["id"] not in {item["id"] for item in [*quick, *case]}], user_id=user_id, profile=profile, db_path=db_path, count=1, question_type="short"), "feynman"
    elif profile.planning_params.assessment_strictness == "宽松":
        optional, optional_name = _select_questions([row for row in rows if row["id"] not in {item["id"] for item in [*quick, *case]}], user_id=user_id, profile=profile, db_path=db_path, count=1, question_type="short"), "oral"
    gates = [{"id":"quick","name":"快问快答","recommended_seconds":QUICK_RECOMMENDED_SECONDS,"questions":[public_question(_to_internal(x)) for x in quick]}, {"id":"case","name":"病例情境","recommended_seconds":CASE_RECOMMENDED_SECONDS,"questions":[public_question(_to_internal(x)) for x in case]}]
    if optional_name: gates.append({"id":"optional","name":"费曼关" if optional_name == "feynman" else "小岐口试","recommended_seconds":OPTIONAL_RECOMMENDED_SECONDS,"questions":[public_question(_to_internal(x)) for x in optional]})
    payload = {"gates":gates,"strictness":profile.planning_params.assessment_strictness,"threshold":ASSESSMENT_PASS_THRESHOLDS[profile.planning_params.assessment_strictness]}
    assessment_id = create_assessment(user_id=user_id,node_id=node_id,payload=payload,db_path=db_path)
    return {"assessment_id":assessment_id,**payload}


async def answer_assessment(*, user_id: str, assessment_id: str, question_id: str, answer: str | list[str], elapsed_seconds: float | None, db_path: Path) -> dict[str, Any]:
    assessment = get_assessment(assessment_id,user_id,db_path)
    if not assessment or assessment["status"] != "started": raise ValueError("assessment is unavailable")
    gate = next((gate["id"] for gate in assessment["payload"]["gates"] if any(q["id"] == question_id for q in gate["questions"])), None)
    if not gate: raise ValueError("question does not belong to assessment")
    result = await grade_question(user_id=user_id,question_id=question_id,raw_answer=answer,elapsed_seconds=elapsed_seconds,db_path=db_path)
    save_assessment_answer(assessment_id,question_id,gate,result.model_dump(),db_path)
    return result.model_dump()


async def finish_assessment_for_user(*, user_id: str, assessment_id: str, db_path: Path) -> dict[str, Any]:
    assessment = get_assessment(assessment_id,user_id,db_path)
    if not assessment or assessment["status"] != "started": raise ValueError("assessment is unavailable")
    gates = assessment["payload"]["gates"]; results_by_gate: dict[str,list[int]] = defaultdict(list)
    for item in assessment["answers"]: results_by_gate[item["gate"]].append(int(item["result"]["score"]))
    missing_gates = [gate["id"] for gate in gates if not results_by_gate[gate["id"]]]
    if missing_gates: raise ValueError("all assessment gates must be answered")
    gate_scores = {gate: round(sum(scores)/len(scores),2) for gate,scores in results_by_gate.items()}
    active_weights = sum(ASSESSMENT_GATE_WEIGHTS[gate] for gate in gate_scores)
    total = round(sum(gate_scores[gate]*ASSESSMENT_GATE_WEIGHTS[gate] for gate in gate_scores)/active_weights,2)
    threshold = int(assessment["payload"]["threshold"]); passed = total >= threshold
    weak = []
    for answer in assessment["answers"]:
        if answer["result"]["correct"]:
            continue
        question = get_question(answer["question_id"], db_path)
        if question:
            weak.append(question["exam_point_id"])
    result = {"assessment_id":assessment_id,"node_id":assessment["node_id"],"score":total,"gate_scores":gate_scores,"threshold":threshold,"passed":passed,"weak_exam_point_ids":sorted(set(weak)),"remediation": {"entry":"/api/resources/generate","node_id":assessment["node_id"]} if not passed else None}
    finish_assessment(assessment_id,result,db_path)
    if passed: set_state(user_id,assessment["node_id"],"completed",meta={"assessment_id":assessment_id,"score":total,"gate_scores":gate_scores,"threshold":threshold,"finished_at":get_assessment(assessment_id,user_id,db_path)["finished_at"]},db_path=db_path)
    return result


def _answer_list(value: str | list[str]) -> list[str]: return [value.strip()] if isinstance(value,str) and value.strip() else [str(x).strip() for x in value if str(x).strip()]
def _norm(value: str) -> str: return "".join(value.strip().lower().split())
