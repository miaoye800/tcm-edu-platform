import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.llm.clients import (
    BaseLLMClient,
    ChatMessage,
    DeepSeekClient,
    LLMConnectTimeoutError,
    LLMHTTPStatusError,
    LLMReadTimeoutError,
    MockClient,
    StreamToken,
)
from app.llm.router import ModelRouter, ModelSpec


def _mock_settings() -> Settings:
    return Settings(
        DEEPSEEK_API_KEY=None,
        ZHIPU_API_KEY=None,
        # 必须显式钉住模式。不写就会从本地 `.env` 读到 `LLM_MODE=auto`，
        # 于是 `chat_stream` 先去查录制回放，命中缓存后**绕过注入的假客户端**，
        # 测试看到的是缓存里被切块的旧文本，而不是假客户端 yield 的内容。
        # 实测过：`TypedStreamClient` 明明 yield 了 thinking，断言里却没有。
        LLM_MODE="mock",
        LLM_TASK_MODEL_MAP={
            "classify": "deepseek_model_flash",
            "route": "deepseek_model_flash",
            "reason": "deepseek_model_flash",
            "generate": "deepseek_model_flash",
            "profile_jury": "deepseek_model_flash",
            "profile_extract": "deepseek_model_flash",
            "profile_update": "deepseek_model_flash",
            "vision": "glm-4.5v",
        },
        MOCK_STREAM_DELAY=0.0,
        LLM_TIMEOUT_SECONDS=0.05,
        LLM_MAX_RETRIES=1,
        LLM_FALLBACK_TO_MOCK=True,
    )


def test_router_selects_expected_models() -> None:
    router = ModelRouter(settings=_mock_settings())

    assert router.resolve("classify").model_name == "deepseek-flash"
    assert router.resolve("route").model_name == "deepseek-flash"
    assert router.resolve("reason").model_name == "deepseek-flash"
    assert router.resolve("generate").model_name == "deepseek-flash"
    assert router.resolve("profile_extract").model_name == "deepseek-flash"
    assert router.resolve("profile_update").model_name == "deepseek-flash"
    assert router.resolve("profile_jury").model_name == "deepseek-flash"
    assert router.resolve("vision").model_name == "glm-4.5v"


def test_router_uses_predictable_mock_without_keys() -> None:
    asyncio.run(_assert_predictable_mock_without_keys())


async def _assert_predictable_mock_without_keys() -> None:
    router = ModelRouter(settings=_mock_settings())
    messages = [{"role": "user", "content": "黄芪有什么功效？"}]

    client = router.select("reason")
    text = await router.chat("reason", messages)
    stream_tokens = [token async for token in router.chat_stream("reason", messages)]
    stream_text = "".join(part for _, part in stream_tokens)

    assert isinstance(client, MockClient)
    assert client.stream_delay == 0.0
    assert text.startswith("[MOCK:deepseek-flash]")
    assert messages[0]["content"] in text
    assert stream_text == text
    assert {kind for kind, _ in stream_tokens} == {"message"}


def test_router_treats_blank_deepseek_key_as_mock() -> None:
    settings = _mock_settings()
    settings.deepseek_api_key = "   "
    router = ModelRouter(settings=settings)

    assert isinstance(router.select("reason"), MockClient)


def test_router_passes_deepseek_base_url_and_model_to_real_client() -> None:
    settings = _real_key_settings()
    settings.deepseek_base_url = "https://example.deepseek.test/v1/"
    settings.deepseek_model_flash = "deepseek-flash-custom"
    router = ModelRouter(settings=settings)

    client = router.select("profile_jury")

    assert isinstance(client, DeepSeekClient)
    assert client.model_name == "deepseek-flash-custom"
    assert client.base_url == "https://example.deepseek.test/v1"


def test_router_does_not_retry_non_retryable_http_401_and_logs_real_reason(capsys: pytest.CaptureFixture[str]) -> None:
    recorder = CallRecorder()
    router = ModelRouter(
        settings=_real_key_settings(),
        client_factory=_failing_client_factory(
            recorder,
            chat_exc=LLMHTTPStatusError("DeepSeek HTTP 401: invalid key", status_code=401, retryable=False),
        ),
    )

    text = asyncio.run(router.chat("reason", [{"role": "user", "content": "测试401"}]))
    out = capsys.readouterr().out

    assert recorder.chat_calls == 1
    assert "[FallbackReason:HTTP401]" in text
    assert "FallbackReason:HTTP401 | LLMHTTPStatusError: DeepSeek HTTP 401: invalid key" in out


