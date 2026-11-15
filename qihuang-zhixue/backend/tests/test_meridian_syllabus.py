"""教材目录事实源：章 / 节 / 知识点必须与教材一致。

这一组测试守的不是代码逻辑，而是**数据的诚实性**：地图上写「第八章 解表药」
就必须真的对应教材第八章；写「第 3 节 清热解毒药」就必须是教材的第三节，
而不是按名称字符串排序碰巧排出来的位置。

历史教训：上一版从 ``knowledge_node`` 反推结构，把《中药学》总论压成
「1 章 1 节 基本理论」，教材前 7 章整章消失——而所有校验都是通过的。
所以这里要逐条钉住"教材有什么、我们有什么"。
"""

from __future__ import annotations

import re

import pytest

from app.meridian import assign as assign_mod
from app.meridian import store, syllabus


@pytest.fixture(scope="module")
def doc() -> dict:
    return syllabus.load("herb")


@pytest.fixture(scope="module")
def mdb(tmp_path_factory: pytest.TempPathFactory) -> object:
    from app.meridian import seed

    db = tmp_path_factory.mktemp("syllabus") / "meridian.db"
    store.init_db(db)
    seed.seed_catalog(db)
    return db


# --------------------------------------------------------------------------- 目录本身


def test_textbook_identity_is_recorded(doc: dict) -> None:
    """目录必须自报家门：哪本书、哪一版、谁主编。答不上来就无法核对。"""

    tb = doc["textbook"]
    assert tb["title"] == "中药学"
    assert tb["edition"] == "第10版"
    assert tb["editor"] == "钟赣生"
    assert "第一章" in tb["toc_source"] and "第二十八章" in tb["toc_source"]


def test_chapter_numbers_are_contiguous(doc: dict) -> None:
    """章号 1–28 连续。断号会让「第 N 章」的显示和书页对不上。"""

    nos = [c["no"] for c in doc["chapters"]]
    assert nos == list(range(1, 29))


def test_general_has_seven_chapters(doc: dict) -> None:
    """总论 7 章。教材第八章是解表药，所以总论只能占 1–7。"""

    general = [c for c in doc["chapters"] if c["part"] == "总论"]
    assert [c["no"] for c in general] == list(range(1, 8))
    assert [c["title"] for c in general] == [
        "中药的起源和中药学的发展",
        "中药的产地、采集与贮藏",
        "中药的炮制",
        "中药的性能",
        "中药的配伍",
        "中药的用药禁忌",
        "中药的剂量与用法",
    ]


def test_general_sections_follow_the_toc(doc: dict) -> None:
    """总论 16 节，逐条对教材目录。

    第 1 章（起源与发展）与第 5 章（配伍）在教材里不分节，只给一个虚拟节。
    """

    by_no = {c["no"]: c for c in doc["chapters"]}
    assert [s["title"] for s in by_no[4]["sections"]] == [
        "四气",
        "五味",
        "升降浮沉",
        "归经",
        "中药的毒性",
    ]
    assert [s["title"] for s in by_no[6]["sections"]] == [
        "配伍禁忌",
        "证候用药禁忌",
        "妊娠用药禁忌",
        "服药饮食禁忌",
    ]
    assert by_no[1]["sections"][0]["virtual"] is True
    assert by_no[5]["sections"][0]["virtual"] is True

    real = [s for c in doc["chapters"] for s in c["sections"] if not s["virtual"]]
    assert len(real) == 54


def test_specific_starts_at_chapter_eight(doc: dict) -> None:
    """第八章 = 解表药。这一条把「总论 7 章」钉死；若总论少一章，全盘错位。"""

    by_no = {c["no"]: c for c in doc["chapters"]}
    assert by_no[8]["title"] == "解表药"
    assert [s["title"] for s in by_no[8]["sections"]] == ["发散风寒药", "发散风热药"]
    assert by_no[28]["title"] == "拔毒化腐生肌药"


def test_section_order_follows_page_numbers_not_titles(doc: dict) -> None:
    """节序必须按书中出现顺序，不能按名称排。

    按字符串排，「清热凉血药」会跑到「清热泻火药」前面（凉 U+51C9 < 泻 U+6CF3），
    教材第一节就变成第四节，整章的学习顺序错位。
    """

    ch9 = next(c for c in doc["chapters"] if c["no"] == 9)
    assert [s["title"] for s in ch9["sections"]] == [
        "清热泻火药",
        "清热燥湿药",
        "清热解毒药",
        "清热凉血药",
        "清虚热药",
    ]
    assert [s["no"] for s in ch9["sections"]] == [1, 2, 3, 4, 5]


