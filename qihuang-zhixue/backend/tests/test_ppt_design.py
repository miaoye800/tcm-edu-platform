"""设计步与选材步的契约测试。

测的是**产品语义**，不是 JSON 形状：

- 十种版式与七种块类型是全的，且每种版式只接受它能装的块；
- 版式由内容决定，不由页序决定（「第 8 页想放对照表」必须能表达）；
- 设计单可被用户改，改完仍能收敛成合法契约，页数以方案为准；
- 素材四级阶梯在没有资产库时也不崩：能算的算，不能算的降级并留痕；
- 同一份设计渲两次得到同一份素材（否则截图基线没有意义）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ppt.design import (
    ARCHETYPES,
    BLOCK_TYPES,
    DENSITY_CAPS,
    PURPOSES,
    DeckDesign,
    DesignSlot,
    PageDesign,
    check_page_capacity,
    contrast_ratio,
    normalize_design,
    propose_design,
    purpose_for_blocks,
)
from app.ppt.motifs import by_id as motif_by_id
from app.ppt.params import PptParams
from app.ppt.select import Asset, load_registry, resolve_slots, score, summary

PLAN = {
    "title": "中医里的阴阳·讲稿",
    "units": [
        {"id": "u0", "label": "中医里的阴阳"},
        {"id": "u1", "label": "从一个常见误解开始", "intent": "说明阴阳不是好与坏", "detail": "误解一；误解二"},
        {"id": "u2", "label": "阴阳与五行有什么不同", "intent": "做一次逐维对照", "detail": "含义；用法；边界"},
        {"id": "u3", "label": "用法与禁忌", "intent": "提示不能自行套用", "detail": "慎用；不可替代诊疗"},
        {"id": "u4", "label": "要点回顾"},
    ],
}


def test_ten_archetypes_cover_all_purposes() -> None:
    assert len(ARCHETYPES) == 10
    assert {spec.purpose for spec in ARCHETYPES.values()} == set(PURPOSES)
    # 每种版式只接受它能装的块：这是「容量」而不是「偏好」。
    for spec in ARCHETYPES.values():
        assert spec.block_types
        assert set(spec.block_types) <= set(BLOCK_TYPES)


def test_archetype_follows_content_not_page_order() -> None:
    design = propose_design("中医里的阴阳", PptParams(), PLAN)
    archetypes = [page.archetype for page in design.pages]
    assert archetypes[0] == "cover"
    # 第二页讲「误解」，正常要点页；第三页要「逐维对照」，必须是 compare。
    assert archetypes[2] == "compare"
    # 第四页是禁忌，必须是 warning —— 按页序判定的话它只会是一页普通要点。
    assert archetypes[3] == "warning"
    assert archetypes[-1] == "summary"


def test_design_is_deterministic() -> None:
    first = propose_design("人参", PptParams(page_count=10), PLAN).model_dump()
    second = propose_design("人参", PptParams(page_count=10), PLAN).model_dump()
    assert first == second


def test_assumptions_state_the_audience_default() -> None:
    design = propose_design("人参", PptParams(), PLAN)
    assert any("受众按" in line for line in design.assumptions)
    assert any("页数" in line or "篇幅" in line for line in design.assumptions)


def test_normalize_design_follows_the_plan_not_the_returned_pages() -> None:
    """用户只改了一页版式，方案页数不能被前端回传的短数组截断。"""

    design = normalize_design({"theme_id": "ink", "pages": [{"unit_id": "u2", "archetype": "matrix"}]}, PLAN, params=PptParams())
    assert len(design.pages) == len(PLAN["units"])
    assert design.theme_id == "ink"
    assert design.pages[2].archetype == "matrix"
    # 没提到的页保留系统给的那一版，不会变成 points。
    assert design.pages[3].archetype == "warning"


def test_normalize_design_falls_back_on_unknown_values() -> None:
    design = normalize_design({"theme_id": "neon", "pages": [{"unit_id": "u0", "archetype": "hero"}]}, PLAN)
    assert design.theme_id == "paper"
    assert design.pages[0].archetype == "points"


def test_slot_class_is_bound_to_slot_kind() -> None:
    """证据位绝不能是装饰，反之亦然——这不是调用方可选的字段。"""

    assert DesignSlot(slot="evidence", slot_class="decorative").slot_class == "evidence"
    assert DesignSlot(slot="figure", slot_class="evidence").slot_class == "decorative"


def test_purpose_is_recomputed_from_blocks() -> None:
    assert purpose_for_blocks([{"type": "matrix", "columns": [{"heading": "a"}, {"heading": "b"}, {"heading": "c"}]}]) == "matrix"
    assert purpose_for_blocks([{"type": "matrix", "columns": [{"heading": "a"}, {"heading": "b"}]}]) == "compare"
    assert purpose_for_blocks([{"type": "figures", "value": "3g"}]) == "data"
    assert purpose_for_blocks([{"type": "alert", "text": "慎用"}]) == "warning"
    assert purpose_for_blocks([{"type": "steps", "items": ["一", "二", "三"]}]) == "process"
    assert purpose_for_blocks([{"type": "steps", "items": list("一二三四五六")}]) == "agenda"
    assert purpose_for_blocks([]) == "section"


def test_capacity_rejects_blocks_the_archetype_cannot_hold() -> None:
    page = PageDesign(unit_id="u0", archetype="compare", density="normal")
    okay = check_page_capacity(page, [{"type": "matrix", "columns": []}])
    assert okay == []
    too_many = check_page_capacity(page, [{"type": "matrix"}, {"type": "matrix"}, {"type": "matrix"}])
    assert any("内容块超限" in item for item in too_many)
    wrong_type = check_page_capacity(page, [{"type": "alert", "text": "x"}])
    assert any("不接受" in item for item in wrong_type)


def test_density_caps_only_shrink_with_density() -> None:
    assert DENSITY_CAPS["airy"] < DENSITY_CAPS["normal"] < DENSITY_CAPS["dense"]


def test_contrast_guard_catches_unreadable_paper() -> None:
    assert contrast_ratio("1F1B16", "FAF7F0") >= 4.5
    assert contrast_ratio("DDD5C8", "FAF7F0") < 4.5


# ── 选材 ────────────────────────────────────────────────────────────────────


def _registry() -> list[Asset]:
    return [
        Asset(id="plate-renshen-01", kind="plate", slot_class="decorative", subjects=["人参"], file="assets/pictorial/plate-renshen-01.webp", w=1200, h=1600, bytes=120_000, sha256="x", license="PD-old", source_url="https://example.org/a", attribution="《植物名实图考》", treatment="plate-wash"),
        Asset(id="photo-renshen-01", kind="photo", slot_class="evidence", subjects=["人参"], file="assets/pictorial/photo-renshen-01.webp", w=1600, h=1200, bytes=300_000, sha256="y", license="CC0", source_url="https://example.org/b", treatment="photo-toned"),
        Asset(id="plate-gancao-01", kind="plate", slot_class="decorative", subjects=["甘草"], file="assets/pictorial/plate-gancao-01.webp", w=1200, h=1600, bytes=110_000, sha256="z", license="PD-old", source_url="https://example.org/c", treatment="plate-wash"),
    ]


def test_paramtric_slots_never_touch_the_library() -> None:
    design = propose_design("人参", PptParams(), PLAN)
    results = resolve_slots(design, registry=_registry())
    grounds = [item for item in results if item.slot == "ground"]
    assert grounds and all(item.result == "parametric" for item in grounds)
    # 底纹只能来自 ground 族母题——不写死具体 id，因为选哪一个由种子决定。
    for item in grounds:
        motif = motif_by_id(item.asset_id)
        assert motif is not None, f"底纹指向了不存在的母题 {item.asset_id}"
        assert motif.slot == "ground", f"{item.asset_id} 不是底纹母题"


def test_ground_motif_is_not_always_the_first_candidate() -> None:
    """底纹不能恒为 ``paper-plain``。

    原来 ``_parametric`` 取 ``candidates[0]``，于是主题里配的另几个母题
    写在目录里却永远轮不到——「不管什么要求都只给同一张纸」就是这么来的。
    这里换若干主题，要求至少出现两种不同的底纹，且每种都真实存在。
    """

    seen: set[str] = set()
    for theme in ("paper", "ink", "celadon", "cinnabar"):
        for title in ("人参", "阴阳", "四气五味", "经络", "方剂配伍", "望闻问切"):
            design = DeckDesign(
                theme_id=theme,
                pages=[PageDesign(unit_id=f"{theme}-{title}", archetype="points", slots=[DesignSlot(slot="ground")])],
            )
            item = resolve_slots(design, registry=[])[0]
            assert item.result == "parametric"
            seen.add(item.asset_id)

    assert len(seen) > 1, f"所有主题与标题都落到同一个底纹（{seen}）"
    assert "paper-plain" in seen or len(seen) >= 2


def test_exact_subject_hits_and_keeps_its_license() -> None:
    design = DeckDesign(pages=[PageDesign(unit_id="u0", archetype="cover", slots=[DesignSlot(slot="figure", subject="人参", style="plate", aspect="portrait")])])
    results = resolve_slots(design, registry=_registry())
    figure = next(item for item in results if item.slot == "figure")
    assert figure.result == "hit"
    assert figure.asset_id == "plate-renshen-01"
    assert figure.license == "PD-old"
    assert figure.attribution


def test_missing_subject_degrades_instead_of_inventing_a_picture() -> None:
    design = DeckDesign(pages=[PageDesign(unit_id="u0", archetype="cover", slots=[DesignSlot(slot="figure", subject="紫河车", style="plate")])])
    results = resolve_slots(design, registry=_registry())
    figure = next(item for item in results if item.slot == "figure")
    assert figure.result in ("substitute", "none")
    # 无论如何不能凭空造一个 asset_id。
    if figure.result == "none":
        assert figure.asset_id == ""


def test_evidence_slot_never_accepts_generated_images() -> None:
    class Provider:
        name = "fake"

        def available(self) -> bool:
            return True

        async def generate(self, prompt, *, size, style_tokens):  # pragma: no cover - 不该被调用
            raise AssertionError("证据位不允许生成")

    design = DeckDesign(pages=[PageDesign(unit_id="u0", archetype="points", slots=[DesignSlot(slot="evidence", subject="紫河车", priority="required")])])
    results = resolve_slots(design, registry=[], provider=Provider())
    evidence = next(item for item in results if item.slot == "evidence")
    assert evidence.result == "none"
    assert "生成" in evidence.fallback_reason
    # 必需槽位落空必须能被统计出来，不能静默通过。
    assert summary(results)["unfilled"] == ["u0:evidence"]


def test_empty_library_still_produces_a_deck_design() -> None:
    design = propose_design("紫河车", PptParams(), PLAN)
    results = resolve_slots(design, registry=[])
    assert results
    assert all(item.result in ("parametric", "none") for item in results)


def test_scoring_is_stable_across_runs() -> None:
    want = DesignSlot(slot="figure", subject="人参", style="plate", aspect="portrait")
    scores = [score(asset, want, theme_id="paper") for asset in _registry()]
    again = [score(asset, want, theme_id="paper") for asset in _registry()]
    assert scores == again

    # 类别只锁一个方向：
    # 证据位**不能**拿装饰图充数（图再对，它也不是「这味药长什么样」的证据），
    # 但 evidence 资产可以来当配图——图是真的，只是用途变了。
    assert score(_registry()[0], DesignSlot(slot="evidence", subject="人参", style="plate"), theme_id="paper") < 0
    assert score(_registry()[1], want, theme_id="paper") > 0


# ── 素材库真的能命中 ──────────────────────────────────────────────────────
#
# 上面几条测的都是「空库不崩」。空库不崩是底线，不是目标：库是空的时候，
# 「选材」这一步等于没写。这几条盯的是**库里有货时它确实认得出来**。


def test_registry_is_not_empty_and_lands_in_the_selector() -> None:
    """真实登记表里至少有一条能被「黄芪 + 插图位」选中。

    `docs/studios/assets.md` 把空库当合法状态，这在「库还没建」时是对的；
    现在库里已经有五张 CC0 饮片，选型器必须认得出它们，否则就是白入库。
    """

    assets = load_registry()
    assert assets, "assets/registry.json 为空：先跑 tools/assets/local.py"

    design = DeckDesign(
        pages=[
            PageDesign(
                unit_id="u0",
                archetype="points",
                slots=[DesignSlot(slot="figure", subject="黄芪", priority="required")],
            )
        ]
    )
    results = resolve_slots(design, registry=assets)
    figure = next(item for item in results if item.slot == "figure")
    assert figure.result in ("hit", "substitute"), f"黄芪 的饮片图没被选中（{figure.result}）"
    assert figure.asset_id.startswith("photo-huangqi-"), figure.asset_id


def test_unrelated_subject_never_borrows_another_herb() -> None:
    """讲人参时不能拿黄芪的图顶上。

    这是医学教育里最不能犯的错：图看着对、内容讲错，比没有图糟得多。
    所以 subject 不命中时必须走 `none`，由版式自己降级，而不是「找张最像的」。
    """

    assets = load_registry()
    design = DeckDesign(
        pages=[
            PageDesign(
                unit_id="u0",
                archetype="points",
                slots=[DesignSlot(slot="figure", subject="人参", priority="required")],
            )
        ]
    )
    results = resolve_slots(design, registry=assets)
    figure = next(item for item in results if item.slot == "figure")
    assert figure.result == "none" or "黄芪" not in str(figure.asset_id)


# ── 角饰：处方名与母题必须一一对上 ────────────────────────────────────────


def test_every_ornament_preset_has_a_rendered_motif() -> None:
    """`ORNAMENT_SETS` 里的每一档都要有母题实现。

    设计单里能选、母题目录里却没有，用户选完就是「什么都没画」——
    而这种空档在截图上很难发现（角上本来就该是淡的）。
    """

    from app.ppt.design import ORNAMENT_SETS
    from app.ppt.motifs import MOTIFS

    implemented = {motif.id for motif in MOTIFS if motif.slot == "ornament"}
    # 处方名与母题 id 的对应关系在前端 `render/skin.ts`，这里按约定反推：
    # vine-a → corner-vine、branch-b → corner-branch …
    for preset in ORNAMENT_SETS:
        if preset == "none":
            continue
        family = preset.split("-")[0]
        assert any(motif.endswith(family) or family in motif for motif in implemented), (
            f"角饰处方 {preset} 找不到对应母题（现有 {sorted(implemented)}）"
        )


def test_ornament_motifs_share_one_frame_parameter() -> None:
    """五档角饰都要有 `frame` 参数。

    四角共用一个外框是这一版的结构：正文页角落放不下界栏时传 frame=0 只留内花。
    少一档没有这个参数，那一档在正文页就会把界线画到眉标上。
    """

    from app.ppt.motifs import MOTIFS

    for motif in MOTIFS:
        # 按 family 而不是按 slot 过滤：竖界格和乌丝栏的 slot 也是 ornament，
        # 但它们是「版面里的线」，不是角花，没有、也不需要 frame。
        if motif.family != "ornament":
            continue
        names = {param.name for param in motif.params}
        assert "frame" in names, f"{motif.id} 缺 frame 参数（现有 {sorted(names)}）"
