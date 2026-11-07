import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from app.config import Settings, get_settings, resolve_database_path
from app.llm import replay, usage
from app.llm.clients import (
    BaseLLMClient,
    ChatMessage,
    DeepSeekClient,
    LLMClientError,
    MockClient,
    QwenClient,
    StreamToken,
    ZhipuClient,
)

DEEPSEEK_PRO_SLOT = "deepseek_model_pro"
DEEPSEEK_FLASH_SLOT = "deepseek_model_flash"
QWEN_FLASH_SLOT = "qwen_model_flash"
QWEN_PLUS_SLOT = "qwen_model_plus"
QWEN_MAX_SLOT = "qwen_model_max"

#: 只要有这些词就是「要一份资源」。注意这里**没有**「图谱」——见 ``classify``。
_RESOURCE_WORDS = (
    "资源",
    "讲解",
    "思维导图",
    "测验",
    "题目",
    "延伸阅读",
    "resource",
    "quiz",
    "mindmap",
)

#: 「图谱」只有带这些动词时才算资源需求，否则它只是被当成知识来源提起。
_GENERATE_VERBS = ("生成", "画", "做个", "做一份", "出一份", "出个", "导出", "制作")

#: 知识问答的显式线索。前四个是教材里成对出现的硬关系，属图谱最擅长的那类，
#: 不在这里认出来就要多跑一次 LLM 分类。
_KNOWLEDGE_WORDS = (
    "功效",
    "主治",
    "用量",
    "归经",
    "药性",
    "禁忌",
    "配伍",
    "十八反",
    "十九畏",
    "配伍禁忌",
    "相须",
    "相使",
    "相畏",
    "相恶",
    "相反",
    "同方",
    "一起用",
    "同用",
    "区别",
    "鉴别",
    "异同",
    "怎么区分",
    "关系",
    "中药",
    "麻黄",
    "桂枝",
    "解表",
    "风寒",
    "风热",
)


class LLMReplayMissError(LLMClientError):
    """replay 模式下请求没有录制记录。

    刻意抛错而不是静默降级：只有让它失败，e2e 才能暴露"这段链路没被录过"，
    否则会拿 mock 文本假装通过。
    """

    retryable = False


@dataclass(frozen=True)
class ModelSpec:
    """路由后的模型规格。"""

    provider: str
    model_name: str