def test_every_chapter_has_a_section_and_virtual_is_explicit(doc: dict) -> None:
    """不分节的章给虚拟节，且必须显式标记。

    不标的话前端会显示「第十二章 化湿药 · 第一节 化湿药」——自己套自己。
    """

    for ch in doc["chapters"]:
        assert ch["sections"], f"第{ch['no']}章没有节"
        for sec in ch["sections"]:
            assert sec["title"].strip()
            if sec["virtual"]:
                assert sec["title"] == ch["title"], "虚拟节的标题就是章名"


def test_entries_are_complete_and_unique(doc: dict) -> None:
    """442 味，每味只出现一次。重复会让人在同一穴位里看到两遍同一味药。"""

    names = [n for c in doc["chapters"] for s in c["sections"] for n in s["entries"]]
    assert len(names) == 442
    assert len(set(names)) == 442


def test_no_entries_in_general(doc: dict) -> None:
    """总论是理论，没有药材条目。若总论里冒出药名，说明章节归并错位了。"""

    for ch in doc["chapters"]:
        if ch["part"] == "总论":
            for sec in ch["sections"]:
                assert sec["entries"] == [], f"总论第{ch['no']}章 {sec['title']} 混进了药材"


# --------------------------------------------------------------------------- 转成提案


def test_proposal_carries_textbook_position(mdb: object) -> None:
    """提案必须带章号与分部，否则落库后地图显示「第 0 章」。"""

    proposal = syllabus.to_proposal(syllabus.load("herb"), db_path=mdb)  # type: ignore[arg-type]
    assert proposal.source == "textbook_toc"
    assert proposal.edition == "第10版（钟赣生）"
    assert [c.no for c in proposal.chapters] == list(range(1, 29))
    assert {c.part for c in proposal.chapters} == {"总论", "各论"}


def test_point_count_matches_node_ids(mdb: object) -> None:
    proposal = syllabus.to_proposal(syllabus.load("herb"), db_path=mdb)  # type: ignore[arg-type]
    for chapter in proposal.chapters:
        for section in chapter.sections:
            assert section.point_count == len(section.node_ids)
            assert section.point_count >= 1


def test_general_nodes_keep_legacy_ids(mdb: object) -> None:
    """总论里与旧知识树同名的点要沿用旧 id。

    ``user_path_state`` 有 45 条进度指着 ``general.p1`` 这几个 id，
    换成 ``concept:*`` 等于把用户已学的东西清零。
    """

    proposal = syllabus.to_proposal(syllabus.load("herb"), db_path=mdb)  # type: ignore[arg-type]
    c04 = next(c for c in proposal.chapters if c.no == 4)
    by_title = {s.title: s.node_ids for s in c04.sections}
    # 升降浮沉在知识树里有同名节点；没有树的临时库里则应退化成 concept:
    assert all(len(v) == 1 for v in by_title.values())


def test_digest_changes_with_content(doc: dict) -> None:
    """指纹必须真的随内容变 —— 否则"目录变了要重建地图"这条约束是假的。"""

    base = syllabus.digest(doc)
    mutated = doc.copy()
    chapters = [dict(c) for c in doc["chapters"]]
    chapters[0] = dict(chapters[0], title=chapters[0]["title"] + "（改）")
    mutated["chapters"] = chapters
    assert syllabus.digest(mutated) != base


# --------------------------------------------------------------------------- 落库


def test_section_rows_carry_full_position(mdb: object) -> None:
    """落库行必须自带完整定位：第几章、章名、分部、第几节、是否虚拟。"""

    proposal = syllabus.to_proposal(syllabus.load("herb"), db_path=mdb)  # type: ignore[arg-type]
    _, rows = assign_mod.assign_sections(proposal, db_path=mdb)  # type: ignore[arg-type]
    assert len(rows) == 65

    for row in rows:
        assert row["chapter_no"] >= 1
        assert row["chapter_title"]
        assert row["part"] in {"总论", "各论"}
        assert row["section_no"] >= 1
        assert isinstance(row["virtual"], bool)

    # 抽查：解表药第一节落在肺经第一穴。
    # id 带学科前缀（``herb:c08.s1``）——``c08.s1`` 在两门学科里重名，
    # 而 map_section.id 是全局主键，不加前缀方剂学第一条就撞唯一键。
    first = next(r for r in rows if r["id"] == "herb:c08.s1")
    assert (first["chapter_no"], first["section_no"]) == (8, 1)
    assert first["book_chapter_no"] == 8  # 中药学全书连续编号，两套号相同
    assert first["title"] == "发散风寒药"


