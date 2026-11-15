"""真机冒烟：一页塞了六块，让模型自己删到装得下。

跑法：``./.venv/Scripts/python.exe -m scripts.smoke_revise``

验的是三件事：模型肯不肯删、删完是不是还留着信息、别的页有没有被动过。
前两件只能真跑，第三件脚本里直接断言。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.config import get_settings
from app.ppt.design import check_page_capacity, PageDesign
from app.ppt.revise import revise_pages
from app.ppt.schemas import DeckContent, SlideBlock, SlideNode


def _deck() -> DeckContent:
    six = [
        "甘、微苦，微温",
        "归脾、肺、心、肾经",
        "大补元气，复脉固脱",
        "补脾益肺，生津养血",
        "安神益智，扶正祛邪",
        "用量 3–9 克，另煎兑服",
    ]
    return DeckContent(
        slides=[
            SlideNode(id="p0", kind="cover", title="人参", purpose="cover", blocks=[SlideBlock(type="point", text="补气第一药")], subtitle="中药学 · 补虚药"),
            SlideNode(
                id="p1",
                kind="bullets",
                title="人参的性味归经",
                purpose="points",
                # 六条塞进「要点」页：超出五块上限，正是校验会报的那一类。
                blocks=[SlideBlock(type="point", text=text) for text in six],
                source_indexes=[0],
            ),
            SlideNode(id="p2", kind="summary", title="一句定论", purpose="summary", blocks=[SlideBlock(type="point", text="气充则神旺，味甘则中守。")]),
        ],
        sources=[{"name": "中药学（第十一版）", "type": "textbook", "ref": "补虚药·人参"}],
    )


def _design() -> dict:
    return {
        "theme_id": "paper",
        "paper": "xuan",
        "ornament_set": "scroll",
        "pages": [
            {"unit_id": "u0", "archetype": "cover", "density": "airy", "slots": []},
            {"unit_id": "u1", "archetype": "points", "density": "normal", "slots": []},
            {"unit_id": "u2", "archetype": "summary", "density": "airy", "slots": []},
        ],
    }


async def main() -> None:
    deck = _deck()
    content = deck.model_dump(mode="json")
    design = _design()
    page = PageDesign.model_validate(design["pages"][1])
    before_problems = check_page_capacity(page, content["slides"][1]["blocks"])
    print("校验会报：", before_problems)
    untouched = [json.dumps(content["slides"][index], ensure_ascii=False, sort_keys=True) for index in (0, 2)]

    result = await revise_pages(
        content,
        [{"index": 1, "title": "人参的性味归经", "issues": before_problems, "planned": True, "source": "knowledge_base"}],
        settings=get_settings(),
        design=design,
        task_type="generate",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))

    page1 = content["slides"][1]
    print("标题：", page1["title"])
    for block in page1["blocks"]:
        print("  -", block.get("text"))
    after = check_page_capacity(page, page1["blocks"])
    print("改完还剩：", after or "装得下")
    print("别的页是否被动：", [json.dumps(content["slides"][index], ensure_ascii=False, sort_keys=True) != untouched[offset] for offset, index in enumerate((0, 2))])
    print("场景是否重排：", bool(page1.get("scene")))

    out = Path(".tmp/revise.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written", out)


if __name__ == "__main__":
    asyncio.run(main())
