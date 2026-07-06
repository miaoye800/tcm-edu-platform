"""Profile collection session API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.config import Settings, get_settings
from app.profile.modes_demo import (
    ProfileMode,
    ProfileSession,
    answer_profile_session,
    finish_profile_session,
    get_profile_session,
    start_profile_session,
)
from app.profile.schema import evidence_card_to_json_dict
from app.profile.store import DB_PATH
from app.profile.narrative import synthesize_profile_narrative
from app.profile.update import get_latest_profile, profile_to_response

router = APIRouter(prefix="/api/profile", tags=["profile"])


class StartSessionRequest(BaseModel):
    mode: ProfileMode


class AnswerSessionRequest(BaseModel):
    session_id: str = Field(min_length=1)
    answer: dict[str, Any] = Field(default_factory=dict)
    response_time_ms: int | None = Field(default=None, ge=0)


class FinishSessionRequest(BaseModel):
    session_id: str = Field(min_length=1)


def get_profile_db_path() -> Path:
    """Return the evidence DB path; tests may override this dependency."""

    return DB_PATH


def get_profile_settings() -> Settings:
    """Return app settings; tests may override this dependency."""

    return get_settings()


@router.get("/narrative")
async def read_profile_narrative(
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
    settings: Settings = Depends(get_profile_settings),
) -> dict[str, Any]:
    """Return a guarded structured narrative for the latest learning profile."""

    profile = await get_latest_profile(current_user.user_id, db_path=db_path, settings=settings)
    narrative = await synthesize_profile_narrative(profile, settings=settings)
    return narrative.model_dump(mode="json")


@router.get("")
async def read_profile(
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
) -> dict[str, Any]:
    """Return the latest learning profile with per-dimension confidence."""

    profile = await get_latest_profile(current_user.user_id, db_path=db_path)
    return profile_to_response(profile)


@router.post("/session/start")
async def start(payload: StartSessionRequest, current_user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Start a profile evidence collection session."""

    session = start_profile_session(user_id=current_user.user_id, mode=payload.mode)
    return _session_payload(session, status="start")


@router.post("/session/answer")
async def answer(
    payload: AnswerSessionRequest,
    current_user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Advance a collection session and return newly emitted cards."""

    try:
        existing = get_profile_session(payload.session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="profile session not found") from exc
    if existing.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="profile session not found")

    answer_payload = dict(payload.answer)
    if payload.response_time_ms is not None:
        answer_payload.setdefault("response_time_ms", payload.response_time_ms)
    try:
        session, cards = await answer_profile_session(payload.session_id, answer_payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="profile session not found") from exc
    return {
        **_session_payload(session, status="finished" if session.finished else "advanced"),
        "evidence_cards": [evidence_card_to_json_dict(card) for card in cards],
    }


@router.post("/session/finish")
async def finish(
    payload: FinishSessionRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
) -> dict[str, Any]:
    """Finish a session and return all objective evidence cards."""

    try:
        existing = get_profile_session(payload.session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="profile session not found") from exc
    if existing.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="profile session not found")

    try:
        session = finish_profile_session(payload.session_id, db_path=db_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="profile session not found") from exc
    return {
        **_session_payload(session, status="finished"),
        "evidence_cards": [evidence_card_to_json_dict(card) for card in session.evidence_cards],
    }


def _session_payload(session: ProfileSession, status: str) -> dict[str, Any]:
    try:
        current_stage = get_profile_session(session.session_id).current_stage
    except KeyError:
        current_stage = None
    return {
        "session_id": session.session_id,
        "user_id": session.user_id,
        "mode": session.mode.value,
        "status": status,
        "current_stage": current_stage,
        "finished": session.finished,
        "evidence_count": len(session.evidence_cards),
    }
