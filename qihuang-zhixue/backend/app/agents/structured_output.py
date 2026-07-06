"""轻量 Instructor 风格结构化输出包装。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.clients import ChatMessage, MockClient
from app.llm.router import ModelRouter

ModelT = TypeVar("ModelT", bound=BaseModel)
MockFactory = Callable[[], ModelT]


class StructuredOutputError(RuntimeError):
    """结构化输出多次重修失败。"""


class InstructorModelRouter:
    """用 ModelRouter 发起 JSON Object 请求，并用 Pydantic 做出模型校验。"""

    def __init__(self, router: ModelRouter | None = None, max_repairs: int = 2) -> None:
        self.router = router or ModelRouter()
        self.max_repairs = max_repairs

    async def create(
        self,
        task_type: str,
        messages: Sequence[ChatMessage],
        response_model: type[ModelT],
        mock_factory: MockFactory[ModelT],
    ) -> ModelT:
        client = self.router.select(task_type)
        if isinstance(client, MockClient):
            return mock_factory()

        repair_messages = [dict(message) for message in messages]
        last_error: Exception | None = None
        for attempt in range(self.max_repairs + 1):
            text = await self.router.chat(
                task_type,
                repair_messages,
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            try:
                return response_model.model_validate_json(_extract_json(text))
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                repair_messages = [
                    *repair_messages,
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            "上一次输出没有通过 Pydantic 校验。请只返回合法 JSON Object，"
                            f"不得输出解释文字。错误：{exc}"
                        ),
                    },
                ]
                if attempt >= self.max_repairs:
                    break
        if self.router.settings.llm_fallback_to_mock:
            return mock_factory()
        raise StructuredOutputError(f"结构化输出重修失败：{last_error}") from last_error


async def structured_create(
    task_type: str,
    messages: Sequence[ChatMessage],
    response_model: type[ModelT],
    mock_factory: MockFactory[ModelT],
    router: ModelRouter | None = None,
) -> ModelT:
    instructor = InstructorModelRouter(router=router)
    return await instructor.create(
        task_type=task_type,
        messages=messages,
        response_model=response_model,
        mock_factory=mock_factory,
    )


async def use_mock_or_structured(
    task_type: str,
    response_model: type[ModelT],
    mock_factory: MockFactory[ModelT],
    llm_messages_factory: Callable[[], Awaitable[Sequence[ChatMessage]] | Sequence[ChatMessage]],
) -> ModelT:
    """供节点在真实/离线两种模式下复用同一输出契约。"""

    router = ModelRouter()
    client = router.select(task_type)
    if isinstance(client, MockClient):
        return mock_factory()
    messages = llm_messages_factory()
    if hasattr(messages, "__await__"):
        messages = await messages  # type: ignore[assignment]
    return await structured_create(
        task_type=task_type,
        messages=messages,  # type: ignore[arg-type]
        response_model=response_model,
        mock_factory=mock_factory,
        router=router,
    )


def generated_by(task_type: str = "generate") -> str:
    router = ModelRouter()
    client = router.select(task_type)
    return f"{client.provider}:{client.model_name}"


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    raise ValueError("模型输出中未找到 JSON Object")
