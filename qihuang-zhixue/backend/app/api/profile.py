"""Profile collection session API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.config import Settings, get_settings
from app.profile.agent import run_cold_start
from app.profile.modes import (
    cards_to_json,
    ProfileMode,
    ProfileSession,
    answer_profile_session,
    dialogue_session_summary,
    dialogue_session_busy,
    finish_profile_session_async,
    get_profile_session,
    session_payload,
    start_profile_session,
    stream_dialogue_chat_events,
)
from app.profile.calibration import CalibrationSubmission, next_calibration, settle_calibration
from app.profile.ability import get_ability
from app.profile.narrative import synthesize_profile_narrative
from app.profile.schema import Profile
from app.profile.goal_agent import GoalAgentResult, run_goal_agent
from app.profile.schema import Goal
from app.profile.store import DB_PATH, add_evidence, get_profile, get_trust, list_profile_changes, record_profile_change, upsert_profile
from app.profile.update import get_latest_profile, profile_to_response
from app.profile.evidence import EvidenceCard, EvidenceTier

router = APIRouter(prefix="/api/profile", tags=["profile"])


class StartSessionRequest(BaseModel):
    user_id: str | None = Field(default=None, min_length=1)
    mode: ProfileMode


class AnswerSessionRequest(BaseModel):
    session_id: str = Field(min_length=1)
    step_id: str | None = Field(default=None, min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    answer: dict[str, Any] | None = None
    response_time_ms: int | None = Field(default=None, ge=0)


class FinishSessionRequest(BaseModel):
    session_id: str = Field(min_length=1)


class DialogueChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    utterance: str = Field(min_length=1)
    client_turn_id: str | None = Field(default=None, min_length=1, max_length=128)


class ColdStartRequest(BaseModel):
    user_id: str | None = Field(default=None, min_length=1)
    first_message: str = Field(min_length=1)
    diagnostic_answers: dict[str, str] = Field(default_factory=dict)


class GoalRequest(BaseModel):
    raw_text: str = Field(min_length=1, max_length=2000)


class GoalConfirmRequest(BaseModel):
    candidate: GoalAgentResult


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

    return await _profile_narrative_payload(current_user.user_id, db_path=db_path, settings=settings)


@router.get("/{user_id}/narrative")
async def read_profile_narrative_by_user_id(
    user_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
    settings: Settings = Depends(get_profile_settings),
) -> dict[str, Any]:
    """Return a guarded structured narrative for the requested current user."""

    if user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="cannot read another user's profile narrative")
    return await _profile_narrative_payload(user_id, db_path=db_path, settings=settings)


@router.get("")
async def read_profile(
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return the latest learning profile with per-dimension confidence."""

    profile = await get_latest_profile(current_user.user_id, db_path=db_path)
    return _profile_response_with_changes(profile, db_path=db_path, limit=limit, offset=offset)


@router.get("/calibration/next")
async def read_next_calibration(
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
) -> dict[str, Any]:
    """Return the next calibration plan without exposing answer keys."""

    return next_calibration(current_user.user_id, db_path=db_path) or {
        "claim_id": None,
        "dimension": None,
        "questions": [],
    }


@router.post("/calibration/submit")
async def submit_calibration(
    payload: CalibrationSubmission,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
) -> dict[str, Any]:
    """Grade and settle one self-report calibration plan."""

    try:
        return settle_calibration(current_user.user_id, payload, db_path=db_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="calibration claim not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/trust")
async def read_profile_trust(
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
) -> dict[str, Any]:
    """Return the user's self-report trust coefficient and display tier."""

    trust = get_trust(current_user.user_id, db_path=db_path)
    if trust >= 0.85:
        tier = "深信不疑"
    elif trust >= 0.55:
        tier = "半信半疑"
    else:
        tier = "眼见为实"
    return {"trust": trust, "tier": tier}


@router.post("/cold-start")
async def cold_start(
    payload: ColdStartRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
) -> dict[str, Any]:
    """First-login or explicit profile-build entry."""

    user_id = payload.user_id or current_user.user_id
    if user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="cannot build a profile for another user")
    state = await run_cold_start(
        user_id=user_id,
        first_message=payload.first_message,
        diagnostic_answers=payload.diagnostic_answers,
        db_path=db_path,
    )
    return {
        "profile": profile_to_response(Profile.model_validate(state["profile"])),
        "trace": state["trace"],
    }


