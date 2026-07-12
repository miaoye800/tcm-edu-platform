import asyncio
import json
from collections.abc import Sequence
from typing import Any

import httpx
import pytest

from app.llm.clients import (
    ChatMessage,
    DeepSeekClient,
    LLMConnectTimeoutError,
    LLMHTTPStatusError,
    LLMResponseParseError,
)


def test_deepseek_client_uses_reasoning_content_when_content_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    payload = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "reasoning_content": json.dumps(
                        {
                            "strength_dimension_keys": ["knowledge_base"],
                            "weakness_dimension_keys": ["weak_points"],
                            "weak_point_labels": ["清热药·剂量"],
                            "advice_dimension_keys": ["weak_points"],
                        },
                        ensure_ascii=False,
                    ),
                }
            }
        ]
    }

    class FakeResponse:
        status_code = 200

        def __init__(self, body: dict[str, Any]) -> None:
            self._body = body
            self.text = json.dumps(body, ensure_ascii=False)

        def json(self) -> dict[str, Any]:
            return self._body

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse(payload)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = DeepSeekClient(
        model_name="deepseek-v4-flash",
        api_key="test-key",
        base_url="https://api.deepseek.com/v1",
        timeout_seconds=60.0,
    )
    result = asyncio.run(
        client.chat(
            [{"role": "user", "content": "生成画像计划"}],
            thinking={"type": "disabled"},
            response_format={"type": "json_object"},
            timeout_seconds=2.5,
            prefer_reasoning_content=True,
        )
    )

    assert client.timeout_seconds == 60.0
    assert captured["timeout"] == 2.5
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["json"]["thinking"] == {"type": "disabled"}
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert json.loads(result)["strength_dimension_keys"] == ["knowledge_base"]


def test_deepseek_client_raises_when_content_and_reasoning_content_both_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"choices": [{"message": {"content": "", "reasoning_content": ""}}]}

    class FakeResponse:
        status_code = 200

        def __init__(self, body: dict[str, Any]) -> None:
            self._body = body
            self.text = json.dumps(body, ensure_ascii=False)

        def json(self) -> dict[str, Any]:
            return self._body

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
            return FakeResponse(payload)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = DeepSeekClient(
        model_name="deepseek-v4-flash",
        api_key="test-key",
        base_url="https://api.deepseek.com/v1",
        timeout_seconds=60.0,
    )

    with pytest.raises(LLMResponseParseError, match="non-empty message.content/reasoning_content"):
        asyncio.run(client.chat([{"role": "user", "content": "生成画像计划"}]))


def test_deepseek_client_maps_http_401_to_non_retryable_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        status_code = 401
        text = '{"error":"invalid key"}'

        def json(self) -> dict[str, Any]:
            return {"error": "invalid key"}

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = DeepSeekClient(
        model_name="deepseek-v4-flash",
        api_key="test-key",
        base_url="https://api.deepseek.com/v1",
        timeout_seconds=60.0,
    )

    with pytest.raises(LLMHTTPStatusError) as exc_info:
        asyncio.run(client.chat([{"role": "user", "content": "生成画像计划"}]))

    assert exc_info.value.status_code == 401
    assert exc_info.value.retryable is False
    assert exc_info.value.fallback_reason == "HTTP401"


def test_deepseek_client_maps_connect_timeout_to_retryable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str], json: dict[str, Any]):
            raise httpx.ConnectTimeout("dial tcp timeout")

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = DeepSeekClient(
        model_name="deepseek-v4-flash",
        api_key="test-key",
        base_url="https://api.deepseek.com/v1",
        timeout_seconds=60.0,
    )

    with pytest.raises(LLMConnectTimeoutError) as exc_info:
        asyncio.run(client.chat([{"role": "user", "content": "生成画像计划"}]))

    assert exc_info.value.retryable is True
    assert exc_info.value.fallback_reason == "ConnectTimeout"
