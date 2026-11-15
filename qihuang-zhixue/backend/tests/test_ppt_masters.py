"""母版系统：五套母版 × 十页型的确定性排版，与 spec/design/painter 的接线。

这组测试守的是「豆包级」路线的根基：

- **确定性**：同一份内容在同一套母版下永远过 validate，标题与正文逐字在场
  （模型排版时这是回灌两轮才能追平的硬合同，母版必须一次到位）；
- **容量**：0/1/3/6 条要点、有无配图都排得下；超出 6 条截断并记账；
- **接线**：模型选母版 → reconcile 回填 → 设计单带 master_id → select 出
  装饰槽 → painter 上纸，一整条链每一跳都有断言；
- **兼容**：老方案没有 master_id，行为与升级前逐字节一致（scene_art 注入桩路径）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.ppt.agent import PptAgent
from app.ppt.design import propose_design
from app.ppt.draft import DeckDraft, DraftColumn, DraftSlide, normalize_deck
from app.ppt.masters import (
    FALLBACK_MASTER,
    MASTERS,
    by_id,
    default_master_for,
    menu,
    write_typescript,
)
from app.ppt.masters.base import PAGE_ROLES, PageContext
from app.ppt.masters.painter import paint_master_scenes
from app.ppt.scene import validate_scene
from app.ppt.select import Asset, resolve_slots
from app.ppt.spec import DeckSpec, compose_deck_spec
from tests.helpers import keyless_settings

ROLES = PAGE_ROLES
BULLET_SETS = [[], ["一条要点"], ["一", "二", "三"], ["一", "二", "三", "四", "五", "六"]]


def _ctx(
    *,
    purpose: str,
    title: str = "麻黄汤证治恶寒发热",
    subtitle: str = "",
    bullets: list[str] | None = None,
    columns: list[dict] | None = None,
    blocks: list[dict] | None = None,
    figure: dict | None = None,
    ground: dict | None = None,
    ornament_motif: str = "",
    index: int = 2,
) -> PageContext:
    # 真实页面上 blocks 与 bullets 二选一（COMPOSE 契约），夹具也照此：
    # 结构化页型（对照/矩阵/流程/数据/警示）显式传 blocks，其余只给 bullets。
    return PageContext(
        purpose=purpose,
        title=title,
        subtitle=subtitle,
        bullets=list(bullets or []),
        columns=columns or [],
        blocks=blocks or [],
        figure=figure,
        ground=ground or {"kind": "motif", "id": "paper-chain"},
        ornament_motif=ornament_motif,
        index=index,
        total=9,
        deck_title="中药学入门试讲",
    )


def _contents(scene) -> list[str]:
    return [
        element.text.content
        for element in scene.elements
        if element.type == "text" and element.text is not None
    ]


# ── 目录与生成物 ────────────────────────────────────────────────────────────


def test_catalog_has_five_masters_and_menu_matches() -> None:
    assert list(MASTERS) == [
        "xuan-songkai", "ink-gold", "celadon-wenkai", "modern-sans", "cinnabar-editorial",
    ]
    entries = menu()
    assert [entry["master_id"] for entry in entries] == list(MASTERS)
    for entry in entries:
        assert entry["label"] and entry["tagline"] and entry["palettes"]


def test_every_master_covers_all_roles_with_two_palette_variants() -> None:
    for master in MASTERS.values():
        assert set(master.painters) >= set(ROLES), master.spec.id
        assert len(master.spec.palette_variants) >= 2
        assert master.spec.decoration_sets[0]["ornament"]


def test_genre_defaults_are_known_masters() -> None:
    for genre in ("lecture", "orientation", "brief", "training", "defense", "pitch"):
        assert by_id(default_master_for(genre)) is not None
    assert by_id(default_master_for("unknown")) is not None


def test_write_typescript_lists_every_master(tmp_path: Path) -> None:
    target = write_typescript(tmp_path / "pptMasters.ts")
    text = target.read_text(encoding="utf-8")
    assert "生成物，不要手改" in text
    for master_id in MASTERS:
        assert master_id in text
    assert "export const PPT_MASTERS" in text


def test_default_ts_path_is_inside_frontend_config() -> None:
    """masters 是 ppt 下的子包，比 motifs.py 深一层——路径数错会把生成物写进
    ``backend/frontend/``（第一版实测踩过）。"""
    from app.ppt.masters import _default_ts_path

    text = str(_default_ts_path()).replace("\\", "/")
    assert text.endswith("frontend/src/config/pptMasters.ts")
    assert "backend/frontend" not in text


# ── 排版：每母版 × 每页型 × 容量档 ──────────────────────────────────────────


@pytest.mark.parametrize("master_id", list(MASTERS))
@pytest.mark.parametrize("role", list(ROLES))
@pytest.mark.parametrize("bullets", BULLET_SETS, ids=lambda value: f"n{len(value)}")
def test_every_role_paints_a_valid_scene(master_id: str, role: str, bullets: list[str]) -> None:
    master = MASTERS[master_id]
    # 封面/章节按 ROLE_CONTRACT 不带 bullets，正文是 subtitle；其余页型正文=bullets。
    body_roles = () if role in ("cover", "section") else tuple(bullets)
    for figure in (None, {"id": "plate-445da94b", "kind": "asset", "caption": "明代本草彩绘"}):
        ctx = _ctx(purpose=role, bullets=bullets, figure=figure)
        scene = master.paint(ctx)
        scene, warnings = validate_scene(scene)
        assert scene is not None, (master_id, role, warnings)
        joined = "".join(_contents(scene))
        assert ctx.title in joined, f"{master_id}/{role} 标题不在场景文字里"
        for bullet in body_roles:
            assert bullet in joined, f"{master_id}/{role} 正文「{bullet}」被删了"


@pytest.mark.parametrize("master_id", list(MASTERS))
def test_columns_and_cards_are_verbatim(master_id: str) -> None:
    """对照栏、矩阵栏、流程步骤、数据卡、警示条：栏题与条目逐字上纸。"""
    master = MASTERS[master_id]
    ctx = _ctx(
        purpose="compare",
        columns=[
            {"heading": "麻黄汤", "items": ["发汗解表", "无汗而喘"]},
            {"heading": "桂枝汤", "items": ["解肌发表", "有汗恶风"]},
        ],
    )
    joined = "".join(_contents(master.paint(ctx)))
    for word in ("麻黄汤", "桂枝汤", "发汗解表", "有汗恶风"):
        assert word in joined
    ctx = _ctx(purpose="data", blocks=[{"type": "figures", "value": "3~9g", "heading": "常用剂量", "source": "《中药学》"}])
    joined = "".join(_contents(master.paint(ctx)))
    for word in ("3~9g", "常用剂量"):
        assert word in joined
    ctx = _ctx(purpose="process", blocks=[{"type": "steps", "items": ["先煎麻黄", "去上沫", "再入余药"]}])
    joined = "".join(_contents(master.paint(ctx)))
    for word in ("先煎麻黄", "再入余药"):
        assert word in joined
    ctx = _ctx(purpose="warning", blocks=[{"type": "alert", "text": "表虚自汗者禁用，不可久服"}])
    joined = "".join(_contents(master.paint(ctx)))
    assert "表虚自汗者禁用" in joined


@pytest.mark.parametrize("master_id", list(MASTERS))
def test_short_cover_title_uses_vertical_or_horizontal_but_keeps_text(master_id: str) -> None:
    """短标题（竖排路径）与长标题（降档路径）都要完整上纸。"""
    master = MASTERS[master_id]
    for title in ("麻黄汤证", "这是一个特别长的测试标题应该要降档处理才行"):
        ctx = _ctx(purpose="cover", title=title)
        scene, warnings = validate_scene(master.paint(ctx))
        assert scene is not None, warnings
        assert title in "".join(_contents(scene))


def test_bullets_beyond_six_are_truncated_with_warning() -> None:
    deck = _deck(purpose="points")
    # SlideNode 落库上限 6，但回放/旧数据可能带更多——painter 是最后一道闸。
    deck.slides[1].bullets = [f"第{index}条要点内容" for index in range(9)]  # type: ignore[assignment]
    stats = paint_master_scenes(deck, design={"master_id": "xuan-songkai"}, deck_title="中药学")
    assert stats["painted"] == len(deck.slides)
    assert any("truncated" in item for item in stats["warnings"])
    joined = "".join(_contents(deck.slides[1].scene))
    assert "第5条要点内容" in joined
    assert "第8条要点内容" not in joined, "第 7 条起应被截断"


# ── painter：接线与失败策略 ─────────────────────────────────────────────────


def _deck(purpose: str = "points", bullets: list[str] | None = None) -> object:
    draft = DeckDraft(
        deck_title="麻黄讲稿",
        slides=[
            DraftSlide(title="麻黄：发汗解表第一药"),
            DraftSlide(
                title="方证要点",
                bullets=bullets or ["发汗解表", "宣肺平喘"],
                columns=[
                    DraftColumn(heading="麻黄", items=["发汗力强"]),
                    DraftColumn(heading="桂枝", items=["发汗力缓"]),
                ],
            ),
        ],
    )
    deck = normalize_deck(draft, topic="麻黄", sources=[])
    deck.slides[1].purpose = purpose  # type: ignore[assignment]
    return deck


def test_painter_sets_scenes_and_reports_master_model() -> None:
    deck = _deck()
    stats = paint_master_scenes(deck, design={"master_id": "xuan-songkai"}, deck_title="麻黄讲稿")
    assert stats["model"] == "master:xuan-songkai"
    assert stats["painted"] == len(deck.slides)
    assert not stats["fallback"]
    for slide in deck.slides:
        assert slide.scene is not None
        scene, warnings = validate_scene(slide.scene)
        assert scene is not None, warnings
    assert "麻黄：发汗解表第一药" in "".join(_contents(deck.slides[0].scene))


def test_painter_unknown_master_returns_skipped() -> None:
    deck = _deck()
    before = deck.slides[0].scene
    stats = paint_master_scenes(deck, design={"master_id": "no-such-master"})
    assert stats.get("skipped") == "no_master"
    # 一页都不能碰：normalize 落库时带的兜底场景原样保留。
    assert deck.slides[0].scene is before


def test_painter_constructor_crash_falls_back_deterministically(monkeypatch: pytest.MonkeyPatch) -> None:
    """构造器抛异常是代码 bug：记日志退 scene_fallback，不炸整份稿子。"""
    master = MASTERS["xuan-songkai"]

    def boom(ctx: PageContext):
        raise RuntimeError("geometry bug")

    monkeypatch.setitem(master.painters, "points", boom)
    deck = _deck(purpose="points")
    stats = paint_master_scenes(deck, design={"master_id": "xuan-songkai"}, deck_title="麻黄讲稿")
    assert stats["fallback"] and stats["fallback"][0]["reason"].startswith("master_error")
    scene = deck.slides[1].scene
    assert scene is not None and scene.note.startswith("兜底")
    joined = "".join(_contents(scene))
    assert "方证要点" in joined and "发汗力强" in joined


def test_painter_only_repaints_requested_pages() -> None:
    deck = _deck()
    paint_master_scenes(deck, design={"master_id": "modern-sans"}, deck_title="麻黄讲稿")
    first = deck.slides[0].scene
    stats = paint_master_scenes(deck, design={"master_id": "modern-sans"}, deck_title="麻黄讲稿", only=[1])
    assert stats["pages"] == 2 and stats["painted"] == 1
    assert deck.slides[0].scene is first, "没改的页连场景都不能碰"


def test_painter_applies_palette_variant_and_override() -> None:
    deck = _deck()
    paint_master_scenes(
        deck,
        design={"master_id": "ink-gold", "accent_variant": 1, "palette": {"accent": "#FF0000"}},
        deck_title="麻黄讲稿",
    )
    # 色值本身在前端映射；后端只保证 palette 合并结果可从 design 读回。
    from app.ppt.masters.painter import _palette_of
    from app.ppt.masters import by_id as _by_id

    palette = _palette_of({"accent_variant": 1, "palette": {"accent": "#FF0000"}}, _by_id("ink-gold"))
    assert palette["accent"] == "#FF0000"
    assert palette["bg"] == "#10151C"  # ink-gold 变体 1 的底色，未被覆盖


# ── spec：模型选母版 → reconcile 回填 ──────────────────────────────────────


def _reply(look: dict, *, genre: str = "brief") -> str:
    return json.dumps(
        {
            "title": "从一味药开始认识中药学",
            "genre": genre,
            "audience": "社区药师",
            "occasion": "工作汇报",
            "goal": "让药师掌握验收要点",
            "thesis": "先学会看一味药",
            "arc": ["结论", "依据", "方案"],
            "target_pages": 5,
            "duration_min": 10,
            "look": look,
            "pages": [
                {"heading": "开场", "role": "cover", "goal": "定调"},
                {"heading": "路线", "role": "agenda", "goal": "给路线"},
                {"heading": "对照", "role": "compare", "goal": "对比"},
                {"heading": "数字", "role": "data", "goal": "量化"},
                {"heading": "收束", "role": "summary", "goal": "一句话"},
            ],
            "interpretation": ["你要一份汇报稿。"],
        },
        ensure_ascii=False,
    )


def _compose(look: dict, *, genre: str = "brief") -> DeckSpec:
    payload = _reply(look, genre=genre)

    async def llm(_messages):
        return payload

    return asyncio.run(compose_deck_spec("社区药房饮片验收工作汇报", llm_call=llm))


def test_model_pick_master_backfills_design_system() -> None:
    spec = _compose({"master_id": "ink-gold", "accent_variant": 1, "mood": "庄重", "rhythm": "结论先行"})
    assert spec.look.master_id == "ink-gold"
    assert spec.look.accent_variant == 1
    # 模型没给的四个字段由母版回填。
    assert spec.look.theme == "ink"
    assert spec.look.type_pair == "serif-full"
    assert spec.look.ornament.motif == "none"
    assert spec.look.background.kind == "solid"


def test_model_explicit_fields_beat_master_defaults() -> None:
    """用户明说「不要角花」时，模型给的 ornament=none 必须活过母版回填。"""
    spec = _compose(
        {
            "master_id": "xuan-songkai",
            "ornament": {"motif": "none"},
            "background": {"kind": "motif", "motif": "paper-mottle"},
        },
        genre="lecture",
    )
    assert spec.look.master_id == "xuan-songkai"
    assert spec.look.ornament.motif == "none"
    assert spec.look.background.motif == "paper-mottle"


def test_unknown_master_falls_back_to_genre_default_with_note() -> None:
    spec = _compose({"master_id": "no-such-master", "mood": "清楚"}, genre="training")
    assert spec.look.master_id == "celadon-wenkai"
    assert any("不在目录里" in note for note in spec.notes)


def test_baseline_looks_carry_master_per_genre() -> None:
    from app.ppt.spec import GENRES, _baseline_look

    for genre in GENRES:
        look = _baseline_look(genre)
        assert look.master_id, genre
        assert look.master_id in MASTERS, genre
        master = MASTERS[look.master_id]
        assert look.theme == master.spec.theme, genre
        assert look.type_pair == master.spec.type_pair, genre


def test_deck_look_validators_are_lenient() -> None:
    """宽松读：master_id 乱写不 raise、accent_variant 越界夹回。"""
    spec = _compose({"master_id": 123, "accent_variant": "9", "mood": "x"})
    assert spec.look.master_id == "modern-sans"  # 未知 id（数字串）→ 场合默认
    assert 0 <= spec.look.accent_variant <= 3


# ── design / select：设计单与装饰槽 ────────────────────────────────────────


def _plan_with_spec(spec: DeckSpec) -> dict:
    return {
        "title": "社区药房饮片验收工作汇报 · 讲稿",
        "units": [
            {"id": f"u{index}", "label": page.clean_heading, "intent": page.goal, "detail": ""}
            for index, page in enumerate(spec.pages)
        ],
        "spec": spec.model_dump(mode="json"),
    }


def test_propose_design_carries_master_and_variant_palette() -> None:
    spec = _compose({"master_id": "ink-gold", "accent_variant": 1, "mood": "庄重"})
    design = propose_design("社区药房饮片验收工作汇报", {}, _plan_with_spec(spec))
    assert design.master_id == "ink-gold"
    assert design.accent_variant == 1
    assert design.theme_id == "ink"
    assert design.palette["accent"] == "#9DB4C8"  # ink-gold 变体 1
    assert design.palette["bg"] == "#10151C"
    # solid 背景：不挂 ground 槽——挂了会被纸纹盖掉。
    assert all(slot.slot != "ground" for page in design.pages for slot in page.slots)


def test_propose_design_ornament_slot_follows_deco_set() -> None:
    # xuan 默认装饰组 = 真纹样（素材包优先）：角饰槽带检索词，motif 留空走库检索。
    # 程序母题降为备选轮换——库空时 select 的阶梯仍会回落 corner-*。
    spec = _compose({"master_id": "xuan-songkai"}, genre="lecture")
    design = propose_design("中药学入门试讲", {}, _plan_with_spec(spec))
    assert design.ornament_motif in ("", "none"), "默认组不钉程序母题，才能去库里找真纹样"
    cover = next(page for page in design.pages if page.archetype == "cover")
    ornament = next(slot for slot in cover.slots if slot.slot == "ornament")
    assert ornament.subject and ornament.subject != "通用"
    assert ornament.keywords, "真纹样槽必须带检索词，否则等于查无此物"
    # ink 默认装饰组 = 不装饰 → 封面也不挂角饰槽。
    spec = _compose({"master_id": "ink-gold"}, genre="defense")
    design = propose_design("毕业课题答辩", {}, _plan_with_spec(spec))
    assert design.ornament_motif in ("", "none")
    assert all(slot.slot != "ornament" for page in design.pages for slot in page.slots)


def _ornament_asset(asset_id: str, keywords: list[str]) -> Asset:
    return Asset(
        id=asset_id,
        kind="ornament",
        slot_class="decorative",
        subjects=["回纹角花"],
        tags=keywords,
        file=f"assets/ornaments/{asset_id}.svg",
        w=900,
        h=900,
        bytes=40_000,
        sha256=asset_id,
        license="CC0",
    )


def test_default_xuan_design_retrieves_real_ornament_from_pack() -> None:
    """默认装饰组就必须吃素材包。

    这条守的是「库导进来了、每份稿子却还是画程序角花」那个病：默认组一旦钉了
    程序母题，select 的阶梯 0 会在搜库之前就返回，素材包永远轮不到。
    """

    spec = _compose({"master_id": "xuan-songkai"}, genre="lecture")
    design = propose_design("中药学入门试讲", {}, _plan_with_spec(spec))
    hit = resolve_slots(design, registry=[_ornament_asset("ornament-corner-antique", ["角花", "卷草", "古典"])])
    ornament = [item for item in hit if item.slot == "ornament"]
    assert ornament and ornament[0].result in ("hit", "substitute")
    assert ornament[0].asset_id == "ornament-corner-antique"


def test_real_ornament_query_hits_library_and_empty_library_falls_back_to_motif() -> None:
    """装饰组走素材检索：库里有真纹样就命中；空库回落程序母题（不许空窗）。"""
    spec = _compose({"master_id": "xuan-songkai", "ornament": {"motif": "none", "subject": "回纹角花", "keywords": ["回纹", "角花"]}}, genre="lecture")
    design = propose_design("中药学入门试讲", {}, _plan_with_spec(spec))

    hit = resolve_slots(design, registry=[_ornament_asset("ornament-greek-01", ["回纹", "角花"])])
    ornament = [item for item in hit if item.slot == "ornament"]
    assert ornament and ornament[0].result in ("hit", "substitute")
    assert ornament[0].asset_id == "ornament-greek-01"

    empty = resolve_slots(design, registry=[])
    ornament = [item for item in empty if item.slot == "ornament"]
    assert ornament and ornament[0].result == "parametric", "空库必须回落程序角花，不是空窗"
    assert ornament[0].asset_id.startswith("corner-")


def test_real_ornament_asset_reaches_the_scene() -> None:
    """命中的真纹样要真的画上纸：painter 读 asset_refs 里的 ornament 槽。"""
    from app.ppt.schemas import AssetRef

    spec = _compose({"master_id": "xuan-songkai", "ornament": {"motif": "none", "subject": "回纹角花", "keywords": ["回纹", "角花"]}}, genre="lecture")
    design = propose_design("中药学入门试讲", {}, _plan_with_spec(spec))
    deck = _deck()
    deck.slides[0].asset_refs = [  # type: ignore[assignment]
        AssetRef(slot="ornament", asset_id="ornament-greek-01", result="hit", provenance="library", url="https://example.org/x.svg")
    ]
    stats = paint_master_scenes(deck, design=design.model_dump(), deck_title="麻黄讲稿")
    assert stats["painted"] == len(deck.slides)
    cover = deck.slides[0].scene
    assets = [element for element in cover.elements if element.type == "asset"]
    assert any(element.asset == "ornament-greek-01" for element in assets), "真纹样角花没上纸"


# ── agent / 回放兼容 ───────────────────────────────────────────────────────


def test_agent_paints_via_master_when_design_carries_it() -> None:
    """注入式 llm_call 也走母版：确定性排版没有「多造一份场景」的成本。"""

    async def run():
        agent = PptAgent(settings=keyless_settings())
        deck = _deck()
        return await agent._paint(
            deck,
            design={"master_id": "celadon-wenkai", "type_pair": "kai-song", "palette": {}},
            deck_title="麻黄讲稿",
        )

    stats = asyncio.run(run())
    assert stats["model"] == "master:celadon-wenkai"


def test_agent_without_master_keeps_old_injected_path() -> None:
    """老方案（无 master_id）+ 注入桩：照旧 skip，行为与升级前一致。"""

    async def stub(_messages):
        return "{}"

    async def run():
        agent = PptAgent(settings=keyless_settings(), llm_call=stub)
        deck = _deck()
        return await agent._paint(deck, design={"theme_id": "paper"}, deck_title="麻黄讲稿")

    stats = asyncio.run(run())
    assert stats == {"skipped": "injected_llm"}


def test_master_painting_needs_no_api_key() -> None:
    """母版路径零模型调用：无 key 也排——这是它与 scene_art 的本质差别。"""
    settings = keyless_settings()
    assert not (getattr(settings, "deepseek_api_key", None) or getattr(settings, "zhipu_api_key", None))
    deck = _deck()
    stats = paint_master_scenes(deck, design={"master_id": FALLBACK_MASTER}, deck_title="麻黄讲稿")
    assert stats["painted"] == len(deck.slides)


# ── Phase 4 渲染润色：shadow / frame / img_treatment / fill2 ────────────────


def test_polish_fields_validate_and_default_to_none() -> None:
    """additive 字段：写了就过校验；不写 = 原样（老场景零改动）。"""
    from app.ppt.scene import SceneElement

    polished = SceneElement.model_validate(
        {
            "type": "asset",
            "asset": "plate-445da94b",
            "box": (10, 10, 20, 20),
            "frame": "mat",
            "shadow": 0.4,
            "img_treatment": "toned",
        }
    )
    assert polished.frame == "mat"
    assert polished.shadow == 0.4
    assert polished.img_treatment == "toned"

    legacy = SceneElement.model_validate({"type": "rect", "box": (0, 0, 5, 5), "fill": "fg"})
    assert legacy.frame == "none"
    assert legacy.shadow == 0.0
    assert legacy.fill2 is None
    assert legacy.gradient_dir == "none"

    gradient = SceneElement.model_validate(
        {"type": "rect", "box": (0, 0, 5, 5), "fill": "fg", "fill2": "bg", "gradient_dir": "v"}
    )
    assert gradient.fill2 == "bg"
    assert gradient.gradient_dir == "v"


@pytest.mark.parametrize(
    ("master_id", "frame", "shadow"),
    [
        ("xuan-songkai", "mat", 0.18),
        ("ink-gold", "hairline", 0.4),
        ("modern-sans", "none", 0.3),
    ],
)
def test_masters_declare_figure_polish(master_id: str, frame: str, shadow: float) -> None:
    """装裱/阴影/调性是母版的设计决定，必须真的写进图位元素。"""
    ctx = _ctx(
        purpose="points",
        figure={"id": "plate-445da94b", "kind": "asset", "caption": "明代本草彩绘"},
    )
    scene = MASTERS[master_id].paint(ctx)
    plates = [el for el in scene.elements if el.type == "asset" and el.asset.startswith("plate-")]
    assert plates, f"{master_id} 要点页应有图位"
    assert plates[0].frame == frame
    assert abs(plates[0].shadow - shadow) < 1e-9
    if master_id == "ink-gold":
        assert plates[0].img_treatment == "toned"
    scene, warnings = validate_scene(scene)
    assert scene is not None, warnings


@pytest.mark.parametrize("master_id", list(MASTERS))
def test_data_cards_paint_gradient_panel(master_id: str) -> None:
    """数值卡的软底板：fg→bg 双色渐变 rect，数值不再裸放在纸上。"""
    ctx = _ctx(
        purpose="data",
        blocks=[{"type": "figures", "value": "3~9g", "heading": "常用剂量", "source": "《中药学》"}],
    )
    scene = MASTERS[master_id].paint(ctx)
    panels = [
        el for el in scene.elements if el.type == "rect" and el.fill2 == "bg" and el.gradient_dir == "v"
    ]
    assert panels, f"{master_id} 数据卡应有渐变软底板"
    scene, warnings = validate_scene(scene)
    assert scene is not None, warnings
