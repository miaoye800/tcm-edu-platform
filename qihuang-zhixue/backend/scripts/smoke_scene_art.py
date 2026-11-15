"""一次性冒烟：让真模型排三页，看它写出来的场景能不能过校验。

跑法：``python -m scripts.smoke_scene_art``（在 backend 目录下）。
产物落 ``.tmp/scene-art.json``，可以拿去看模型到底怎么排的。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.config import get_settings
from app.ppt import scene_art
from app.ppt.scene_fallback import fallback_scene
from app.ppt.schemas import AssetRef, DeckContent, SlideBlock, SlideNode


def _slide(index: int, title: str, bullets: list[str], *, purpose: str, asset: str = "") -> SlideNode:
    refs = (
        [AssetRef(slot="figure", asset_id=asset, result="hit", provenance="library", license="CC0", note="黄芪饮片")]
        if asset
        else []
    )
    blocks = [SlideBlock(type="point", text=item) for item in bullets]
    return SlideNode(
        id=f"s{index}",
        kind="cover" if purpose == "cover" else "bullets",
        title=title,
        subtitle="补气第一药" if purpose == "cover" else "",
        bullets=bullets,
        blocks=blocks,
        purpose=purpose,
        asset_refs=refs,
        scene=fallback_scene(
            archetype=purpose,
            title=title,
            blocks=[block.model_dump() for block in blocks],
            bullets=bullets,
            figure_id=asset,
            figure_kind="asset",
        ),
    )


async def main() -> None:
    deck = DeckContent(
        slides=[
            _slide(1, "人参：补气第一药", ["甘、微苦，微温", "归脾、肺、心、肾经"], purpose="cover", asset="photo-huangqi-01"),
            _slide(2, "性味归经", ["甘、微苦，微温", "归脾、肺、心、肾经", "大补元气，复脉固脱"], purpose="points"),
            _slide(3, "用量与禁忌", ["煎服 3–9 g，另煎兑服", "不宜与藜芦、五灵脂同用"], purpose="warning"),
        ]
    )
    stats = await scene_art.paint_scenes(deck, settings=get_settings(), deck_title="人参")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    out = Path(".tmp/scene-art.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    pages = [slide.model_dump(mode="json") for slide in deck.slides]
    out.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
    # 同时落一份给 `/_lab/ppt` 用：评审时看的是真模型排的三页，不是手写的样张。
    lab = Path(__file__).resolve().parents[2] / "frontend" / "public" / "lab" / "scene-samples.json"
    lab.parent.mkdir(parents=True, exist_ok=True)
    lab.write_text(
        json.dumps(
            [{"title": page["title"], "scene": page["scene"]} for page in pages], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    print("written", out, "and", lab)


if __name__ == "__main__":
    asyncio.run(main())
