import asyncio
from pathlib import Path

from app.kb.ingest import ingest_herbs
from app.kb.retriever import search


def test_search_jiebiao_fenghan_hits_mahuang_or_guizhi(tmp_path: Path) -> None:
    asyncio.run(_assert_search_jiebiao_fenghan_hits_mahuang_or_guizhi(tmp_path))


async def _assert_search_jiebiao_fenghan_hits_mahuang_or_guizhi(tmp_path: Path) -> None:
    await ingest_herbs(persist_dir=tmp_path / ".chroma")

    hits = await search("发汗解表 风寒", top_k=3)

    # 教材【功效】里**字面含「发汗解表」**的只有三味：麻黄、香薷、葱白。
    # 桂枝是「发汗解肌」，不在这三个里——原来这条测试允许桂枝，是因为
    # 当时药材只有 76 味，检索池小，靠同义词也能撞上。
    #
    # 抽全 442 味之后，query 里的「发汗」「风寒」会同时命中一批发散风寒药
    # （淡豆豉、生姜、紫苏叶…），它们并列同分，谁进前三由确定性兜底规则定，
    # **没有医学含义**。所以断言「前三里至少有一味是教材明写发汗解表的」，
    # 而不是点名某味药。
    assert {"麻黄", "香薷", "葱白"} & {hit.name for hit in hits}
