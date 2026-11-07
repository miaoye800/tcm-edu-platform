"""幻灯智能体：草案归一化、版式判定、无 key 兜底与模型路径的契约测试。"""

import asyncio

from app.agents.resource_context import retrieve_resource_docs
from app.ppt import agent as agent_module
from app.ppt.agent import PptAgent
from app.ppt.draft import DeckDraft, DraftColumn, DraftSlide, OutlineDraft, OutlineDraftSlide, normalize_deck
from app.ppt.params import PptParams
from tests.helpers import keyless_settings

SOURCES = [
    {"name": "麻黄", "type": "教材", "ref": "p.1", "quote": "发汗解表"},
    {"name": "桂枝", "type": "教材", "ref": "p.2", "quote": "解肌发表"},
]


def _draft() -> DeckDraft:
    return DeckDraft(
        deck_title="麻黄讲稿",
        slides=[
            DraftSlide(title="功效", bullets=["发汗解表", "宣肺平喘"], source_indexes=[0]),
            DraftSlide(title="鉴别", columns=[DraftColumn(heading="麻黄", items=["发汗力强"]), DraftColumn(heading="桂枝", items=["发汗力缓"])], source_indexes=[1]),
        ],
    )


def test_normalize_prepends_cover_when_missing():
    deck = normalize_deck(_draft(), topic="麻黄", sources=SOURCES)

    assert deck.slides[0].kind == "cover"
    assert deck.slides[0].title.startswith("麻黄")
    assert [slide.id for slide in deck.slides] == ["s0", "s1", "s2"]


def test_normalize_strips_layout_labels_from_titles():
    """模型会把版式词写进标题：「封面：麻黄——发汗解表第一药」上屏就是两个字的噪音。"""

    draft = DeckDraft(
        deck_title="封面：麻黄——发汗解表第一药",
        slides=[
            DraftSlide(title="封面：麻黄——发汗解表第一药"),
            DraftSlide(title="导览：从性味到禁忌", bullets=["性味归经", "核心功效", "用量禁忌"]),
            DraftSlide(title="封面：", bullets=["不该被剥光", "留在正文"]),
        ],
    )
    deck = normalize_deck(draft, topic="麻黄", sources=SOURCES)

    assert deck.slides[0].title == "麻黄——发汗解表第一药"
    assert deck.slides[1].title == "从性味到禁忌"
    # 「封面：」后面没有内容时不能剥，否则这一页连标题都没了（尾冒号由 clean_text 去掉）。
    assert deck.slides[2].title == "封面"


def test_normalize_resolves_kind_by_structure_not_model_claim():
    """模型自报 kind 不作数：两行字的页被标成 compare 时不能真画两个空栏。"""

    draft = DeckDraft(
        deck_title="x",
        slides=[
            DraftSlide(kind="cover", title="封面"),
            DraftSlide(kind="compare", title="只有一句话", bullets=["发汗解表"]),
            DraftSlide(kind="bullets", title="收束", bullets=["记住它"]),
        ],
    )
    deck = normalize_deck(draft, topic="麻黄", sources=SOURCES)

    kinds = [slide.kind for slide in deck.slides]
    assert kinds == ["cover", "bullets", "summary"]


def test_normalize_truncates_long_bullets_and_caps_columns():
    long_bullet = "本品具有发汗解表、宣肺平喘、利水消肿的功效，为发汗解表之要药，主治风寒表实证"
    draft = DeckDraft(
        deck_title="x",
        slides=[
            DraftSlide(title="封面"),
            DraftSlide(
                title="要点",
                bullets=[long_bullet, long_bullet, "短句"],
                columns=[
                    DraftColumn(heading="甲", items=[f"条目{index}" for index in range(9)]),
                    DraftColumn(heading="乙", items=["一"]),
                    DraftColumn(heading="丙", items=["二"]),
                ],
            ),
        ],
    )
    deck = normalize_deck(draft, topic="麻黄", sources=SOURCES)

    slide = deck.slides[1]
    assert len(slide.bullets) == 2  # 重复项被去掉
    assert all(len(bullet) <= 40 for bullet in slide.bullets)
    assert len(slide.columns) == 2
    assert len(slide.columns[0].items) == 5


def test_normalize_drops_out_of_range_source_indexes():
    deck = normalize_deck(
        DeckDraft(deck_title="x", slides=[DraftSlide(title="封面", source_indexes=[0, 0, 9, -1, 1])]),
        topic="麻黄",
        sources=SOURCES,
    )
    assert deck.slides[0].source_indexes == [0, 1]


def test_normalize_respects_page_count():
    draft = DeckDraft(deck_title="x", slides=[DraftSlide(title=f"第{index}页", bullets=["要点"]) for index in range(20)])
    deck = normalize_deck(draft, topic="麻黄", sources=SOURCES, params={"page_count": 8})
    assert len(deck.slides) == 8


def test_normalize_never_returns_empty_deck():
    deck = normalize_deck(DeckDraft(deck_title="", slides=[]), topic="", sources=[])
    assert len(deck.slides) == 1
    assert deck.slides[0].kind == "cover"
    assert deck.slides[0].title


