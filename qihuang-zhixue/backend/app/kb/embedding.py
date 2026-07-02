"""知识库向量化客户端。"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Sequence
from typing import Any

import httpx
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import get_settings


class EmbeddingSettings(BaseSettings):
    """Embedding 配置，缺省复用智谱 Key。"""

    embedding_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("EMBEDDING_API_KEY", "ZHIPU_API_KEY", "ZHIPUAI_API_KEY"),
    )
    embedding_base_url: str = Field(
        default="https://open.bigmodel.cn/api/paas/v4",
        alias="EMBEDDING_BASE_URL",
    )
    embedding_model: str = Field(default="embedding-3", alias="EMBEDDING_MODEL")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class EmbeddingClient:
    """有 Key 时调用真实 Embedding API，无 Key 时返回确定性 MOCK 向量。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        mock_dim: int = 384,
    ) -> None:
        settings = EmbeddingSettings()
        app_settings = get_settings()
        self.api_key = api_key or settings.embedding_api_key or app_settings.zhipu_api_key
        self.base_url = (base_url or settings.embedding_base_url).rstrip("/")
        self.model = model or settings.embedding_model
        self.mock_dim = mock_dim

    async def embed(self, text: str) -> list[float]:
        """生成单条文本向量。"""

        vectors = await self.embed_many([text])
        return vectors[0]

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        """批量生成向量。"""

        if not texts:
            return []
        if not self.api_key:
            return [self._mock_embed(text) for text in texts]
        return await self._real_embed_many(texts)

    async def _real_embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        payload: dict[str, Any] = {"model": self.model, "input": list(texts)}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{self.base_url}/embeddings", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        items = sorted(data.get("data", []), key=lambda item: int(item.get("index", 0)))
        vectors = [item.get("embedding", []) for item in items]
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding API 返回数量与输入数量不一致")
        return [[float(value) for value in vector] for vector in vectors]

    def _mock_embed(self, text: str) -> list[float]:
        vector = [0.0] * self.mock_dim
        for token in _mock_tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.mock_dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            weight = 1.0 + min(len(token), 4) * 0.2
            vector[index] += sign * weight

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


def _mock_tokens(text: str) -> Iterable[str]:
    """抽取中文 ngram 和英文词，提升离线检索的可解释性。"""

    compact = re.sub(r"\s+", "", text)
    for size in (2, 3, 4):
        for index in range(0, max(len(compact) - size + 1, 0)):
            token = compact[index : index + size]
            if any("\u4e00" <= char <= "\u9fff" for char in token):
                yield token

    for word in re.findall(r"[A-Za-z0-9_]+", text.lower()):
        yield word