@router.post("/goal")
async def normalize_goal(
    payload: GoalRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
) -> dict[str, Any]:
    """Return an unpersisted planning proposal from a user's stated goal."""

    profile = get_profile(current_user.user_id, db_path=db_path)
    result = await run_goal_agent(payload.raw_text, profile=profile)
    return result.model_dump(mode="json")


@router.post("/goal/confirm")
async def confirm_goal(
    payload: GoalConfirmRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
) -> dict[str, Any]:
    """Persist a reviewed proposal and feed its goal assertion into profile fusion."""

    candidate = payload.candidate
    profile = get_profile(current_user.user_id, db_path=db_path)
    old_params = profile.planning_params.model_dump(mode="json")
    old_goal = profile.goal.model_dump(mode="json")
    confirmed_goal = candidate.goal.model_copy(update={"confirmed_by_user": True})
    profile.planning_params = candidate.planning_params
    profile.goal = confirmed_goal
    upsert_profile(profile, db_path=db_path)

    card = EvidenceCard(
        dimension="learning_goal",
        source="goal_agent_confirm",
        tier=EvidenceTier.SELF_REPORT,
        fact=f"用户确认目标：{confirmed_goal.raw_text}",
        objective_signal={"label": confirmed_goal.normalized},
        weight=max(0.3, confirmed_goal.confidence),
    )
    add_evidence(current_user.user_id, card, db_path=db_path)
    record_profile_change(
        current_user.user_id,
        "planning_params",
        old_params,
        profile.planning_params.model_dump(mode="json"),
        reason="用户确认 GoalAgent 规划参数",
        source_event="goal_confirm",
        evidence_ids=[card.evidence_id],
        db_path=db_path,
    )
    record_profile_change(
        current_user.user_id,
        "goal",
        old_goal,
        confirmed_goal.model_dump(mode="json"),
        reason="用户确认 GoalAgent 学习目标",
        source_event="goal_confirm",
        evidence_ids=[card.evidence_id],
        db_path=db_path,
    )
    fused_profile = await get_latest_profile(current_user.user_id, db_path=db_path)
    return {
        "planning_params": fused_profile.planning_params.model_dump(mode="json"),
        "goal": fused_profile.goal.model_dump(mode="json"),
        "restatement": candidate.restatement,
        "profile": profile_to_response(fused_profile),
    }


@router.post("/session/start")
async def start(
    payload: StartSessionRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
) -> dict[str, Any]:
    """Start a profile evidence collection session."""

    user_id = payload.user_id or current_user.user_id
    if user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="cannot start a profile session for another user")
    session = start_profile_session(user_id=user_id, mode=payload.mode, db_path=db_path)
    response = session_payload(session, status="start")
    if session.mode == ProfileMode.dialogue:
        response.update(dialogue_session_summary(session, db_path=db_path))
    return response


@router.get("/session/{session_id}")
async def read_session(
    session_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
) -> dict[str, Any]:
    """Restore an owned collection session after a refresh or stream break."""

    try:
        session = get_profile_session(session_id, user_id=current_user.user_id, db_path=db_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="画像会话已过期，请重新开始") from exc
    response = session_payload(session, status="finished" if session.finished else "restored")
    if session.mode == ProfileMode.dialogue:
        response.update(dialogue_session_summary(session, db_path=db_path))
    return response


