"""``meridian.attach``：把计划里的点贴到地图刻度上。

**这个模块以前一个测试都没有**，于是"接上真数据就崩"的缺陷一路绿灯：
``connect()`` 不开 ``row_factory``，``dict(row)`` 拿 18 列的 tuple 当键值对解，
``POST /api/path/detail/draft`` 在真实库上 500。单测里 ``map_*`` 表是空的，
循环一次都没进去，所以永远绿。

这一组用例刻意**造真数据**：一条经、一个穴、一个小节、一个刻度。宁可贵一点，
也不要再出现"测试全绿、点开就崩"。
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from app.meridian.attach import attach_nodes, attach_plan, tick_of
from app.meridian.store import init_db
from app.profile.store import connect


@pytest.fixture()
def mdb(tmp_path: Path) -> Path:
    db_path = tmp_path / "meridian.db"
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO meridian_channel(id, abbr, title, kind) VALUES (?, ?, ?, ?)",
            ("LU", "LU", "手太阴肺经", "meridian"),
        )
        conn.execute(
            "INSERT INTO meridian_acupoint(id, code, name, pinyin, channel_id, t, verified) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("LU5", "LU5", "尺泽", "chize", "LU", 0.5, 1),
        )
        conn.execute(
            "INSERT INTO map_layer(id, title, kind, graph_version, color_token, ord) VALUES (?, ?, ?, ?, ?, ?)",
            ("herb", "中药学", "container", "t", "--qh-accent", 1),
        )
        conn.execute(
            "INSERT INTO map_section(id, layer_id, chapter_key, title, acupoint_id, slot_ord, ord, "
            "chapter_no, book_chapter_no, chapter_title, part, section_no, virtual) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sec01", "herb", "ch01", "第一节 发散风寒药", "LU5", 1, 1, 1, 1, "第一章 解表药", "各论", 1, 0),
        )
        conn.execute(
            "INSERT INTO map_point_slot(node_id, section_id, tick_index) VALUES (?, ?, ?)",
            ("d01.s1.p1", "sec01", 1),
        )
        conn.execute(
            "INSERT INTO map_point_slot(node_id, section_id, tick_index) VALUES (?, ?, ?)",
            ("d01.s1.p2", "sec01", 2),
        )
        conn.execute(
            "INSERT INTO map_layer(id, title, kind, graph_version, color_token, ord) VALUES (?, ?, ?, ?, ?, ?)",
            ("formula", "方剂学", "container", "t", "--qh-positive", 2),
        )
        conn.execute(
            "INSERT INTO map_section(id, layer_id, chapter_key, title, acupoint_id, slot_ord, ord, "
            "chapter_no, book_chapter_no, chapter_title, part, section_no, virtual) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("formula:sec01", "formula", "ch01", "方剂的起源与发展", "LU5", 1, 1, 1, 1, "第一章 方剂的起源与发展", "总论", 1, 1),
        )
        conn.execute(
            "INSERT INTO map_point_slot(node_id, section_id, tick_index) VALUES (?, ?, ?)",
            ("f01.s1.p1", "formula:sec01", 1),
        )
        conn.commit()
    return db_path


def test_a_cross_subject_route_maps_every_subject(mdb: Path) -> None:
    """跨六门课的路线，每一门都得贴得上刻度。

    以前默认 ``layer_id="herb"``：确认完一整条路线（中药 + 方剂 + 针灸…），
    只有中药学的点落到穴位上，其余全进 ``unmapped``——地图上只剩零星几个穴位。
    """

    nodes = [{"node_id": "d01.s1.p1"}, {"node_id": "f01.s1.p1"}]
    result = attach_plan(nodes, db_path=mdb)

    assert set(result["mapped"]) == {"d01.s1.p1", "f01.s1.p1"}
    assert result["unmapped"] == []
    assert result["coverage"] == 1.0
    assert result["mapped"]["f01.s1.p1"]["layer_id"] == "formula"


def test_attach_nodes_reads_a_real_row(mdb: Path) -> None:
    """有数据的库上不能炸，且字段要真的取到（不是"能跑就算过"）。"""

    mapped = attach_nodes(["d01.s1.p1"], db_path=mdb)
    item = mapped["d01.s1.p1"]
    assert item["section_id"] == "sec01"
    assert item["acupoint_name"] == "尺泽"
    assert item["acupoint_code"] == "LU5"
    assert item["chapter_title"] == "第一章 解表药"
    assert item["tick_index"] == 1
    assert item["virtual"] is False


def test_attach_nodes_filters_by_layer(mdb: Path) -> None:
    assert attach_nodes(["d01.s1.p1"], layer_id="herb", db_path=mdb)
    assert attach_nodes(["d01.s1.p1"], layer_id="formula", db_path=mdb) == {}


def test_attach_plan_reports_unmapped_instead_of_dropping(mdb: Path) -> None:
    """落不到刻度的点必须出现在 ``unmapped`` 里：用户会数"我计划里 3 个点，
    地图上怎么只有 2 个"。"""

    nodes = [{"node_id": "d01.s1.p1"}, {"node_id": "d01.s1.p2"}, {"node_id": "general.p1"}]
    result = attach_plan(nodes, db_path=mdb)
    assert set(result["mapped"]) == {"d01.s1.p1", "d01.s1.p2"}
    assert result["unmapped"] == ["general.p1"]
    assert result["coverage"] == pytest.approx(2 / 3, abs=1e-4)


def test_tick_of_returns_ordinal_or_none(mdb: Path) -> None:
    assert tick_of("d01.s1.p2", db_path=mdb) == 2
    assert tick_of("general.p1", db_path=mdb) is None
