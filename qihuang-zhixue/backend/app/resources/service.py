"""Task runner. Progress is committed before each outward SSE event batch."""

from __future__ import annotations

import json
import random
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from app.agents.resources_graph import run_resource_generation
from app.agents.resource_context import retrieve_resource_docs
from app.agents.graph_intelligence import GraphAgent
from app.config import get_settings
from app.mindmap import MindmapAgent
from app.resources.constants import ALCHEMY_STEPS
from app.resources import storage

_EVENT_CACHE: dict[str, list[dict[str, Any]]] = {}
_PROGRESS_CACHE: dict[str, dict[str, Any]] = {}


def envelope(event: str, step_index: int, total_steps: int, step_label: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"event": event, "step_index": step_index, "total_steps": total_steps, "step_label": step_label, "payload": payload or {}}


async def generate_content(resource_type: str, topic: str, *, node_id: str | None = None, db_path: Any = None, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Adapter for the existing validated LangGraph resource agents.

    It is deliberately a narrow seam: unit tests replace it with a deterministic
    generator, and a future node-dialogue agent can replace it without changing
    the persistence or SSE contract.
    """
    if resource_type == "graph":
        content = await GraphAgent().generate(center_id=node_id or topic, center_name=topic, db_path=db_path)
        payload = content.model_dump(mode="json")
        return payload, payload["sources"]
    if resource_type == "mindmap":
        docs = await retrieve_resource_docs(topic)
        # Resource tasks currently do not mutate the learner profile; the profile
        # is injected by callers that have it. Keyless execution still succeeds.
        content = await MindmapAgent().generate(topic, docs, profile=(params or {}).get("profile"))
        payload = content.model_dump(mode="json")
        return payload, payload["sources"]
    settings = get_settings()
    if not (settings.deepseek_api_key or settings.zhipu_api_key):
        raise RuntimeError("未配置可用的 LLM 密钥")
    old_type = {"doc": "explain", "story": "reading"}.get(resource_type, resource_type)
    state = await run_resource_generation(topic, resource_types=[old_type])
    resource = state["resources"].get(old_type)
    if resource is None:
        raise RuntimeError("丹方未能产出资源")
    raw = resource.model_dump(mode="json")
    sources = [{"出处": item.get("name", "知识库"), "原文摘录": item.get("quote", ""), "定位": item.get("ref", "")} for item in raw.get("sources", [])]
    payload = raw["payload"]
    if resource_type == "doc":
        return {"blocks": _doc_blocks(payload)}, sources
    return payload, sources


def _doc_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for section in payload.get("sections", []):
        blocks.append({"type": "heading", "text": section.get("title", "讲解")})
        blocks.append({"type": "paragraph", "text": section.get("content", "")})
        claims = [str(item) for item in section.get("claims", []) if str(item).strip()]
        if claims:
            blocks.append({"type": "bullet", "items": claims[:5]})
        if section.get("title") == "功效" and claims:
            blocks.append({"type": "table", "columns": ["要点", "依据"], "rows": [[claim, "知识库已检索"] for claim in claims[:4]]})
        if section.get("title") == "用量与使用注意":
            blocks.append({"type": "callout", "text": section.get("content", "")})
        for source in section.get("sources", [])[:1]:
            quote = str(source.get("quote", "")).strip() or str(source.get("ref", ""))
            if quote:
                blocks.append({"type": "quote", "text": quote, "source": source.get("ref", "")})
    return blocks or [{"type": "callout", "text": "知识库暂未覆盖此节点。"}]


async def stream_task(task_id: str, user_id: str, db_path: Any) -> AsyncIterator[dict[str, Any]]:
    task = storage.get_task(task_id, user_id, db_path)
    if task is None:
        yield envelope("error", 0, 0, "任务不存在", {"message": "未找到此炼制任务"})
        return
    # SQLite is authoritative across process restarts; this cache fills the
    # small window between an emitted event and a concurrent reader seeing it.
    cached = _PROGRESS_CACHE.get(task_id)
    if cached and int(cached["current_step"]) >= int(task["current_step"]):
        task["current_step"] = cached["current_step"]
        task["steps_done"] = dict(cached["steps_done"])
    steps = ALCHEMY_STEPS[task["resource_type"]]
    total = len(steps)
    done = task["steps_done"]
    if task["status"] == "failed":
        yield envelope("error", task["current_step"], total, "炸炉", {"message": done.get("error", "炼制失败，请炸炉重炼")})
        return
    if task["status"] == "done":
        yield envelope("done", total, total, "丹成", {"resource_id": task["resource_id"]})
        return
    storage.update_task(task_id, user_id, status="running", db_path=db_path)
    start = int(task["current_step"])
    try:
        for index in range(start, total):
            spec = steps[index]
            label = random.choice(spec["variants"])
            # A disconnect can happen after step_start reached the browser. Keep
            # that fact so a reconnection finishes this stage rather than replaying it.
            resuming_active_step = done.get("active_step") == index
            if not resuming_active_step:
                done["active_step"] = index
                storage.update_task(task_id, user_id, current_step=index, steps_done=done, db_path=db_path)
                _cache_progress(task_id, index, done)
                start_event = envelope("step_start", index, total, label, {"step_id": spec["id"]})
                _remember(task_id, start_event)
                yield start_event
            if spec["id"] == "distill" or (task["resource_type"] != "doc" and index == total - 2):
                if "draft" not in done:
                    content, sources = await _generate_task_content(task, db_path)
                    done["draft"] = {"content": content, "sources": sources}
            if task["resource_type"] == "doc" and spec["id"] == "layout":
                draft = done.get("draft")
                if draft is None:
                    content, sources = await generate_content("doc", storage.node_name(task["node_id"], db_path)); draft={"content":content,"sources":sources}; done["draft"] = draft
                for block_index, block in enumerate(draft["content"].get("blocks", [])):
                    block_event = envelope("block", index, total, label, {"index": block_index, "block": block})
                    _remember(task_id, block_event)
                    yield block_event
            done_event = envelope("step_done", index, total, label, {"step_id": spec["id"]})
            _remember(task_id, done_event)
            done.pop("active_step", None)
            storage.update_task(task_id, user_id, current_step=index + 1, steps_done=done, db_path=db_path)
            _cache_progress(task_id, index + 1, done)
            yield done_event
        draft = done.get("draft")
        if draft is None:
            content, sources = await _generate_task_content(task, db_path); draft={"content":content,"sources":sources}; done["draft"] = draft
        resource_id = str(uuid4())
        resource = {"id": resource_id, "user_id": user_id, "node_id": task["node_id"], "type": task["resource_type"], "title": f"{storage.node_name(task['node_id'], db_path)} · {task['resource_type']}", "content": draft["content"], "sources": draft["sources"], "notes": "炼丹手记：依次完成本草检索、结构编排与溯源校验后封存此资源。", "status": "ready", "created_at": storage.now(), "meta": {"task_id": task_id, "generator": "langgraph-resource-adapter"}}
        resource["meta"]["params"] = task.get("meta", {}).get("params", {})
        if task["resource_type"] == "graph":
            resource["meta"]["generator"] = "graph-agent"
        if task["resource_type"] == "mindmap":
            resource["meta"]["generator"] = "mindmap-agent"
        resource["notes"] = f"炼丹手记：已完成本草检索、结构编排与溯源校验；收录 {len(draft['sources'])} 条依据。"
        storage.create_resource(resource, db_path)
        storage.update_task(task_id, user_id, status="done", current_step=total, steps_done=done, resource_id=resource_id, db_path=db_path)
        _cache_progress(task_id, total, done)
        event = envelope("done", total, total, "丹成", {"resource_id": resource_id})
        _remember(task_id, event)
        yield event
    except Exception as exc:
        done["error"] = "炉火不稳，资源未能炼成，请炸炉重炼。"
        storage.update_task(task_id, user_id, status="failed", steps_done=done, db_path=db_path)
        _cache_progress(task_id, int(task["current_step"]), done)
        event = envelope("error", int(task["current_step"]), total, "炸炉", {"message": done["error"], "detail": str(exc)})
        _remember(task_id, event)
        yield event


def _remember(task_id: str, event: dict[str, Any]) -> None:
    _EVENT_CACHE.setdefault(task_id, []).append(event)


def _cache_progress(task_id: str, current_step: int, steps_done: dict[str, Any]) -> None:
    _PROGRESS_CACHE[task_id] = {"current_step": current_step, "steps_done": dict(steps_done)}


async def _generate_task_content(task: dict[str, Any], db_path: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    topic = storage.node_name(task["node_id"], db_path)
    if task["resource_type"] == "graph":
        return await generate_content("graph", topic, node_id=task["node_id"], db_path=db_path, params=task.get("meta", {}).get("params", {}))
    return await generate_content(task["resource_type"], topic)
