import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from app.config import Settings
from app.llm.clients import BaseLLMClient, ChatMessage, DeepSeekClient, MockClient
from app.llm.router import ModelRouter, ModelSpec


def _mock_settings() -> Settings:
    return Settings(
        DEEPSEEK_API_KEY=None,
        ZHIPU_API_KEY=None,
        LLM_TASK_MODEL_MAP={
            "classify": "deepseek_model_flash",
            "route": "deepseek_model_flash",
            "reason": "deepseek_model_flash",
            "generate": "deepseek_model_flash",
            "profile_jury": "deepseek_model_pro",
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

    assert router.resolve("classify").model_name == "deepseek-v4-flash"
    assert router.resolve("route").model_name == "deepseek-v4-flash"
    assert router.resolve("reason").model_name == "deepseek-v4-flash"
    assert router.resolve("generate").model_name == "deepseek-v4-flash"
    assert router.resolve("profile_extract").model_name == "deepseek-v4-flash"
    assert router.resolve("profile_update").model_name == "deepseek-v4-flash"
    assert router.resolve("profile_jury").model_name == "deepseek-v4-pro"
    assert router.resolve("vision").model_name == "glm-4.5v"


def test_router_uses_predictable_mock_without_keys() -> None:
    asyncio.run(_assert_predictable_mock_without_keys())


async def _assert_predictable_mock_without_keys() -> None:
    router = ModelRouter(settings=_mock_settings())
    messages = [{"role": "user", "content": "黄芪有什么功效？"}]

    client = router.select("reason")
    text = await router.chat("reason", messages)
    stream_text = "".join([token async for token in router.chat_stream("reason", messages)])

    assert isinstance(client, MockClient)
    assert client.stream_delay == 0.0
    assert text == "[MOCK:deepseek-v4-flash] 你问的是：黄芪有什么功效？"
    assert stream_text == text


def test_router_treats_blank_deepseek_key_as_mock() -> None:
    settings = _mock_settings()
    settings.deepseek_api_key = "   "
    router = ModelRouter(settings=settings)

    assert isinstance(router.select("reason"), MockClient)


def test_router_passes_deepseek_base_url_and_model_to_real_client() -> None:
    settings = _real_key_settings()
    settings.deepseek_base_url = "https://example.deepseek.test/v1/"
    settings.deepseek_model_pro = "deepseek-v4-pro-custom"
    router = ModelRouter(settings=settings)

    client = router.select("profile_jury")

    assert isinstance(client, DeepSeekClient)
    assert client.model_name == "deepseek-v4-pro-custom"
    assert client.base_url == "https://example.deepseek.test/v1"


def test_router_falls_back_to_mock_when_real_chat_raises() -> None:
    asyncio.run(_assert_fallback_when_real_chat_raises())


async def _assert_fallback_when_real_chat_raises() -> None:
    router = ModelRouter(settings=_real_key_settings(), client_factory=_raising_client_factory)
    messages = [{"role": "user", "content": "黄芪有什么功效？"}]

    text = await router.chat("reason", messages)

    assert text.startswith("[MOCK:deepseek-v4-flash]")
    assert "黄芪有什么功效" in text


def test_router_falls_back_to_mock_when_real_chat_times_out() -> None:
    asyncio.run(_assert_fallback_when_real_chat_times_out())


async def _assert_fallback_when_real_chat_times_out() -> None:
    router = ModelRouter(settings=_real_key_settings(), client_factory=_slow_client_factory)
    messages = [{"role": "user", "content": "麻黄有什么功效？"}]

    text = await router.chat("reason", messages)

    assert text.startswith("[MOCK:deepseek-v4-flash]")
    assert "麻黄有什么功效" in text


def test_router_stream_falls_back_to_mock_when_real_stream_raises() -> None:
    asyncio.run(_assert_stream_fallback_when_real_stream_raises())


async def _assert_stream_fallback_when_real_stream_raises() -> None:
    router = ModelRouter(settings=_real_key_settings(), client_factory=_raising_client_factory)
    messages = [{"role": "user", "content": "桂枝有什么功效？"}]

    text = "".join([token async for token in router.chat_stream("reason", messages)])

    assert text.startswith("[MOCK:deepseek-v4-flash]")
    assert "桂枝有什么功效" in text


def test_router_returns_friendly_error_when_fallback_disabled() -> None:
    asyncio.run(_assert_friendly_error_when_fallback_disabled())


async def _assert_friendly_error_when_fallback_disabled() -> None:
    router = ModelRouter(settings=_real_key_settings(fallback_to_mock=False), client_factory=_raising_client_factory)
    messages = [{"role": "user", "content": "黄芪有什么功效？"}]

    text = await router.chat("reason", messages)

    assert "RuntimeError" in text
    assert "模型服务" in text


def _real_key_settings(fallback_to_mock: bool = True) -> Settings:
    settings = _mock_settings()
    settings.deepseek_api_key = "test-key"
    settings.llm_fallback_to_mock = fallback_to_mock
    return settings


def _raising_client_factory(spec: ModelSpec, api_key: str | None) -> BaseLLMClient:
    return RaisingClient(model_name=spec.model_name, provider=spec.provider)


def _slow_client_factory(spec: ModelSpec, api_key: str | None) -> BaseLLMClient:
    return SlowClient(model_name=spec.model_name, provider=spec.provider)


class RaisingClient(BaseLLMClient):
    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> str:
        raise RuntimeError("upstream failed")

    async def chat_stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        raise RuntimeError("upstream stream failed")
        if False:
            yield ""


class SlowClient(BaseLLMClient):
    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> str:
        await asyncio.sleep(1)
        return "too late"

    async def chat_stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        await asyncio.sleep(1)
        yield "too late"