def test_keyless_agent_builds_teachable_deck():
    """无 key 时的产物必须能直接上讲台：封面/使用注意/总结都在，要点不超长。"""

    async def run():
        docs = await retrieve_resource_docs("麻黄")
        return await PptAgent(settings=keyless_settings()).generate("麻黄", docs, PptParams(page_count=12))

    deck = asyncio.run(run())
    kinds = [slide.kind for slide in deck.slides]

    assert deck.slides[0].kind == "cover"
    assert kinds[-1] == "summary"
    assert 8 <= len(deck.slides) <= 12
    assert any(slide.title == "使用注意" for slide in deck.slides)
    assert all(len(bullet) <= 40 for slide in deck.slides for bullet in slide.bullets)
    assert all(slide.title for slide in deck.slides)


def test_keyless_agent_honours_page_count_without_dropping_safety_pages():
    async def run(pages: int):
        docs = await retrieve_resource_docs("麻黄")
        return await PptAgent(settings=keyless_settings()).generate("麻黄", docs, PptParams(page_count=pages))

    short = asyncio.run(run(8))
    assert len(short.slides) == 8
    titles = [slide.title for slide in short.slides]
    assert titles[0].startswith("麻黄")
    assert "使用注意" in titles
    # 章号必须连续：砍掉「一、」却留下「二、」会让人以为缺页
    sections = [title for title in titles if title[:2] in ("一、", "二、", "三、")]
    assert sections == [] or sections[0].startswith("一、")


def test_agent_uses_structured_deck_when_model_available(monkeypatch):
    async def fake_create(**kwargs):
        if kwargs.get("response_model") is OutlineDraft:
            return OutlineDraft(deck_title="麻黄讲稿", slides=[OutlineDraftSlide(title="功效"), OutlineDraftSlide(title="鉴别")])
        return _draft()

    monkeypatch.setattr(agent_module, "structured_create", fake_create)

    async def run():
        docs = await retrieve_resource_docs("麻黄")
        return await PptAgent(settings=keyless_settings()).generate("麻黄", docs)

    deck = asyncio.run(run())
    assert [slide.title for slide in deck.slides] == ["麻黄讲稿", "功效", "鉴别"]
    assert deck.slides[2].kind == "compare"


def test_agent_falls_back_when_model_raises(monkeypatch):
    async def boom(**_kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(agent_module, "structured_create", boom)

    async def run():
        docs = await retrieve_resource_docs("麻黄")
        return await PptAgent(settings=keyless_settings()).generate("麻黄", docs)

    deck = asyncio.run(run())
    assert len(deck.slides) >= 6


def test_agent_falls_back_when_model_returns_too_few_slides(monkeypatch):
    async def tiny(**kwargs):
        if kwargs.get("response_model") is OutlineDraft:
            return OutlineDraft(deck_title="x", slides=[OutlineDraftSlide(title="只有一页", points=["要点"])])
        return DeckDraft(deck_title="x", slides=[DraftSlide(title="只有一页", bullets=["要点"])])

    monkeypatch.setattr(agent_module, "structured_create", tiny)

    async def run():
        docs = await retrieve_resource_docs("麻黄")
        return await PptAgent(settings=keyless_settings()).generate("麻黄", docs)

    deck = asyncio.run(run())
    assert len(deck.slides) >= 6


def test_compose_keeps_confirmed_outline_titles():
    """用户在大纲里改过的标题必须原样出现在成品里。"""

    async def run():
        docs = await retrieve_resource_docs("麻黄")
        agent = PptAgent(settings=keyless_settings())
        outline = await agent.outline("麻黄", docs)
        edited = outline[:2] + [type(outline[0])(id="s9", title="我自己加的补充页", points=["补充一点"], source_indexes=[])]
        return await agent.compose("麻黄", docs, outline=edited)

    deck = asyncio.run(run())
    assert any(slide.title == "我自己加的补充页" for slide in deck.slides)


def test_compose_keeps_renamed_cover_as_the_cover():
    """封面标题被整个改写时，它仍然是封面，而不是被降级成第二页。"""

    async def run():
        docs = await retrieve_resource_docs("桂枝")
        agent = PptAgent(settings=keyless_settings())
        outline = await agent.outline("桂枝", docs)
        outline[0] = type(outline[0])(id="s0", title="桂枝汤 · 方义总纲", points=["辛、甘，温"], source_indexes=[0])
        return await agent.compose("桂枝", docs, outline=outline)

    deck = asyncio.run(run())
    assert deck.slides[0].kind == "cover"
    assert deck.slides[0].title == "桂枝汤 · 方义总纲"
    # 大纲给封面写的要点不能压到封面上去。
    assert deck.slides[0].bullets == []
    assert deck.slides[0].subtitle, "封面副标题仍取模板里的性味归经"
    assert len(deck.slides) == 12


def test_polish_only_counts():
    async def run():
        docs = await retrieve_resource_docs("麻黄")
        agent = PptAgent(settings=keyless_settings())
        deck = await agent.generate("麻黄", docs)
        return agent.polish(deck), deck

    stats, deck = asyncio.run(run())
    assert stats["slides"] == len(deck.slides)
    assert stats["bullets"] == sum(len(slide.bullets) for slide in deck.slides)
