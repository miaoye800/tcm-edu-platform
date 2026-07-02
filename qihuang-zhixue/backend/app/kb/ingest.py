"""药材知识库入库命令。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.kb.embedding import EmbeddingClient
from app.kb.models import Herb
from app.kb.store import backend_root, chroma_path, get_collection


def load_herbs(data_dir: Path | None = None) -> list[Herb]:
    """读取 data/herbs 下的全部药材。"""

    herbs_dir = data_dir or backend_root() / "data" / "herbs"
    herbs: list[Herb] = []
    for path in sorted(herbs_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else [raw]
        herbs.extend(Herb.model_validate(item) for item in items if isinstance(item, dict))
    return herbs


async def ingest_herbs(data_dir: Path | None = None, persist_dir: Path | None = None) -> int:
    """将药材 embedding_text 写入向量库。"""

    herbs = load_herbs(data_dir)
    client = EmbeddingClient()
    documents = [herb.embedding_text or herb.name for herb in herbs]
    embeddings = await client.embed_many(documents)
    collection = get_collection(persist_dir)

    collection.upsert(
        ids=[herb.id for herb in herbs],
        embeddings=embeddings,
        metadatas=[_metadata_for_herb(herb) for herb in herbs],
        documents=documents,
    )
    return len(herbs)


def _metadata_for_herb(herb: Herb) -> dict[str, Any]:
    sources = [source.model_dump(mode="json") for source in herb.sources]
    return {
        "id": herb.id,
        "name": herb.name,
        "category": str(herb.category),
        "subcategory": herb.subcategory,
        "verified": herb.verified,
        "sources": json.dumps(sources, ensure_ascii=False),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="将 data/herbs 药材数据入库到 Chroma")
    parser.add_argument("--data-dir", type=Path, default=None, help="药材 JSON 目录，默认 data/herbs")
    args = parser.parse_args()

    count = await ingest_herbs(data_dir=args.data_dir)
    print(f"已入库 {count} 条药材，持久化目录：{chroma_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
