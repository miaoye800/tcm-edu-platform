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


def _load_index(name: str) -> dict[str, dict[str, Any]]:
    """读取 data/<name>/_index.json 的主数据条目（按 id 索引）。

    注意：这些条目**尚未**走 ``Formula`` / ``Syndrome`` 的严格校验，因为
    P1 阶段它们由 ``combinations`` / ``indications`` 反推而来，缺 ``sources``，
    且 ``incomplete=true`` / ``verified=false``。等 P4 教材抽取补齐来源后再收紧。
    """

    path = backend_root() / "data" / name / "_index.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get(name) or {}
    return {str(key): dict(value) for key, value in items.items() if isinstance(value, dict)}


def load_formulas() -> dict[str, dict[str, Any]]:
    """方剂主数据（id → 条目）。缺文件时返回空字典，调用方须容忍。"""

    return _load_index("formulas")


def _load_textbook_syndromes() -> dict[str, dict[str, Any]]:
    """读 ``data/syndromes/_textbook.json`` 里的证候实体（名称 → 条目）。"""

    path = backend_root() / "data" / "syndromes" / "_textbook.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("syndromes") or {}
    return {str(key): dict(value) for key, value in items.items() if isinstance(value, dict)}


def load_syndromes() -> dict[str, dict[str, Any]]:
    """证候主数据（名称 → 条目）。

    两个来源合并：

    - ``_index.json``：早期从 76 味人工 monograph 反推的 224 条。
      **优先级最高**——它有 related_herbs / related_formulas，且被现有
      药证链引用着，不能被覆盖。
    - ``_textbook.json``：P4 从教材 441 个条目抽出的 1027 个证名。
      教材原文，用来接住 86 味新补药材的主治。

    同名时以 ``_index.json`` 为准：教材证名和人工证名完全一样，说明
    本来就是一个证，没必要建两条。
    """

    merged = dict(_load_index("syndromes"))
    for name, item in _load_textbook_syndromes().items():
        merged.setdefault(name, item)
    return merged


def load_syndrome_aliases() -> dict[str, str]:
    """教材证名的别名表（别名 → 正名）。

    来源 ``data/syndromes/_textbook.json`` 的 ``aliases``。用于让
    「头痛眩晕」这类从并列证名里拆出来的单项也能搜到主证候。
    """

    path = backend_root() / "data" / "syndromes" / "_textbook.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in (payload.get("aliases") or {}).items()}


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
