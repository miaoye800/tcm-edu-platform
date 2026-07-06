"""学习资源、路径、复习与测验 API。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.path_agent import run_path_planning
from app.agents.resource_schemas import ResourceType
from app.agents.resources_graph import run_resource_generation
from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.kb.ingest import load_herbs
from app.mastery.bkt import get_mastery, record_mastery_event
from app.mastery.review import due_reviews, record_review_result
from app.mastery.storage import DB_PATH as MASTERY_DB_PATH
from app.profile.update import ProfileUpdateEvent, get_latest_profile, update_profile_from_event

router = APIRouter(prefix="/api", tags=["learning"])


class ResourceGenerateRequest(BaseModel):
    """资源生成 SSE 入参。"""

    topic: str = Field(min_length=1)
    types: list[ResourceType] = Field(default_factory=lambda: ["explain", "graph", "mindmap", "quiz", "reading"])


class QuizSubmitItem(BaseModel):
    """单题提交结果。"""

    question_id: str = Field(min_length=1)
    kc_id: str = Field(min_length=1)
    answer: list[str] = Field(default_factory=list)
    correct_answer: list[str] = Field(default_factory=list)
    elapsed_seconds: float | None = Field(default=None, ge=0)
    difficulty: int = Field(default=3, ge=1, le=5)
    bloom_level: str = "理解"
    topic: str = "中药学"


class QuizSubmitRequest(BaseModel):
    """测验提交入参，可一次提交多题。"""

    items: list[QuizSubmitItem] = Field(min_length=1)


def get_mastery_db_path() -> Path:
    """返回掌握度/复习数据库路径，测试可覆盖该依赖。"""

    return MASTERY_DB_PATH


@router.post("/resources/generate")
async def generate_resources(
    payload: ResourceGenerateRequest,
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
) -> StreamingResponse:
    """以 SSE 推送资源生成进度、讲解 token 与完整资源包。"""

    topic = payload.topic.strip()
    user_id = current_user.user_id
    if not topic:
        raise HTTPException(status_code=422, detail="topic 不能为空")

    return StreamingResponse(
        _resource_sse_iter(topic=topic, resource_types=payload.types, user_id=user_id, request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/path")
async def read_learning_path(
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_mastery_db_path),
) -> dict[str, Any]:
    """返回技能树、Top3 推荐与复习摘要。"""

    state = await run_path_planning(user_id=current_user.user_id, db_path=db_path)
    return {
        "user_id": state["user_id"],
        "skill_tree": state["skill_tree"],
        "recommendations": state["recommendations"],
        "reviews": state["reviews"],
        "summary": state["answer"],
        "trace": state["trace"],
    }


@router.get("/reviews")
async def read_reviews(
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_mastery_db_path),
) -> dict[str, Any]:
    """返回今日 FSRS 到期复习队列。"""

    return {
        "user_id": current_user.user_id,
        **due_reviews(current_user.user_id, db_path=db_path),
    }


@router.post("/quiz/submit")
async def submit_quiz(
    payload: QuizSubmitRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_mastery_db_path),
) -> dict[str, Any]:
    """判分并回写 BKT、FSRS 与画像证据。"""

    user_id = current_user.user_id
    results: list[dict[str, Any]] = []
    profile_answers: list[dict[str, Any]] = []

    for item in payload.items:
        correct = _is_answer_correct(item.answer, item.correct_answer)
        kc_id = _normalize_kc_id(item.kc_id)
        evidence = {
            "question_id": item.question_id,
            "answer": item.answer,
            "correct_answer": item.correct_answer,
            "difficulty": item.difficulty,
            "bloom_level": item.bloom_level,
            "topic": item.topic,
        }
        record_mastery_event(
            user_id=user_id,
            kc_id=kc_id,
            correct=correct,
            event_type="quiz",
            elapsed_seconds=item.elapsed_seconds,
            evidence=evidence,
            db_path=db_path,
        )
        review = record_review_result(
            user_id=user_id,
            kc_id=kc_id,
            correct=correct,
            elapsed_seconds=item.elapsed_seconds,
            evidence=evidence,
            db_path=db_path,
        )
        profile_answers.append(
            {
                "question_id": item.question_id,
                "knowledge_point": item.topic,
                "difficulty": "hard" if item.difficulty >= 4 else "easy",
                "correct": correct,
            }
        )
        results.append(
            {
                "question_id": item.question_id,
                "kc_id": kc_id,
                "correct": correct,
                "review": review.as_dict(),
            }
        )

    await update_profile_from_event(
        user_id,
        ProfileUpdateEvent(type="quiz", payload={"answers": profile_answers}),
        db_path=db_path,
    )
    return {
        "user_id": user_id,
        "results": results,
        "mastery": get_mastery(user_id, db_path=db_path),
        "reviews": due_reviews(user_id, db_path=db_path),
    }


async def _resource_sse_iter(
    *,
    topic: str,
    resource_types: list[ResourceType],
    user_id: str,
    request: Request,
) -> AsyncIterator[str]:
    """按前端渲染顺序输出 SSE，避免首屏等待 LangGraph 全部完成。"""

    yield _format_sse("node_start", {"node": "profile_context", "label": "读取弟子档案"})
    profile = await get_latest_profile(user_id)
    profile_payload = profile.model_dump(mode="json")
    await asyncio.sleep(0.03)

    if await request.is_disconnected():
        return
    yield _format_sse("node_start", {"node": "retrieve_agent", "label": "检索中"})
    await asyncio.sleep(0.03)

    if await request.is_disconnected():
        return
    yield _format_sse("node_start", {"node": "dispatch", "label": "资源编排中"})
    state = await run_resource_generation(topic=topic, resource_types=resource_types, profile=profile_payload)

    for trace in state["trace"]:
        if await request.is_disconnected():
            return
        yield _format_sse(
            "node_start",
            {
                "node": trace.node,
                "label": _node_label(trace.node),
                "summary": trace.summary,
            },
        )
        await asyncio.sleep(0.02)

    explain = state["resources"].get("explain")
    if explain is not None:
        markdown = str(getattr(explain.payload, "markdown", ""))
        for chunk in _markdown_token_chunks(markdown):
            if await request.is_disconnected():
                return
            yield _format_sse("token", {"type": "explain", "text": chunk})
            await asyncio.sleep(0.012)

    for resource_type in resource_types:
        resource = state["resources"].get(resource_type)
        if resource is None:
            continue
        if await request.is_disconnected():
            return
        yield _format_sse("resource_done", {"resource": resource.model_dump(mode="json")})
        await asyncio.sleep(0.02)

    yield _format_sse(
        "done",
        {
            "topic": topic,
            "types": [resource_type for resource_type in state["resources"]],
            "trace": [item.model_dump(mode="json") for item in state["trace"]],
        },
    )


def _format_sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _markdown_token_chunks(markdown: str, size: int = 18) -> list[str]:
    """按小块推送 Markdown，前端会再做块级缓冲防抖。"""

    return [markdown[index : index + size] for index in range(0, len(markdown), size)] or [""]


def _node_label(node: str) -> str:
    labels = {
        "router": "路由中",
        "retrieve_agent": "检索中",
        "dispatch": "资源分发中",
        "explain_agent": "讲解生成中",
        "graph_agent": "图谱生成中",
        "mindmap_agent": "导图生成中",
        "quiz_agent": "题库生成中",
        "reading_agent": "阅读推荐中",
        "verify_agent": "证据校验中",
    }
    return labels.get(node, "生成中")


def _is_answer_correct(answer: list[str], correct_answer: list[str]) -> bool:
    normalized = {item.strip().lower() for item in answer if item.strip()}
    expected = {item.strip().lower() for item in correct_answer if item.strip()}
    if not expected:
        return False
    return normalized == expected


def _normalize_kc_id(raw: str) -> str:
    value = raw.strip()
    if not value:
        return value
    for herb in load_herbs():
        names = {herb.id, herb.name, *herb.aliases}
        if value in names:
            return herb.id
    return value
