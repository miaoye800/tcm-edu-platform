import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Literal

import httpx

from app.config import get_settings

ChatMessage = Mapping[str, Any]
StreamTokenKind = Literal["thinking", "message"]
StreamToken = tuple[StreamTokenKind, str]
logger = logging.getLogger(__name__)


class LLMClientError(RuntimeError):
    """Base error type exposed to router retry logic."""

    retryable = False

    def __init__(self, message: str, *, fallback_reason: str | None = None) -> None:
        super().__init__(message)
        self.fallback_reason = fallback_reason or self.__class__.__name__


class LLMConnectTimeoutError(LLMClientError):
    retryable = True

    def __init__(self, message: str) -> None:
        super().__init__(message, fallback_reason="ConnectTimeout")


class LLMReadTimeoutError(LLMClientError):
    retryable = True

    def __init__(self, message: str) -> None:
        super().__init__(message, fallback_reason="ReadTimeout")


class LLMRequestTimeoutError(LLMClientError):
    retryable = True

    def __init__(self, message: str, *, fallback_reason: str = "TimeoutError") -> None:
        super().__init__(message, fallback_reason=fallback_reason)


class LLMConnectError(LLMClientError):
    retryable = True

    def __init__(self, message: str) -> None:
        super().__init__(message, fallback_reason="ConnectError")


class LLMHTTPStatusError(LLMClientError):
    def __init__(self, message: str, *, status_code: int, retryable: bool) -> None:
        super().__init__(message, fallback_reason=f"HTTP{status_code}")
        self.status_code = status_code
        self.retryable = retryable


class LLMResponseParseError(LLMClientError):
    def __init__(self, message: str, *, fallback_reason: str = "ResponseParseError") -> None:
        super().__init__(message, fallback_reason=fallback_reason)


class LLMResponseShapeError(LLMClientError):
    def __init__(self, message: str) -> None:
        super().__init__(message, fallback_reason="ResponseShapeError")


def _last_user_content(messages: Sequence[ChatMessage]) -> str:
    """Extract the last user message so mock output stays predictable."""

    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    if messages:
        return str(messages[-1].get("content", ""))
    return ""


class BaseLLMClient(ABC):
    """Common interface for full and streaming LLM calls."""

    provider: str
    model_name: str

    def __init__(self, model_name: str, provider: str) -> None:
        self.model_name = model_name
        self.provider = provider
        # 最近一次调用返回的 usage 快照，供 ModelRouter 落库计量。
        self.last_usage: dict[str, Any] | None = None

    @abstractmethod
    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> str:
        """Return a complete model response."""

    @abstractmethod
    async def chat_stream(self, messages: Sequence[ChatMessage], **kw: Any) -> AsyncIterator[StreamToken]:
        """Yield typed streaming tokens from a model response."""

        if False:
            yield ("message", "")


class MockClient(BaseLLMClient):
    """Deterministic client used when no API key is configured."""

    def __init__(self, model_name: str, provider: str = "mock", stream_delay: float | None = None) -> None:
        super().__init__(model_name=model_name, provider=provider)
        self.stream_delay = get_settings().mock_stream_delay if stream_delay is None else stream_delay

    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> str:
        question = _last_user_content(messages)
        return f"[MOCK:{self.model_name}] 你问的是：{question}"

    async def chat_stream(self, messages: Sequence[ChatMessage], **kw: Any) -> AsyncIterator[StreamToken]:
        text = await self.chat(messages)
        for token in text:
            yield ("message", token)
            if self.stream_delay > 0:
                await asyncio.sleep(self.stream_delay)


