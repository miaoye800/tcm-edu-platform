"""HTTP 层：把能力表与统一调用口暴露出去。

路径与既有资源接口并列而不重叠：

- ``GET  /api/capabilities``            能力目录（给调用方自己发现有什么）
- ``GET  /api/capabilities/{name}``     单条声明（字段、错误码、artifact）
- ``POST /api/capabilities/{name}``     统一调用（202/200，失败按码给状态）
- ``GET  /api/capability-invocations/{id}``        轮询兜底
- ``POST /api/capability-invocations/{id}/cancel`` 取消

**为什么还要轮询兜底**：SSE 会断，尤其是在移动网络与代理后面。断了之后
调用方需要一个不依赖流的真相来源。它也顺便承担「重放拿到的 task_id 现在
跑到哪了」这个问题。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.profile.store import DB_PATH
from app.resources import storage

from . import dispatch, registry
from . import invocation as invocation_store
from .bootstrap import install
from .errors import CapabilityError, ErrorCode
from .spec import CapabilityResult

router = APIRouter(prefix="/api", tags=["capabilities"])

install()


def get_capability_db_path() -> Path:
    return DB_PATH


def _respond(result: CapabilityResult) -> JSONResponse:
    return JSONResponse(status_code=result.http_status, content=result.body, headers=result.headers or None)


def _error(exc: CapabilityError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.body())


@router.get("/capabilities")
async def list_capabilities() -> dict[str, Any]:
    return registry.snapshot()


@router.get("/capabilities/{name}")
async def describe_capability(name: str) -> Any:
    entry = registry.get(name)
    if entry is None:
        return _error(CapabilityError(ErrorCode.UNKNOWN_CAPABILITY, f"没有这个能力：{name}"))
    return entry.spec.public()


@router.post("/capabilities/{name}")
async def invoke_capability(
    name: str,
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_capability_db_path),
) -> Any:
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        result = await dispatch.invoke(name, body, current_user, db_path=db_path)
    except CapabilityError as exc:
        return _error(exc)
    return _respond(result)


@router.get("/capability-invocations/{invocation_id}")
async def read_invocation(
    invocation_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_capability_db_path),
) -> Any:
    record = invocation_store.get(invocation_id, current_user.user_id, db_path)
    if record is None:
        return _error(CapabilityError(ErrorCode.NOT_FOUND, "没有这次调用", remedy="确认 invocation_id"))
    task = storage.get_task(str(record["task_id"]), current_user.user_id, db_path) if record.get("task_id") else None
    record = invocation_store.reconcile(invocation_id, task, current_user.user_id, db_path) or record
    resource_id = record.get("resource_id") or (task or {}).get("resource_id")
    payload = {
        "ok": True,
        "invocation_id": record["invocation_id"],
        "capability": record["capability"],
        "status": record["status"],
        "task_id": record.get("task_id") or "",
        "resource_id": resource_id,
        "created_at": record.get("created_at", ""),
        "task": None if task is None else {"status": task.get("status"), "current_step": task.get("current_step")},
    }
    if resource_id:
        # 产物形状必须从**这次能力自己的声明**派生。写死 kind="mindmap" 时，
        # 幻灯任务的轮询回包会把 open_url 指到导图页——调用方拿到的是一个
        # 结构合法但语义错误的地址，比报错更难发现。
        payload["artifact"] = _artifact_of(str(record["capability"]), resource_id)
    return payload


def _artifact_of(capability: str, resource_id: str) -> dict[str, Any]:
    """按能力声明拼 artifact。`open_url_template` 用 `{resource_id}` 占位。"""

    entry = registry.get(capability)
    spec = entry.spec if entry is not None else None
    if spec is None or spec.artifact is None:
        return {"kind": "unknown", "resource_id": resource_id, "open_url": None}
    return {
        "kind": spec.artifact.kind,
        "resource_id": resource_id,
        "open_url": spec.artifact.open_url_template.format(resource_id=resource_id),
        "resolve_on": spec.artifact.resolve_on,
    }


@router.post("/capability-invocations/{invocation_id}/cancel")
async def cancel_invocation(
    invocation_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_capability_db_path),
) -> Any:
    record = invocation_store.get(invocation_id, current_user.user_id, db_path)
    if record is None:
        return _error(CapabilityError(ErrorCode.NOT_FOUND, "没有这次调用", remedy="确认 invocation_id"))
    if record["status"] in ("done", "failed", "cancelled"):
        return _error(CapabilityError(ErrorCode.ALREADY_FINISHED, "已经结束了，不能再取消"))
    task_id = str(record.get("task_id") or "")
    if not task_id:
        # 还没接上任务：直接收尾，没有跑起来的东西可停。
        invocation_store.finish(invocation_id, status="cancelled", db_path=db_path)
        return JSONResponse(status_code=202, content={"ok": True, "invocation_id": invocation_id, "status": "cancelled"})

    task = storage.get_task(task_id, current_user.user_id, db_path)
    if task is not None and task.get("status") in ("done", "failed"):
        invocation_store.finish(invocation_id, status=str(task.get("status")), db_path=db_path)
        return _error(CapabilityError(ErrorCode.ALREADY_FINISHED, "任务已经跑完了，取消不了"))

    # 打标记而不是杀进程：流会在下一个工序边界自己停下，已完成的工序不重跑。
    done = dict(task.get("steps_done", {}) or {}) if task is not None else {}
    done["cancel_requested"] = True
    if task is not None:
        storage.update_task(task_id, current_user.user_id, steps_done=done, db_path=db_path)
    invocation_store.finish(invocation_id, status="cancelled", db_path=db_path)
    return JSONResponse(status_code=202, content={"ok": True, "invocation_id": invocation_id, "status": "cancelled", "task_id": task_id})
