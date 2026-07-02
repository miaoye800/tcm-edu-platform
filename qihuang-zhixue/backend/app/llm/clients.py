import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from app.config import get_settings

ChatMessage = Mapping[str, Any]


def _last_user_content(messages: Sequence[ChatMessage]) -> str:
    """提取最后一条用户消息，保证 MOCK 输出稳定可预测。"""

    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    if messages:
        return str(messages[-1].get("content", ""))
    return ""


class BaseLLMClient(ABC):
    """大模型客户端抽象基类，统一普通调用和流式调用接口。"""

    provider: str
    model_name: str

    def __init__(self, model_name: str, provider: str) -> None:
        self.model_name = model_name
        self.provider = provider

    @abstractmethod
    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> str:
        """返回完整模型回复。"""

    @abstractmethod
    async def chat_stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        """异步逐 token 产出模型回复。"""

        if False:
            yield ""


class MockClient(BaseLLMClient):
    """无 API Key 时使用的确定性客户端。"""

    def __init__(self, model_name: str, provider: str = "mock", stream_delay: float | None = None) -> None:
        super().__init__(model_name=model_name, provider=provider)
        self.stream_delay = get_settings().mock_stream_delay if stream_delay is None else stream_delay

    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> str:
        question = _last_user_content(messages)
        return f"[MOCK:{self.model_name}] 你问的是：{question}"

    async def chat_stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        text = await self.chat(messages)
        for token in text:
            yield token
            if self.stream_delay > 0:
                await asyncio.sleep(self.stream_delay)


class DeepSeekClient(BaseLLMClient):
    """DeepSeek 客户端占位实现，后续在此接入真实 HTTP 协议。"""

    def __init__(self, model_name: str, api_key: str | None = None) -> None:
        settings = get_settings()
        super().__init__(model_name=model_name, provider="deepseek")
        self.api_key = api_key or settings.deepseek_api_key

    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> str:
        if not self.api_key:
            return await MockClient(self.model_name, self.provider).chat(messages, **kw)
        raise NotImplementedError("DeepSeek 真实 API 调用将在后续任务接入。")

    async def chat_stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        if not self.api_key:
            async for token in MockClient(self.model_name, self.provider).chat_stream(messages):
                yield token
            return
        raise NotImplementedError("DeepSeek 真实流式 API 调用将在后续任务接入。")


class ZhipuClient(BaseLLMClient):
    """智谱客户端占位实现，后续在此接入真实 HTTP 协议。"""

    def __init__(self, model_name: str, api_key: str | None = None) -> None:
        settings = get_settings()
        super().__init__(model_name=model_name, provider="zhipu")
        self.api_key = api_key or settings.zhipu_api_key

    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> str:
        if not self.api_key:
            return await MockClient(self.model_name, self.provider).chat(messages, **kw)
        raise NotImplementedError("智谱真实 API 调用将在后续任务接入。")

    async def chat_stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        if not self.api_key:
            async for token in MockClient(self.model_name, self.provider).chat_stream(messages):
                yield token
            return
        raise NotImplementedError("智谱真实流式 API 调用将在后续任务接入。")
