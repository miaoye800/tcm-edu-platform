"""P7 呈现二选一：体裁选择必须可复现，产物必须是只读快照。"""

from __future__ import annotations

from pathlib import Path

from app.capabilities.handlers.plan_present import SPEC as PRESENT_SPEC
from app.path.present import (
    DEFAULT_DOC_ARCHETYPE,
    DOC_ARCHETYPES,
    MINDMAP_NODE_LIMIT,
    MINDMAP_STAGE_LIMIT,
    build_doc,
    build_mindmap,
    choose_doc_archetype,
    choose_presentation,
    open_url_for,
    present_plan,
)
from app.path.store import upsert_node
from app.profile.schema import (
    CategoricalDimension,
    Goal,
    MotivationDimension,
    NumericDimension,
    Profile,
    ResourcePreferenceDimension,
    WeakPointsDimension,
)
from app.profile.store import upsert_profile


def _profile(*, label: str = "医案文选", confidence: float = 0.2, style: str = "文字型") -> Profile:
    return Profile(
        user_id="u1",
        knowledge_base=NumericDimension(score=55, confidence=0.9),
        learning_goal=CategoricalDimension(label="应试提分", confidence=0.9),
        learning_style=CategoricalDimension(label=style, confidence=0.9),
        weak_points=WeakPointsDimension(label="功效鉴别", confidence=0.8),
        pace=NumericDimension(score=60, confidence=0.8),
        motivation=MotivationDimension(score=75, label="内驱", confidence=0.8),
        resource_preference=ResourcePreferenceDimension(label=label, confidence=confidence),
        goal=Goal(raw_text="考研", normalized="应试提分", confirmed_by_user=True, confidence=0.9),
    )


def _nodes(count: int, *, stages: int = 1) -> list[dict[str, object]]:
    return [
        {
            "node_id": f"d01.s1.p{index}",
            "name": f"知识点{index}",
            "stage": (index % stages) + 1,
            "domain_id": "d01",
            "target_mastery": 0.8,
            "resource_hint": "讲解",
        }
        for index in range(1, count + 1)
    ]


# ── 体裁选择：显式 > 画像(≥0.5) > 规模 > 默认 ──────────────────────────────


def test_explicit_preference_wins_over_everything() -> None:
    kind, reason = choose_presentation(_profile(label="知识图谱", confidence=0.9), _nodes(30), stages=5, prefer="doc")

    assert kind == "doc"
    assert "指定" in reason


def test_confident_visual_preference_beats_scale() -> None:
    kind, _reason = choose_presentation(
        _profile(label="知识图谱", confidence=0.8),
        _nodes(MINDMAP_NODE_LIMIT + 10),
        stages=MINDMAP_STAGE_LIMIT + 2,
    )

    assert kind == "mindmap"


def test_low_confidence_preference_is_ignored() -> None:
    kind, _reason = choose_presentation(_profile(label="知识图谱", confidence=0.3), _nodes(40), stages=5)

    assert kind == "doc"


def test_large_plan_falls_back_to_doc_with_a_reason() -> None:
    kind, reason = choose_presentation(_profile(), _nodes(MINDMAP_NODE_LIMIT + 1))

    assert kind == "doc"
    assert "导图" in reason


def test_small_plan_defaults_to_mindmap() -> None:
    kind, _reason = choose_presentation(_profile(), _nodes(6))

    assert kind == "mindmap"


# ── 结构：叶子必须带 node_id ─────────────────────────────────────────────


