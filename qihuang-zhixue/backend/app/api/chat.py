from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.graph import stream_agent_events

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatStreamRequest(BaseModel):
    """聊天流式接口入参。"""

    question: str = Field(min_length=1)


@router.post("/stream")
async def chat_stream(payload: ChatStreamRequest, request: Request) -> StreamingResponse:
    """运行 LangGraph，并将节点进度、增量文本和来源以 SSE 推送给前端。"""

    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question 不能为空")

    return StreamingResponse(
        _sse_event_iter(question=question, request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _sse_event_iter(question: str, request: Request) -> AsyncIterator[str]:
    async for item in stream_agent_events(question):
        if await request.is_disconnected():
            break

        yield _format_sse(event=item["event"], data=item["data"])


def _format_sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"