class DeepSeekClient(BaseLLMClient):
    """OpenAI-compatible chat client; 千问复用同一实现，只换 base_url 与参数映射。"""

    # 允许平铺进请求体的参数。子类可覆盖（千问把 thinking 收进 extra_body）。
    PARAM_WHITELIST: tuple[str, ...] = (
        "temperature",
        "max_tokens",
        "top_p",
        "thinking",
        "reasoning_effort",
        "response_format",
    )

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        *,
        provider: str = "deepseek",
    ) -> None:
        settings = get_settings()
        super().__init__(model_name=model_name, provider=provider)
        self.api_key = (api_key if api_key is not None else settings.deepseek_api_key) or None
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else settings.llm_timeout_seconds

    def _apply_params(self, payload: dict[str, Any], kw: Mapping[str, Any]) -> None:
        for key in self.PARAM_WHITELIST:
            if key in kw and kw[key] is not None:
                payload[key] = kw[key]

    def _capture_usage(self, data: Mapping[str, Any]) -> None:
        usage = data.get("usage")
        self.last_usage = dict(usage) if isinstance(usage, Mapping) else None

    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> str:
        if not self.api_key:
            return await MockClient(self.model_name, self.provider).chat(messages, **kw)
        timeout_seconds = _timeout_seconds(kw, self.timeout_seconds)
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [dict(message) for message in messages],
            "stream": False,
        }
        self._apply_params(payload, kw)
        _log_deepseek_chat_request(payload, timeout_seconds=timeout_seconds)

        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise _wrap_httpx_error("request", exc) from exc

        _raise_for_status(response)
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise LLMResponseParseError(
                f"DeepSeek response JSON decode failed: {exc}",
                fallback_reason=exc.__class__.__name__,
            ) from exc
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseShapeError(f"DeepSeek response missing choices[0].message: {data}") from exc
        self._capture_usage(data)
        text = _deepseek_message_text(
            message,
            prefer_reasoning_content=bool(kw.get("prefer_reasoning_content")),
        )
        if text:
            return text
        raise LLMResponseParseError(
            f"DeepSeek response missing non-empty message.content/reasoning_content: {data}"
        )

    async def chat_stream(self, messages: Sequence[ChatMessage], **kw: Any) -> AsyncIterator[StreamToken]:
        if not self.api_key:
            async for token in MockClient(self.model_name, self.provider).chat_stream(messages, **kw):
                yield token
            return
        timeout_seconds = _timeout_seconds(kw, self.timeout_seconds)

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [dict(message) for message in messages],
            "stream": True,
        }
        self._apply_params(payload, kw)
        _log_deepseek_chat_request(payload, timeout_seconds=timeout_seconds)

        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as response:
                    # Never pre-read a successful SSE response: `aread()` drains
                    # the whole body and turns a genuine provider stream into one
                    # buffered response for the browser. Read a body only when
                    # reporting a non-success status.
                    if response.status_code >= 400:
                        _raise_for_status(response, body=await response.aread())
                    frame_lines: list[str] = []
                    async for line in response.aiter_lines():
                        if line == "":
                            frame = "\n".join(frame_lines)
                            frame_lines = []
                            if _is_done_sse_frame(frame):
                                break
                            for token in _stream_tokens_from_sse_frame(frame):
                                yield token
                            continue
                        if line.startswith("data:") and frame_lines and any(item.startswith("data:") for item in frame_lines):
                            frame = "\n".join(frame_lines)
                            frame_lines = []
                            if _is_done_sse_frame(frame):
                                break
                            for token in _stream_tokens_from_sse_frame(frame):
                                yield token
                        if _is_done_sse_frame(line):
                            break
                        frame_lines.append(line)
                    else:
                        if frame_lines:
                            frame = "\n".join(frame_lines)
                            if not _is_done_sse_frame(frame):
                                for token in _stream_tokens_from_sse_frame(frame):
                                    yield token
        except httpx.HTTPError as exc:
            raise _wrap_httpx_error("stream request", exc) from exc


class QwenClient(DeepSeekClient):
    """千问（DashScope OpenAI 兼容模式）客户端。

    与 DeepSeek 共用请求与解析逻辑，差异只有两点：

    1. base_url 与 api_key 来源不同；
    2. 思考模式参数不同 —— Qwen3 没有 ``reasoning_effort`` 概念，用
       ``enable_thinking`` + ``thinking_budget`` 表达。OpenAI SDK 的
       ``extra_body`` 最终是 merge 到请求体顶层，所以这里直接平铺，
       不再透传 ``thinking`` / ``reasoning_effort``（千问不认，会 400）。
    """

    PARAM_WHITELIST: tuple[str, ...] = ("temperature", "max_tokens", "top_p", "response_format")

    THINKING_BUDGET = {"low": 1024, "medium": 4096, "high": 8192}

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        super().__init__(
            model_name=model_name,
            api_key=api_key if api_key is not None else settings.qwen_api_key,
            base_url=base_url or settings.qwen_base_url,
            timeout_seconds=timeout_seconds,
            provider="qwen",
        )

    def _apply_params(self, payload: dict[str, Any], kw: Mapping[str, Any]) -> None:
        super()._apply_params(payload, kw)
        thinking = kw.get("thinking")
        if not isinstance(thinking, Mapping):
            return
        enabled = thinking.get("type") == "enabled"
        payload["enable_thinking"] = enabled
        if not enabled:
            return
        budget = self.THINKING_BUDGET.get(str(kw.get("reasoning_effort") or "").lower())
        if budget:
            payload["thinking_budget"] = budget


class ZhipuClient(BaseLLMClient):
    """Zhipu placeholder client; real HTTP support can be added later."""

    def __init__(self, model_name: str, api_key: str | None = None) -> None:
        settings = get_settings()
        super().__init__(model_name=model_name, provider="zhipu")
        self.api_key = api_key or settings.zhipu_api_key

    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> str:
        if not self.api_key:
            return await MockClient(self.model_name, self.provider).chat(messages, **kw)
        raise NotImplementedError("Zhipu real API calls will be added in a later task.")

    async def chat_stream(self, messages: Sequence[ChatMessage], **kw: Any) -> AsyncIterator[StreamToken]:
        if not self.api_key:
            async for token in MockClient(self.model_name, self.provider).chat_stream(messages, **kw):
                yield token
            return
        raise NotImplementedError("Zhipu real streaming API calls will be added in a later task.")


