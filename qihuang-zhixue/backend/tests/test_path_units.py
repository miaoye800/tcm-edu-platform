"""教材层级单元：三级梯子的判定，以及"单元集合 == 穴位集合"这条不变式。

这一组测试盯的是**粒度**，不是某一门课的数据：用户看到的是"总论顶掉前七章"
"解表药顶掉两个节"，根因却在于"规划单位被绑死在图的一层"。所以每个用例都构造
一个**结构**，而不是复述某个学科的具体目录。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.path.units import (
    KIND_CHAPTER,
    KIND_SECTION,
    KIND_SUBSECTION,
    Curriculum,
    resolve_units,
)

# ── 构造一棵最小知识树 ────────────────────────────────────────────────────


def _domain(domain_id: str, name: str, **extra: object) -> dict[str, object]:
    return {"id": domain_id, "name": name, "level": "domain", "parent_id": None, "extra": dict(extra)}


def _subtype(subtype_id: str, name: str, parent: str, *, sort_hint: int = 0, **extra: object) -> dict[str, object]:
    return {
        "id": subtype_id,
        "name": name,
        "level": "subtype",
        "parent_id": parent,
        "sort_hint": sort_hint,
        "extra": dict(extra),
    }


def _point(point_id: str, name: str, parent: str, *, sort_hint: int = 0) -> dict[str, object]:
    return {"id": point_id, "name": name, "level": "point", "parent_id": parent, "sort_hint": sort_hint}


def _syllabus(tmp_path: Path, subject: str, chapters: list[dict[str, object]]) -> Path:
    """写一份临时教材大纲，`syllabus_dir` 指过去。"""

    path = tmp_path / f"{subject}.json"
    path.write_text(
        json.dumps({"subject": subject, "title": subject, "textbook": {}, "chapters": chapters}, ensure_ascii=False),
        encoding="utf-8",
    )
    return tmp_path


def _chapter(no: int, title: str, sections: list[dict[str, object]], *, part: str = "各论") -> dict[str, object]:
    return {"no": no, "book_no": no, "part": part, "title": title, "sections": sections}


def _section(no: int, title: str, *, virtual: bool = False, subsections: tuple[str, ...] = ()) -> dict[str, object]:
    body: dict[str, object] = {"no": no, "title": title, "kind": "concept", "virtual": virtual, "entries": []}
    if subsections:
        body["subsections"] = list(subsections)
    return body


# ── 梯子：有小节用小节、无小节但有节用节、都没有才用章 ────────────────────


def test_sections_win_over_the_chapter(tmp_path: Path) -> None:
    """图里建了节、教材也分节：单位是节，不是章。"""

    syllabus = _syllabus(
        tmp_path,
        "herb",
        [_chapter(8, "解表药", [_section(1, "发散风寒药"), _section(2, "发散风热药")])],
    )
    nodes = [
        _domain("d01", "解表药"),
        _subtype("d01.s1", "发散风寒药", "d01", sort_hint=1),
        _subtype("d01.s2", "发散风热药", "d01", sort_hint=2),
        _point("d01.s1.p1", "麻黄", "d01.s1"),
        _point("d01.s2.p1", "薄荷", "d01.s2"),
    ]

    units = resolve_units(nodes, "d01", syllabus_dir=syllabus)

    assert [unit.title for unit in units] == ["发散风寒药", "发散风热药"]
    assert {unit.kind for unit in units} == {KIND_SECTION}
    # 标题不拼章名：章的信息在 chapter_title 里
    assert all(unit.chapter_title == "解表药" for unit in units)
    assert all("解表药" not in unit.title for unit in units)


def test_a_chapter_without_real_sections_uses_the_chapter_itself(tmp_path: Path) -> None:
    """退化壳：章下只有一个同名节（教材不分节）→ 单位是章，名字用章名。"""

    syllabus = _syllabus(
        tmp_path,
        "herb",
        [_chapter(5, "化湿药", [_section(1, "化湿药", virtual=True)])],
    )
    nodes = [
        _domain("d05", "化湿药"),
        _subtype("d05.s1", "化湿药", "d05", sort_hint=1),
        _point("d05.s1.p1", "藿香", "d05.s1"),
    ]

    units = resolve_units(nodes, "d05", syllabus_dir=syllabus)

    assert len(units) == 1
    assert units[0].kind == KIND_CHAPTER
    assert units[0].title == "化湿药"
    assert units[0].virtual is True
    # 单单位域沿用域 id：历史草稿与已确认路线都拿它当行键
    assert units[0].id == "d05"


def test_subsections_win_over_sections_when_the_outline_declares_them(tmp_path: Path) -> None:
    """教材大纲一旦补上小节，单位自动落到小节，不用改代码。"""

    syllabus = _syllabus(
        tmp_path,
        "internal",
        [
            _chapter(
                1,
                "肺系病证",
                [_section(1, "感冒", subsections=("风寒束表证", "风热犯表证", "暑湿伤表证"))],
            )
        ],
    )
    nodes = [
        _domain("n01", "肺系病证"),
        _subtype("n01.s1", "感冒", "n01", sort_hint=1),
        _point("n01.s1.p1", "风寒束表证", "n01.s1", sort_hint=1),
        _point("n01.s1.p2", "风热犯表证", "n01.s1", sort_hint=2),
        _point("n01.s1.p3", "暑湿伤表证", "n01.s1", sort_hint=3),
    ]

    units = resolve_units(nodes, "n01", syllabus_dir=syllabus)

    assert [unit.title for unit in units] == ["风寒束表证", "风热犯表证", "暑湿伤表证"]
    assert {unit.kind for unit in units} == {KIND_SUBSECTION}
    assert all(unit.chapter_title == "肺系病证" for unit in units)


def test_unmatched_points_are_kept_with_the_last_subsection(tmp_path: Path) -> None:
    """小节匹配不上的知识点不能丢：宁可多带一个，也不要用户确认完发现少了。"""

    syllabus = _syllabus(
        tmp_path,
        "internal",
        [_chapter(1, "肺系病证", [_section(1, "感冒", subsections=("风寒束表证", "风热犯表证"))])],
    )
    nodes = [
        _domain("n01", "肺系病证"),
        _subtype("n01.s1", "感冒", "n01"),
        _point("n01.s1.p1", "风寒束表证", "n01.s1"),
        _point("n01.s1.p9", "感冒的转归", "n01.s1"),
    ]

    units = resolve_units(nodes, "n01", syllabus_dir=syllabus)

    assert sum(unit.point_count for unit in units) == 2
    assert "n01.s1.p9" in units[-1].point_ids


# ── 篇容器：总论不能顶掉七个章 ────────────────────────────────────────────


def test_a_part_container_expands_into_its_chapters(tmp_path: Path) -> None:
    """`general` 是**篇**级容器，14 个节分属教材 1–7 章。

    以前一个 `general` 就是一行"总论"，把前七章整片盖掉。现在按节上的
    `chapter_no` 还原成教材的章 → 节，并且**同章的节挨在一起**。
    """

    syllabus = _syllabus(
        tmp_path,
        "herb",
        [
            _chapter(1, "中药的起源和中药学的发展", [_section(1, "中药的起源和中药学的发展", virtual=True)], part="总论"),
            _chapter(2, "中药的产地、采集与贮藏", [_section(1, "中药的产地"), _section(2, "中药的采集")], part="总论"),
        ],
    )
    nodes = [
        _domain("general", "中药学总论"),
        _subtype("general.c01.s1", "中药的起源和中药学的发展", "general", chapter_no=1, chapter="中药的起源和中药学的发展", section_no=1),
        _subtype("general.c02.s2", "中药的采集", "general", chapter_no=2, chapter="中药的产地、采集与贮藏", section_no=2),
        _subtype("general.c02.s1", "中药的产地", "general", chapter_no=2, chapter="中药的产地、采集与贮藏", section_no=1),
        _point("general.c01.s1.p1", "神农本草经", "general.c01.s1"),
        _point("general.c02.s1.p1", "道地药材", "general.c02.s1"),
        _point("general.c02.s2.p1", "采收时节", "general.c02.s2"),
    ]

    units = resolve_units(nodes, "general", syllabus_dir=syllabus)

    assert [unit.title for unit in units] == ["中药的起源和中药学的发展", "中药的产地", "中药的采集"]
    assert [unit.chapter_title for unit in units] == ["中药的起源和中药学的发展", "中药的产地、采集与贮藏", "中药的产地、采集与贮藏"]
    assert [unit.chapter_no for unit in units] == [1, 2, 2]
    # 节序以教材为准：图里 sort_hint 全为 0，顺序仍按 1 → 2
    assert [unit.section_no for unit in units] == [1, 1, 2]


def test_section_order_prefers_the_textbook_over_sort_hint(tmp_path: Path) -> None:
    """补虚药那条链的 `sort_hint` 是乱序的（补气/补阳/补血/补阴），按教材目录排。"""

    syllabus = _syllabus(
        tmp_path,
        "herb",
        [
            _chapter(
                17,
                "补虚药",
                [_section(1, "补气药"), _section(2, "补阳药"), _section(3, "补血药"), _section(4, "补阴药")],
            )
        ],
    )
    nodes = [
        _domain("d17", "补虚药"),
        _subtype("d17.s1", "补气药", "d17", sort_hint=1),
        _subtype("d17.s2", "补阳药", "d17", sort_hint=2),
        _subtype("d17.s3", "补血药", "d17", sort_hint=4),
        _subtype("d17.s4", "补阴药", "d17", sort_hint=3),
    ] + [_point(f"{sid}.p1", f"{name}一味", sid) for sid, name in (
        ("d17.s1", "补气药"), ("d17.s2", "补阳药"), ("d17.s3", "补血药"), ("d17.s4", "补阴药"),
    )]

    units = resolve_units(nodes, "d17", syllabus_dir=syllabus)

    assert [unit.title for unit in units] == ["补气药", "补阳药", "补血药", "补阴药"]


def test_nodes_missing_from_the_outline_sink_to_the_end_of_their_domain(tmp_path: Path) -> None:
    """图里有、教材没有的遗留节不能因为章号认不出就被当成第 1 章。"""

    syllabus = _syllabus(
        tmp_path,
        "herb",
        [_chapter(2, "中药的产地、采集与贮藏", [_section(1, "中药的产地")])],
    )
    nodes = [
        _domain("general", "中药学总论"),
        _subtype("general.s1", "基本理论", "general"),
        _subtype("general.c02.s1", "中药的产地", "general", chapter_no=2, chapter="中药的产地、采集与贮藏", section_no=1),
        _point("general.s1.p1", "某条旧知识", "general.s1"),
        _point("general.c02.s1.p1", "道地药材", "general.c02.s1"),
    ]

    units = resolve_units(nodes, "general", syllabus_dir=syllabus)

    assert [unit.title for unit in units] == ["中药的产地", "基本理论"]
    assert units[-1].chapter_no == 0 and units[-1].order[1] >= 900


# ── 归位：教材有这一节、图里没建节点 ──────────────────────────────────────


def test_reconcile_moves_points_into_the_section_the_textbook_declares(tmp_path: Path) -> None:
    """归位只在"这一节还没有单元"时发生，且不从已有单元里抢点。"""

    syllabus = _syllabus(
        tmp_path,
        "herb",
        [
            _chapter(4, "中药的性能", [_section(1, "四气"), _section(3, "升降浮沉")], part="总论"),
            _chapter(6, "中药的用药禁忌", [_section(1, "配伍禁忌")], part="总论"),
        ],
    )
    nodes = [
        _domain("general", "中药学总论"),
        _subtype("general.c04.s1", "四气", "general", chapter_no=4, chapter="中药的性能", section_no=1),
        _subtype("general.s1", "基本理论", "general"),
        _point("general.c04.s1.p1", "四气的内容", "general.c04.s1"),
        _point("general.p2", "升降浮沉", "general.s1"),
        _point("general.p3", "配伍禁忌", "general.s1"),
    ]

    units = resolve_units(nodes, "general", syllabus_dir=syllabus)

    titles = [unit.title for unit in units]
    assert "基本理论" not in titles, "遗留容器把点交出去之后不该再占一行"
    assert titles == ["四气", "升降浮沉", "配伍禁忌"]
    # 归位来的单元带着教材章号，而且能被点位反查回来
    moved = next(unit for unit in units if unit.title == "配伍禁忌")
    assert (moved.chapter_no, moved.chapter_title) == (6, "中药的用药禁忌")
    assert moved.point_ids == ("general.p3",)


def test_reconcile_does_not_steal_points_from_an_existing_section(tmp_path: Path) -> None:
    """同名节已经有单元时，别处的同名知识点不许被搬过去。"""

    syllabus = _syllabus(
        tmp_path,
        "herb",
        [_chapter(8, "解表药", [_section(1, "发散风寒药")]), _chapter(9, "清热药", [_section(1, "清热泻火药")])],
    )
    nodes = [
        _domain("d01", "解表药"),
        _domain("d02", "清热药"),
        _subtype("d01.s1", "发散风寒药", "d01"),
        _subtype("d02.s1", "清热泻火药", "d02"),
        _point("d01.s1.p1", "麻黄", "d01.s1"),
        # 一个碰巧与别章节同名的知识点：不许被搬去 d01
        _point("d02.s1.p9", "发散风寒药", "d02.s1"),
        _point("d02.s1.p1", "石膏", "d02.s1"),
    ]

    units = Curriculum(nodes, syllabus_dir=syllabus).all_units()

    by_title = {(unit.domain_id, unit.title): unit for unit in units}
    assert by_title[("d02", "清热泻火药")].point_count == 2
    assert "d02.s1.p9" not in by_title[("d01", "发散风寒药")].point_ids


def test_ambiguous_section_titles_are_left_alone(tmp_path: Path) -> None:
    """整本书里出现两次的节名不做归位——宁可不归位，不要归错。"""

    syllabus = _syllabus(
        tmp_path,
        "herb",
        [
            _chapter(4, "中药的性能", [_section(1, "四气")], part="总论"),
            _chapter(9, "清热药", [_section(1, "清热泻火药")]),
            _chapter(10, "泻下药", [_section(1, "清热泻火药")]),
        ],
    )
    nodes = [
        _domain("general", "中药学总论"),
        _subtype("general.s1", "基本理论", "general"),
        _point("general.p1", "某条旧知识", "general.s1"),
    ]

    units = resolve_units(nodes, "general", syllabus_dir=syllabus)

    assert [unit.title for unit in units] == ["基本理论"]


def test_without_an_outline_the_graph_structure_decides(tmp_path: Path) -> None:
    """自定义学科没有大纲：退回只依据图结构，不抛。"""

    nodes = [
        _domain("x01", "自定义章"),
        _subtype("x01.s1", "自定义节", "x01"),
        _point("x01.s1.p1", "知识点", "x01.s1"),
    ]

    units = resolve_units(nodes, "x01", syllabus_dir=tmp_path)

    assert [unit.title for unit in units] == ["自定义节"]
    assert units[0].kind == KIND_SECTION


def test_points_are_partitioned_exactly_once(tmp_path: Path) -> None:
    """每个知识点只属于一个单元，且没有点被吞掉。"""

    syllabus = _syllabus(
        tmp_path,
        "herb",
        [
            _chapter(8, "解表药", [_section(1, "发散风寒药"), _section(2, "发散风热药")]),
            _chapter(5, "化湿药", [_section(1, "化湿药", virtual=True)]),
        ],
    )
    nodes = [
        _domain("d01", "解表药"),
        _subtype("d01.s1", "发散风寒药", "d01"),
        _subtype("d01.s2", "发散风热药", "d01"),
        _domain("d05", "化湿药"),
        _subtype("d05.s1", "化湿药", "d05"),
        _point("d01.s1.p1", "麻黄", "d01.s1"),
        _point("d01.s2.p1", "薄荷", "d01.s2"),
        _point("d05.s1.p1", "藿香", "d05.s1"),
    ]

    curriculum = Curriculum(nodes, syllabus_dir=syllabus)
    units = curriculum.all_units()
    claimed = [point_id for unit in units for point_id in unit.point_ids]

    assert sorted(claimed) == sorted(node["id"] for node in nodes if node["level"] == "point")
    assert len(claimed) == len(set(claimed))
    assert curriculum.unit_of_point("d01.s1.p1").title == "发散风寒药"


# ── 不变式：单元集合 == 穴位集合（对着真实库） ────────────────────────────

REAL_DB = Path(__file__).resolve().parents[1] / ".data" / "qihuang.db"


@pytest.mark.skipif(not REAL_DB.exists(), reason="真实库不在本机（CI 只有夹具图）")
def test_every_acupoint_has_exactly_one_plan_unit() -> None:
    """经脉图一穴一节；规划单元必须与它同一批。

    对不上就会两头都坏：地图上的穴位点不进路线，路线里的节在地图上找不到穴位。
    实测曾经差 3 个（中药学总论那个"基本理论"容器），归位之后应当严丝合缝。
    """

    import sqlite3
    from collections import Counter

    from app.path.store import list_nodes

    curriculum = Curriculum(list_nodes(db_path=REAL_DB))
    units = curriculum.all_units()

    connection = sqlite3.connect(REAL_DB)
    try:
        rows = connection.execute("select layer_id, title, chapter_no, section_no from map_section").fetchall()
    finally:
        connection.close()

    # 逐条比多重集，不是比集合：`概述` 这种节名在基本理论里有两次（第四章、第六章），
    # 数量也必须对得上，否则"少了两个穴位"会被集合比较吃掉。
    assert Counter((unit.subject_id, unit.title) for unit in units) == Counter((layer, title) for layer, title, _c, _s in rows)
    # 一穴一节：地图上的每个穴位都得有知识点，否则点亮不了
    assert all(unit.point_count > 0 for unit in units)
    # 重名的节必须分属不同的章，界面靠分组区分，而不是靠改名字。
    # `绪论` 在三门课里各出现一次，那不算重名——按学科分组比。
    by_title: dict[tuple[str, str], list[int]] = {}
    for layer, title, chapter_no, _section_no in rows:
        by_title.setdefault((layer, title), []).append(chapter_no)
    for (layer, title), chapter_nos in by_title.items():
        if len(chapter_nos) > 1:
            assert len(set(chapter_nos)) == len(chapter_nos), f"{layer} 的重名节「{title}」落在同一章里，界面无法区分"