def test_mindmap_leaves_carry_node_id_and_chapters_nest(tmp_path: Path) -> None:
    """没有确认过的单元行时，层级退回知识图的祖先链——但必须是**逐点退**。

    以前整棵树退成一层（`学习路线 → 78 个点`），用户看到的是一屏并列穴位。
    """

    db_path = tmp_path / "plans.db"
    upsert_node({"id": "d01", "name": "解表药", "level": "domain"}, db_path=db_path)
    upsert_node({"id": "d01.s1", "name": "发散风寒药", "level": "subtype", "parent_id": "d01"}, db_path=db_path)
    upsert_node({"id": "d01.s1.p1", "name": "麻黄", "level": "point", "parent_id": "d01.s1"}, db_path=db_path)
    upsert_node({"id": "d01.s2", "name": "发散风热药", "level": "subtype", "parent_id": "d01"}, db_path=db_path)
    upsert_node({"id": "d01.s2.p1", "name": "薄荷", "level": "point", "parent_id": "d01.s2"}, db_path=db_path)
    from app.path.present import _catalog  # noqa: PLC0415 - 只为取同一份目录

    content = build_mindmap(
        title="我的路线",
        nodes=[
            {"node_id": "d01.s1.p1", "name": "麻黄", "target_mastery": 0.86},
            {"node_id": "d01.s2.p1", "name": "薄荷", "target_mastery": 0.7},
        ],
        catalog=_catalog(db_path),
    )

    root = content["mindmap_data"]["nodeData"]
    chapter = root["children"][0]
    section = chapter["children"][0]
    leaf = section["children"][0]
    assert chapter["topic"] == "解表药"
    assert section["topic"] == "发散风寒药"
    assert leaf["meta"]["node_id"] == "d01.s1.p1"
    assert leaf["meta"]["target_mastery"] == 0.86
    # 同章的另一节是**兄弟**，不是被摊平到章下面
    assert [child["topic"] for child in chapter["children"]] == ["发散风寒药", "发散风热药"]


def test_mindmap_hierarchy_comes_from_confirmed_units(tmp_path: Path) -> None:
    """有确认过的单元行时，章从 ``chapter_title`` 来，不从知识图祖先链来。

    总论是个篇容器：一个域里装着教材的七个章。走祖先链会把七章压成一层
    "中药学总论"，走 ``stages`` 才对。
    """

    db_path = tmp_path / "plans.db"
    upsert_node({"id": "general", "name": "中药学总论", "level": "domain"}, db_path=db_path)

    content = build_mindmap(
        title="我的路线",
        nodes=[
            {"node_id": "general.p1", "name": "四气", "unit_id": "general.s1", "subject_name": "中药学"},
            {"node_id": "general.p2", "name": "五味", "unit_id": "general.s1", "subject_name": "中药学"},
        ],
        catalog={},
        stages=[
            {
                "stage_id": "general.s1",
                "domain_id": "general",
                "subject_name": "中药学",
                "chapter_title": "第一章 中药的起源和中药学的发展",
                "unit_title": "中药的起源",
            }
        ],
    )

    subject = content["mindmap_data"]["nodeData"]["children"][0]
    chapter = subject["children"][0]
    unit = chapter["children"][0]
    assert subject["topic"] == "中药学"
    assert chapter["topic"] == "第一章 中药的起源和中药学的发展"
    # 节名不带章名：用户要的是"中药的起源"，不是"第一章·中药的起源"
    assert unit["topic"] == "中药的起源"
    assert [leaf["topic"] for leaf in unit["children"]] == ["四气", "五味"]


def test_doc_blocks_carry_meta_for_map_jump() -> None:
    """块类型必须是 ``DocBlockType`` 认的值，锚点要能回到地图。"""

    from typing import get_args  # noqa: PLC0415 - 只为读 Literal 的取值

    from app.resources.doc_schema import DocBlockType  # noqa: PLC0415 - 只想用它的枚举

    content = build_doc(
        title="我的路线",
        nodes=[{"node_id": "d01.s1.p1", "name": "麻黄", "unit_id": "d01.s1", "target_mastery": 0.8}],
        catalog={},
        stages=[
            {
                "stage_id": "d01.s1",
                "domain_id": "d01",
                "subject_name": "中药学",
                "unit_title": "发散风寒药",
                "chapter_title": "第四章 解表药",
                "point_count": 1,
                "estimated_minutes": 30,
            }
        ],
    )

    blocks = content["blocks"]
    allowed = set(get_args(DocBlockType))
    assert {block["type"] for block in blocks} <= allowed
    assert blocks[0]["type"] == "heading1"
    # 说明书按"节"组织，所以锚点是 stage_id 而不是单个 node_id
    assert any(block["meta"].get("stage_id") == "d01.s1" for block in blocks)
    assert "发散风寒药" in "".join(str(block.get("text") or "") for block in blocks)


