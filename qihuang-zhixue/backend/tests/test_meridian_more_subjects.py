"""第四到第六门学科（中医诊断学 / 针灸学 / 中医内科学）接入时才会暴露的问题。

前三门都是「药 / 方」，结构长一个样。这三门各自踩到一个新坑：

1. **针灸学各论：一节就是一条经**。章级归经表在这里是错的——第三章 15 节如果
   整章落在一条经上，十四经里十三条会空着。必须支持 ``SECTION_HINTS`` 逐节指定，
   并且章级表仍然要有（作兜底 + 缺章检查）。
2. **绪论 / 总论的章号是 0**。以前写 ``book_no or chapter_no``，0 被当假值兜成 1，
   地图上把「绪论」标成「第一章」。三本教材都没有「第零章」，这个 0 必须留住。
3. **同名的「绪论」跨学科撞节点**。知识点按名字回查旧 id，两门课都有「绪论」，
   后建的会把前面那门顶掉——地图上看不出来，进度与错题却串了学科。
"""

from __future__ import annotations

import json

import pytest

from app.meridian import assign as assign_mod
from app.meridian import hints, seed, store, syllabus

NEW_SUBJECTS = ("diagnosis", "acup", "internal")


@pytest.fixture(scope="module")
def mdb(tmp_path_factory: pytest.TempPathFactory) -> object:
    db = tmp_path_factory.mktemp("more") / "meridian.db"
    store.init_db(db)
    seed.seed_catalog(db)
    return db


def _doc(subject: str) -> dict:
    return syllabus.load(subject)


# --------------------------------------------------------------------------- 目录


@pytest.mark.parametrize(
    ("subject", "chapters", "sections", "title"),
    [
        ("diagnosis", 10, 28, "中医诊断学"),
        ("acup", 12, 32, "针灸学"),
        ("internal", 9, 54, "中医内科学"),
    ],
)
def test_chapter_and_section_counts_follow_the_textbook(
    subject: str, chapters: int, sections: int, title: str
) -> None:
    """章/节数直接对教材目录。数字写死是**有意的**：目录被改错时要有人被绊一下。"""

    doc = _doc(subject)
    assert doc["textbook"]["title"] == title
    assert len(doc["chapters"]) == chapters
    assert sum(len(c["sections"]) for c in doc["chapters"]) == sections


def test_preface_and_general_chapters_keep_book_no_zero() -> None:
    """绪论 / 总论在书上没有章号，只能存 0；被兜底成 1 就是把整章号推错一格。"""

    assert _doc("diagnosis")["chapters"][0]["book_no"] == 0
    assert _doc("acup")["chapters"][0]["book_no"] == 0
    assert _doc("internal")["chapters"][0]["book_no"] == 0
    # 正文从 1 起，且篇内连续
    assert [c["book_no"] for c in _doc("internal")["chapters"][1:]] == list(range(1, 9))
    assert [c["book_no"] for c in _doc("acup")["chapters"][1:4]] == [1, 2, 3]


def test_unsectioned_chapters_are_marked_virtual_not_given_fake_sections() -> None:
    """教材只印章名的章，只能有一个 ``virtual`` 节——不许造「第一节 毫针刺法」。"""

    acup = _doc("acup")
    # 中篇 2 章 + 下篇 2 章 + 附篇 4 章，全部不分节
    unsectioned = [c for c in acup["chapters"] if c["title"] in {
        "刺灸法总论", "刺灸法各论", "针灸治疗总论", "针灸治疗各论",
        "针灸研究进展", "古代人体部位释义", "子午流注针法与灵龟八法", "针灸歌赋选",
    }]
    assert len(unsectioned) == 8
    for ch in unsectioned:
        assert len(ch["sections"]) == 1
        assert ch["sections"][0]["virtual"] is True
        assert ch["sections"][0]["title"] == ch["title"], "虚拟节只能是章名本身"

    # 绪论/总论的「一、二、三」是行文标题，按不分节处理，但内容要留在知识点里
    assert [s["title"] for s in _doc("internal")["chapters"][0]["sections"]] == ["总论"]
    assert len(_doc("internal")["chapters"][0]["sections"][0]["entries"]) == 6
    assert len(_doc("diagnosis")["chapters"][0]["sections"][0]["entries"]) == 5
    assert len(_doc("acup")["chapters"][0]["sections"][0]["entries"]) == 3


def test_internal_sections_are_the_diseases_in_book_order() -> None:
    """各论每节就是一个病证，顺序照书：肺系从感冒起。"""

    lung = next(c for c in _doc("internal")["chapters"] if c["title"] == "肺系病证")
    assert [s["title"] for s in lung["sections"]] == [
        "感冒", "咳嗽", "哮病", "喘证", "肺痈", "肺痨", "肺胀", "肺痿",
    ]


def test_acup_chapter_three_sections_are_the_meridians() -> None:
    """第三章 15 节：十二经各一节 + 奇经八脉 + 十五络脉 + 奇穴。"""

    ch = next(c for c in _doc("acup")["chapters"] if c["title"] == "经络腧穴各论")
    titles = [s["title"] for s in ch["sections"]]
    assert len(titles) == 15
    assert titles[0] == "手太阴肺经及其腧穴"
    assert titles[11] == "足厥阴肝经及其腧穴"
    assert titles[12:] == ["奇经八脉及其相关腧穴", "十五络脉及其络穴", "常用经外奇穴"]


# --------------------------------------------------------------------------- 归经


def test_every_chapter_of_new_subjects_has_a_channel() -> None:
    for subject in NEW_SUBJECTS:
        keys = [syllabus.chapter_key(c["no"]) for c in _doc(subject)["chapters"]]
        assert hints.missing_hints(subject, keys) == [], subject
        assert sorted(hints.CHANNEL_HINTS[subject]) == sorted(keys), subject


