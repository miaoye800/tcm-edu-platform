"""Node-study resource tasks and the user's durable resource library."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.profile.store import DB_PATH
from app.resources.constants import RESOURCE_TYPES, ResourceType
from app.resources import service, storage
from app.agents.resource_context import retrieve_resource_docs
from app.mindmap import MindmapAgent
from app.mindmap.schemas import ExpandRequest, ExpandResponse, MindElixirData, merge_branch
from app.profile.update import ProfileUpdateEvent, update_profile_from_event

router = APIRouter(prefix="/api", tags=["resources"])


def get_resource_db_path() -> Path:
    return DB_PATH


class CreateTaskRequest(BaseModel):
    node_id: str
    resource_type: ResourceType
    params: dict[str, object] = Field(default_factory=dict)


class PatchMindmapRequest(BaseModel):
    template_id: str


@router.post("/resource/tasks", status_code=201)
async def create_resource_task(payload: CreateTaskRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> dict[str, object]:
    if payload.resource_type not in RESOURCE_TYPES:
        raise HTTPException(status_code=422, detail="不支持的资源类型")
    if not storage.node_exists(payload.node_id, db_path):
        raise HTTPException(status_code=404, detail="知识节点不存在")
    task = storage.create_task(str(uuid4()), current_user.user_id, payload.node_id, payload.resource_type, db_path, meta={"params": payload.params})
    return task


@router.post("/resources/{resource_id}/refine", status_code=201)
async def refine_resource(resource_id: str, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> dict[str, object]:
    resource = storage.get_resource(resource_id, current_user.user_id, db_path)
    if resource is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    params = dict(resource.get("meta", {}).get("params", {}))
    return storage.create_task(str(uuid4()), current_user.user_id, str(resource["node_id"]), str(resource["type"]), db_path, meta={"params": params, "replay_of": resource_id})


@router.get("/resource/tasks/{task_id}/stream")
async def stream_resource_task(task_id: str, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        async for item in service.stream_task(task_id, current_user.user_id, db_path):
            yield f"event: {item['event']}\ndata: {json.dumps(item, ensure_ascii=False, separators=(',', ':'))}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/resources")
async def list_user_resources(node_id: str | None = None, type: ResourceType | None = None, favorited: bool | None = None, q: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> dict[str, object]:
    items, total = storage.list_resources(current_user.user_id, node_id=node_id, resource_type=type, favorited=favorited, query=q, offset=offset, limit=limit, db_path=db_path)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/resources/{resource_id}")
async def read_user_resource(resource_id: str, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> dict[str, object]:
    resource = storage.get_resource(resource_id, current_user.user_id, db_path)
    if resource is None: raise HTTPException(status_code=404, detail="资源不存在")
    return resource


@router.patch("/resources/{resource_id}/mindmap")
async def patch_mindmap_template(resource_id: str, payload: PatchMindmapRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> dict[str, object]:
    from app.mindmap.rules import ALLOWED_TEMPLATE_IDS
    resource = storage.get_resource(resource_id, current_user.user_id, db_path)
    if resource is None or resource["type"] != "mindmap":
        raise HTTPException(status_code=404, detail="导图资源不存在")
    if payload.template_id not in ALLOWED_TEMPLATE_IDS:
        raise HTTPException(status_code=422, detail="模板不在已批准枚举中")
    content = dict(resource["content"])
    content["template_id"] = payload.template_id
    saved = storage.update_resource_content(resource_id, current_user.user_id, content, db_path)
    # Reuse the established interaction event contract; failures cannot block UI persistence.
    try:
        await update_profile_from_event(current_user.user_id, ProfileUpdateEvent(type="interaction", payload={"subtype": "resource_choice", "selected_type": "mindmap", "resource_preference": "知识图谱", "topic": str(resource["node_id"]), "template_id": payload.template_id}), db_path=db_path)
    except Exception:
        pass
    return saved  # type: ignore[return-value]


@router.post("/mindmap/{resource_id}/expand", response_model=ExpandResponse)
async def expand_mindmap(resource_id: str, payload: ExpandRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> ExpandResponse:
    resource = storage.get_resource(resource_id, current_user.user_id, db_path)
    if resource is None or resource["type"] != "mindmap":
        raise HTTPException(status_code=404, detail="导图资源不存在")
    try:
        data = MindElixirData.model_validate(resource["content"].get("mindmap_data"))
        parent = data.nodeData
        if parent.id != payload.node_path[0]:
            raise ValueError("node_path root does not match mindmap")
        for node_id in payload.node_path[1:]:
            parent = next(child for child in parent.children if child.id == node_id)
    except (ValueError, StopIteration):
        raise HTTPException(status_code=422, detail="导图分支不存在") from None
    # Existing descendants make a repeated request observationally idempotent.
    if parent.children:
        return ExpandResponse(node_path=payload.node_path, added_nodes=[], mindmap_data=data, idempotent=True)
    topic = storage.node_name(str(resource["node_id"]), db_path)
    docs = await retrieve_resource_docs(topic)
    added = await MindmapAgent().expand(topic, docs, parent, {child.topic for child in parent.children})
    try:
        merged, idempotent = merge_branch(data, payload.node_path, added)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    content = dict(resource["content"])
    content["mindmap_data"] = merged.model_dump(mode="json")
    storage.update_resource_content(resource_id, current_user.user_id, content, db_path)
    return ExpandResponse(node_path=payload.node_path, added_nodes=added, mindmap_data=merged, idempotent=idempotent)


@router.post("/resources/{resource_id}/favorite")
async def favorite_resource(resource_id: str, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> dict[str, object]:
    resource = storage.toggle_favorite(resource_id, current_user.user_id, db_path)
    if resource is None: raise HTTPException(status_code=404, detail="资源不存在")
    return resource
