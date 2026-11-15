"""AI 服务助手的 HTTP 层：会话 CRUD、对话流、轮次快照与确认续跑。

与 ``capabilities/http.py`` 同一套做法：这一层只做鉴权、参数形状与响应，
编排逻辑全在 ``loop.py`` / ``bridge.py``。好处是没有 FastAPI 也能测循环，
坏处是多一层——但这个项目里工坊已经吃过「逻辑写在端点里，测试只能起服务」的亏。

**进程内 run 注册表**：一条对话流背后是一个 asyncio 任务。它挂在模块字典里，
所以「取消」能拿到同一个任务。多进程部署时这张表不共享，取消会失效——与
``resources/service.py`` 的 ``_EVENT_CACHE`` 是同一个限制，写在这里免得被当成 bug。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.profile.store import DB_PATH
from app.resources import storage as resource_storage

from . import store
from .bridge import watch_task
from .contract import ChatTurnRequest, CreateConversationRequest, RenameConversationRequest
from .events import envelope, get_channel, open_channel, sse
from .loop import run_turn
from .prompts import plain_text_of

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

#: run_id → 正在跑的 asyncio 任务。只在本进程有效。
_RUNNERS: dict[str, asyncio.Task[Any]] = {}


def get_assistant_db_path() -> Path:
    return DB_PATH


def _sse_response(iterator: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        iterator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #


@router.post("/conversations", status_code=201)
async def create_conversation(
    payload: CreateConversationRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_assistant_db_path),
) -> dict[str, Any]:
    return store.create_conversation(current_user.user_id, payload.title, db_path)


@router.get("/conversations")
async def list_conversations(
    limit: int = 30,
    offset: int = 0,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_assistant_db_path),
) -> dict[str, Any]:
    items = store.list_conversations(current_user.user_id, limit=max(1, min(limit, 100)), offset=max(0, offset), db_path=db_path)
    return {"items": items}


@router.get("/conversations/{conversation_id}")
async def read_conversation(
    conversation_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_assistant_db_path),
) -> dict[str, Any]:
    conversation = store.get_conversation(conversation_id, current_user.user_id, db_path)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        **conversation,
        "messages": store.list_messages(conversation_id, current_user.user_id, db_path=db_path),
        "artifacts": store.list_artifacts(conversation_id, current_user.user_id, db_path=db_path),
    }


@router.patch("/conversations/{conversation_id}")
async def patch_conversation(
    conversation_id: str,
    payload: RenameConversationRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_assistant_db_path),
) -> dict[str, Any]:
    updated = store.update_conversation(
        conversation_id,
        current_user.user_id,
        title=payload.title or None,
        archived=payload.archived or None,
        db_path=db_path,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return updated


# --------------------------------------------------------------------------- #
# 对话流
# --------------------------------------------------------------------------- #


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatTurnRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_assistant_db_path),
) -> StreamingResponse:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="这句话是空的")

    conversation = (
        store.get_conversation(payload.conversation_id, current_user.user_id, db_path)
        if payload.conversation_id
        else None
    )
    if conversation is None:
        conversation = store.create_conversation(current_user.user_id, store.auto_title(text), db_path)
    conversation_id = str(conversation["conversation_id"])

    # 轮次幂等：同一个 client_turn_id 重放，返回**同一条流**，不新建消息与任务。
    existing = store.find_run_by_turn(conversation_id, payload.client_turn_id, db_path)
    if existing is not None:
        return _sse_response(_replay_stream(str(existing["run_id"])))

    history = [
        # message_id 也带上：摘要把「压到哪一条」记在会话上，没有 id 就只能按条数猜，
        # 而条数会因为重放/失败多出一条而错位。
        {"role": item["role"], "content": plain_text_of(item["content"]), "message_id": str(item["message_id"])}
        for item in store.list_messages(conversation_id, current_user.user_id, db_path=db_path)
    ]
    user_message = store.append_message(conversation_id, "user", {"type": "text", "text": text}, db_path=db_path)
    run = store.create_run(conversation_id, str(user_message["message_id"]), turn_key=payload.client_turn_id, db_path=db_path)
    run_id = str(run["run_id"])
    store.touch_conversation(conversation_id, db_path)

    channel = open_channel(run_id)

    async def worker() -> None:
        result: dict[str, Any] = {"status": "failed"}
        try:
            result = await run_turn(
                conversation_id=conversation_id,
                run_id=run_id,
                text=text,
                history=history,
                actor=current_user,
                channel=channel,
                db_path=db_path,
                refs=payload.refs,
                node_id=payload.node_id,
            )
        finally:
            channel.publish(
                envelope(
                    "done",
                    {
                        "run_id": run_id,
                        "conversation_id": conversation_id,
                        "message_id": user_message["message_id"],
                        "status": result.get("status", "failed"),
                    },
                )
            )
            channel.close()
            _RUNNERS.pop(run_id, None)

    _RUNNERS[run_id] = asyncio.create_task(worker())

    shop = envelope("message_start", {"run_id": run_id, "conversation_id": conversation_id, "message_id": user_message["message_id"]})

    async def stream() -> AsyncIterator[str]:
        yield sse(shop)
        async for item in channel.stream():
            yield sse(item)

    return _sse_response(stream())


async def _replay_stream(run_id: str) -> AsyncIterator[str]:
    """重放一条已存在的轮次。通道还在就补缓冲；进程重启过就只好给快照。"""

    channel = get_channel(run_id)
    if channel is not None:
        async for item in channel.stream(replay=True):
            yield sse(item)
        return
    yield sse(envelope("done", {"run_id": run_id, "status": "done", "replayed": True}))


@router.get("/runs/{run_id}")
async def read_run(
    run_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_assistant_db_path),
) -> dict[str, Any]:
    run = store.get_run(run_id, current_user.user_id, db_path)
    if run is None:
        raise HTTPException(status_code=404, detail="没有这一轮")
    artifacts = store.list_artifacts(str(run["conversation_id"]), current_user.user_id, db_path=db_path)
    return {**run, "artifacts": artifacts}


@router.get("/runs/{run_id}/stream")
async def resume_run(
    run_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_assistant_db_path),
) -> StreamingResponse:
    run = store.get_run(run_id, current_user.user_id, db_path)
    if run is None:
        raise HTTPException(status_code=404, detail="没有这一轮")
    return _sse_response(_replay_stream(run_id))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_assistant_db_path),
) -> dict[str, Any]:
    run = store.get_run(run_id, current_user.user_id, db_path)
    if run is None:
        raise HTTPException(status_code=404, detail="没有这一轮")
    task = _RUNNERS.get(run_id)
    if task is not None and not task.done():
        task.cancel()
        return {"ok": True, "run_id": run_id, "status": "cancelling"}
    # 已经停了：同时把还在跑的生成任务打上取消标记，避免「点了停止却还在炼」。
    for artifact in store.list_artifacts(str(run["conversation_id"]), current_user.user_id, db_path=db_path):
        task_id = str(artifact.get("task_id") or "")
        if task_id and artifact.get("state") in ("accepted", "awaiting_plan"):
            running = resource_storage.get_task(task_id, current_user.user_id, db_path)
            if running is not None and running.get("status") not in ("done", "failed"):
                done = dict(running.get("steps_done") or {})
                done["cancel_requested"] = True
                resource_storage.update_task(task_id, current_user.user_id, steps_done=done, db_path=db_path)
    store.update_run(run_id, status="cancelled", finished=True, db_path=db_path)
    return {"ok": True, "run_id": run_id, "status": "cancelled"}


# --------------------------------------------------------------------------- #
# 确认续跑
# --------------------------------------------------------------------------- #


@router.post("/artifacts/{task_id}/plan/confirm")
async def confirm_plan(
    task_id: str,
    payload: dict[str, Any],
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_assistant_db_path),
) -> dict[str, Any]:
    """确认走既有端点，**不复制一份闸门语义**。

    v2/v3 的分流、空方案拦截、`gate_step` 续跑位置都在那边；复制一遍就等于
    维护两套「确认之后从哪里接着跑」的定义，迟早不一致。
    """

    from app.api.resources import ConfirmPlanRequest, confirm_task_plan

    artifact = store.get_artifact_by_task(task_id, db_path)
    if artifact is None or store.get_conversation(str(artifact["conversation_id"]), current_user.user_id, db_path) is None:
        raise HTTPException(status_code=404, detail="这份产物不属于当前用户")
    request = ConfirmPlanRequest(plan=payload.get("plan") or {}, design=payload.get("design"))
    result = await confirm_task_plan(task_id, request, current_user, db_path)
    store.update_artifact_by_task(task_id, state="accepted", db_path=db_path)
    return result


@router.get("/artifacts/{task_id}/stream")
async def stream_artifact(
    task_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_assistant_db_path),
) -> StreamingResponse:
    """确认之后的续流。事件形状与对话流一致，前端不用换解析器。"""

    artifact = store.get_artifact_by_task(task_id, db_path)
    if artifact is None or store.get_conversation(str(artifact["conversation_id"]), current_user.user_id, db_path) is None:
        raise HTTPException(status_code=404, detail="这份产物不属于当前用户")

    channel = open_channel(f"task:{task_id}")

    async def worker() -> None:
        outcome = await watch_task(
            task_id=task_id,
            user_id=current_user.user_id,
            channel=channel,
            call_id=str(artifact.get("call_id") or ""),
            capability=str(artifact.get("capability") or ""),
            kind=str(artifact.get("kind") or "resource"),
            db_path=db_path,
            conversation_id=str(artifact["conversation_id"]),
        )
        channel.publish(envelope("done", {"task_id": task_id, "run_id": str(artifact.get("run_id") or ""), "status": outcome.state}))
        channel.close()

    asyncio.create_task(worker())

    async def stream() -> AsyncIterator[str]:
        async for item in channel.stream():
            yield sse(item)

    return _sse_response(stream())
