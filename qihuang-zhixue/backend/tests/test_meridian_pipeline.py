"""造图管线：教材目录 → 章 / 节 → 穴位。

这些测试守的是**可解释性**，不是"能跑"。地图上每一节落在哪个穴位，都要能答出
「因为你这一章是解表药，走肺经；肺经的前两穴已经被前面的节占了」。所以断言的重点是
确定性、连续性、以及"落不下去的时候要说话"。

结构基准是 ``data/syllabus/herb.json``（教材目录），不是 ``knowledge_node``。
后者只留了 ``test_read_graph_*`` 几条作对照——它把总论压成 1 章 1 节的那件事，
正是这一轮要修掉的问题。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.meridian import assign as assign_mod
from app.meridian import authoring, hints, seed, store, syllabus
from app.meridian.schema import ChapterProposal, SectionProposal, StructureProposal


def _seed_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "seed_knowledge_tree.py"
    spec = importlib.util.spec_from_file_location("seed_knowledge_tree", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mdb(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """带知识树 + 穴位名录的临时库。建一次给整个模块用（362 次 upsert 不便宜）。"""

    db = tmp_path_factory.mktemp("meridian") / "meridian.db"
    _seed_module().seed(db)
    store.init_db(db)
    seed.seed_catalog(db)
    return db


# --------------------------------------------------------------------------- 名录


def test_seed_catalog_has_fourteen_channels_and_362_points(mdb: Path) -> None:
    channels = store.list_channels(mdb)
    assert len(channels) == 14
    assert [c["id"] for c in channels][:3] == ["LU", "LI", "ST"]
    assert store.count_acupoints(mdb) == 362

    lu = store.list_acupoints(mdb, channel_id="LU")
    assert len(lu) == 11
    assert [a["name"] for a in lu][:3] == ["中府", "云门", "天府"]
    # t 随穴序递增，且首尾不贴 0/1——通经连线要靠它排序
    assert lu[0]["t"] < lu[-1]["t"]
    assert 0 < lu[0]["t"] and lu[-1]["t"] < 1


def test_midline_points_are_single_instance(mdb: Path) -> None:
    """中线穴（任督）只建一行，``side='M'``；体侧穴一行代表左右这一对。"""

    gv = store.list_acupoints(mdb, channel_id="GV")
    assert gv and all(a["side"] == "M" for a in gv)
    lu = store.list_acupoints(mdb, channel_id="LU")
    assert all(a["side"] == "L" for a in lu)


# --------------------------------------------------------------------------- 结构来源


def test_syllabus_proposal_maps_three_levels(mdb: Path) -> None:
    proposal = _proposal(mdb)
    assert proposal.chapters
    assert all(c.sections for c in proposal.chapters), "每章都必须有节"
    for chapter in proposal.chapters:
        for section in chapter.sections:
            assert section.key.startswith(chapter.key + ".")
            assert section.point_count == len(section.node_ids)
    assert proposal.input_hash, "目录指纹必须非空，否则提案无法交代来源"


def test_read_graph_is_kept_only_as_contrast(mdb: Path) -> None:
    """``read_graph`` 仍能跑，但它读的是知识树——章是 22 个、总论只有 1 节。

    这条不是给旧路径背书，而是把差异钉住：如果哪天有人把默认来源改回 graph，
    章数会立刻从 28 掉到 22，测试就会响。
    """

    legacy = authoring.read_graph(mdb)
    general = next(c for c in legacy.chapters if c.key == "general")
    assert len(general.sections) == 1, "知识树里总论就是 1 章 1 节，这是它做不了地图的原因"
    # 知识树的章数取决于 seed 的规模，不钉死具体值；只钉「比教材少」
    assert len(legacy.chapters) < 28

    assert len(_proposal(mdb).chapters) == 28


def test_read_graph_is_deterministic(mdb: Path) -> None:
    a = authoring.read_graph(mdb)
    b = authoring.read_graph(mdb)
    assert a.model_dump_json() == b.model_dump_json()


# --------------------------------------------------------------------------- 校验


def test_validate_rejects_over_pruning(mdb: Path, sdoc: dict) -> None:
    """只留 3 章就是"把课砍过头"，必须挡住——用户会以为系统坏了。"""

    full = _proposal(mdb)
    pruned = full.model_copy(update={"chapters": full.chapters[:3]})
    result = authoring.validate_structure(pruned, mdb, doc=sdoc)
    assert result.ok is False
    assert any("教材有此章，提案里缺失" in r.reason for r in result.rejected)
    # 被拒的章不能凭空补回来，留下的必须真的是前 3 章
    assert [c.key for c in result.proposal.chapters] == [c.key for c in full.chapters[:3]]


def test_validate_rejects_chapter_not_in_syllabus(mdb: Path, sdoc: dict) -> None:
    full = _proposal(mdb)
    ghost = ChapterProposal(
        key="c99",
        title="模型自己造的章",
        no=99,
        part="各论",
        sections=[SectionProposal(key="c99.s1", chapter_key="c99", title="伪节")],
    )
    result = authoring.validate_structure(
        full.model_copy(update={"chapters": [*full.chapters, ghost]}), mdb, doc=sdoc
    )
    assert any(r.kind == "chapter" and r.key == "c99" for r in result.rejected)
    assert all(c.key != "c99" for c in result.proposal.chapters)


def test_validate_keeps_textbook_chapter_number_and_part(mdb: Path, sdoc: dict) -> None:
    """章号与分部必须活着穿过校验。

    这两个字段一旦在校验里被丢掉，落库后 65 个小节全是 ``chapter_no=0``，
    地图上显示「第 0 章」——而校验本身是通过的，因为它只看结构没看这两列。
    """

    full = _proposal(mdb)
    result = authoring.validate_structure(full, mdb, doc=sdoc)
    assert result.ok is True
    assert [(c.no, c.part) for c in result.proposal.chapters] == [
        (c["no"], c["part"]) for c in sdoc["chapters"]
    ]
    assert all(c.no >= 1 for c in result.proposal.chapters)


def test_validate_flags_unlisted_terms(mdb: Path, sdoc: dict) -> None:
    """名称不在核定名录里的，不报错也不静默接受，进 pending_terms 等人抽检。"""

    full = _proposal(mdb)
    chapters = list(full.chapters)
    first = chapters[0]
    chapters[0] = ChapterProposal(
        key=first.key,
        title=first.title,
        no=first.no,
        part=first.part,
        sections=[
            SectionProposal(
                key=first.sections[0].key,
                chapter_key=first.key,
                title=first.sections[0].title,
                sample_terms=["不存在的虚构药"],
            ),
            *first.sections[1:],
        ],
    )
    result = authoring.validate_structure(
        full.model_copy(update={"chapters": chapters}), mdb, doc=sdoc
    )
    assert "不存在的虚构药" in result.pending_terms


# --------------------------------------------------------------------------- 分配


def _proposal(mdb: Path) -> StructureProposal:
    """以教材目录为准的结构提案。"""

    return syllabus.to_proposal(syllabus.load("herb"), db_path=mdb)


@pytest.fixture(scope="module")
def sdoc() -> dict:
    return syllabus.load("herb")


def test_assign_maps_every_section(mdb: Path) -> None:
    proposal = _proposal(mdb)
    result, rows = assign_mod.assign_sections(proposal, db_path=mdb)

    total = sum(len(c.sections) for c in proposal.chapters)
    assert len(rows) == total
    assert result.unmapped == []

    # 首版一穴一节：不允许悄悄把 3 个节塞进同一个穴位
    assert len({r["acupoint_id"] for r in rows}) == total
    assert all(r["slot_ord"] == 1 for r in rows)
    assert result.balance.triple_slots == 0


def test_assign_keeps_each_chapter_on_contiguous_points(mdb: Path) -> None:
    """一章的节必须沿经线顺序排开，否则地图上会碎成几段。"""

    proposal = _proposal(mdb)
    _result, rows = assign_mod.assign_sections(proposal, db_path=mdb)

    channel_rank = {c["id"]: i for i, c in enumerate(store.list_channels(mdb))}
    position = {
        a["id"]: (channel_rank[a["channel_id"]], a["t"]) for a in store.list_acupoints(mdb)
    }

    by_chapter: dict[str, list[str]] = {}
    for row in rows:
        by_chapter.setdefault(row["chapter_key"], []).append(row["acupoint_id"])

    for chapter_key, ids in by_chapter.items():
        seq = [position[i] for i in ids]
        assert seq == sorted(seq), f"{chapter_key} 的穴位没有沿经线顺序排列：{ids}"


def test_assign_is_deterministic(mdb: Path) -> None:
    proposal = _proposal(mdb)
    _, first = assign_mod.assign_sections(proposal, db_path=mdb)
    _, second = assign_mod.assign_sections(proposal, db_path=mdb)
    assert [r["acupoint_id"] for r in first] == [r["acupoint_id"] for r in second]


def test_balance_never_exceeds_threshold(mdb: Path) -> None:
    proposal = _proposal(mdb)
    result, _ = assign_mod.assign_sections(proposal, db_path=mdb)
    assert result.balance.max_ratio <= result.balance.threshold
    assert result.balance.over_threshold == []


def test_cursor_spills_to_next_channel_when_full(mdb: Path) -> None:
    """肺经 11 穴 × 3 槽 = 33 个位置。塞 40 节进去，多出来的必须顺着流注顺序溢到大肠经。"""

    channels = store.list_channels(mdb)
    acupoints = store.list_acupoints(mdb)
    cursor = assign_mod.ChannelCursor(channels, acupoints)

    got, spill = cursor.claim("LU", 40)
    assert len(got) == 40
    assert spill == "LI"
    assert all(ap["channel_id"] == "LU" for ap, _ in got[:33])
    assert all(ap["channel_id"] == "LI" for ap, _ in got[33:])


def test_cursor_uses_extra_slots_before_dropping(mdb: Path) -> None:
    """容量算到 3 槽：362 穴 × 3 = 1086 个位置，除非全塞满否则不该有节落不下去。"""

    channels = store.list_channels(mdb)
    acupoints = store.list_acupoints(mdb)
    cursor = assign_mod.ChannelCursor(channels, acupoints)
    got, _spill = cursor.claim("LU", 30)   # 11 穴 × 3 槽 = 33
    assert len(got) == 30
    assert max(slot for _, slot in got) == 3


# --------------------------------------------------------------------------- 落库


def test_replace_sections_roundtrip(tmp_path: Path, mdb: Path) -> None:
    proposal = _proposal(mdb)
    result, rows = assign_mod.assign_sections(proposal, layer_id="herb", db_path=mdb)
    store.upsert_layer(
        layer_id="herb", title="中药学", kind="container", graph_version="t",
        color_token="--qh-accent", ord=1, db_path=mdb,
    )
    n = store.replace_sections("herb", rows, db_path=mdb)
    assert n == len(rows)
    assert result.unmapped == []

    stored = store.load_sections_ordered("herb", mdb)
    assert [r["id"] for r in stored] == [r["id"] for r in rows]
    assert [r["ord"] for r in stored] == list(range(1, len(rows) + 1))

    stats = store.layer_stats("herb", mdb)
    assert stats["sections"] == len(rows)
    assert stats["point_slots"] == stats["points"]


def test_replace_sections_drops_stale_point_slots(tmp_path: Path, mdb: Path) -> None:
    """整层替换必须连 ``map_point_slot`` 一起重建，否则会留下指向已删小节的孤儿行。"""

    proposal = _proposal(mdb)
    _, rows = assign_mod.assign_sections(proposal, layer_id="herb", db_path=mdb)
    store.upsert_layer(
        layer_id="herb", title="中药学", kind="container", graph_version="t",
        color_token="--qh-accent", ord=1, db_path=mdb,
    )
    store.replace_sections("herb", rows, db_path=mdb)
    before = store.layer_stats("herb", mdb)["point_slots"]

    store.replace_sections("herb", rows[:3], db_path=mdb)
    after = store.layer_stats("herb", mdb)["point_slots"]
    assert after < before
    assert after == sum(len(r["node_ids"]) for r in rows[:3])


# --------------------------------------------------------------------------- 快照


def test_snapshot_is_reused_only_for_same_tree(tmp_path: Path) -> None:
    proposal = StructureProposal(subject="herb", source="llm", chapters=[], input_hash="abc123")
    path = authoring.save_cached_proposal(tmp_path / "m.db", "herb", "llm", proposal)
    assert path.exists()

    assert authoring.load_cached_proposal(tmp_path / "m.db", "herb", "llm", "abc123") is not None
    # 树一变，旧快照必须作废——拿旧结构硬套新知识点比重跑更糟
    assert authoring.load_cached_proposal(tmp_path / "m.db", "herb", "llm", "different") is None


def test_chapter_hint_covers_every_chapter(mdb: Path) -> None:
    """每章都得有归经偏好，而且**不允许兜底**。

    以前写法是 ``hint_table.get(key, 第一条经)``，漏一章就整章静默挤到肺经上，
    日志里一个字都没有。现在缺章直接抛 KeyError，所以这里断言 missing 为空。
    """

    proposal = _proposal(mdb)
    missing = hints.missing_hints("herb", [c.key for c in proposal.chapters])
    assert missing == []

    table = hints.hints_for("herb")
    # 14 条经都要有人用，否则地图上会有一半经络永远是黑的
    assert set(table.values()) == set(assign_mod._SPILL_ORDER)


def test_missing_hint_raises_instead_of_silently_stacking(mdb: Path) -> None:
    """新学科最容易漏的就是章→经表。漏了必须当场炸，不能默默全落到第一条经。"""

    full = _proposal(mdb)
    ghost = ChapterProposal(key="c99", title="表里没有的章", sections=list(full.chapters[0].sections))
    with pytest.raises(KeyError, match="归经偏好"):
        assign_mod.assign_sections(
            full.model_copy(update={"chapters": [ghost]}), layer_id="herb", db_path=mdb
        )