def _stream_token_from_sse_line(line: str) -> StreamToken | None:
    tokens = _stream_tokens_from_sse_line(line)
    return tokens[0] if tokens else None


def _stream_tokens_from_sse_line(line: str) -> list[StreamToken]:
    return _stream_tokens_from_sse_frame(line)


def _stream_tokens_from_sse_frame(frame: str) -> list[StreamToken]:
    data = _sse_data_text(frame)
    if data is None:
        return []

    if data.strip() == "[DONE]":
        return []

    try:
        payload = json.loads(data)
        delta = payload["choices"][0].get("delta") or {}
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        fallback_reason = exc.__class__.__name__ if isinstance(exc, json.JSONDecodeError) else "ResponseShapeError"
        raise LLMResponseParseError(
            f"DeepSeek stream chunk missing choices[0].delta: {data}",
            fallback_reason=fallback_reason,
        ) from exc

    _log_deepseek_stream_delta(delta)

    tokens: list[StreamToken] = []
    reasoning_content = delta.get("reasoning_content")
    if reasoning_content:
        tokens.append(("thinking", str(reasoning_content)))
    content = delta.get("content")
    if content:
        tokens.append(("message", str(content)))
    return tokens


def _sse_data_text(frame: str) -> str | None:
    data_lines: list[str] = []
    for raw_line in frame.splitlines():
        if not raw_line.startswith("data:"):
            continue
        data = raw_line.removeprefix("data:")
        data_lines.append(data[1:] if data.startswith(" ") else data)
    if not data_lines:
        return None
    return "\n".join(data_lines)


def _log_deepseek_stream_delta(delta: Mapping[str, Any]) -> None:
    if get_settings().env.lower() not in {"dev", "development", "local"}:
        return
    reasoning = str(delta.get("reasoning_content") or "")
    content = str(delta.get("content") or "")
    logger.debug(
        "DeepSeek stream delta keys=%s has_reasoning=%s reasoning_len=%s content_len=%s",
        sorted(str(key) for key in delta.keys()),
        bool(reasoning),
        len(reasoning),
        len(content),
    )


def _deepseek_message_text(
    message: Mapping[str, Any],
    *,
    prefer_reasoning_content: bool = False,
) -> str:
    content = _deepseek_text_value(message.get("content"))
    reasoning_content = _deepseek_text_value(message.get("reasoning_content"))
    ordered = (reasoning_content, content) if prefer_reasoning_content else (content, reasoning_content)
    for item in ordered:
        if item.strip():
            return item
    return ""


def _deepseek_text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        parts: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                text = item.get("text")
                if text is None:
                    text = item.get("content")
                if text:
                    parts.append(str(text))
                continue
            if item:
                parts.append(str(item))
        return "".join(parts)
    return str(value)


def _raise_for_status(response: Any, *, body: bytes | None = None) -> None:
    status_code = int(response.status_code)
    if status_code < 400:
        return
    raw = body.decode("utf-8", errors="replace") if body is not None else str(response.text)
    detail = raw.replace("\n", " ")[:1000]
    retryable = status_code == 429 or 500 <= status_code < 600
    raise LLMHTTPStatusError(
        f"DeepSeek HTTP {status_code}: {detail}",
        status_code=status_code,
        retryable=retryable,
    )


def _wrap_httpx_error(action: str, exc: httpx.HTTPError) -> LLMClientError:
    message = f"DeepSeek {action} failed: {exc.__class__.__name__}: {exc}"
    if isinstance(exc, httpx.ConnectTimeout):
        return LLMConnectTimeoutError(message)
    if isinstance(exc, httpx.ReadTimeout):
        return LLMReadTimeoutError(message)
    if isinstance(exc, (httpx.WriteTimeout, httpx.PoolTimeout, httpx.TimeoutException)):
        return LLMRequestTimeoutError(message, fallback_reason=exc.__class__.__name__)
    if isinstance(exc, httpx.ConnectError):
        return LLMConnectError(message)
    return LLMClientError(message, fallback_reason=exc.__class__.__name__)


def _timeout_seconds(kw: Mapping[str, Any], default: float) -> float:
    raw = kw.get("timeout_seconds")
    try:
        return max(float(raw), 0.001) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _log_deepseek_chat_request(payload: Mapping[str, Any], *, timeout_seconds: float) -> None:
    if get_settings().env.lower() not in {"dev", "development", "local"}:
        return
    logger.debug(
        "DeepSeek request model=%s stream=%s thinking=%s response_format=%s timeout_seconds=%s",
        payload.get("model"),
        payload.get("stream"),
        bool(payload.get("thinking")),
        bool(payload.get("response_format")),
        timeout_seconds,
    )


def _is_done_sse_line(line: str) -> bool:
    return _is_done_sse_frame(line)


def _is_done_sse_frame(frame: str) -> bool:
    data = _sse_data_text(frame)
    return data is not None and data.strip() == "[DONE]"
