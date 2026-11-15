"""第三门学科（中医基础理论）接入时才会暴露的问题。

前两门（中药学、方剂学）都是「药 / 方」——条目本身就是知识点，一章几十条。
中医基础理论不一样，它三条都不同：

1. **章少、节少**：10 章 38 节。``MIN_SECTIONS`` 原来按中药学定在 40，
   接进来会直接被判「过度裁剪」。下限必须按最短的规划教材定。
2. **绪论不是章**：教材没有「第零章」，绪论的 ``book_no`` 只能是 0，
   正文第一章的 ``book_no`` 才是 1。拿 ``book_no`` 当"顺序号"会把整章号推错一格。
3. **归经逻辑不同**：讲的是"人体的理"，不是"哪味药入哪经"。藏象取背俞、
   精气血取肾、病机取肝——理由写在 ``hints.py`` 里，不能照抄中药学那张表。
"""

from __future__ import annotations

import pytest

from app.meridian import assign as assign_mod
from app.meridian import hints, seed, store, syllabus


@pytest.fixture(scope="module")
def bdb(tmp_path_factory: pytest.TempPathFactory) -> object:
    db = tmp_path_factory.mktemp("basic") / "meridian.db"
    store.init_db(db)
    seed.seed_catalog(db)
    return db


@pytest.fixture(scope="module")
def bdoc() -> dict:
    return syllabus.load("basic")


def test_basic_textbook_identity(bdoc: dict) -> None:
    tb = bdoc["textbook"]
    assert tb["title"] == "中医基础理论"
    assert "郑洪新" in tb["editor"]
    assert "9787513269056" in tb["toc_source"]


def test_basic_chapter_and_section_counts(bdoc: dict) -> None:
    chapters = bdoc["chapters"]
    assert len(chapters) == 10
    sections = [s for c in chapters for s in c["sections"]]
    assert len(sections) == 38
    # 绪论 + 九章；只有绪论是章级虚拟节
    assert sum(1 for s in sections if s["virtual"]) == 1


def test_preface_has_no_chapter_number(bdoc: dict) -> None:
    """教材没有「第零章」：绪论 book_no=0，正文从 1 起。"""

    preface = bdoc["chapters"][0]
    assert preface["title"] == "绪论"
    assert preface["part"] == "绪论"
    assert preface["book_no"] == 0
    assert [c["book_no"] for c in bdoc["chapters"][1:]] == list(range(1, 10))


def test_basic_entries_are_textbook_subheads(bdoc: dict) -> None:
    """知识点是教材节下的「一、二、三」，不是事后拆的。"""

    sections = [s for c in bdoc["chapters"] for s in c["sections"]]
    entries = [n for s in sections for n in s["entries"]]
    assert len(entries) == 128
    assert all(entries)
    # 藏象·五脏 就是心肝脾肺肾，顺序照书
    five = next(s for s in sections if s["title"] == "五脏")
    assert five["entries"] == ["心", "肺", "脾", "肝", "肾"]


def test_basic_hints_cover_every_chapter(bdoc: dict) -> None:
    keys = [syllabus.chapter_key(c["no"]) for c in bdoc["chapters"]]
    assert hints.missing_hints("basic", keys) == []
    assert set(hints.CHANNEL_HINTS["basic"]) == set(keys)


def test_basic_assigns_onto_body_without_spilling(bdb: object) -> None:
    proposal = syllabus.to_proposal(syllabus.load("basic"), subject="basic", db_path=bdb)  # type: ignore[arg-type]
    result, rows = assign_mod.assign_sections(proposal, subject="basic", db_path=bdb)  # type: ignore[arg-type]
    assert result.unmapped == []
    assert len(rows) == 38
    assert len({r["acupoint_id"] for r in rows}) == 38
    assert result.balance.max_ratio <= 0.8
    # 每节都带着完整教材定位，地图上要能答「第几章第几节」
    assert all(r["chapter_title"] for r in rows)
    assert all(r["part"] == "绪论" or r["book_chapter_no"] >= 1 for r in rows)


def test_short_syllabus_is_not_treated_as_over_pruned(bdb: object) -> None:
    """38 节必须能通过结构校验——``MIN_SECTIONS`` 若按中药学定成 40，这门课永远造不出图。"""

    import asyncio

    from app.meridian import authoring

    validated = asyncio.run(
        authoring.author_subject("basic", source="syllabus", db_path=bdb)  # type: ignore[arg-type]
    )
    assert validated.ok, [r.reason for r in validated.rejected]


def test_three_layers_share_the_body_without_colliding(
    bdb: object,
) -> None:
    """三门学科叠在同一副人体图上：小节 id、穴位槽位都不能互相覆盖。"""

    ids: dict[str, set[str]] = {}
    for subject in ("herb", "formula", "basic"):
        proposal = syllabus.to_proposal(syllabus.load(subject), subject=subject, db_path=bdb)  # type: ignore[arg-type]
        _, rows = assign_mod.assign_sections(proposal, subject=subject, db_path=bdb)  # type: ignore[arg-type]
        assert all(r["id"].startswith(subject + ":") for r in rows)
        ids[subject] = {r["id"] for r in rows}

    assert not (ids["herb"] & ids["formula"])
    assert not (ids["herb"] & ids["basic"])
    assert not (ids["formula"] & ids["basic"])
