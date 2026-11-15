"""教材核对：对账表能对着书勾，差异清单每条都能落到一个动作上。

这一组的重点是**判据**，不是文案：每条 code 看的是"教材 / 知识图 / 穴位"三份数据
里同一件事是否一致。造数据时刻意让三者不一致，再看它报不报、报得对不对。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.path.store import upsert_node
from app.path.textbook import audit, build_index, fix_map_chapters, render_markdown, summary, write_textbook
from app.path.units import outline_for
from app.profile.store import connect


def _syllabus(tmp_path: Path, subject: str, chapters: list[dict[str, object]]) -> Path:
    path = tmp_path / f"{subject}.json"
    path.write_text(
        json.dumps(
            {
                "subject": subject,
                "title": subject,
                "textbook": {"title": "测试教材", "edition": "第1版", "editor": "某人", "publisher": "某社"},
                "chapters": chapters,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def _chapter(no: int, title: str, sections: list[dict[str, object]], *, book_no: int | None = None) -> dict[str, object]:
    body: dict[str, object] = {"no": no, "part": "各论", "title": title, "sections": sections}
    if book_no is not None:
        body["book_no"] = book_no
    return body


def _section(no: int, title: str, *, virtual: bool = False) -> dict[str, object]:
    return {"no": no, "title": title, "kind": "concept", "virtual": virtual, "entries": []}


def _graph(db_path: Path, *, extra_domain: bool = False) -> None:
    """两章：第一章分两节且都有点，第二章有个空节。"""

    upsert_node({"id": "d01", "name": "解表药", "level": "domain"}, db_path=db_path)
    upsert_node({"id": "d01.s1", "name": "发散风寒药", "level": "subtype", "parent_id": "d01"}, db_path=db_path)
    upsert_node({"id": "d01.s2", "name": "发散风热药", "level": "subtype", "parent_id": "d01"}, db_path=db_path)
    upsert_node({"id": "d01.s1.p1", "name": "麻黄", "level": "point", "parent_id": "d01.s1"}, db_path=db_path)
    upsert_node({"id": "d01.s2.p1", "name": "薄荷", "level": "point", "parent_id": "d01.s2"}, db_path=db_path)
    upsert_node({"id": "d02", "name": "清热药", "level": "domain"}, db_path=db_path)
    upsert_node({"id": "d02.s1", "name": "清热泻火药", "level": "subtype", "parent_id": "d02"}, db_path=db_path)
    upsert_node({"id": "d02.s1.p1", "name": "石膏", "level": "point", "parent_id": "d02.s1"}, db_path=db_path)
    if extra_domain:
        upsert_node({"id": "d03", "name": "旧章节", "level": "domain"}, db_path=db_path)
        upsert_node({"id": "d03.s1", "name": "旧节", "level": "subtype", "parent_id": "d03"}, db_path=db_path)
        upsert_node({"id": "d03.s1.p1", "name": "旧知识点", "level": "point", "parent_id": "d03.s1"}, db_path=db_path)


def _map(db_path: Path, rows: list[tuple[str, str, str, int, int, int, int]]) -> None:
    """(section_id, title, acupoint, book_chapter_no, section_no, point_count, chapter_no)"""

    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO meridian_channel(id, abbr, title, kind) VALUES ('LU','LU','手太阴肺经','meridian')"
        )
        conn.execute(
            "INSERT INTO meridian_acupoint(id, code, name, pinyin, channel_id, t, verified) "
            "VALUES ('LU5','LU5','尺泽','chize','LU',0.5,1)"
        )
        for extra_id, extra_name in (("LU6", "孔最"), ("LU7", "列缺")):
            conn.execute(
                "INSERT INTO meridian_acupoint(id, code, name, pinyin, channel_id, t, verified) "
                "VALUES (?,?,?,?, 'LU', 0.6, 1)",
                (extra_id, extra_id, extra_name, extra_name.lower()),
            )
        conn.execute(
            "INSERT INTO map_layer(id, title, kind, graph_version, color_token, ord) "
            "VALUES ('herb','中药学','container','t','--qh-accent',1)"
        )
        for section_id, title, acupoint, book_no, section_no, count, chapter_no in rows:
            conn.execute(
                "INSERT INTO map_section(id, layer_id, chapter_key, title, acupoint_id, slot_ord, ord, "
                "chapter_no, book_chapter_no, chapter_title, part, section_no, virtual, point_count) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (section_id, "herb", "c01", title, acupoint, 1, 1, chapter_no, book_no, "解表药", "各论", section_no, 0, count),
            )
        conn.commit()


@pytest.fixture()
def lab(tmp_path: Path) -> tuple[Path, Path]:
    """返回 (db, syllabus_dir)。图、教材、穴位三者**基本一致**，各用例再各自破坏一处。"""

    db = tmp_path / "plans.db"
    from app.meridian.store import init_db

    init_db(db)
    _graph(db)
    syllabus = _syllabus(
        tmp_path,
        "herb",
        [
            _chapter(1, "解表药", [_section(1, "发散风寒药"), _section(2, "发散风热药")]),
            _chapter(2, "清热药", [_section(1, "清热泻火药")]),
        ],
    )
    _map(
        db,
        [
            ("herb:c01.s1", "发散风寒药", "LU5", 1, 1, 1, 1),
            ("herb:c01.s2", "发散风热药", "LU6", 1, 2, 1, 1),
            ("herb:c02.s1", "清热泻火药", "LU7", 2, 1, 1, 2),
        ],
    )
    with closing(connect(db)) as conn:
        conn.execute("INSERT INTO map_point_slot(node_id, section_id, tick_index) VALUES ('d01.s1.p1','herb:c01.s1',1)")
        conn.execute("INSERT INTO map_point_slot(node_id, section_id, tick_index) VALUES ('d01.s2.p1','herb:c01.s2',1)")
        conn.execute("INSERT INTO map_point_slot(node_id, section_id, tick_index) VALUES ('d02.s1.p1','herb:c02.s1',1)")
        conn.commit()
    return db, syllabus


# ── 对账表 ────────────────────────────────────────────────────────────────


def test_index_follows_the_textbook_and_carries_the_acupoint(lab: tuple[Path, Path]) -> None:
    db, syllabus = lab
    rows = build_index(db_path=db, syllabus_dir=syllabus)

    assert [(row.chapter_no, row.section_no, row.section_title) for row in rows] == [
        (1, 1, "发散风寒药"),
        (1, 2, "发散风热药"),
        (2, 1, "清热泻火药"),
    ]
    first = rows[0]
    assert first.acupoint_code == "LU5" and first.acupoint_name == "尺泽"
    assert first.point_names == ("麻黄",)
    assert first.in_textbook and first.in_graph


def test_markdown_lists_chapter_section_acupoint_and_points(lab: tuple[Path, Path]) -> None:
    db, syllabus = lab
    rows = build_index(db_path=db, syllabus_dir=syllabus)
    text = render_markdown("herb", rows, outline=outline_for("herb", directory=syllabus))

    assert "测试教材" in text and "第1版" in text
    assert "| 第 1 章 解表药 | 发散风寒药 | 尺泽 | LU5 | 1 | 麻黄 |" in text
    assert "共 **2** 章、**3** 节、**3** 个知识点" in text


def test_write_textbook_creates_one_file_per_subject(lab: tuple[Path, Path], tmp_path: Path) -> None:
    db, syllabus = lab
    out = tmp_path / "textbook"
    written = write_textbook(out, db_path=db, syllabus_dir=syllabus)

    assert [path.name for path in written] == ["herb.md"]
    assert "教材对照表" in written[0].read_text(encoding="utf-8")


# ── 差异清单 ──────────────────────────────────────────────────────────────


def test_a_clean_lab_reports_nothing(lab: tuple[Path, Path]) -> None:
    db, syllabus = lab
    assert audit(db_path=db, syllabus_dir=syllabus) == []


def test_textbook_section_without_a_graph_node_is_reported(lab: tuple[Path, Path]) -> None:
    db, syllabus = lab
    upsert_node({"id": "d02.s1.p1", "name": "石膏", "level": "point", "parent_id": "d02.s1"}, db_path=db)
    # 教材多一节，图里没建
    (syllabus / "herb.json").write_text(
        json.dumps(
            {
                "subject": "herb",
                "title": "herb",
                "textbook": {},
                "chapters": [
                    _chapter(1, "解表药", [_section(1, "发散风寒药"), _section(2, "发散风热药")]),
                    _chapter(2, "清热药", [_section(1, "清热泻火药"), _section(2, "清热燥湿药")]),
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    codes = summary(audit(db_path=db, syllabus_dir=syllabus))
    assert codes.get("section_without_unit") == 1
    assert codes.get("section_missing_on_map") == 1


def test_graph_node_without_a_textbook_section_is_reported(lab: tuple[Path, Path]) -> None:
    db, syllabus = lab
    upsert_node({"id": "d03", "name": "旧章节", "level": "domain"}, db_path=db)
    upsert_node({"id": "d03.s1", "name": "旧节", "level": "subtype", "parent_id": "d03"}, db_path=db)
    upsert_node({"id": "d03.s1.p1", "name": "旧知识点", "level": "point", "parent_id": "d03.s1"}, db_path=db)

    issues = audit(db_path=db, syllabus_dir=syllabus)
    assert any(issue.code == "unit_without_section" and "旧节" in issue.message for issue in issues)


def test_a_section_with_no_points_is_reported_as_missing(lab: tuple[Path, Path]) -> None:
    """没有知识点的节在图里等于不存在：穴位点亮不了，必须报。"""

    db, syllabus = lab
    # 把清热泻火药的唯一知识点删掉——单元解析会整节跳过它
    upsert_node({"id": "d02.s1.p1", "name": "石膏", "level": "point", "parent_id": None}, db_path=db)
    with closing(connect(db)) as conn:
        conn.execute("UPDATE knowledge_node SET parent_id = NULL WHERE id = 'd02.s1.p1'")
        conn.commit()

    codes = summary(audit(db_path=db, syllabus_dir=syllabus))
    assert codes.get("section_without_unit") == 1


def test_acupoint_chapter_mismatch_is_reported_and_fixable(lab: tuple[Path, Path]) -> None:
    """书上是第 0 章（绪论/总论），穴位表写成第 1 章：报出来，并且能改回去。"""

    db, syllabus = lab
    # 教材把第一章标成书上的第 0 章（像绪论那样），穴位表仍是 1
    (syllabus / "herb.json").write_text(
        json.dumps(
            {
                "subject": "herb",
                "title": "herb",
                "textbook": {},
                "chapters": [
                    _chapter(1, "解表药", [_section(1, "发散风寒药"), _section(2, "发散风热药")], book_no=0),
                    _chapter(2, "清热药", [_section(1, "清热泻火药")]),
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    issues = audit(db_path=db, syllabus_dir=syllabus)
    mismatches = [issue for issue in issues if issue.code == "acupoint_chapter_mismatch"]
    assert len(mismatches) == 2
    assert "第 0 章" in mismatches[0].message

    changes = fix_map_chapters(db_path=db, syllabus_dir=syllabus)
    assert len(changes) == 2
    with closing(connect(db)) as conn:
        assert {row[0] for row in conn.execute("select book_chapter_no from map_section where layer_id='herb'")} == {0, 2}
    assert not [issue for issue in audit(db_path=db, syllabus_dir=syllabus) if issue.code == "acupoint_chapter_mismatch"]


def test_dry_run_does_not_touch_the_database(lab: tuple[Path, Path]) -> None:
    db, syllabus = lab
    (syllabus / "herb.json").write_text(
        json.dumps(
            {
                "subject": "herb",
                "title": "herb",
                "textbook": {},
                "chapters": [
                    _chapter(1, "解表药", [_section(1, "发散风寒药"), _section(2, "发散风热药")], book_no=0),
                    _chapter(2, "清热药", [_section(1, "清热泻火药")]),
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    fix_map_chapters(db_path=db, syllabus_dir=syllabus, dry_run=True)

    with closing(connect(db)) as conn:
        assert {row[0] for row in conn.execute("select book_chapter_no from map_section where layer_id='herb'")} == {1, 2}


def test_point_without_a_slot_is_reported(lab: tuple[Path, Path]) -> None:
    """点上没有刻度 = 学完也点不亮。"""

    db, syllabus = lab
    with closing(connect(db)) as conn:
        conn.execute("DELETE FROM map_point_slot")
        conn.commit()

    orphans = [issue for issue in audit(db_path=db, syllabus_dir=syllabus) if issue.code == "point_without_slot"]
    assert {issue.detail["node_id"] for issue in orphans} == {"d01.s1.p1", "d01.s2.p1", "d02.s1.p1"}


def test_duplicate_section_title_in_one_chapter_is_reported(tmp_path: Path) -> None:
    db = tmp_path / "plans.db"
    from app.meridian.store import init_db

    init_db(db)
    upsert_node({"id": "d01", "name": "解表药", "level": "domain"}, db_path=db)
    upsert_node({"id": "d01.s1", "name": "概述", "level": "subtype", "parent_id": "d01"}, db_path=db)
    upsert_node({"id": "d01.s1.p1", "name": "麻黄", "level": "point", "parent_id": "d01.s1"}, db_path=db)
    syllabus = _syllabus(tmp_path, "herb", [_chapter(1, "解表药", [_section(1, "概述"), _section(2, "概述")])])
    _map(db, [("herb:c01.s1", "概述", "LU5", 1, 1, 1, 1)])

    issues = audit(db_path=db, syllabus_dir=syllabus)
    assert any(issue.code == "duplicate_section_title" for issue in issues)
