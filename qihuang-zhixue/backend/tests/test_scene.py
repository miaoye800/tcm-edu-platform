"""场景 DSL 与素材渠道目录的契约测试。

守的是三件容易被顺手改坏的事：

1. **颜色不许写字面量**。模型写 `accent`，12 套配色下都成立；写 `B5443A` 就只在一套
   下对。这条一旦松掉，同一份稿换主题就有一半文字看不见。
2. **证据位只能来自两条渠道**。今晚加一条「看起来也行」的渠道，明天就会有一份课件
   拿黄芪顶人参。`EVIDENCE_CHANNELS` 是代码里的硬约束，不是提示词里的建议。
3. **兜底必须永远能出合法场景**。模型挂了走兜底，兜底再挂就是白屏。
"""

from __future__ import annotations

import pytest

from app.ppt import channels
from app.ppt.scene import (
    MAX_ELEMENTS,
    MOTIF_IDS,
    Scene,
    SceneElement,
    validate_scene,
)
from app.ppt.scene_fallback import fallback_scene

ARCHETYPES = (
    "cover",
    "agenda",
    "section",
    "points",
    "compare",
    "matrix",
    "process",
    "data",
    "warning",
    "summary",
)


# ────────────────────────────────────────────────────────── 颜色与母题


def test_hex_color_is_rejected() -> None:
    """颜色只认角色名。这条是 12 套配色能成立的前提。"""
    with pytest.raises(Exception):
        Scene.model_validate(
            {
                "elements": [
                    {
                        "type": "text",
                        "box": [9, 8, 40, 6],
                        "text": {"content": "标题", "color": "B5443A"},
                    }
                ]
            }
        )


def test_unknown_motif_is_rejected() -> None:
    with pytest.raises(Exception):
        Scene.model_validate({"elements": [{"type": "motif", "box": [0, 0, 10, 10], "motif": "paper-glitter"}]})


def test_motif_catalog_is_not_empty() -> None:
    assert len(MOTIF_IDS) >= 12


def test_asset_id_must_follow_naming_rule() -> None:
    """`xiaoqi-mascot` 那种随手写的 id 是静默兜底成空白的源头。"""
    with pytest.raises(Exception):
        SceneElement.model_validate({"type": "asset", "box": [0, 0, 10, 10], "asset": "renshen"})


def test_bleed_is_allowed_but_not_unbounded() -> None:
    """出血是手法，画到纸外三十格是错。"""
    allowed = SceneElement.model_validate({"type": "rect", "box": [-4, 0, 10, 56], "fill": "accent"})
    assert allowed.box[0] == -4
    with pytest.raises(Exception):
        SceneElement.model_validate({"type": "rect", "box": [-40, 0, 10, 56], "fill": "accent"})


# ────────────────────────────────────────────────────────── 校验与警告


def test_element_budget_warns_but_keeps_scene() -> None:
    """超预算只警告，不丢弃——排得密是手艺问题，不是合法性回题。"""
    elements = [{"type": "rule", "box": [0, i * 1.2, 10, 0.2], "fill": "line"} for i in range(MAX_ELEMENTS + 3)]
    scene, warnings = validate_scene({"elements": elements})
    assert scene is not None
    assert any("scene_dense" in item for item in warnings)


def test_text_overflow_warns() -> None:
    scene, warnings = validate_scene(
        {
            "elements": [
                {
                    "type": "text",
                    "box": [9, 8, 12, 4],
                    "text": {"content": "这一行字明显会挤不进去" * 3, "role": "body", "max_lines": 1},
                }
            ]
        }
    )
    assert scene is not None
    assert any("text_overflow" in item for item in warnings)


def test_invalid_scene_returns_none_not_raise() -> None:
    """模型产出永远不抛异常——抛了整份稿子就废了，降级只需要一个 None。"""
    scene, errors = validate_scene({"elements": [{"type": "text", "box": [0, 0, 1, 1]}]})
    assert scene is None
    assert errors


def test_text_role_capacity_is_enforced() -> None:
    with pytest.raises(Exception):
        Scene.model_validate({"elements": [{"type": "text", "box": [0, 0, 60, 10], "text": {"content": "长" * 20, "role": "kicker"}}]})


# ────────────────────────────────────────────────────────── 兜底


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_fallback_produces_valid_scene(archetype: str) -> None:
    scene = fallback_scene(
        archetype=archetype,
        title="人参：补气第一药",
        subtitle="从性味归经到临床配伍",
        blocks=[
            {"type": "point", "text": "甘、微苦，微温"},
            {"type": "point", "text": "归脾、肺、心、肾经"},
        ],
        bullets=["甘、微苦，微温", "归脾、肺、心、肾经"],
        density="normal",
    )
    assert scene.elements, f"{archetype} 兜底不该是空页"
    again, warnings = validate_scene(scene.model_dump())
    assert again is not None
    assert not warnings, warnings