def _provider_for_model(model_name: str) -> str:
    if model_name.startswith("deepseek"):
        return "deepseek"
    if model_name.startswith("qwen"):
        return "qwen"
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
        if spec.provider == "qwen":
            return QwenClient(
                model_name=spec.model_name,
                api_key=api_key,
                base_url=self.settings.qwen_base_url,
                timeout_seconds=self.settings.llm_timeout_seconds,
            )
        if spec.provider == "zhipu":
            return ZhipuClient(model_name=spec.model_name, api_key=api_key)
        raise ValueError(f"不支持的模型供应商：{spec.provider}")

    async def chat(self, task_type: str, messages: Sequence[ChatMessage], **kw: Any) -> str:
        params = self._with_default_params(kw)
        mode = self._mode()

        if mode == "mock":
            text, _client = await self._call_with_retry(
                task_type, lambda client: client.chat(messages, **params)
            )
            return text

        spec = self.resolve(task_type)
        fingerprint_value = replay.fingerprint(
            task_type=task_type, model=spec.model_name, messages=messages, params=params
        )
        if mode in {"replay", "auto"}:
            cached = replay.load(fingerprint_value, db_path=self._db_path())
            if cached is not None:
                return cached
            if mode == "replay":
                raise LLMReplayMissError(
                    f"replay 未命中：{task_type} / {spec.model_name}（{fingerprint_value[:12]}）"
                )

        text, client = await self._call_with_retry(
            task_type, lambda item: item.chat(messages, **params)
        )
        self._after_call(task_type, client, mode, fingerprint_value, messages, params, text)
        return text

    async def classify(
        self,
        messages: str | Sequence[ChatMessage],
    ) -> Literal["profile_setup", "knowledge_qa", "resource_generation", "learning_path", "other"]:
        """将用户输入归类为画像设置、知识问答或其他意图。"""

        text = messages if isinstance(messages, str) else _last_user_content(messages)
        if _contains_any(text, _RESOURCE_WORDS) or (
            "图谱" in text and _contains_any(text, _GENERATE_VERBS)
        ):
            # 「图谱」原先无条件算资源生成，于是「说说图谱里麻黄和桂枝为什么配」
            # 也被拐去做图了。它只有在**带生成动词**时才是资源需求
            # （「生成知识图谱」「画个图谱」），否则是拿图谱当知识来源在提问。
            return "resource_generation"
        if _contains_any(text, ("画像", "建立画像", "学习目标", "基础", "年级", "偏好")):
            return "profile_setup"
        if _contains_any(text, ("规划", "计划", "接下来学什么", "复习", "学习路径", "路径规划", "闯关", "掌握度", "精准推送", "今日到期")):
            return "learning_path"
        if _contains_any(text, _KNOWLEDGE_WORDS):
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
        params = self._with_default_params(kw)
        mode = self._mode()
        fingerprint_value = ""

        if mode != "mock":
            spec = self.resolve(task_type)
            fingerprint_value = replay.fingerprint(
                task_type=task_type, model=spec.model_name, messages=messages, params=params
            )
            if mode in {"replay", "auto"}:
                cached = replay.load(fingerprint_value, db_path=self._db_path())
                if cached is not None:
                    async for token in _replay_stream(cached):
                        yield token
                    return
                if mode == "replay":
                    raise LLMReplayMissError(
                        f"replay 未命中：{task_type} / {spec.model_name}（{fingerprint_value[:12]}）"
                    )

        collected: list[str] = []
        async for token in self._stream_with_retry(task_type, messages, params):
            if token[0] == "message":
                collected.append(token[1])
            yield token

        if fingerprint_value and mode in {"live", "auto"}:
            text = "".join(collected)
            if text:
                spec = self.resolve(task_type)
                replay.save(
                    fingerprint_value=fingerprint_value,
                    task_type=task_type,
                    provider=spec.provider,
                    model=spec.model_name,
                    messages=messages,
                    params=params,
                    response_text=text,
                    db_path=self._db_path(),
                )

    async def _stream_with_retry(
        self,
        task_type: str,
        messages: Sequence[ChatMessage],
        params: Mapping[str, Any],
    ) -> AsyncIterator[StreamToken]:
        client = self.select(task_type)
        if isinstance(client, MockClient):
            async for token in client.chat_stream(messages, **params):
                yield token
            return

        attempts = max(0, self.settings.llm_max_retries) + 1
        last_exc: Exception | None = None
        timeout = max(self.settings.llm_timeout_seconds, 0.001)
        for attempt in range(1, attempts + 1):
            try:
                async with asyncio.timeout(timeout):
                    async for token in client.chat_stream(messages, **params):
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
            async for token in mock_client.chat_stream(messages, **params):
                yield token
            yield ("message", f"\n[FallbackReason:{detail}]")
            return

        yield ("message", f"模型服务暂时不可用，已触发容错保护（{detail}），请稍后重试。")

    def _api_key_for_provider(self, provider: str) -> str | None:
        if provider == "deepseek":
            return _non_empty(self.settings.deepseek_api_key)
        if provider == "qwen":
            return _non_empty(self.settings.qwen_api_key)
        if provider == "zhipu":
            return _non_empty(self.settings.zhipu_api_key)
        return None

    async def _call_with_retry(
        self,
        task_type: str,
        operation: Callable[[BaseLLMClient], Awaitable[str]],
    ) -> tuple[str, BaseLLMClient]:
        """执行一次调用，返回 (文本, 实际使用的 client)。

        返回 client 是为了让调用方读取 ``last_usage`` 落库计量。
        """

        client = self.select(task_type)
        if isinstance(client, MockClient):
            return await operation(client), client

        attempts = max(0, self.settings.llm_max_retries) + 1
        last_exc: Exception | None = None
        timeout = max(self.settings.llm_timeout_seconds, 0.001)
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(operation(client), timeout=timeout), client
            except Exception as exc:
                last_exc = exc
                if not self._should_retry(exc) or attempt >= attempts:
                    break

        if self.settings.llm_fallback_to_mock:
            mock_client = self._mock_client_for_task(task_type)
            mock_text = await operation(mock_client)
            detail = self._fallback_reason(last_exc)
            self._log_fallback_reason(last_exc)
            return f"{mock_text}\n[FallbackReason:{detail}]", mock_client

        detail = self._fallback_reason(last_exc)
        return f"模型服务暂时不可用，已触发容错保护（{detail}），请稍后重试。", client

    def _mode(self) -> str:
        mode = str(self.settings.llm_mode or "auto").strip().lower()
        return mode if mode in {"live", "replay", "auto", "mock"} else "auto"

    def _db_path(self) -> Path:
        """回放与计量的存储位置跟随本 router 的 settings，而不是全局缓存。

        否则测试注入的临时库会被忽略，录制与回放写到不同的库里。
        """

        return resolve_database_path(self.settings)

    def _with_default_params(self, kw: Mapping[str, Any]) -> dict[str, Any]:
        """统一注入默认思考模式，覆盖所有未显式指定的调用点。

        抽取、分类、评分这类结构化任务不需要推理 token；确实需要推理的任务
        在调用侧显式传 ``thinking={"type": "enabled"}`` 即可绕过默认值。
        """

        params = dict(kw)
        params.setdefault("thinking", {"type": self.settings.llm_thinking_default})
        return params

    def _after_call(
        self,
        task_type: str,
        client: BaseLLMClient,
        mode: str,
        fingerprint_value: str,
        messages: Sequence[ChatMessage],
        params: Mapping[str, Any],
        text: str,
    ) -> None:
        db_path = self._db_path()
        usage.record_usage(
            task_type=task_type,
            provider=client.provider,
            model=client.model_name,
            usage=client.last_usage,
            mode=mode,
            db_path=db_path,
        )
        if mode not in {"live", "auto"} or isinstance(client, MockClient):
            return
        replay.save(
            fingerprint_value=fingerprint_value,
            task_type=task_type,
            provider=client.provider,
            model=client.model_name,
            messages=messages,
            params=params,
            response_text=text,
            usage=client.last_usage,
            db_path=db_path,
        )

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
            # Keep the historical class/status context, but redact credentials and
            # bound each provider message so response bodies cannot be dumped.
            def safe_message(value: str) -> str:
                value = re.sub(r"Bearer\s+[^\s,}]+", "Bearer [REDACTED]", value, flags=re.I)
                value = re.sub(r"(?:api[_-]?key|authorization)\s*[:=]\s*[^,}\s]+", "[REDACTED]", value, flags=re.I)
                # Provider JSON bodies can contain account metadata and should
                # never be copied into application logs.
                if "{" in value and "}" in value:
                    value = value.split("{", 1)[0].rstrip() + "{provider response redacted}"
                return value[:240]

            chain = [f"{exc.__class__.__name__}: {safe_message(str(exc))}"]
            current = exc.__cause__ or exc.__context__
            while current is not None and len(chain) < 3:
                chain.append(f"{current.__class__.__name__}: {safe_message(str(current))}")
                current = current.__cause__ or current.__context__
            print(f"FallbackReason:{reason} | " + " <- ".join(chain))

    def _resolve_model_slot(self, model_name: str) -> str:
        if model_name == DEEPSEEK_PRO_SLOT:
            return self.settings.deepseek_model_pro
        if model_name == DEEPSEEK_FLASH_SLOT:
            return self.settings.deepseek_model_flash
        if model_name == QWEN_FLASH_SLOT:
            return self.settings.qwen_model_flash
        if model_name == QWEN_PLUS_SLOT:
            return self.settings.qwen_model_plus
        if model_name == QWEN_MAX_SLOT:
            return self.settings.qwen_model_max
        return model_name


async def _replay_stream(text: str, chunk_size: int = 3) -> AsyncIterator[StreamToken]:
    """把录制的完整文本切成小块，模拟流式输出。

    前端消费的是 SSE token 流，回放必须保持同样的形状，否则打字机效果和
    首字延迟都会变。chunk_size 取 3 个字，接近中文模型的实际 token 粒度。
    """

    for start in range(0, len(text), chunk_size):
        yield ("message", text[start : start + chunk_size])
        await asyncio.sleep(0)


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
