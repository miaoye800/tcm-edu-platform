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


# ── 工具调用 ────────────────────────────────────────────────────────────────


def test_tool_calls_are_parsed_with_arguments() -> None:
    from app.llm.clients import tool_turn_from_message

    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search_assets", "arguments": '{"query": "黄芪", "limit": 3}'},
            }
        ],
    }
    turn = tool_turn_from_message(message)
    assert turn.wants_tools
    assert turn.text == ""
    assert turn.tool_calls[0].name == "search_assets"
    assert turn.tool_calls[0].arguments == {"query": "黄芪", "limit": 3}


def test_broken_arguments_keep_the_call_and_the_raw_text() -> None:
    """参数写错一个逗号不能把这一轮丢掉——模型会以为自己调用成功了。"""

    from app.llm.clients import tool_turn_from_message

    turn = tool_turn_from_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "function": {"name": "assign_asset", "arguments": "{unit_id: p1}"}}],
        }
    )
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].arguments == {}
    assert "unit_id" in turn.tool_calls[0].raw_arguments


def test_message_without_content_or_calls_is_empty_turn() -> None:
    from app.llm.clients import tool_turn_from_message

    turn = tool_turn_from_message({"role": "assistant", "content": None})
    assert not turn.wants_tools
    assert turn.text == ""


def test_tools_reach_the_request_payload() -> None:
    """白名单里没有 `tools` 时它会被静默丢掉——那正是升级前的缺陷。"""

    from app.llm.clients import DeepSeekClient

    payload: dict = {}
    client = DeepSeekClient("deepseek-flash", api_key="k", base_url="http://x")
    client._apply_params(payload, {"tools": [{"type": "function"}], "tool_choice": "auto", "thinking": {"type": "disabled"}})
    assert payload["tools"] == [{"type": "function"}]
    assert payload["tool_choice"] == "auto"