@router.post("/session/answer")
async def answer(
    payload: AnswerSessionRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
) -> dict[str, Any]:
    """Advance a collection session and return newly emitted cards."""

    try:
        existing = get_profile_session(payload.session_id, user_id=current_user.user_id, db_path=db_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="profile session not found") from exc

    answer_payload = dict(payload.payload or payload.answer or {})
    if payload.response_time_ms is not None:
        answer_payload.setdefault("response_time_ms", payload.response_time_ms)
    step_id = payload.step_id or (existing.current_step.id if existing.current_step else None)
    if step_id is None:
        raise HTTPException(status_code=400, detail="profile session has no active step")
    try:
        session, cards = await answer_profile_session(
            payload.session_id,
            user_id=current_user.user_id,
            step_id=step_id,
            payload=answer_payload,
            db_path=db_path,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="profile session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = {
        **session_payload(session, status="finished" if session.finished else "advanced"),
        "evidence_cards": cards_to_json(cards),
    }
    if session.mode == ProfileMode.dialogue:
        response.update(dialogue_session_summary(session, db_path=db_path))
    return response


@router.post("/session/finish")
async def finish(
    payload: FinishSessionRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
) -> dict[str, Any]:
    """Finish a session and return all objective evidence cards."""

    try:
        existing = get_profile_session(payload.session_id, user_id=current_user.user_id, db_path=db_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="profile session not found") from exc

    try:
        session = await finish_profile_session_async(payload.session_id, user_id=current_user.user_id, db_path=db_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="profile session not found") from exc
    response = {
        **session_payload(session, status="finished"),
        "evidence_cards": cards_to_json(session.evidence_cards),
    }
    if session.mode == ProfileMode.dialogue:
        response.update(dialogue_session_summary(session, db_path=db_path))
    return response


@router.post("/dialogue/chat")
async def dialogue_chat(
    payload: DialogueChatRequest,
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
) -> StreamingResponse:
    """Stream one dialogue-profile turn with visible chat and hidden extraction."""

    utterance = payload.utterance.strip()
    if not utterance:
        raise HTTPException(status_code=422, detail="utterance cannot be empty")
    try:
        session = get_profile_session(payload.session_id, user_id=current_user.user_id, db_path=db_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="profile session not found") from exc
    if session.mode != ProfileMode.dialogue:
        raise HTTPException(status_code=400, detail="profile session is not dialogue mode")
    if dialogue_session_busy(payload.session_id, db_path=db_path):
        raise HTTPException(status_code=409, detail="本轮对话正在处理中，请勿重复提交")

    return StreamingResponse(
        _dialogue_sse_iter(
            session_id=payload.session_id,
            user_id=current_user.user_id,
            utterance=utterance,
            client_turn_id=payload.client_turn_id,
            db_path=db_path,
            request=request,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{user_id}")
async def read_profile_by_user_id(
    user_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_profile_db_path),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return the latest profile for the requested current user."""

    if user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="cannot read another user's profile")
    profile = await get_latest_profile(user_id, db_path=db_path)
    return _profile_response_with_changes(profile, db_path=db_path, limit=limit, offset=offset)


def _profile_response_with_changes(
    profile: Profile,
    *,
    db_path: Path,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    payload = profile_to_response(profile)
    payload["change_log"] = list_profile_changes(
        profile.user_id,
        limit=limit,
        offset=offset,
        db_path=db_path,
    )
    payload["change_log_pagination"] = {"limit": limit, "offset": offset}
    payload["ability"] = get_ability(profile.user_id, db_path=db_path).as_dict()
    return payload


def _session_payload(session: ProfileSession, status: str) -> dict[str, Any]:
    return session_payload(session, status=status)


async def _dialogue_sse_iter(
    *,
    session_id: str,
    user_id: str,
    utterance: str,
    client_turn_id: str | None,
    db_path: Path,
    request: Request,
) -> AsyncIterator[str]:
    async for event, data in stream_dialogue_chat_events(
        session_id,
        user_id=user_id,
        utterance=utterance,
        client_turn_id=client_turn_id,
        db_path=db_path,
    ):
        # Do not abort before the inner generator commits chat/evidence. A
        # browser may disconnect after message_done but before done; the
        # request-id replay then needs the committed state.
        if event == "done" and await request.is_disconnected():
            break
        yield _format_sse(event, data)


def _format_sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


async def _profile_narrative_payload(user_id: str, *, db_path: Path, settings: Settings) -> dict[str, Any]:
    profile = await get_latest_profile(user_id, db_path=db_path, settings=settings)
    narrative = await synthesize_profile_narrative(profile, settings=settings)
    return narrative.model_dump(mode="json")
