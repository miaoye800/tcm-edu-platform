import asyncio
from pathlib import Path

from app.kb.ingest import ingest_herbs
from app.kb.retriever import search


def test_search_jiebiao_fenghan_hits_mahuang_or_guizhi(tmp_path: Path) -> None:
    asyncio.run(_assert_search_jiebiao_fenghan_hits_mahuang_or_guizhi(tmp_path))


async def _assert_search_jiebiao_fenghan_hits_mahuang_or_guizhi(tmp_path: Path) -> None:
    await ingest_herbs(persist_dir=tmp_path / ".chroma")

    hits = await search("发汗解表 风寒", top_k=3)

    assert {"麻黄", "桂枝"} & {hit.name for hit in hits}