@pytest.mark.parametrize("archetype", ("points", "summary", "data"))
def test_fallback_with_figure_gives_room(archetype: str) -> None:
    """有图时文字区必须让位。让不开就会压字——这是兜底最容易犯的错。"""
    scene = fallback_scene(
        archetype=archetype,
        title="黄芪",
        blocks=[{"type": "point", "text": "补气升阳"}],
        figure_id="photo-huangqi-01",
        figure_caption="饮片实拍",
    )
    assets = [element for element in scene.elements if element.type == "asset"]
    assert len(assets) == 1
    assert assets[0].asset == "photo-huangqi-01"


def test_compare_never_takes_a_figure() -> None:
    """对照页的双栏本身就是内容。再塞一张图，两栏会挤到各剩二十个字——那不是排版，
    是硬塞。对照页的图应当由模型另起一页放，而不是压在栏里。"""
    scene = fallback_scene(
        archetype="compare",
        title="人参与西洋参",
        blocks=[{"type": "matrix", "columns": [{"heading": "人参", "items": ["温补"]}, {"heading": "西洋参", "items": ["清补"]}]}],
        figure_id="photo-huangqi-01",
    )
    assert not [element for element in scene.elements if element.type == "asset"]


def test_cover_without_figure_keeps_text_wide() -> None:
    """无图封面不该留出右侧空位——留了就是「这里本来该有张图」。"""
    scene = fallback_scene(archetype="cover", title="中药文化知识")
    texts = [element for element in scene.elements if element.type == "text"]
    widest = max(element.box[2] for element in texts)
    assert widest > 60


# ────────────────────────────────────────────────────────── 渠道目录


def test_every_channel_id_is_unique() -> None:
    ids = [channel.id for channel in channels.CHANNELS]
    assert len(ids) == len(set(ids))


def test_evidence_channels_are_the_only_ones_marked_evidence_capable() -> None:
    """反向一致性：标了 evidence 的渠道必须在上锁名单里，名单里也必须都能进证据位。

    只查一个方向会漏——加一条新渠道时最容易忘了把它加进名单，
    于是它的产物能进证据位却不受证据位的规矩约束。
    """
    declared = {channel.id for channel in channels.CHANNELS if channel.usable_for_evidence}
    assert declared == set(channels.EVIDENCE_CHANNELS)


def test_evidence_channels_are_persisted_or_public_domain() -> None:
    """证据位必须有可追溯的来源：要么已落盘入库，要么是 PD 且有 IIIF 直链。"""
    for channel_id in channels.EVIDENCE_CHANNELS:
        channel = channels.by_id(channel_id)
        assert channel is not None
        assert channel.cls in {"A", "B"}, channel_id
        assert channel.status in {"ready", "verified"}, channel_id


def test_url_only_channels_are_never_local() -> None:
    """本地渠道却标「只钉 URL」是自相矛盾——它本来就在仓库里。"""
    for channel in channels.CHANNELS:
        if channel.persist == "url-only":
            assert channel.cls != "A", channel.id


def test_no_redistribute_channels_are_url_only() -> None:
    """许可禁止再配布的三家，绝对不能落盘。"""
    banned = {"web.linustock", "web.enpitsu-sozai", "web.rakugakiicon"}
    for channel_id in banned:
        channel = channels.by_id(channel_id)
        assert channel is not None
        assert channel.persist == "url-only", channel_id
        assert "禁止再配布" in channel.license


def test_generation_is_decorative_only() -> None:
    """生成图永远不进证据位。学员会以为那是教材原图。"""
    generated = channels.by_id("gen.image")
    assert generated is not None
    assert "evidence" not in generated.slot_classes


def test_routes_reference_existing_channels() -> None:
    known = {channel.id for channel in channels.CHANNELS}
    for purpose, order in channels.ROUTES.items():
        assert order, f"{purpose} 没有路由"
        for channel_id in order:
            assert channel_id in known, f"{purpose} 指向了不存在的渠道 {channel_id}"


def test_generation_never_leads_a_route() -> None:
    """排序规则：零成本的先试，有检索的次之，生成永远排最后。

    生成是兜底而不是首选——模型一旦习惯「缺图就生成」，一稿十二页会生出十二张
    风格各异的插画，比没有图更糟。所以这条测验的是「D 类不许打头」，而不是
    「必须是 A/E」：`photo` 这类用途本来就没有本地库，首站是 Unsplash 是对的。
    """
    for purpose, order in channels.ROUTES.items():
        first = channels.by_id(order[0])
        assert first is not None
        assert first.cls != "D", f"{purpose} 的首选不该是生成"
        if len(order) > 1:
            last = channels.by_id(order[-1])
            assert last is not None
            # 生成若在队尾，说明它确实是兜底；若不在，说明这条用途不需要生成。
            assert last.cls != "D" or "gen.image" not in order[:-1]


def test_herb_evidence_route_has_no_shortcut() -> None:
    """药材实证这条路只有两站，中间不许插「通用植物图」。"""
    assert channels.ROUTES["herb-evidence"] == ("local.herbs", "api.wellcome")


def test_catalog_is_serializable_and_complete() -> None:
    catalog = channels.catalog()
    assert catalog["total"] == len(channels.CHANNELS)
    assert set(catalog["channels"]) == {"A", "B", "C", "D", "E", "F"}
    assert "A" in catalog["classes"]
