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
    test_settings = Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None)
    monkeypatch.setattr('app.profile.jury.get_settings', lambda: test_settings)
    monkeypatch.setattr('app.profile.update.get_settings', lambda: test_settings)
    monkeypatch.setattr('app.profile.modes.get_settings', lambda: test_settings)
    monkeypatch.setattr('app.llm.router.get_settings', lambda: test_settings)
    yield
