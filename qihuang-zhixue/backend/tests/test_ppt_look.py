"""视觉方案（look）：模型读用户的话定风格，设计单照它出槽位，选材照它取素材。

这一组针对的是「每个 PPT 都长一样」的病根：

- 「长什么样」以前由主题名决定（paper → 宣纸 + 卷草角花 + 宋体），模型没有说话的地方；
- 角饰由稳定种子抽，方案里选了什么都不作数，「不要角花」无法表达；
- 排版提示词把「四角角花 + 左文右图」写成五种固定手法，每页照抄；
- 封面/章节/正文全都要图，图位永远存在，搜不到就整页空白。
"""

from __future__ import annotations

import asyncio
import json

from app.ppt.design import propose_design
from app.ppt.params import PptParams
from app.ppt.scene_art import _page_brief
from app.ppt.select import resolve_slots
from app.ppt.spec import (
    DeckSpec,
    compose_deck_spec,
    deck_params_from_plan,
)
from app.ppt.select import Asset


def _asset(
    asset_id: str,
    *,
    subjects: list[str],
    kind: str = "photo",
    slot_class: str = "evidence",
    tags: list[str] | None = None,
) -> Asset:
    return Asset(
        id=asset_id,
        kind=kind,
        slot_class=slot_class,
        subjects=subjects,
        tags=tags or [],
        file=f"assets/pictorial/{asset_id}.webp",
        w=1600,
        h=1200,
        bytes=300_000,
        sha256=asset_id,
        license="CC0",
        source_url="https://example.org/x",
    )


def _reply(look: dict | None = None, pages: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "title": "从一味药开始，认识中药学",
            "genre": "orientation",
            "audience": "中药学专业新生",
            "occasion": "新生入学第一周的入门试讲",
            "goal": "让新生对中药学有整体认识，并愿意按一条路径开始学",
            "thesis": "先学会看一味药，比背完药名更重要。",
            "arc": ["先勾住人", "说清是什么", "给一个抓手", "落到他自己"],
            "target_pages": 5,
            "duration_min": 15,
            "look": look
            or {
                "mood": "静气、古籍感，留白多，不要卡通插画",
                "theme": "cinnabar",
                "palette": {"bg": "#F7EFE6", "fg": "#2A1815", "accent": "#A83B2E"},
                "background": {
                    "kind": "asset",
                    "subject": "中药房内景",
                    "keywords": ["中药房", "药柜"],
                    "veil": 0.45,
                },
                "ornament": {"motif": "none"},
                "type_pair": "serif-kai",
                "image_treatment": "plate",
                "rhythm": "封面满版图压字；正文左文右图；章节居中",
            },
            "pages": pages
            or [
                {"heading": "从一味药开始", "role": "cover", "goal": "定调", "must_cover": ["具体药物"], "material": "药柜一格", "keywords": ["药柜"], "composition": "full-bleed"},
                {"heading": "今天走四步", "role": "agenda", "goal": "给路线", "must_cover": ["路线"], "composition": "text-only"},
                {"heading": "四把钥匙", "role": "process", "goal": "给顺序", "must_cover": ["性味"], "composition": "text-only"},
                {"heading": "寒热温凉", "role": "compare", "goal": "对照", "must_cover": ["四气"], "material": "黄芪饮片实拍", "keywords": ["黄芪", "饮片"], "composition": "text-image-left"},
                {"heading": "只记住一句", "role": "summary", "goal": "收束", "must_cover": ["看一味药"], "composition": "centered"},
            ],
            "interpretation": ["你想让新生先对这门学问产生兴趣。"],
        },
        ensure_ascii=False,
    )


def _llm(payload: str):
    async def call(_messages):
        return payload

    return call


def test_look_is_read_from_the_request_not_from_the_theme() -> None:
    spec = asyncio.run(compose_deck_spec("给新生讲中药学入门，要中式、静一点", llm_call=_llm(_reply())))

    assert spec.look.theme == "cinnabar"
    assert spec.look.ornament.motif == "none"
    assert spec.look.background.kind == "asset"
    assert spec.look.background.subject == "中药房内景"
    assert spec.look.background.veil >= 0.3
    assert spec.look.palette is not None and spec.look.palette.usable
    # 旧 id serif-kai 走 fontpairs 的别名映射落到 kai-song。
    assert spec.look.type_pair == "kai-song"
    assert spec.look.rhythm
    # goal 与 thesis 是两件事：卡片第一栏不该是一句演讲词。
    assert spec.goal.startswith("让新生")
    assert spec.thesis.startswith("先学会")