def test_every_section_lands_on_a_distinct_acupoint(mdb: object) -> None:
    """65 节 → 65 个不同穴位。首版不启用多槽，塞两个节进一个穴是分配 bug。"""

    proposal = syllabus.to_proposal(syllabus.load("herb"), db_path=mdb)  # type: ignore[arg-type]
    result, rows = assign_mod.assign_sections(proposal, db_path=mdb)  # type: ignore[arg-type]
    assert result.unmapped == []
    assert len({r["acupoint_id"] for r in rows}) == len(rows)
    assert all(r["slot_ord"] == 1 for r in rows)


def test_general_chapters_sit_on_the_conception_vessel(mdb: object) -> None:
    """总论 7 章 18 节全部落任脉，且必须是 CV1 起的连续段。

    总论是全书底座，落在一堆经里会显得零碎；任脉居中、为阴脉之海，正好。
    """

    proposal = syllabus.to_proposal(syllabus.load("herb"), db_path=mdb)  # type: ignore[arg-type]
    _, rows = assign_mod.assign_sections(proposal, db_path=mdb)  # type: ignore[arg-type]
    general = [r for r in rows if r["part"] == "总论"]
    assert len(general) == 18
    assert all(r["acupoint_id"].startswith("CV") for r in general)
    assert [r["acupoint_id"] for r in general] == [f"CV{i}" for i in range(1, 19)]


def test_toc_source_regex_is_sane(doc: dict) -> None:
    """目录来源里写的章名必须是纯中文，不能混进页码或空白（防抓取串行）。"""

    for ch in doc["chapters"]:
        assert re.search(r"[\u4e00-\u9fa5]", ch["title"]), ch
        assert "\n" not in ch["title"]


# --------------------------------------------------------------------------- 多学科


def test_every_available_subject_has_full_channel_hints() -> None:
    """**任何**已生成目录的学科，章→经表都必须覆盖全部章。

    这条不是为中药学写的，是为"下一个学科"写的。以前 ``assign`` 用
    ``hint_table.get(key, 第一条经)`` 兜底，漏章的结果是整章静默挤在肺经上，
    地图看起来"这章内容特别多"，而日志一个字都没有。现在缺章会抛错，
    但抛错发生在跑分配的那一刻；这里提前在测试里拦住。
    """

    from app.meridian import hints

    for subject in syllabus.available():
        doc = syllabus.load(subject)
        keys = [syllabus.chapter_key(ch["no"]) for ch in doc["chapters"]]
        missing = hints.missing_hints(subject, keys)
        assert missing == [], f"{subject} 缺章→经偏好：{missing}"


def test_channel_hint_table_has_no_ghost_chapters() -> None:
    """表里多出来的章号同样是 bug：``c29`` 打错成 ``c9`` 会静默顶掉第九章的落点。"""

    from app.meridian import hints

    for subject in syllabus.available():
        doc = syllabus.load(subject)
        real = {syllabus.chapter_key(ch["no"]) for ch in doc["chapters"]}
        ghost = sorted(set(hints.CHANNEL_HINTS.get(subject, {})) - real)
        assert ghost == [], f"{subject} 的章→经表里有不存在的章：{ghost}"


def test_unknown_subject_raises_with_actionable_message() -> None:
    """没目录的学科不许退回读知识树——退回去等于把"按教材分章"悄悄取消。

    用 ``surgery``（契约里压根没有的学科）而不是某门真学科：拿真学科当"未接入"的
    例子，会在它接入的那天变成一条假绿——第六门学科接入时就是这么踩到的。
    """

    from app.meridian import authoring

    with pytest.raises(NotImplementedError, match="还没有教材目录"):
        import asyncio

        asyncio.run(authoring.author_subject("surgery", source="syllabus"))
