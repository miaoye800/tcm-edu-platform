"""统一调用入口：校验 → 幂等 → 分流 → 统一响应。

**为什么请求体允许两种形状**：契约文档里写的是

```json
{"client_invocation_id": "...", "query": "...", "params": {"depth": 3}}
```

而工具调用（模型直接调）生成的是扁平参数。两种都是真实调用方，强迫任何
一边改造都是白费。这里把 ``params`` 展开合并到顶层：显式顶层字段优先，
因为「我明确写了 query」比「params 里那个 query」更可信。

**为什么 replay 要重新拼响应**：调用方重放时拿到的必须是**同一个**
``task_id`` 和 ``invocation_id``，否则它的去重逻辑就失效了。artifact 里
``resource_id`` 若已可就填上，没跑完就是 null——不猜。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.profile.store import DB_PATH
from app.resources import storage

from . import invocation as invocation_store
from . import registry
from .errors import CapabilityError, ErrorCode
from .handlers import CapabilityInput
from .spec import CapabilityResult, CapabilitySpec

_JOB = "job"


def flatten(body: dict[str, Any]) -> dict[str, Any]:
    """把 ``{query, params:{…}, context:{…}}`` 摊平成模型字段。"""

    if not isinstance(body, dict):
        return {}
    params = body.get("params")
    merged: dict[str, Any] = {}
    if isinstance(params, dict):
        merged.update(params)
    for key, value in body.items():
        if key == "params":
            continue
        merged[key] = value
    return merged


def validate(spec: CapabilitySpec, body: dict[str, Any]) -> CapabilityInput:
    try:
        return spec.input_model.model_validate(flatten(body))
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "body"
        raise CapabilityError(
            ErrorCode.INVALID_INPUT,
            f"{spec.name} 的参数 {field} 不合法：{first['msg']}",
            remedy="按 GET /api/capabilities/" + spec.name + " 的 input_schema 传参",
        ) from exc


def _replay_body(spec: CapabilitySpec, record: dict[str, Any], task: dict[str, Any] | None) -> dict[str, Any]:
    resource_id = record.get("resource_id") or (task or {}).get("resource_id")
    task_id = record.get("task_id") or (task or {}).get("task_id") or ""
    artifact = None
    if spec.artifact is not None:
        artifact = {
            "kind": spec.artifact.kind,
            "resource_id": resource_id,
            "open_url": spec.artifact.open_url_template.format(resource_id=resource_id) if resource_id else None,
            "structure": (task or {}).get("meta", {}).get("params", {}).get("structure") or spec.artifact.structure,
            "theme_id": (task or {}).get("meta", {}).get("params", {}).get("theme") or spec.artifact.theme,
            "resolve_on": spec.artifact.resolve_on,
        }
    return {
        "ok": True,
        "capability": spec.name,
        "capability_version": spec.version,
        "invocation_id": record["invocation_id"],
        "status": record.get("status") or "accepted",
        "replayed": True,
        "task_id": task_id,
        "stream_url": f"/api/resource/tasks/{task_id}/stream" if task_id else "",
        "artifact": artifact,
    }


async def invoke(name: str, body: dict[str, Any], actor: Any, db_path: Path = DB_PATH) -> CapabilityResult:
    entry = registry.get(name)
    if entry is None:
        raise CapabilityError(ErrorCode.UNKNOWN_CAPABILITY, f"没有这个能力：{name}", remedy="先 GET /api/capabilities")

    spec, handler = entry.spec, entry.handler
    payload = validate(spec, body)
    client_id = str(getattr(payload, "client_invocation_id", "") or "")

    if not spec.idempotency:
        # 查询/命令不落受理记录：它们秒回，记一次调用只会在库里堆垃圾。
        return await handler(payload, actor, invocation_id=invocation_store.new_id(), db_path=db_path)

    digest = invocation_store.digest_of(spec.name, str(getattr(payload, "query", "") or ""), _digest_params(payload))
    record, replayed = invocation_store.claim(
        capability=spec.name,
        user_id=actor.user_id,
        digest=digest,
        invocation_id=client_id,
        payload={"query": str(getattr(payload, "query", "") or "")},
        db_path=db_path,
    )
    if replayed:
        task = storage.get_task(str(record["task_id"]), actor.user_id, db_path) if record.get("task_id") else None
        record = invocation_store.reconcile(record["invocation_id"], task, actor.user_id, db_path) or record
        return CapabilityResult(http_status=202, body=_replay_body(spec, record, task))

    result = await handler(payload, actor, invocation_id=record["invocation_id"], db_path=db_path)
    task_id = str(result.body.get("task_id") or "")
    invocation_store.attach(record["invocation_id"], task_id=task_id, status="accepted" if task_id else "done", db_path=db_path)
    return result


def _digest_params(payload: CapabilityInput) -> dict[str, Any]:
    """摘要只吃**会影响产物**的字段，不含 context（它是旁路信息）。"""

    data = payload.model_dump(exclude_none=True)
    data.pop("client_invocation_id", None)
    data.pop("context", None)
    return data
