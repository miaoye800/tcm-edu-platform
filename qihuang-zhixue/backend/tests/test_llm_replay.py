"""录制回放、思考模式默认值、千问参数映射的单元测试。

这些测试不触网：``mock`` 模式走 MockClient，``replay`` 模式在未命中时直接抛错，
千问只验证请求体构造。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import Settings
from app.llm.clients import DeepSeekClient, QwenClient
from app.llm.router import LLMReplayMissError, ModelRouter


def _router(tmp_path: Path, **overrides: object) -> ModelRouter:
    base: dict[str, object] = {
        "DEEPSEEK_API_KEY": None,
        "ZHIPU_API_KEY": None,
        "QWEN_API_KEY": None,
        "QIHUANG_DB_PATH": str(tmp_path / "test.db"),
    }
    base.update(overrides)
    return ModelRouter(settings=Settings(**base))


def test_thinking_defaults_to_disabled() -> None:
    settings = Settings(DEEPSEEK_API_KEY=None, LLM_THINKING_DEFAULT="disabled")
    router = ModelRouter(settings=settings)
    assert router._with_default_params({})["thinking"] == {"type": "disabled"}


def test_explicit_thinking_is_not_overridden() -> None:
    settings = Settings(DEEPSEEK_API_KEY=None, LLM_THINKING_DEFAULT="disabled")
    router = ModelRouter(settings=settings)
    params = router._with_default_params({"thinking": {"type": "enabled"}})
    assert params["thinking"] == {"type": "enabled"}


def test_mock_mode_returns_mock_text(tmp_path: Path) -> None:
    router = _router(tmp_path, LLM_MODE="mock")
    text = asyncio.run(router.chat("generate", [{"role": "user", "content": "你好"}]))
    assert "MOCK" in text


def test_replay_mode_raises_on_miss(tmp_path: Path) -> None:
    router = _router(tmp_path, LLM_MODE="replay", DEEPSEEK_API_KEY="sk-fake")
    with pytest.raises(LLMReplayMissError):
        asyncio.run(router.chat("generate", [{"role": "user", "content": "没有录过"}]))
    # 抛错是刻意的：只有失败才能让 e2e 暴露"这段链路没被录过"。


def test_replay_returns_recorded_response(tmp_path: Path) -> None:
    from app.llm import replay

    router = _router(tmp_path, LLM_MODE="replay", DEEPSEEK_API_KEY="sk-fake")
    # 用 router 解析出的模型名录制：指纹包含模型名，换模型必须重新录制。
    model_name = router.resolve("generate").model_name
    messages = [{"role": "user", "content": "复述这句"}]
    params = {"thinking": {"type": "disabled"}, "temperature": 0}
    fingerprint_value = replay.fingerprint(
        task_type="generate", model=model_name, messages=messages, params=params
    )
    replay.save(
        fingerprint_value=fingerprint_value,
        task_type="generate",
        provider="deepseek",
        model=model_name,
        messages=messages,
        params=params,
        response_text="这是录制下来的真实响应",
        db_path=tmp_path / "test.db",
    )

    text = asyncio.run(router.chat("generate", messages, temperature=0))
    assert text == "这是录制下来的真实响应"


def test_fingerprint_ignores_unrelated_params() -> None:
    from app.llm import replay

    base = replay.fingerprint(
        task_type="generate",
        model="m",
        messages=[{"role": "user", "content": "x"}],
        params={"temperature": 0},
    )
    with_timeout = replay.fingerprint(
        task_type="generate",
        model="m",
        messages=[{"role": "user", "content": "x"}],
        params={"temperature": 0, "timeout_seconds": 99},
    )
    assert base == with_timeout


def test_qwen_maps_thinking_to_enable_thinking() -> None:
    client = QwenClient(model_name="qwen-flash", api_key="x", base_url="https://example.test")
    payload: dict[str, object] = {}
    client._apply_params(payload, {"thinking": {"type": "disabled"}, "temperature": 0})
    assert payload["enable_thinking"] is False
    assert "thinking" not in payload  # 千问不认这个字段，必须过滤掉


def test_qwen_maps_reasoning_effort_to_budget() -> None:
    client = QwenClient(model_name="qwen-flash", api_key="x", base_url="https://example.test")
    payload: dict[str, object] = {}
    client._apply_params(
        payload,
        {"thinking": {"type": "enabled"}, "reasoning_effort": "medium"},
    )
    assert payload["enable_thinking"] is True
    assert payload["thinking_budget"] == 4096


def test_deepseek_keeps_native_thinking_fields() -> None:
    client = DeepSeekClient(model_name="deepseek-v4-flash", api_key="x", base_url="https://example.test")
    payload: dict[str, object] = {}
    client._apply_params(
        payload,
        {"thinking": {"type": "disabled"}, "reasoning_effort": "low", "max_tokens": 100},
    )
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["reasoning_effort"] == "low"
    assert payload["max_tokens"] == 100


def test_tool_turn_round_trips_without_text(tmp_path) -> None:  # noqa: ANN001
    """只有工具调用、没有正文的一轮也必须能存能取。

    升级前 ``save()`` 开头就 ``if not response_text: return``，这类轮次全被丢掉，
    回放时循环第一轮就跑不下去。
    """

    from app.llm import replay

    db = tmp_path / "replay.db"
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "c1", "function": {"name": "finish", "arguments": "{}"}}],
    }
    fp = replay.fingerprint(
        task_type="ppt_assets",
        model="deepseek-flash",
        messages=[{"role": "user", "content": "找图"}],
        params={},
    )
    replay.save(
        fingerprint_value=fp,
        task_type="ppt_assets",
        provider="deepseek",
        model="deepseek-flash",
        messages=[{"role": "user", "content": "找图"}],
        params={},
        response_text="",
        response_message=message,
        db_path=db,
    )
    assert not replay.load(fp, db), "正文为空：老接口拿不到东西，新接口必须拿得到"
    assert replay.load_message(fp, db) == message


def test_fingerprint_separates_different_tool_sets(tmp_path) -> None:  # noqa: ANN001
    from app.llm import replay

    base = dict(task_type="ppt_assets", model="deepseek-flash", messages=[{"role": "user", "content": "x"}])
    with_tools = replay.fingerprint(**base, params={"tools": [{"type": "function"}]})
    without = replay.fingerprint(**base, params={})
    assert with_tools != without