# ── 文稿三体裁：确定性选择 + 各自的"决策信息" ────────────────────────────


def _stages() -> list[dict[str, object]]:
    return [
        {
            "stage_id": "general.s1",
            "domain_id": "general",
            "subject_name": "中药学",
            "chapter_title": "第一章 中药的起源和中药学的发展",
            "unit_title": "中药的起源",
            "point_count": 2,
            "estimated_minutes": 40,
        },
        {
            "stage_id": "general.s2",
            "domain_id": "general",
            "subject_name": "中药学",
            "chapter_title": "第一章 中药的起源和中药学的发展",
            "unit_title": "中药的产地",
            "point_count": 1,
            "estimated_minutes": 20,
        },
    ]


def _unit_nodes() -> list[dict[str, object]]:
    return [
        {"node_id": "general.p1", "name": "四气", "unit_id": "general.s1", "target_mastery": 0.8},
        {"node_id": "general.p2", "name": "五味", "unit_id": "general.s1", "target_mastery": 0.7},
        {"node_id": "general.p3", "name": "道地药材", "unit_id": "general.s2", "target_mastery": 0.7},
    ]


def test_archetype_choice_is_deterministic_and_explained() -> None:
    explicit, reason = choose_doc_archetype(_unit_nodes(), preferred="syllabus_map")
    assert explicit == "syllabus_map"
    assert "考纲对照表" in reason

    short, _ = choose_doc_archetype(_unit_nodes(), total_days=30)
    assert short == "task_cards"

    wide, _ = choose_doc_archetype([{} for _ in range(200)])
    assert wide == "syllabus_map"

    default, _ = choose_doc_archetype(_unit_nodes())
    assert default == DEFAULT_DOC_ARCHETYPE == "route_guide"

    # 同一个输入两次问，答案必须一样（用户看到换格式会以为系统不稳定）
    assert choose_doc_archetype(_unit_nodes(), total_days=30) == choose_doc_archetype(_unit_nodes(), total_days=30)


def test_route_guide_states_the_first_step_and_the_next() -> None:
    content = build_doc(
        title="我的路线",
        nodes=_unit_nodes(),
        catalog={},
        stages=_stages(),
        archetype="route_guide",
    )
    text = "\n".join(str(block.get("text") or "") for block in content["blocks"])

    assert "从「中药的起源」开始" in text
    assert "第一步" in text and "中药的产地" in text
    # 没学什么要写出来：取舍的边界是这份文档存在的理由之一
    assert "这份计划怎么改" in text


def test_task_cards_cut_the_route_into_checkable_days() -> None:
    content = build_doc(
        title="我的路线",
        nodes=_unit_nodes(),
        catalog={},
        stages=_stages(),
        archetype="task_cards",
        extras={"budget": {"daily_minutes": 60}},
    )
    blocks = content["blocks"]
    todos = [block for block in blocks if block["type"] == "todo"]

    assert todos, "任务卡里没有可勾的项，等于还是清单"
    assert all(block["props"].get("checked") is False for block in todos)
    # 每张卡收尾都要有一句自测，否则勾完就不知道记没记住
    assert any("收尾自测" in str(block.get("text") or "") for block in todos)
    assert any(block["meta"].get("node_id") == "general.p1" for block in todos)


