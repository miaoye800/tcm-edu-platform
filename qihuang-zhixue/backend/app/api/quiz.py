"""Secure stage 3 quiz and mixed-assessment API."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.profile.store import DB_PATH
from app.quiz.schemas import AssessmentAnswerRequest, AssessmentFinishRequest, AssessmentStartRequest, GradeRequest
from app.quiz.service import answer_assessment, compose_quiz, finish_assessment_for_user, grade_question, public_question, start_assessment
from app.quiz.storage import get_assessment

router = APIRouter(prefix="/api", tags=["quiz"])


def get_quiz_db_path() -> Path:
    """Shared DB dependency; override this in API tests."""
    return DB_PATH


@router.post("/quiz/generate")
async def generate_quiz(node_id: str, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_quiz_db_path)) -> dict[str, object]:
    group = await compose_quiz(user_id=current_user.user_id, node_id=node_id, scenario="practice", db_path=db_path)
    return {"scenario":group.scenario,"questions":[public_question(question) for question in group.questions]}


@router.post("/quiz/grade")
async def grade_quiz(payload: GradeRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_quiz_db_path)) -> dict[str, object]:
    try:
        return (await grade_question(user_id=current_user.user_id,question_id=payload.question_id,raw_answer=payload.answer,elapsed_seconds=payload.elapsed_seconds,db_path=db_path)).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/assessment/start")
async def assessment_start(payload: AssessmentStartRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_quiz_db_path)) -> dict[str, object]:
    return await start_assessment(user_id=current_user.user_id,node_id=payload.node_id,db_path=db_path)


@router.post("/assessment/answer")
async def assessment_answer(payload: AssessmentAnswerRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_quiz_db_path)) -> dict[str, object]:
    try:
        return await answer_assessment(user_id=current_user.user_id,assessment_id=payload.assessment_id,question_id=payload.question_id,answer=payload.answer,elapsed_seconds=payload.elapsed_seconds,db_path=db_path)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/assessment/finish")
async def assessment_finish(payload: AssessmentFinishRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_quiz_db_path)) -> dict[str, object]:
    try:
        return await finish_assessment_for_user(user_id=current_user.user_id,assessment_id=payload.assessment_id,db_path=db_path)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/assessment/{assessment_id}")
async def assessment_detail(assessment_id: str, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_quiz_db_path)) -> dict[str, object]:
    assessment = get_assessment(assessment_id,current_user.user_id,db_path)
    if assessment is None: raise HTTPException(status_code=404, detail="assessment not found")
    # Stored payload has the same public questions as start; do not reveal answer material.
    return assessment