def test_router_retries_connect_timeout_and_logs_real_reason(capsys: pytest.CaptureFixture[str]) -> None:
    recorder = CallRecorder()
    router = ModelRouter(
        settings=_real_key_settings(),
        client_factory=_failing_client_factory(
            recorder,
            chat_exc=LLMConnectTimeoutError("DeepSeek request failed: ConnectTimeout: dial tcp timeout"),
        ),
    )

    text = asyncio.run(router.chat("reason", [{"role": "user", "content": "测试连接超时"}]))
    out = capsys.readouterr().out

    assert recorder.chat_calls == 2
    assert "[FallbackReason:ConnectTimeout]" in text
    assert "FallbackReason:ConnectTimeout | LLMConnectTimeoutError: DeepSeek request failed: ConnectTimeout: dial tcp timeout" in out


def test_router_retries_read_timeout_via_client_mapping(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {"calls": 0}

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str], json: dict[str, Any]):
            captured["calls"] += 1
            raise httpx.ReadTimeout("socket read timed out")

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    router = ModelRouter(settings=_real_key_settings())
    text = asyncio.run(router.chat("reason", [{"role": "user", "content": "测试读超时"}]))
    out = capsys.readouterr().out

    assert captured["calls"] == 2
    assert "[FallbackReason:ReadTimeout]" in text
    assert "FallbackReason:ReadTimeout | LLMReadTimeoutError: DeepSeek request failed: ReadTimeout: socket read timed out" in out