def test_background_asset_without_subject_falls_back_to_paper() -> None:
    """声称要用库图却没说是什么图，等于给了一个空承诺：回落纸纹并写明。"""

    look = {"background": {"kind": "asset"}, "ornament": {"motif": "none"}}
    spec = asyncio.run(compose_deck_spec("做一个中药学汇报", llm_call=_llm(_reply(look))))

    assert spec.look.background.kind == "motif"
    assert any("背景" in note for note in spec.notes), spec.notes


def test_page_composition_is_carried_into_the_plan() -> None:
    spec = asyncio.run(compose_deck_spec("给新生讲中药学入门", llm_call=_llm(_reply())))

    assert [page.composition for page in spec.pages] == [
        "full-bleed",
        "text-only",
        "text-only",
        "text-image-left",
        "centered",
    ]


def _plan(spec: DeckSpec) -> dict:
    return {
        "kind": "ppt",
        "units": [
            {"id": f"u{index}", "label": page.clean_heading, "intent": page.goal, "detail": "；".join(page.must_cover)}
            for index, page in enumerate(spec.pages)
        ],
        "spec": spec.model_dump(mode="json"),
    }


def test_design_honours_ornament_none_and_composition() -> None:
    """「不要角花」是方案里的一个决定，设计单不能偷偷塞回卷草。"""

    spec = asyncio.run(compose_deck_spec("给新生讲中药学入门", llm_call=_llm(_reply())))
    design = propose_design("从一味药开始", PptParams(), _plan(spec))

    assert design.ornament_motif == "none"
    assert design.theme_id == "cinnabar"
    assert design.palette.get("bg") == "#F7EFE6"
    assert design.paper_motif == "paper-plain"
    assert not any(slot.slot == "ornament" for page in design.pages for slot in page.slots)
    # 纯文字页不给图位；构图跟着方案走。
    agenda = design.pages[1]
    assert agenda.composition == "text-only"
    assert not any(slot.slot in ("figure", "evidence") for slot in agenda.slots)
    # 左图右文页给图位，且搜的是关键词。
    compare = design.pages[3]
    assert compare.composition == "text-image-left"
    figure = next(slot for slot in compare.slots if slot.slot in ("figure", "evidence"))
    assert "黄芪" in figure.keywords
    # 满版图页的图在背景上，不另给图位。
    assert not any(slot.slot in ("figure", "evidence") for slot in design.pages[0].slots)


def test_ornament_motif_from_look_wins_over_seed() -> None:
    """方案选了竹叶，就不能被稳定种子抽成卷草——「每个 PPT 一朵花」就是这么来的。"""

    spec = asyncio.run(
        compose_deck_spec(
            "给新生讲中药学入门，要清雅一点",
            llm_call=_llm(_reply({"theme": "paper", "ornament": {"motif": "corner-bamboo", "corners": ["tl", "br"]}, "background": {"kind": "motif", "motif": "paper-chain"}})),
        )
    )
    design = propose_design("从一味药开始", PptParams(), _plan(spec))
    results = resolve_slots(design, registry=[])

    ornaments = [item for item in results if item.slot == "ornament"]
    assert ornaments and all(item.asset_id == "corner-bamboo" for item in ornaments)
    grounds = [item for item in results if item.slot == "ground"]
    assert grounds and all(item.asset_id == "paper-chain" for item in grounds)


def test_keywords_land_library_assets_that_a_poetic_subject_cannot() -> None:
    """`material` 是给人读的描述，`keywords` 才是拿去搜的词。"""

    from app.ppt.design import DeckDesign, DesignSlot, PageDesign

    design = DeckDesign(
        theme_id="paper",
        pages=[
            PageDesign(
                unit_id="u1",
                archetype="points",
                slots=[
                    DesignSlot(slot="figure", subject="水墨远山与药材剪影叠合，留白为主", style="photo", keywords=["黄芪", "饮片"])
                ],
            )
        ],
    )
    registry = [_asset("photo-huangqi", subjects=["黄芪"], kind="photo", slot_class="evidence")]
    results = resolve_slots(design, registry=registry)

    assert results[0].result in ("hit", "substitute")
    assert results[0].asset_id == "photo-huangqi"


def test_page_brief_carries_the_confirmed_look() -> None:
    """排版层拿到的必须是**确认过的方案**，不是又一份默认模板。"""

    spec = asyncio.run(compose_deck_spec("给新生讲中药学入门", llm_call=_llm(_reply())))
    design = propose_design("从一味药开始", PptParams(), _plan(spec))
    slide = type(
        "Slide",
        (),
        {
            "purpose": "points",
            "title": "寒热温凉",
            "subtitle": "",
            "bullets": ["四气"],
            "blocks": [],
            "asset_refs": [],
        },
    )()
    brief = _page_brief(slide, index=3, total=5, deck_title="从一味药开始", design=design, design_page=design.pages[3])

    assert brief["composition"] == "text-image-left"
    assert brief["look"]["mood"].startswith("静气")
    assert brief["look"]["ornament_motif"] == "none"
    assert brief["look"]["type_pair"] == "kai-song"
    assert brief["look"]["palette"]["bg"] == "#F7EFE6"
    assert brief["look"]["background"]["kind"] == "asset"


