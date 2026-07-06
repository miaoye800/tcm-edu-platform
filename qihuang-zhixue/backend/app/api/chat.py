from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.graph import stream_agent_events
from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.profile import storage as profile_storage

router = APIRouter(prefix="/api/chat", tags=["chat"])
DEFAULT_SESSION_ID = "default"


class ChatStreamRequest(BaseModel):
    """聊天流式接口入参。"""

    question: str = Field(min_length=1)
    session_id: str | None = Field(default=None, min_length=1)


@router.post("/stream")
async def chat_stream(
    payload: ChatStreamRequest,
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
) -> StreamingResponse:
    """运行 LangGraph，并将节点进度、增量文本和来源以 SSE 推送给前端。"""

    question = payload.question.strip()
    user_id = current_user.user_id
    session_id = (payload.session_id or DEFAULT_SESSION_ID).strip() or DEFAULT_SESSION_ID
    if not question:
        raise HTTPException(status_code=422, detail="question 不能为空")

    return StreamingResponse(
        _sse_event_iter(question=question, user_id=user_id, session_id=session_id, request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _sse_event_iter(question: str, user_id: str, session_id: str, request: Request) -> AsyncIterator[str]:
    history_messages = profile_storage.list_chat_messages(user_id=user_id, session_id=session_id)
    answer_parts: list[str] = []
    completed = False

    async for item in stream_agent_events(
        question,
        user_id=user_id,
        session_id=session_id,
        history_messages=history_messages,
    ):
        if await request.is_disconnected():
            break

        if item["event"] == "token":
            answer_parts.append(str(item["data"].get("text", "")))
        if item["event"] == "done":
            item["data"]["user_id"] = user_id
            item["data"]["session_id"] = session_id
            completed = True

        yield _format_sse(event=item["event"], data=item["data"])

    if completed:
        profile_storage.append_chat_messages(
            user_id=user_id,
            session_id=session_id,
            messages=[
                {"role": "user", "content": question},
                {"role": "assistant", "content": "".join(answer_parts)},
            ],
        )


def _format_sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"