def test_syllabus_map_is_a_checkable_table() -> None:
    content = build_doc(
        title="我的路线",
        nodes=_unit_nodes(),
        catalog={},
        stages=_stages(),
        archetype="syllabus_map",
        extras={"textbook": "《中药学》第十一版"},
    )
    tables = [block for block in content["blocks"] if block["type"] == "table"]

    assert len(tables) == 1
    props = tables[0]["props"]
    assert props["columns"] == ["章", "节", "知识点", "预计", "高频点", "目标掌握度"]
    assert [row[1] for row in props["rows"]] == ["中药的起源", "中药的产地"]
    assert all(len(row) == len(props["columns"]) for row in props["rows"])


def test_every_archetype_emits_schema_valid_blocks() -> None:
    """三种体裁都要能落成文稿——块类型不合法会在保存时 422。"""

    from typing import get_args  # noqa: PLC0415 - 只为读 Literal 的取值

    from app.resources.doc_schema import DocBlockType  # noqa: PLC0415 - 只想用它的枚举

    allowed = set(get_args(DocBlockType))
    for archetype in DOC_ARCHETYPES:
        content = build_doc(title="我的路线", nodes=_unit_nodes(), catalog={}, stages=_stages(), archetype=archetype)
        blocks = content["blocks"]
        assert {block["type"] for block in blocks} <= allowed, archetype
        assert len({block["id"] for block in blocks}) == len(blocks), f"{archetype} 有重复块 id"
        assert all(str(block.get("text") or "").strip() or block["type"] == "table" for block in blocks), archetype


# ── 落库：同版本同体裁只产一份 ────────────────────────────────────────────


