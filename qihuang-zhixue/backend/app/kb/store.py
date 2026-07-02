"""知识库向量存储适配层。"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Protocol


COLLECTION_NAME = "herbs"


class VectorCollection(Protocol):
    def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
        documents: list[str],
    ) -> None:
        """写入或更新向量。"""

    def query(
        self,
        query_embeddings: list[list[float]],
        n_results: int,
        where: dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        """查询相似向量。"""


def backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def chroma_path() -> Path:
    return backend_root() / ".chroma"


def get_collection(persist_dir: Path | None = None) -> VectorCollection:
    """优先使用 Chroma，缺依赖时降级为同目录 JSON 向量库。"""

    path = persist_dir or chroma_path()
    path.mkdir(parents=True, exist_ok=True)
    try:
        import chromadb
    except ImportError:
        return JsonVectorCollection(path / f"{COLLECTION_NAME}.json")

    try:
        client = chromadb.PersistentClient(path=str(path))
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    except Exception:
        # Chroma 本地库可能因比赛现场拷贝、权限或 SQLite 锁变为不可写，失败时降级到 JSON。
        return JsonVectorCollection(path / f"{COLLECTION_NAME}.json")


class JsonVectorCollection:
    """用于离线兜底的极简持久化向量库。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
        documents: list[str],
    ) -> None:
        rows = self._load()
        by_id = {str(row["id"]): row for row in rows}
        for item_id, embedding, metadata, document in zip(ids, embeddings, metadatas, documents, strict=True):
            by_id[item_id] = {
                "id": item_id,
                "embedding": embedding,
                "metadata": metadata,
                "document": document,
            }
        self.path.write_text(
            json.dumps(list(by_id.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def query(
        self,
        query_embeddings: list[list[float]],
        n_results: int,
        where: dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        query_embedding = query_embeddings[0]
        rows = [
            row
            for row in self._load()
            if where is None or all(row["metadata"].get(key) == value for key, value in where.items())
        ]
        ranked = sorted(
            rows,
            key=lambda row: _cosine_distance(query_embedding, row["embedding"]),
        )[:n_results]
        return {
            "ids": [[row["id"] for row in ranked]],
            "metadatas": [[row["metadata"] for row in ranked]],
            "documents": [[row["document"] for row in ranked]],
            "distances": [[_cosine_distance(query_embedding, row["embedding"]) for row in ranked]],
        }

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [row for row in data if isinstance(row, dict)]


def _cosine_distance(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 1.0
    return 1.0 - dot / (left_norm * right_norm)
