"""校验之后的单页重写（`revise.py`）。

要验的是「只改该改的那一页」这一条，所以每条用例都盯着**别的页有没有被动过**。
另外三条是护栏：标题不许改、改坏了要退回原文、一次最多三页。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.config import Settings
from app.resources import service
from app.ppt import revise
from app.ppt.revise import PagePatch, fixable, revise_pages
from app.ppt.schemas import DeckContent, SlideBlock, SlideNode
from tests.helpers import keyless_settings


def _settings() -> Settings:
    return Settings(DEEPSEEK_API_KEY="test-key", LLM_MODE="mock")


def _points_page(title: str, count: int = 6) -> SlideNode:
    return SlideNode(
        id=f"p-{title}",
        kind="bullets",
        title=title,
        purpose="points",
        blocks=[SlideBlock(type="point", text=f"第{i}条要点") for i in range(count)],
        source_indexes=[0],
    )


def _content(count: int = 3, blocks: int = 6) -> dict[str, Any]:
    slides = [SlideNode(id="p0", kind="cover", title="人参", purpose="cover", blocks=[SlideBlock(type="point", text="补气第一药")])]
    slides.extend(_points_page(f"第{i}页", blocks) for i in range(1, count))
    deck = DeckContent(slides=slides, sources=[{"name": "中药学", "type": "textbook", "ref": "教材 §3"}])
    return deck.model_dump(mode="json")


def _design(count: int = 3) -> dict[str, Any]:
    pages = [{"unit_id": "u0", "archetype": "cover", "density": "airy", "slots": []}]
    pages.extend({"unit_id": f"u{i}", "archetype": "points", "density": "normal", "slots": []} for i in range(1, count))
    return {"theme_id": "paper", "pages": pages}


def _issues(*indexes: int, text: str = "内容块超限（6/5）") -> list[dict[str, Any]]:
    return [{"index": index, "title": f"第{index}页", "issues": [text], "planned": True, "source": "model"} for index in indexes]


def _patch(monkeypatch: pytest.MonkeyPatch, patch: PagePatch | list[PagePatch]) -> tuple[list[dict[str, Any]], list[list[int]]]:
    """把模型换成一个剧本。传列表时按调用顺序取，用完之后一直用最后一个。

    返回 ``(模型收到的请求, 每次重排要求排哪几页)``。
    """

    calls: list[dict[str, Any]] = []
    script = patch if isinstance(patch, list) else [patch]

    async def fake_create(**kwargs):
        calls.append(kwargs)
        return script[min(len(calls) - 1, len(script) - 1)]

    monkeypatch.setattr(revise, "structured_create", fake_create)
    painted: list[list[int]] = []

    async def fake_paint(deck, *, settings, design=None, task_type="generate", only=None):
        painted.append(list(only or []))
        return {"painted": len(only or []), "pages": len(deck.slides)}

    monkeypatch.setattr(revise, "paint_scenes", fake_paint)
    return calls, painted


def test_fixable_takes_page_level_problems_only() -> None:
    """素材与方案类问题不归单页重写管：改正文改不了它们。"""

    kept = fixable(["内容块超限（6/5）", "引用越界", "页面没有内容", "资产缺署名", "不在确认方案内"])
    assert kept == ["内容块超限（6/5）", "引用越界", "页面没有内容"]


def test_over_capacity_page_is_rewritten_and_only_that_page_is_repainted(monkeypatch: pytest.MonkeyPatch) -> None:
    content = _content()
    before = [dict(page) for page in content["slides"]]
    _calls, painted = _patch(
        monkeypatch,
        PagePatch(blocks=[SlideBlock(type="point", text=f"精简{i}").model_dump() for i in range(4)], source_indexes=[0]),
    )

    result = asyncio.run(
        revise_pages(content, _issues(1), settings=_settings(), design=_design(), task_type="generate")
    )

    assert [item["page"] for item in result["revised"]] == [2]
    assert len(content["slides"][1]["blocks"]) == 4
    # 封面与末页一个字都不该动——这是「只重写有问题的那一页」的全部意义。
    assert content["slides"][0] == before[0]
    assert content["slides"][2] == before[2]
    # 只重排第 2 页，且旧版面先作废。
    assert painted == [[1]]


def test_title_survives_the_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    content = _content()
    _patch(monkeypatch, PagePatch(blocks=[SlideBlock(type="point", text="短一点").model_dump()]))

    asyncio.run(revise_pages(content, _issues(1), settings=_settings(), design=_design()))

    assert content["slides"][1]["title"] == "第1页"


def test_still_broken_after_one_retry_keeps_the_original(monkeypatch: pytest.MonkeyPatch) -> None:
    """两次都装不下就放手：继续改只会把这一页越改越薄。"""

    content = _content()
    before = [dict(page) for page in content["slides"]]
    # 六条仍然超过 points 的五块上限：两轮都修不好。
    _patch(monkeypatch, PagePatch(blocks=[SlideBlock(type="point", text=f"还是六条{i}").model_dump() for i in range(6)]))

    result = asyncio.run(revise_pages(content, _issues(1), settings=_settings(), design=_design()))

    assert result["revised"] == []
    assert result["skipped"][0]["page"] == 2
    assert "still_broken" in result["skipped"][0]["reason"]
    assert content["slides"][1]["blocks"] == before[1]["blocks"]


def test_at_most_three_pages_per_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    content = _content(count=5)
    _patch(monkeypatch, PagePatch(blocks=[SlideBlock(type="point", text="精简").model_dump()]))

    result = asyncio.run(
        revise_pages(content, _issues(1, 2, 3, 4), settings=_settings(), design=_design(count=5))
    )

    assert len(result["revised"]) == 3
    assert result["skipped"] == [{"page": 5, "reason": "over_budget", "issues": ["内容块超限（6/5）"]}]


def test_keyless_does_not_call_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    content = _content()
    calls, painted = _patch(monkeypatch, PagePatch(blocks=[SlideBlock(type="point", text="精简").model_dump()]))

    result = asyncio.run(revise_pages(content, _issues(1), settings=keyless_settings()))

    assert result["revised"] == []
    assert result["skipped"] == [{"reason": "no_key"}]
    assert calls == [] and painted == []


def test_service_does_not_touch_pages_with_asset_problems(monkeypatch: pytest.MonkeyPatch) -> None:
    """资产问题不归单页重写管：那是选材步定下的，改正文改不了许可与署名。"""

    calls: list[Any] = []

    async def fake(*args: Any, **kwargs: Any):
        calls.append(args)
        return {"revised": [{"page": 2}], "skipped": []}

    monkeypatch.setattr("app.ppt.revise.revise_pages", fake)
    pages = [{"index": 1, "title": "x", "issues": ["资产缺署名", "生成图未标注"]}]

    result = asyncio.run(service._revise_deck_pages({"content": {"slides": []}}, {"pages": pages}, None))

    assert result == {"revised": [], "skipped": []}
    assert calls == []


def test_service_delegates_pages_with_capacity_problems(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[dict[str, Any]]] = []

    async def fake(content, pages, *, settings, design=None, task_type="generate"):
        seen.append(pages)
        return {"revised": [{"page": 2, "issues": ["内容块超限（6/5）"]}], "skipped": []}

    monkeypatch.setattr("app.ppt.revise.revise_pages", fake)
    pages = [
        {"index": 0, "title": "封面", "issues": []},
        {"index": 1, "title": "x", "issues": ["内容块超限（6/5）"]},
    ]

    result = asyncio.run(service._revise_deck_pages({"content": {"slides": []}}, {"pages": pages}, None))

    assert [page["index"] for page in seen[0]] == [0, 1]
    assert result["revised"]