@pytest.mark.parametrize(
    ("status_code", "reason"),
    [
        (429, "HTTP429"),
        (503, "HTTP503"),
    ],
)
def test_router_retries_retryable_http_statuses_and_preserves_reason(
    status_code: int,
    reason: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recorder = CallRecorder()
    router = ModelRouter(
        settings=_real_key_settings(),
        client_factory=_failing_client_factory(
            recorder,
            chat_exc=LLMHTTPStatusError(
                f"DeepSeek HTTP {status_code}: upstream unavailable",
                status_code=status_code,
                retryable=True,
            ),
        ),
    )

    text = asyncio.run(router.chat("reason", [{"role": "user", "content": f'测试{status_code}'}]))
    out = capsys.readouterr().out

    assert recorder.chat_calls == 2
    assert f"[FallbackReason:{reason}]" in text
    assert f"FallbackReason:{reason} | LLMHTTPStatusError: DeepSeek HTTP {status_code}: upstream unavailable" in out


def test_router_retries_wait_for_timeout_when_client_hangs() -> None:
    recorder = CallRecorder()
    router = ModelRouter(settings=_real_key_settings(), client_factory=_slow_client_factory(recorder))

    text = asyncio.run(router.chat("reason", [{"role": "user", "content": "麻黄有什么功效？"}]))

    assert recorder.chat_calls == 2
    assert "[FallbackReason:TimeoutError]" in text


def test_router_stream_passes_through_typed_tokens() -> None:
    asyncio.run(_assert_router_stream_passes_through_typed_tokens())


async def _assert_router_stream_passes_through_typed_tokens() -> None:
    router = ModelRouter(settings=_real_key_settings(), client_factory=_typed_stream_client_factory)
    messages = [{"role": "user", "content": "桂枝有什么功效？"}]

    tokens = [token async for token in router.chat_stream("reason", messages, reasoning_effort="high")]

    assert tokens == [("thinking", "先检索证据"), ("message", "桂枝可解肌发表。")]


def test_router_stream_falls_back_to_mock_when_real_stream_raises(capsys: pytest.CaptureFixture[str]) -> None:
    recorder = CallRecorder()
    router = ModelRouter(
        settings=_real_key_settings(),
        client_factory=_failing_client_factory(
            recorder,
            stream_exc=LLMHTTPStatusError("DeepSeek HTTP 401: invalid key", status_code=401, retryable=False),
        ),
    )
    messages = [{"role": "user", "content": "桂枝有什么功效？"}]

    tokens = asyncio.run(_collect_stream(router.chat_stream("reason", messages)))
    out = capsys.readouterr().out
    text = "".join(part for _, part in tokens)

    assert recorder.stream_calls == 1
    assert {kind for kind, _ in tokens} == {"message"}
    assert text.startswith("[MOCK:deepseek-flash]")
    assert messages[0]["content"] in text
    assert "[FallbackReason:HTTP401]" in text
    assert "FallbackReason:HTTP401 | LLMHTTPStatusError: DeepSeek HTTP 401: invalid key" in out


def test_router_returns_friendly_error_when_fallback_disabled() -> None:
    recorder = CallRecorder()
    router = ModelRouter(
        settings=_real_key_settings(fallback_to_mock=False),
        client_factory=_failing_client_factory(
            recorder,
            chat_exc=LLMHTTPStatusError("DeepSeek HTTP 401: invalid key", status_code=401, retryable=False),
        ),
    )

    text = asyncio.run(router.chat("reason", [{"role": "user", "content": "黄芪有什么功效？"}]))

    assert recorder.chat_calls == 1
    assert "HTTP401" in text
    assert "模型服务" in text


def _real_key_settings(fallback_to_mock: bool = True) -> Settings:
    settings = _mock_settings()
    settings.deepseek_api_key = "test-key"
    settings.llm_fallback_to_mock = fallback_to_mock
    return settings


@dataclass
class CallRecorder:
    chat_calls: int = 0
    stream_calls: int = 0


def _failing_client_factory(
    recorder: CallRecorder,
    *,
    chat_exc: Exception | None = None,
    stream_exc: Exception | None = None,
) -> Any:
    def factory(spec: ModelSpec, api_key: str | None) -> BaseLLMClient:
        return FailingClient(
            model_name=spec.model_name,
            provider=spec.provider,
            recorder=recorder,
            chat_exc=chat_exc,
            stream_exc=stream_exc,
        )

    return factory


def _slow_client_factory(recorder: CallRecorder) -> Any:
    def factory(spec: ModelSpec, api_key: str | None) -> BaseLLMClient:
        return SlowClient(model_name=spec.model_name, provider=spec.provider, recorder=recorder)

    return factory


def _typed_stream_client_factory(spec: ModelSpec, api_key: str | None) -> BaseLLMClient:
    return TypedStreamClient(model_name=spec.model_name, provider=spec.provider)


class FailingClient(BaseLLMClient):
    def __init__(
        self,
        *,
        model_name: str,
        provider: str,
        recorder: CallRecorder,
        chat_exc: Exception | None,
        stream_exc: Exception | None,
    ) -> None:
        super().__init__(model_name=model_name, provider=provider)
        self.recorder = recorder
        self.chat_exc = chat_exc or RuntimeError("upstream failed")
        self.stream_exc = stream_exc or RuntimeError("upstream stream failed")

    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> str:
        self.recorder.chat_calls += 1
        raise self.chat_exc

    async def chat_stream(self, messages: Sequence[ChatMessage], **kw: Any) -> AsyncIterator[StreamToken]:
        self.recorder.stream_calls += 1
        raise self.stream_exc
        if False:
            yield ("message", "")


class SlowClient(BaseLLMClient):
    def __init__(self, *, model_name: str, provider: str, recorder: CallRecorder) -> None:
        super().__init__(model_name=model_name, provider=provider)
        self.recorder = recorder

    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> str:
        self.recorder.chat_calls += 1
        await asyncio.sleep(1)
        return "too late"

    async def chat_stream(self, messages: Sequence[ChatMessage], **kw: Any) -> AsyncIterator[StreamToken]:
        self.recorder.stream_calls += 1
        await asyncio.sleep(1)
        yield ("message", "too late")


class TypedStreamClient(BaseLLMClient):
    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> str:
        return "unused"

    async def chat_stream(self, messages: Sequence[ChatMessage], **kw: Any) -> AsyncIterator[StreamToken]:
        assert kw["reasoning_effort"] == "high"
        yield ("thinking", "先检索证据")
        yield ("message", "桂枝可解肌发表。")


async def _collect_stream(stream: AsyncIterator[StreamToken]) -> list[StreamToken]:
    return [token async for token in stream]
