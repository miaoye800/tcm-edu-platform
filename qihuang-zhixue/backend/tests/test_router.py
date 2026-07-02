import asyncio

from app.config import Settings
from app.llm.clients import MockClient
from app.llm.router import ModelRouter


def _mock_settings() -> Settings:
    return Settings(
        DEEPSEEK_API_KEY=None,
        ZHIPU_API_KEY=None,
        LLM_TASK_MODEL_MAP={
            "classify": "deepseek-flash",
            "route": "deepseek-flash",
            "reason": "deepseek-pro",
            "generate": "deepseek-pro",
            "vision": "glm-4.5v",
        },
        MOCK_STREAM_DELAY=0.0,
    )


def test_router_selects_expected_models() -> None:
    router = ModelRouter(settings=_mock_settings())

    assert router.resolve("classify").model_name == "deepseek-flash"
    assert router.resolve("route").model_name == "deepseek-flash"
    assert router.resolve("reason").model_name == "deepseek-pro"
    assert router.resolve("generate").model_name == "deepseek-pro"
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
    assert text == "[MOCK:deepseek-pro] 你问的是：黄芪有什么功效？"
    assert stream_text == text
