"""逐页排版（scene_art）：模型自己写场景，写不出来退回兜底。

四条断言对应四件真事：

1. 好场景会被采用，页码由渲染层盖，不交给模型；
2. 标题没写进场景 → 整页退回兜底（宁可版式普通，也不能丢标题）；
3. 编一个清单外的素材 id → 退回兜底（那张图不存在，画上去就是断图）；
4. 没有 key → 整段跳过，一行都不调。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.config import Settings
from app.ppt import scene_art
from app.ppt.scene import Scene
from app.ppt.scene_fallback import fallback_scene
from app.ppt.schemas import AssetRef, DeckContent, SlideBlock, SlideNode
from tests.helpers import keyless_settings


def _settings() -> Settings:
    return Settings(DEEPSEEK_API_KEY="test-key", LLM_MODE="mock")


def _slide(title: str = "人参的性味归经", asset: str = "") -> SlideNode:
    refs = (
        [AssetRef(slot="figure", asset_id=asset, result="hit", provenance="library", license="CC0")]
        if asset
        else []
    )
    return SlideNode(
        id="s1",
        kind="bullets",
        title=title,
        bullets=["甘、微苦，微温", "归脾、肺、心、肾经"],
        blocks=[
            SlideBlock(type="point", text="甘、微苦，微温"),
            SlideBlock(type="point", text="归脾、肺、心、肾经"),
        ],
        asset_refs=refs,
        scene=fallback_scene(archetype="points", title=title, bullets=["甘、微苦，微温"]),
    )


def _deck(slides: list[SlideNode]) -> DeckContent:
    return DeckContent(slides=slides, sources=[])


def _scene_payload(title: str, *, asset: str = "", extra: int = 3) -> Scene:
    """一份能通过全部检查的场景。"""

    elements: list[dict[str, Any]] = [
        {
            "type": "text",
            "box": [9, 8.4, 60, 9],
            "text": {"content": title, "role": "title", "size": 4.6},
        },
        {"type": "rule", "box": [9, 19, 82, 0], "orient": "h", "weight": 0.1, "fill": "line"},
        {
            "type": "text",
            "box": [9, 22, 40, 20],
            "text": {"content": "甘、微苦，微温；归脾、肺、心、肾经。", "role": "body"},
        },
    ]
    if asset:
        elements.append({"type": "asset", "box": [55, 22, 36, 26], "asset": asset, "fit": "cover"})
    for index in range(extra):
        elements.append({"type": "circle", "box": [6 + index * 2, 30, 1.0, 1.0], "fill": "accent"})
    return Scene.model_validate({"note": "左文右图", "background": {"kind": "motif", "motif": "paper-plain"}, "elements": elements})


def _patch(monkeypatch: pytest.MonkeyPatch, result: Scene | Exception | list[Scene | Exception]) -> list[dict[str, Any]]:
    """打桩成"第 n 次调用返回第 n 个结果，用完就重复最后一个"。"""

    sequence = result if isinstance(result, list) else [result]
    calls: list[dict[str, Any]] = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        item = sequence[min(len(calls) - 1, len(sequence) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(scene_art, "structured_create", fake_create)
    return calls


def test_good_scene_is_adopted(monkeypatch: pytest.MonkeyPatch) -> None:
    deck = _deck([_slide(asset="photo-huangqi-01")])
    calls = _patch(monkeypatch, _scene_payload("人参的性味归经", asset="photo-huangqi-01"))

    stats = asyncio.run(scene_art.paint_scenes(deck, settings=_settings(), deck_title="人参"))

    assert stats["painted"] == 1 and not stats["fallback"]
    scene = deck.slides[0].scene
    assert scene is not None
    assert any(element.type == "asset" for element in scene.elements)
    # 页码不写进场景：页脚由渲染层统一盖，模型排的话十二页会落在十二个地方。
    assert not any(
        element.type == "text" and element.text and "/" in element.text.content for element in scene.elements
    )
    # 一次调用带一页简报，简报里给了素材清单与画布。
    assert len(calls) == 1
    brief = calls[0]["messages"][1]["content"]
    assert "photo-huangqi-01" in brief and "56.25" in brief


def test_missing_title_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    deck = _deck([_slide()])
    before = deck.slides[0].scene
    _patch(monkeypatch, _scene_payload("性味归经"))  # 少了「人参的」

    stats = asyncio.run(scene_art.paint_scenes(deck, settings=_settings(), deck_title="人参"))

    assert stats["painted"] == 0
    assert stats["fallback"][0]["reason"] == "title_missing"
    assert deck.slides[0].scene == before, "退回时不能改动兜底场景"


def test_invented_asset_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    deck = _deck([_slide(asset="photo-huangqi-01")])
    _patch(monkeypatch, _scene_payload("人参的性味归经", asset="photo-renshen-01"))

    stats = asyncio.run(scene_art.paint_scenes(deck, settings=_settings(), deck_title="人参"))

    assert stats["painted"] == 0
    assert stats["fallback"][0]["reason"].startswith("asset_unknown")


def test_invalid_scene_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    deck = _deck([_slide()])
    _patch(monkeypatch, ValueError("model returned prose"))

    stats = asyncio.run(scene_art.paint_scenes(deck, settings=_settings(), deck_title="人参"))

    assert stats["painted"] == 0
    assert stats["fallback"][0]["reason"].startswith("scene_error")


def test_keyless_skips_without_calling_model(monkeypatch: pytest.MonkeyPatch) -> None:
    deck = _deck([_slide()])
    calls = _patch(monkeypatch, _scene_payload("人参的性味归经"))

    stats = asyncio.run(scene_art.paint_scenes(deck, settings=keyless_settings(), deck_title="人参"))

    assert stats == {"skipped": "no_key", "pages": 1}
    assert calls == []


def test_vertical_title_box_is_fitted_to_one_column(monkeypatch: pytest.MonkeyPatch) -> None:
    """竖排标题按横排习惯给的框（宽 20、高 4.6）会被改成"一列"。

    不改的话，四个字会排成一条压在正文上的长列——这是坐标系约定，不是设计判断。
    """

    deck = _deck([_slide("人参的性味归经")])
    raw = _scene_payload("人参的性味归经").model_dump()
    raw["elements"][0]["text"].update({"write": "v", "size": 4.6})
    raw["elements"][0]["box"] = [9, 8.4, 20, 4.6]
    # 竖排标题收成一列后会占住左侧，正文让它一让——这是模型该做的事，这里手工摆好，
    # 好让用例只测"框被改正"这一件事。
    raw["elements"][2]["box"] = [22, 22, 30, 18]
    _patch(monkeypatch, Scene.model_validate(raw))

    stats = asyncio.run(scene_art.paint_scenes(deck, settings=_settings(), deck_title="人参"))

    assert stats["painted"] == 1
    title = deck.slides[0].scene.elements[0]
    assert title.text is not None and title.text.write == "v"
    width, height = title.box[2], title.box[3]
    assert width <= 7.6, "一列竖排的宽应约等于字号 × 1.6"
    assert height >= 7 * 4.6, "七个字要装得下"


def test_text_collision_gets_one_repair_chance(monkeypatch: pytest.MonkeyPatch) -> None:
    """文字压字会被回灌一次，模型改对了就采用。

    竖排标题一路写下来撞上正文，是模型排版最常见的硬伤；压字比版式普通难看得多，
    所以它算"问题"，不算"风格"。
    """

    deck = _deck([_slide("人参的性味归经")])
    bad = _scene_payload("人参的性味归经").model_dump()
    bad["elements"][2]["box"] = [9, 12, 30, 6]  # 正文压在标题上
    calls = _patch(monkeypatch, [Scene.model_validate(bad), _scene_payload("人参的性味归经")])

    stats = asyncio.run(scene_art.paint_scenes(deck, settings=_settings(), deck_title="人参"))

    assert stats["painted"] == 1 and not stats["fallback"]
    assert len(calls) == 2, "第一次不合格，应回灌一次"
    assert "重叠" in calls[1]["messages"][-1]["content"]


def test_collision_twice_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    deck = _deck([_slide("人参的性味归经")])
    bad = _scene_payload("人参的性味归经").model_dump()
    bad["elements"][2]["box"] = [9, 12, 30, 6]
    calls = _patch(monkeypatch, Scene.model_validate(bad))

    stats = asyncio.run(scene_art.paint_scenes(deck, settings=_settings(), deck_title="人参"))

    assert stats["painted"] == 0
    assert stats["fallback"][0]["reason"] == "text_collision"
    assert len(calls) == 2, "回灌只给一次，不替模型做设计"


def test_title_split_into_two_texts_still_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    """竖排标题常拆两段，拼起来能对上就算写进来了。"""

    deck = _deck([_slide("人参的性味归经")])
    raw = _scene_payload("人参").model_dump()
    raw["elements"][0]["text"]["content"] = "人参"
    raw["elements"][2]["box"] = [9, 30, 40, 12]  # 正文下移，给第二段标题让位
    raw["elements"].append(
        {
            "type": "text",
            "box": [9, 18.5, 40, 6],
            "text": {"content": "的性味归经", "role": "title", "size": 4.6},
        }
    )
    _patch(monkeypatch, Scene.model_validate(raw))

    stats = asyncio.run(scene_art.paint_scenes(deck, settings=_settings(), deck_title="人参"))

    assert stats["painted"] == 1