def test_stale_wireframe_slots_never_survive_confirm() -> None:
    """确认时不能收下前端回传的**旧槽位**。

    用户把角花改成「不挂」之后，线框上的槽位仍是提案那一刻算的（那时选了折枝）。
    收下它，成品就会照着旧决定画角花——用户改了、页面也显示改了、成品没改。
    """

    from app.ppt.design import normalize_design

    spec = asyncio.run(compose_deck_spec("给新生讲中药学入门", llm_call=_llm(_reply())))
    plan = _plan(spec)
    fresh = propose_design("从一味药开始", PptParams(), plan)
    stale = fresh.model_dump(mode="json")
    for page in stale["pages"]:
        page["slots"] = [{"slot": "ornament", "role": "角饰", "subject": "通用", "style": "motif"}]

    design = normalize_design(stale, plan, params=PptParams())
    assert not any(slot.slot == "ornament" for page in design.pages for slot in page.slots)


def test_ornament_slot_without_a_chosen_motif_draws_nothing() -> None:
    """历史设计单里还剩角饰槽位时，选材也不能补一朵卷草回来。"""

    from app.ppt.design import DeckDesign, DesignSlot, PageDesign

    design = DeckDesign(
        theme_id="paper",
        ornament_motif="none",
        pages=[PageDesign(unit_id="u0", archetype="cover", slots=[DesignSlot(slot="ornament", style="motif")])],
    )
    item = resolve_slots(design, registry=[])[0]

    assert item.result == "none"
    assert item.asset_id == ""
    assert item.fallback_reason


def test_decoration_slots_retrieve_real_ornament_assets() -> None:
    """角饰槽带检索词时先搜库：装饰素材包（真角花/回纹）在这里接上。

    这是「角饰是内置的、不是 AI 找来的」那条病的反向测试：同样的槽位、
    有真素材的库，必须落 library，而不是程序母题。
    """

    from app.ppt.design import DeckDesign, DesignSlot, PageDesign

    design = DeckDesign(
        theme_id="paper",
        pages=[
            PageDesign(
                unit_id="u1",
                archetype="points",
                slots=[
                    DesignSlot(slot="ornament", subject="回纹角花", style="motif", keywords=["回纹", "角花"])
                ],
            )
        ],
    )
    registry = [
        _asset("ornament-corner-antique", subjects=["古典角花"], kind="ornament", tags=["角花", "角饰", "古典"]),
        _asset("ornament-band-greekkey", subjects=["回纹横带"], kind="ornament", tags=["回纹", "横带", "边框", "几何"]),
    ]
    results = resolve_slots(design, registry=registry)

    item = results[0]
    assert item.result in ("hit", "substitute")
    assert item.provenance == "library"
    assert item.asset_id.startswith("ornament-")


def test_decoration_query_falls_back_to_parametric_when_library_empty() -> None:
    """库里没有对应纹样时零感知回落程序母题——装饰缺位不许空窗。"""

    from app.ppt.design import DeckDesign, DesignSlot, PageDesign

    design = DeckDesign(
        theme_id="paper",
        paper_motif="paper-chain",
        pages=[
            PageDesign(
                unit_id="u1",
                archetype="points",
                slots=[
                    DesignSlot(slot="ornament", subject="回纹角花", style="motif", keywords=["回纹"])
                ],
            )
        ],
    )
    results = resolve_slots(design, registry=[])

    assert results[0].result == "parametric"


def test_decoration_assets_never_fill_figure_slots() -> None:
    """装饰资产只进装饰位：配图页不该抓一张纹样当插图。"""

    from app.ppt.design import DeckDesign, DesignSlot, PageDesign
    from app.ppt.select import score

    design = DeckDesign(
        theme_id="paper",
        pages=[
            PageDesign(
                unit_id="u1",
                archetype="points",
                slots=[DesignSlot(slot="figure", subject="回纹", style="photo", keywords=["回纹"])],
            )
        ],
    )
    registry = [_asset("ornament-band-greekkey", subjects=["回纹横带"], kind="ornament", tags=["回纹", "横带"])]
    slot = design.pages[0].slots[0]
    assert score(registry[0], slot) == -1

    results = resolve_slots(design, registry=registry)
    assert results[0].asset_id != "ornament-band-greekkey"
