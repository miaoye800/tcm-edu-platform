"""HTTP and SSE interface for profile-aware immersive node dialogue."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.config import Settings, get_settings
from app.immersive import service
from app.immersive.schemas import ChatRequest, ConfirmRequest, Recommendation, SessionRequest
from app.profile.store import DB_PATH

router = APIRouter(prefix="/api/immersive", tags=["immersive"])


def get_immersive_db_path() -> Path:
    return DB_PATH


def get_immersive_settings() -> Settings:
    return get_settings()


@router.post("/session")
async def start_immersive_session(
    payload: SessionRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_immersive_db_path),
) -> dict[str, Any]:
    try:
        return service.create_session(current_user.user_id, payload.node_id, db_path=db_path)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/chat")
async def immersive_chat(
    payload: ChatRequest,
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_immersive_db_path),
    settings: Settings = Depends(get_immersive_settings),
) -> StreamingResponse:
    return StreamingResponse(
        _chat_events(
            user_id=current_user.user_id,
            session_id=payload.session_id,
            message=payload.message,
            request=request,
            db_path=db_path,
            settings=settings,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/confirm", status_code=201)
async def confirm_immersive_resources(
    payload: ConfirmRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_immersive_db_path),
    settings: Settings = Depends(get_immersive_settings),
) -> dict[str, list[str]]:
    try:
        # Request parsing is not a trust boundary. Validate the serialized
        # browser payload again before it reaches resource_task creation.
        resources = [Recommendation.model_validate(item.model_dump(mode="json")) for item in payload.resources]
        task_ids = await service.confirm_resources(
            current_user.user_id,
            payload.session_id,
            resources,
            db_path=db_path,
            settings=settings,
        )
        return {"task_ids": task_ids}
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _chat_events(
    *,
    user_id: str,
    session_id: str,
    message: str,
    request: Request,
    db_path: Path,
    settings: Settings,
) -> AsyncIterator[str]:
    try:
        result = await service.chat_turn(user_id, session_id, message, db_path=db_path, settings=settings)
    except LookupError as exc:
        yield _sse("error", {"message": str(exc), "status": 404})
        return
    except Exception:
        # The service already uses a deterministic fallback for expected LLM
        # failures. This protects SSE clients against unexpected integration faults.
        yield _sse("error", {"message": "对话暂不可用，请稍后重试。", "status": 500})
        return

    for token in result.reply:
        if await request.is_disconnected():
            return
        yield _sse("token", {"text": token})
    yield _sse("recommendation", {"recommendation": [item.model_dump(mode="json") for item in result.recommendation]})
    yield _sse("done", {"session_id": session_id})


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
