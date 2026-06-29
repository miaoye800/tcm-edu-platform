from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.config import Settings, get_settings
from app.llm.clients import BaseLLMClient, ChatMessage, DeepSeekClient, MockClient, ZhipuClient


@dataclass(frozen=True)
class ModelSpec:
    """路由后的模型规格。"""

    provider: str
    model_name: str


def _provider_for_model(model_name: str) -> str:
    if model_name.startswith("deepseek"):
        return "deepseek"
    if model_name.startswith("glm"):
        return "zhipu"
    raise ValueError(f"无法根据模型名推断供应商：{model_name}")


class ModelRouter:
    """按任务类型选择模型，并在缺少 API Key 时自动降级到 MOCK。"""

    def __init__(
        self,
        task_model_map: Mapping[str, str] | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.task_model_map = dict(task_model_map or self.settings.llm_task_model_map)

    def resolve(self, task_type: str) -> ModelSpec:
        model_name = self.task_model_map.get(task_type)
        if model_name is None:
            raise ValueError(f"未配置 task_type 对应模型：{task_type}")
        return ModelSpec(provider=_provider_for_model(model_name), model_name=model_name)

    def select(self, task_type: str) -> BaseLLMClient:
        spec = self.resolve(task_type)
        api_key = self._api_key_for_provider(spec.provider)
        if not api_key:
            return MockClient(model_name=spec.model_name, provider=spec.provider)
        if spec.provider == "deepseek":
            return DeepSeekClient(model_name=spec.model_name, api_key=api_key)
        if spec.provider == "zhipu":
            return ZhipuClient(model_name=spec.model_name, api_key=api_key)
        raise ValueError(f"不支持的模型供应商：{spec.provider}")

    async def chat(self, task_type: str, messages: Sequence[ChatMessage], **kw: Any) -> str:
        client = self.select(task_type)
        return await client.chat(messages, **kw)

    async def chat_stream(self, task_type: str, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        client = self.select(task_type)
        async for token in client.chat_stream(messages):
            yield token

    def _api_key_for_provider(self, provider: str) -> str | None:
        if provider == "deepseek":
            return self.settings.deepseek_api_key
        if provider == "zhipu":
            return self.settings.zhipu_api_key
        return None