def test_present_is_idempotent_per_version_and_kind(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    upsert_profile(_profile(), db_path=db_path)
    upsert_node({"id": "general", "name": "中药学总论", "level": "domain"}, db_path=db_path)
    upsert_node({"id": "d01", "name": "解表药", "level": "domain"}, db_path=db_path)
    nodes = _nodes(3)

    first = present_plan("u1", nodes=nodes, title="我的路线", version=7, prefer="mindmap", db_path=db_path)
    second = present_plan("u1", nodes=nodes, title="我的路线", version=7, prefer="mindmap", db_path=db_path)

    assert first["resource_id"] == second["resource_id"] == "plan_7_mindmap"
    assert second["reused"] is True


def test_nodes_without_name_are_filled_from_the_tree(tmp_path: Path) -> None:
    """快照里的点必须带中文名，否则导图叶子是一屏 `b01.s1.p1`。

    ``NodePlan`` 只带 id，早年确认的计划更没有 name 字段；不补的话用户打开一张
    "导图"，看到的是 78 个编号。
    """

    from app.capabilities.handlers.plan_present import _with_names

    db_path = tmp_path / "plans.db"
    upsert_node({"id": "d01.s1.p1", "name": "麻黄", "level": "point"}, db_path=db_path)
    nodes = [{"node_id": "d01.s1.p1"}, {"node_id": "gone.p1"}]

    _with_names(nodes, db_path)

    assert nodes[0]["name"] == "麻黄"
    # 查不到就退回 id：留空会让导图出现一个没有标签的节点
    assert nodes[1]["name"] == "gone.p1"


def test_open_url_matches_the_capability_declaration() -> None:
    """桥与 handler 都不许自己拼地址；这一条盯着两边别漂。"""

    artifact = PRESENT_SPEC.artifact
    assert artifact is not None
    assert artifact.template_for("mindmap").format(resource_id="r1") == open_url_for("mindmap", "r1")
    # 文稿那一支以前只活在 open_url_for 的 if/else 里，能力文档里查不到。
    assert artifact.template_for("doc").format(resource_id="r1") == open_url_for("doc", "r1")
    assert open_url_for("doc", "r1") != open_url_for("mindmap", "r1")


# ── 版本语义：拿"确认那一刻返回的版号"导出，只能得到最后一个域 ──────────────


def test_every_presentation_option_explains_itself() -> None:
    """选择器里每一种形态都要自带四条说明，且 `value` 必须还是形态名。

    ``{"value": key, **spec}`` 这种拼法最怕 spec 里也有 ``value``——"适用价值"那条
    一撞就把形态名覆盖成一段中文，前端拿它当 kind 传回去后端就认不出来了。
    """

    from app.path.present import DOC_ARCHETYPES, DOC_ARCHETYPE_SPECS, MINDMAP_SPEC

    specs = {"mindmap": MINDMAP_SPEC, **DOC_ARCHETYPE_SPECS}
    assert set(specs) == {"mindmap", *DOC_ARCHETYPES}
    for key, spec in specs.items():
        for field in ("label", "when", "contents", "form", "principle", "merit"):
            assert str(spec.get(field) or "").strip(), f"{key} 缺 {field}"
        assert "value" not in spec, f"{key} 的 spec 不许有 value 字段（会和形态名撞车）"


def test_confirmed_detail_version_exports_the_whole_route(tmp_path: Path) -> None:
    """确认明细返回的 ``version`` 是**最后一个域**的 l2 行号，不是路线版本。

    拿它去导出，导出来只有那一章的 2 个点（实测：确认 81 个点，导图 2 个）。
    整条路线必须从 active l1 认出来，逐点在按域分装的 l2 里。
    """

    import asyncio

    from app.auth.models import AuthUser
    from app.capabilities.handlers.plan_present import PlanPresentInput, handler
    from app.path.plan_store import save_plan_version

    db_path = tmp_path / "plans.db"
    upsert_profile(_profile(label="知识图谱", confidence=0.9), db_path=db_path)
    upsert_node({"id": "general", "name": "通用", "level": "domain"}, db_path=db_path)
    upsert_node({"id": "d01", "name": "解表药", "level": "domain"}, db_path=db_path)

    nodes = _nodes(5)
    # l1：取舍与阶段（nodes 按设计恒为空）
    l1_version = save_plan_version(
        user_id="u1",
        plan_kind="l1",
        payload={"stages": [{"domain_id": "d01", "name": "解表药"}], "nodes": []},
        db_path=db_path,
        status="active",
    )
    # 两个域的 l2，确认时会在同一个事务里追加；接口返回的是最后一条的版号
    for index, chunk in enumerate((nodes[:2], nodes[2:]), 1):
        last_version = save_plan_version(
            user_id="u1",
            plan_kind="l2",
            domain_id=f"d{index:02d}",
            payload={"domain_id": f"d{index:02d}", "stages": [], "nodes": chunk},
            db_path=db_path,
            status="active",
        )

    actor = AuthUser(
        user_id="u1",
        username="u1",
        display_name="u1",
        role="student",
        is_guest=False,
        created_at="2026-11-12T00:00:00+00:00",
    )

    async def run(version: int):
        return await handler(
            PlanPresentInput(present="mindmap", version=version),
            actor,
            invocation_id="inv_test",
            db_path=db_path,
        )

    # 1) 拿"最后一个域的版号"导出：只能看到那一章
    single = asyncio.run(run(last_version))
    assert single.body["node_count"] == len(nodes[2:])

    # 2) 拿 l1 版号导出：整条路线，一个点都不少
    whole = asyncio.run(run(l1_version))
    assert whole.body["node_count"] == len(nodes)

    # 3) 不给版号：也不许退回某个域的零头
    auto = asyncio.run(
        handler(PlanPresentInput(present="mindmap"), actor, invocation_id="inv_test", db_path=db_path)
    )
    assert auto.body["node_count"] == len(nodes)

    # 4) 备选形态随响应一起回去，每种都讲清四条；value 仍是形态名本身
    options = auto.body["presentation_options"]
    assert [item["value"] for item in options] == ["mindmap", "route_guide", "task_cards", "syllabus_map"]
    for item in options:
        assert item["contents"] and item["form"] and item["principle"] and item["merit"]