def test_section_level_hints_point_at_real_sections() -> None:
    """打错一个节 key 会让那一节静默落回章级经脉，地图上看不出来。"""

    for subject in NEW_SUBJECTS:
        keys = [
            syllabus.section_key(c["no"], s["no"])
            for c in _doc(subject)["chapters"]
            for s in c["sections"]
        ]
        assert hints.orphan_section_hints(subject, keys) == [], subject


def test_acup_named_meridian_sections_land_on_their_own_channel(mdb: object) -> None:
    """**这是本次接入的核心断言**：一节一条经，不能被章级兜底抹平。"""

    proposal = syllabus.to_proposal(_doc("acup"), subject="acup", db_path=mdb)  # type: ignore[arg-type]
    result, rows = assign_mod.assign_sections(proposal, subject="acup", db_path=mdb)  # type: ignore[arg-type]
    assert result.unmapped == []

    by_section = {r["id"]: r["acupoint_id"] for r in rows}
    expected = {
        "c04.s1": "LU", "c04.s2": "LI", "c04.s3": "ST", "c04.s4": "SP",
        "c04.s5": "HT", "c04.s6": "SI", "c04.s7": "BL", "c04.s8": "KI",
        "c04.s9": "PC", "c04.s10": "TE", "c04.s11": "GB", "c04.s12": "LR",
    }
    for sec, channel in expected.items():
        ap = by_section[f"acup:{sec}"]
        assert ap.startswith(channel) and ap[len(channel)].isdigit(), f"{sec} 落到 {ap}"

    # 十二经各占一条，不是全挤在一条经上
    used = {ap.rstrip("0123456789") for sec, ap in by_section.items() if sec.startswith("acup:c04")}
    assert len(used) >= 12


def test_new_subjects_assign_onto_the_body_without_spilling(mdb: object) -> None:
    for subject in NEW_SUBJECTS:
        proposal = syllabus.to_proposal(_doc(subject), subject=subject, db_path=mdb)  # type: ignore[arg-type]
        result, rows = assign_mod.assign_sections(proposal, subject=subject, db_path=mdb)  # type: ignore[arg-type]
        assert result.unmapped == [], subject
        assert all(r["id"].startswith(subject + ":") for r in rows)
        assert result.balance.max_ratio <= 0.8, subject
        assert all(r["chapter_title"] for r in rows)
        assert all(r["point_count"] >= 1 for r in rows)


def test_six_layers_do_not_collide(mdb: object) -> None:
    seen: dict[str, set[str]] = {}
    for subject in ("herb", "formula", "basic", *NEW_SUBJECTS):
        proposal = syllabus.to_proposal(_doc(subject), subject=subject, db_path=mdb)  # type: ignore[arg-type]
        _, rows = assign_mod.assign_sections(proposal, subject=subject, db_path=mdb)  # type: ignore[arg-type]
        seen[subject] = {r["id"] for r in rows}
    for a in seen:
        for b in seen:
            if a < b:
                assert not (seen[a] & seen[b]), (a, b)


# --------------------------------------------------------------------------- 同名校验


def test_same_name_points_across_subjects_do_not_steal_each_other(tmp_path: object) -> None:
    """两门课都有「绪论」，按名字回查旧 id 时必须各归各学科。"""

    db = tmp_path / "tree.db"  # type: ignore[operator]
    store.init_db(db)
    conn = store.connect(db)
    try:
        # knowledge_node 由档案库建，地图库只种穴位名录，这里手动补上最小结构
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_node (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                level TEXT NOT NULL CHECK(level IN ('domain','subtype','point')),
                parent_id TEXT REFERENCES knowledge_node(id),
                description TEXT NOT NULL DEFAULT '',
                exam_frequency REAL NOT NULL DEFAULT 0,
                sort_hint INTEGER NOT NULL DEFAULT 0,
                extra_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        for nid, name, subject in [("b01.s1.p1", "绪论", "basic"), ("z01.s1.p1", "绪论", "diagnosis")]:
            conn.execute(
                "INSERT INTO knowledge_node (id,name,level,parent_id,description,exam_frequency,"
                "sort_hint,extra_json) VALUES (?,?,'point',NULL,'',0.6,1,?)",
                (nid, name, json.dumps({"subject": subject}, ensure_ascii=False)),
            )
        conn.commit()
    finally:
        conn.close()

    assert syllabus._tree_points(db, "basic")["绪论"] == "b01.s1.p1"  # type: ignore[arg-type]
    assert syllabus._tree_points(db, "diagnosis")["绪论"] == "z01.s1.p1"  # type: ignore[arg-type]


def test_book_no_zero_survives_proposal_conversion(mdb: object) -> None:
    """``0`` 是合法章号：以前 ``book_no or chapter_no`` 会把它变成 1。"""

    proposal = syllabus.to_proposal(_doc("internal"), subject="internal", db_path=mdb)  # type: ignore[arg-type]
    assert proposal.chapters[0].book_no == 0
    assert proposal.chapters[0].title == "总论"
    assert [c.book_no for c in proposal.chapters[1:]] == list(range(1, 9))


# --------------------------------------------------------------------------- 结构校验


@pytest.mark.parametrize("subject", NEW_SUBJECTS)
def test_new_subjects_pass_structure_validation(subject: str, mdb: object) -> None:
    import asyncio

    from app.meridian import authoring

    validated = asyncio.run(
        authoring.author_subject(subject, source="syllabus", db_path=mdb)  # type: ignore[arg-type]
    )
    assert validated.ok, [r.reason for r in validated.rejected]
