import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.config import Settings, get_settings
from app.llm.clients import (
    BaseLLMClient,
    ChatMessage,
    DeepSeekClient,
    LLMClientError,
    MockClient,
    StreamToken,
    ZhipuClient,
)

DEEPSEEK_PRO_SLOT = "deepseek_model_pro"
DEEPSEEK_FLASH_SLOT = "deepseek_model_flash"


@dataclass(frozen=True)
class ModelSpec:
    """路由后的模型规格。"""

    provider: str
    model_name: str


def _provider_for_model(model_name: str) -> str:
    if model_name.startswith("deepseek"):
        return "deepseek"
    if model_name.startswith("glm"):
        return "zhipu"
    raise ValueError(f"无法根据模型名推断供应商：{model_name}")


class ModelRouter:
    """按任务类型选择模型，并在缺少 API Key 时自动降级到 MOCK。"""

    def __init__(
        self,
        task_model_map: Mapping[str, str] | None = None,
        settings: Settings | None = None,
        client_factory: Callable[[ModelSpec, str | None], BaseLLMClient] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.task_model_map = dict(task_model_map or self.settings.llm_task_model_map)
        self._client_factory = client_factory

    def resolve(self, task_type: str) -> ModelSpec:
        model_name = self.task_model_map.get(task_type)
        if model_name is None:
            raise ValueError(f"未配置 task_type 对应模型：{task_type}")
        model_name = self._resolve_model_slot(model_name)
        return ModelSpec(provider=_provider_for_model(model_name), model_name=model_name)

    def select(self, task_type: str) -> BaseLLMClient:
        spec = self.resolve(task_type)
        api_key = self._api_key_for_provider(spec.provider)
        if self._client_factory is not None:
            return self._client_factory(spec, api_key)
        if not api_key:
            return MockClient(
                model_name=spec.model_name,
                provider=spec.provider,
                stream_delay=self.settings.mock_stream_delay,
            )
        if spec.provider == "deepseek":
            return DeepSeekClient(
                model_name=spec.model_name,
                api_key=api_key,
                base_url=self.settings.deepseek_base_url,
                timeout_seconds=self.settings.llm_timeout_seconds,
            )
        if spec.provider == "zhipu":
            return ZhipuClient(model_name=spec.model_name, api_key=api_key)
        raise ValueError(f"不支持的模型供应商：{spec.provider}")

    async def chat(self, task_type: str, messages: Sequence[ChatMessage], **kw: Any) -> str:
        return await self._call_with_retry(task_type, lambda client: client.chat(messages, **kw))

    async def classify(
        self,
        messages: str | Sequence[ChatMessage],
    ) -> Literal["profile_setup", "knowledge_qa", "resource_generation", "learning_path", "other"]:
        """将用户输入归类为画像设置、知识问答或其他意图。"""

        text = messages if isinstance(messages, str) else _last_user_content(messages)
        if _contains_any(
            text,
            ("资源", "讲解", "图谱", "思维导图", "测验", "题目", "延伸阅读", "resource", "quiz", "mindmap", "graph"),
        ):
            return "resource_generation"
        if _contains_any(text, ("画像", "建立画像", "学习目标", "基础", "年级", "偏好")):
            return "profile_setup"
        if _contains_any(text, ("规划", "计划", "接下来学什么", "复习", "学习路径", "路径规划", "闯关", "掌握度", "精准推送", "今日到期")):
            return "learning_path"
        if _contains_any(
            text,
            (
                "功效",
                "主治",
                "用量",
                "归经",
                "药性",
                "禁忌",
                "配伍",
                "中药",
                "麻黄",
                "桂枝",
                "解表",
                "风寒",
                "风热",
            ),
        ):
            return "knowledge_qa"

        result = await self.chat(
                "classify",
                [
                    {"role": "system", "content": "只输出 profile_setup、knowledge_qa、resource_generation、learning_path 或 other。"},
                    {"role": "user", "content": text},
                ],
            )

        normalized = result.strip().lower()
        if "profile_setup" in normalized:
            return "profile_setup"
        if "knowledge_qa" in normalized:
            return "knowledge_qa"
        if "resource_generation" in normalized:
            return "resource_generation"
        if "learning_path" in normalized:
            return "learning_path"
        return "other"

    async def chat_stream(self, task_type: str, messages: Sequence[ChatMessage], **kw: Any) -> AsyncIterator[StreamToken]:
        client = self.select(task_type)
        if isinstance(client, MockClient):
            async for token in client.chat_stream(messages, **kw):
                yield token
            return

        attempts = max(0, self.settings.llm_max_retries) + 1
        last_exc: Exception | None = None
        timeout = max(self.settings.llm_timeout_seconds, 0.001)
        for attempt in range(1, attempts + 1):
            try:
                async with asyncio.timeout(timeout):
                    async for token in client.chat_stream(messages, **kw):
                        yield token
                return
            except Exception as exc:
                last_exc = exc
                if not self._should_retry(exc) or attempt >= attempts:
                    break

        detail = self._fallback_reason(last_exc)
        if self.settings.llm_fallback_to_mock:
            self._log_fallback_reason(last_exc)
            mock_client = self._mock_client_for_task(task_type)
            async for token in mock_client.chat_stream(messages, **kw):
                yield token
            yield ("message", f"\n[FallbackReason:{detail}]")
            return

        yield ("message", f"模型服务暂时不可用，已触发容错保护（{detail}），请稍后重试。")

    def _api_key_for_provider(self, provider: str) -> str | None:
        if provider == "deepseek":
            return _non_empty(self.settings.deepseek_api_key)
        if provider == "zhipu":
            return _non_empty(self.settings.zhipu_api_key)
        return None

    async def _call_with_retry(
        self,
        task_type: str,
        operation: Callable[[BaseLLMClient], Awaitable[str]],
    ) -> str:
        client = self.select(task_type)
        if isinstance(client, MockClient):
            return await operation(client)

        attempts = max(0, self.settings.llm_max_retries) + 1
        last_exc: Exception | None = None
        timeout = max(self.settings.llm_timeout_seconds, 0.001)
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(operation(client), timeout=timeout)
            except Exception as exc:
                last_exc = exc
                if not self._should_retry(exc) or attempt >= attempts:
                    break

        if self.settings.llm_fallback_to_mock:
            mock_client = self._mock_client_for_task(task_type)
            mock_text = await operation(mock_client)
            detail = self._fallback_reason(last_exc)
            self._log_fallback_reason(last_exc)
            return f"{mock_text}\n[FallbackReason:{detail}]"

        detail = self._fallback_reason(last_exc)
        return f"模型服务暂时不可用，已触发容错保护（{detail}），请稍后重试。"

    def _mock_client_for_task(self, task_type: str) -> MockClient:
        spec = self.resolve(task_type)
        return MockClient(
            model_name=spec.model_name,
            provider=spec.provider,
            stream_delay=self.settings.mock_stream_delay,
        )

    def _should_retry(self, exc: Exception) -> bool:
        if isinstance(exc, LLMClientError):
            return bool(getattr(exc, "retryable", False))
        if isinstance(exc, asyncio.TimeoutError):
            return True
        if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.ConnectError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code if exc.response is not None else 0
            return status_code == 429 or 500 <= status_code < 600
        if isinstance(exc, json.JSONDecodeError):
            return False
        return False

    def _fallback_reason(self, exc: Exception | None) -> str:
        if exc is None:
            return "UnknownError"
        if isinstance(exc, LLMClientError):
            return exc.fallback_reason
        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
            return f"HTTP{exc.response.status_code}"
        if isinstance(exc, json.JSONDecodeError):
            return exc.__class__.__name__
        return exc.__class__.__name__

    def _log_fallback_reason(self, exc: Exception | None) -> None:
        if self.settings.env == "dev":
            reason = self._fallback_reason(exc)
            if exc is None:
                print(f"FallbackReason:{reason}")
                return
            chain = [f"{exc.__class__.__name__}: {exc}"]
            current = exc.__cause__ or exc.__context__
            while current is not None:
                chain.append(f"{current.__class__.__name__}: {current}")
                current = current.__cause__ or current.__context__
            print(f"FallbackReason:{reason} | " + " <- ".join(chain))

    def _resolve_model_slot(self, model_name: str) -> str:
        if model_name == DEEPSEEK_PRO_SLOT:
            return self.settings.deepseek_model_pro
        if model_name == DEEPSEEK_FLASH_SLOT:
            return self.settings.deepseek_model_flash
        return model_name


def _last_user_content(messages: Sequence[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    if messages:
        return str(messages[-1].get("content", ""))
    return ""


def _contains_any(text: str, keywords: Sequence[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _non_empty(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
