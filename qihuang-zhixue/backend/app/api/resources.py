"""Node-study resource tasks and the user's durable resource library."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.profile.store import DB_PATH
from app.resources.constants import CURRENT_PIPELINE_VERSION, RESOURCE_TYPES, ResourceType, gate_step, step_ids, steps_for
from app.resources.designs import normalize_design_for
from app.resources.planning import confirm_payload, revise as revise_resource_plan
from app.resources import service, storage
from app.resources.doc_schema import SaveDocumentRequest, SaveDocumentResponse, sanitize_meta, validate_blocks
from app.resources.doc_markdown import blocks_to_markdown
from app.ppt.deck_schema import (
    ConfirmOutlineRequest,
    ConfirmOutlineResponse,
    SaveDeckRequest,
    SaveDeckResponse,
    deck_content,
)
from app.agents.resource_context import retrieve_resource_docs
from app.mindmap import MindmapAgent
from app.mindmap.appearance import (
    ALLOWED_STRUCTURE_IDS,
    ALLOWED_THEME_IDS,
    normalize_structure,
    normalize_theme,
)
from app.mindmap.params import validate_mindmap_params
from app.mindmap.rules import ALLOWED_TEMPLATE_IDS
from app.mindmap.schemas import ExpandRequest, ExpandResponse, MindElixirData, merge_branch
from app.mindmap.tree_schema import (
    SaveMindmapTreeRequest,
    SaveMindmapTreeResponse,
    apply_tree,
    tree_stats,
    validate_tree,
)
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


class MindmapAppearanceRequest(BaseModel):
    """结构与配色。两者都是视图属性，不改树，因此不该让版本号 +1。"""

    structure: str = ""
    theme_id: str = ""


@router.post("/resource/tasks", status_code=201)
async def create_resource_task(payload: CreateTaskRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> dict[str, object]:
    if payload.resource_type not in RESOURCE_TYPES:
        raise HTTPException(status_code=422, detail="不支持的资源类型")
    if not storage.node_exists(payload.node_id, db_path):
        raise HTTPException(status_code=404, detail="知识节点不存在")
    if payload.resource_type == "mindmap" and payload.params:
        try:
            validate_mindmap_params(payload.params)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    task = storage.create_task(
        str(uuid4()),
        current_user.user_id,
        payload.node_id,
        payload.resource_type,
        db_path,
        meta={"params": payload.params, **_pipeline_meta(payload.resource_type)},
    )
    return task


@router.post("/resources/{resource_id}/refine", status_code=201)
async def refine_resource(resource_id: str, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> dict[str, object]:
    resource = storage.get_resource(resource_id, current_user.user_id, db_path)
    if resource is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    params = dict(resource.get("meta", {}).get("params", {}))
    return storage.create_task(
        str(uuid4()),
        current_user.user_id,
        str(resource["node_id"]),
        str(resource["type"]),
        db_path,
        meta={"params": params, "replay_of": resource_id, **_pipeline_meta(str(resource["type"]))},
    )


def _pipeline_meta(resource_type: str) -> dict[str, object]:
    """新任务写清走哪一版管线。

    v3 在 v2 的「拟定方案」之后插入「设计」与「选材」两步，并把用户确认闸门
    后移到 design——方案与设计必须同批确认。

    这一步只写在**新任务**上：库里已有的任务没有这个键，``_pipeline_version``
    会读成 1（或它自己落库的 2），继续按自己那版表跑完，产物逐字段不变。
    """

    supported = ("mindmap", "ppt", "doc")
    return {"pipeline_version": CURRENT_PIPELINE_VERSION if resource_type in supported else 1}


class RevisePlanRequest(BaseModel):
    """一条合并后的反馈；可选「当前方案」与「重新描述的目标」。

    ``plan`` 带上用户在面板里已经改过的版本，避免「一按修订，手改全丢」。
    ``goal`` 非空表示推翻重提：按新目标重拟整份方案。
    """

    feedback: str = Field(default="", max_length=2000)
    plan: dict[str, object] | None = None
    goal: str = Field(default="", max_length=200)


class ConfirmPlanRequest(BaseModel):
    plan: dict[str, object]
    #: 设计单与方案同批确认。缺省表示「接受系统给的那一份」。
    design: dict[str, object] | None = None


def _task_topic(task: dict[str, object], db_path: Path) -> str:
    params = (task.get("meta") or {}).get("params") or {}
    full = " ".join(str(params.get("query") or "").split())
    return full[:200] or str(params.get("topic") or "").strip()[:200] or storage.node_name(str(task["node_id"]), db_path)


@router.get("/resource/tasks/{task_id}")
async def read_resource_task(task_id: str, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> dict[str, object]:
    """任务快照。刷新后据此恢复「已确认的方案」，不必重新跑一遍提案。"""

    task = storage.get_task(task_id, current_user.user_id, db_path)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    steps = (task.get("steps_done") or {})
    return {
        "task_id": task["task_id"],
        "resource_type": task["resource_type"],
        "status": task["status"],
        "current_step": task["current_step"],
        "resource_id": task["resource_id"],
        "plan": steps.get("plan"),
        "design": steps.get("design"),
        "plan_confirmed": bool(steps.get("plan_confirmed")),
        "awaiting_plan": bool(steps.get("awaiting_plan")),
        "pipeline_version": int((task.get("meta") or {}).get("pipeline_version") or 1),
        "steps": [{"id": step["id"], "label": step["label"]} for step in steps_for(task["resource_type"], int((task.get("meta") or {}).get("pipeline_version") or 1))],
    }


@router.post("/resource/tasks/{task_id}/plan/revise")
async def revise_task_plan(task_id: str, payload: RevisePlanRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> dict[str, object]:
    task = storage.get_task(task_id, current_user.user_id, db_path)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    steps = dict(task.get("steps_done") or {})
    plan = steps.get("plan")
    if not plan or task["status"] == "done" or not steps.get("awaiting_plan"):
        raise HTTPException(status_code=409, detail="当前任务没有待修订方案")
    if not payload.feedback.strip() and not payload.goal.strip():
        raise HTTPException(status_code=422, detail="请写下要调整的内容")
    # 用户在面板里手改过的那一版优先：修订要基于「当前约束」，而不是最初那版。
    base = payload.plan or plan
    revised = await revise_resource_plan(
        task["resource_type"],
        _task_topic(task, db_path),
        base,
        payload.feedback,
        goal=payload.goal,
        params=(task.get("meta") or {}).get("params") or {},
    )
    steps["plan"] = revised
    steps.pop("plan_confirmed", None)
    steps["awaiting_plan"] = True
    storage.update_task(task_id, current_user.user_id, status="pending", current_step=0, steps_done=steps, db_path=db_path)
    service.reset_progress(task_id)
    return {"plan": revised}


@router.post("/resource/tasks/{task_id}/plan/confirm")
async def confirm_task_plan(task_id: str, payload: ConfirmPlanRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> dict[str, object]:
    task = storage.get_task(task_id, current_user.user_id, db_path)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if int((task.get("meta") or {}).get("pipeline_version") or 1) < 2 or task["status"] == "done":
        raise HTTPException(status_code=409, detail="当前任务不支持方案确认")
    steps = dict(task["steps_done"] or {})
    if not steps.get("plan") or not steps.get("awaiting_plan"):
        raise HTTPException(status_code=409, detail="任务当前不在方案确认阶段")
    try:
        # 确认是最后一道闸：空方案、错类型、超长单元都在这里拦住，
        # 不能让一份坏方案进生成阶段。
        plan = confirm_payload(task["resource_type"], payload.plan)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    steps["plan"] = plan.model_dump(mode="json")
    # 设计单与方案同批确认：v3 起缺了它，生成期就只能自己猜版式，
    # 「确认完又回到固定模板」就是这么来的。
    params = dict((task.get("meta") or {}).get("params") or {})
    version = int((task.get("meta") or {}).get("pipeline_version") or 1)
    if version >= 3:
        design = normalize_design_for(
            task["resource_type"],
            payload.design if isinstance(payload.design, dict) else steps.get("design"),
            plan,
            params,
        )
        steps["design"] = design
    else:
        design = steps.get("design")
    steps["plan_confirmed"] = True
    steps.pop("awaiting_plan", None)
    steps.pop("active_step", None)
    # 从闸门的下一步开始跑：确认后不该重放方案/设计两步。
    gate = gate_step(task["resource_type"], version)
    order = step_ids(task["resource_type"], version)
    resume = (order.index(gate) + 1) if gate in order else 1
    storage.update_task(
        task_id,
        current_user.user_id,
        status="pending",
        current_step=resume,
        steps_done=steps,
        meta={"confirmed_plan": plan.model_dump(mode="json"), "confirmed_design": design or {}},
        db_path=db_path,
    )
    service.reset_progress(task_id)
    return {"ok": True, "current_step": resume, "plan": plan.model_dump(mode="json"), "design": design or {}}


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
    # 面包屑需要知识节点名；资源表只存 node_id，这里补上，避免前端再拉一次知识树。
    resource["node_name"] = storage.node_name(str(resource["node_id"]), db_path)
    return resource


@router.patch("/resources/{resource_id}/document", response_model=SaveDocumentResponse)
async def save_document(resource_id: str, payload: SaveDocumentRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> SaveDocumentResponse:
    """保存讲解文档的块结构。

    只允许 doc 类型：导图有自己的 PATCH（模板/续展），题库有判分链路，
    混在一起会让「谁能写 content」变得不可推理。
    """
    resource = storage.get_resource(resource_id, current_user.user_id, db_path)
    if resource is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    if resource["type"] != "doc":
        raise HTTPException(status_code=422, detail="仅讲解文档支持块编辑")
    if resource["status"] != "ready":
        raise HTTPException(status_code=409, detail="文档尚未生成完成，暂不可编辑")
    try:
        blocks = validate_blocks(payload.blocks)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        saved = storage.update_document(
            resource_id,
            current_user.user_id,
            blocks,
            expected_version=payload.version,
            meta_patch=sanitize_meta(payload.meta),
            db_path=db_path,
        )
    except storage.VersionConflict as exc:
        raise HTTPException(status_code=409, detail=f"文档已被更新到版本 {exc.current}，请重新加载后再保存") from exc
    if saved is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    return SaveDocumentResponse(**saved)


@router.get("/resources/{resource_id}/document.md")
async def export_document_markdown(resource_id: str, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> Response:
    """把块结构导回 Markdown，用于交付、二次编辑与「往返一致」校验。"""
    resource = storage.get_resource(resource_id, current_user.user_id, db_path)
    if resource is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    if resource["type"] != "doc":
        raise HTTPException(status_code=422, detail="仅讲解文档支持导出")
    markdown = blocks_to_markdown(list(resource["content"].get("blocks") or []))
    filename = f"{resource['title'] or 'document'}.md"
    quoted = quote(filename, safe="")
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=\"document.md\"; filename*=UTF-8''{quoted}"},
    )


@router.patch("/resources/{resource_id}/mindmap")
async def patch_mindmap_template(resource_id: str, payload: PatchMindmapRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> dict[str, object]:
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


@router.put("/resources/{resource_id}/mindmap/tree", response_model=SaveMindmapTreeResponse)
async def save_mindmap_tree(resource_id: str, payload: SaveMindmapTreeRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> SaveMindmapTreeResponse:
    """保存整棵导图（编辑态自动保存）。

    路径刻意与 ``PATCH /resources/{id}/mindmap``（换模板）分开：两者写的字段
    不同、并发语义也不同，合成一个接口就没法判断「这次请求到底想改什么」。
    """
    resource = storage.get_resource(resource_id, current_user.user_id, db_path)
    if resource is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    if resource["type"] != "mindmap":
        raise HTTPException(status_code=422, detail="仅思维导图支持整树保存")
    if resource["status"] != "ready":
        raise HTTPException(status_code=409, detail="导图尚未生成完成，暂不可编辑")
    if payload.template_id is not None and payload.template_id not in ALLOWED_TEMPLATE_IDS and payload.template_id not in ALLOWED_THEME_IDS:
        raise HTTPException(status_code=422, detail="模板不在已批准枚举中")
    try:
        node = validate_tree(payload.nodeData, source_count=len(resource.get("sources") or []))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    content = apply_tree(dict(resource["content"]), node, payload.template_id)
    try:
        saved = storage.update_mindmap_tree(
            resource_id,
            current_user.user_id,
            content,
            expected_version=payload.version,
            db_path=db_path,
        )
    except storage.VersionConflict as exc:
        raise HTTPException(status_code=409, detail=f"导图已被更新到版本 {exc.current}，请重新载入后再保存") from exc
    if saved is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    node_count, depth = tree_stats(node)
    return SaveMindmapTreeResponse(
        version=int(saved["version"]),
        node_count=node_count,
        depth=depth,
        template_id=str(content.get("template_id") or "") or None,
    )


@router.put("/resources/{resource_id}/mindmap/appearance")
async def save_mindmap_appearance(resource_id: str, payload: MindmapAppearanceRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> dict[str, object]:
    """保存结构与配色（视图偏好）。

    与整树保存分开的三条理由：不改树、不该 +1 版本号、连点色卡会连发请求。
    非法取值不报错而是落到默认值——旧客户端可能传旧的 8 套主题 id，
    为此把整个请求打回 422 只会让用户换一次配色就报一次错。
    """
    resource = storage.get_resource(resource_id, current_user.user_id, db_path)
    if resource is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    if resource["type"] != "mindmap":
        raise HTTPException(status_code=422, detail="仅思维导图支持保存外观")
    if payload.structure and payload.structure not in ALLOWED_STRUCTURE_IDS:
        raise HTTPException(status_code=422, detail="结构不在已批准枚举中")
    if payload.theme_id and payload.theme_id not in ALLOWED_THEME_IDS:
        raise HTTPException(status_code=422, detail="配色不在已批准枚举中")
    saved = storage.update_mindmap_appearance(
        resource_id,
        current_user.user_id,
        structure=normalize_structure(payload.structure or str((resource.get("meta") or {}).get("mindmap_structure") or "")),
        theme_id=normalize_theme(payload.theme_id or str((resource.get("meta") or {}).get("mindmap_theme") or "")),
        db_path=db_path,
    )
    if saved is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    return {"ok": True, **saved}


@router.put("/resources/{resource_id}/ppt/deck", response_model=SaveDeckResponse)
async def save_ppt_deck(resource_id: str, payload: SaveDeckRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> SaveDeckResponse:
    """保存整份幻灯（编辑态自动保存）。

    与 ``PUT /resources/{id}/mindmap/tree`` 同构：整份覆盖 + ``meta.ppt_version``
    乐观并发。``sources`` 不在这里提交，由存储层保留生成期固化的溯源证据。
    """

    resource = storage.get_resource(resource_id, current_user.user_id, db_path)
    if resource is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    if resource["type"] != "ppt":
        raise HTTPException(status_code=422, detail="仅幻灯支持整份保存")
    if resource["status"] != "ready":
        raise HTTPException(status_code=409, detail="幻灯尚未生成完成，暂不可编辑")
    try:
        saved = storage.update_deck(
            resource_id,
            current_user.user_id,
            deck_content(payload),
            expected_version=payload.version,
            db_path=db_path,
        )
    except storage.VersionConflict as exc:
        raise HTTPException(status_code=409, detail=f"幻灯已被更新到版本 {exc.current}，请重新载入后再保存") from exc
    if saved is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    return SaveDeckResponse(**saved)


@router.post("/resource/tasks/{task_id}/ppt/outline", response_model=ConfirmOutlineResponse)
async def confirm_ppt_outline(task_id: str, payload: ConfirmOutlineRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_resource_db_path)) -> ConfirmOutlineResponse:
    """确认（可能被改过的）大纲，把任务推进到敷演步。

    写入 params 而不是另起一张表：确认动作的本质是「改写这次任务的输入」，
    与「重新炼制」携带 params 的语义一致；task.meta 由 ``update_task`` 合并写入。
    """

    task = storage.get_task(task_id, current_user.user_id, db_path)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["resource_type"] != "ppt":
        raise HTTPException(status_code=422, detail="仅幻灯任务支持确认大纲")
    if task["status"] == "done":
        raise HTTPException(status_code=409, detail="任务已完成，无法再改大纲")
    version = int((task.get("meta") or {}).get("pipeline_version") or 1)
    if version >= 2:
        # v2 的大纲就是方案里的页，确认走 /plan/confirm；两条确认路径并存会让
        # 「到底以哪份为准」变得不可推理。
        raise HTTPException(status_code=409, detail="该任务请通过方案确认继续生成")
    compose_index = next(index for index, step in enumerate(steps_for("ppt", version)) if step["id"] == "compose")

    params = dict(task.get("meta", {}).get("params") or {})
    params["confirmed_outline"] = [slide.model_dump(mode="json") for slide in payload.slides]
    steps = dict(task["steps_done"])
    steps.pop("awaiting_outline", None)
    steps.pop("active_step", None)
    storage.update_task(
        task_id,
        current_user.user_id,
        status="pending",
        current_step=compose_index,
        steps_done=steps,
        meta={"params": params},
        db_path=db_path,
    )
    service.reset_progress(task_id)
    return ConfirmOutlineResponse(ok=True, current_step=compose_index)


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
