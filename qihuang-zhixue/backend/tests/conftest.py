from collections.abc import Iterator

import httpx
import pytest

from app.config import Settings
from app.main import app


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def block_real_llm_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep tests deterministic even when a developer .env contains an LLM key."""

    class NoNetworkAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> 'NoNetworkAsyncClient':
            return self

        async def __aexit__(self, *args: object, **kwargs: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError('LLM network is disabled during pytest')

        def stream(self, *args: object, **kwargs: object) -> object:
            raise httpx.ConnectError('LLM network is disabled during pytest')

    monkeypatch.setattr('app.llm.clients.httpx.AsyncClient', NoNetworkAsyncClient)
    # LLM_MODE=mock 让测试完全不经过录制回放与用量落库，避免在真实库上建表、
    # 也避免 fallback 的假文本被误录成"真实响应"。三家 key 全清空，
    # 否则 ModelRouter.select 会拿到 .env 里的 QWEN_API_KEY 去打真模型。
    test_settings = Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None, QWEN_API_KEY=None, LLM_MODE='mock')
    monkeypatch.setattr('app.profile.jury.get_settings', lambda: test_settings)
    monkeypatch.setattr('app.profile.update.get_settings', lambda: test_settings)
    monkeypatch.setattr('app.profile.modes.get_settings', lambda: test_settings)
    monkeypatch.setattr('app.llm.router.get_settings', lambda: test_settings)
    yield
