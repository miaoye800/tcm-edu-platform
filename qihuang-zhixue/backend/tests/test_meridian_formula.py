"""方剂学目录 + 多学科共存 + 补药脚本。

这组测试守的是三件在接入第二门学科时才会暴露的事：

1. **章号有两套**。方剂学的各论从「第一章」重新起算，内部必须连续成 c01–c27，
   但地图上要说得出「各论第一章 解表剂」。两套号都得存对。
2. **主键会撞**。``c01.s1`` 在两门学科里同名，而 ``map_section.id`` 是全局主键。
3. **补药不能动老 id**。已有药味的 id 被学习进度引用，改一个就是一片进度丢失。
"""

from __future__ import annotations

import sqlite3

import pytest

from app.meridian import assign as assign_mod
from app.meridian import hints, seed, store, syllabus


@pytest.fixture(scope="module")
def fdb(tmp_path_factory: pytest.TempPathFactory) -> object:
    db = tmp_path_factory.mktemp("formula") / "meridian.db"
    store.init_db(db)
    seed.seed_catalog(db)
    return db


@pytest.fixture(scope="module")
def fdoc() -> dict:
    return syllabus.load("formula")


# --------------------------------------------------------------------------- 目录


def test_formula_textbook_identity(fdoc: dict) -> None:
    tb = fdoc["textbook"]
    assert tb["title"] == "方剂学"
    assert "李冀" in tb["editor"]
    assert "9787513268714" in tb["toc_source"]


def test_formula_chapter_counts(fdoc: dict) -> None:
    chapters = fdoc["chapters"]
    assert len(chapters) == 27
    assert sum(1 for c in chapters if c["part"] == "总论") == 6
    assert sum(1 for c in chapters if c["part"] == "各论") == 21


def test_formula_book_no_restarts_per_part(fdoc: dict) -> None:
    """各论的书上章号从 1 重新起算——这是这本书最容易搞错的地方。"""

    general = [c["book_no"] for c in fdoc["chapters"] if c["part"] == "总论"]
    specific = [c["book_no"] for c in fdoc["chapters"] if c["part"] == "各论"]
    assert general == [1, 2, 3, 4, 5, 6]
    assert specific == list(range(1, 22))

    # 内部编号必须一路连续，否则 chapter_key 会撞
    assert [c["no"] for c in fdoc["chapters"]] == list(range(1, 28))


def test_formula_section_total(fdoc: dict) -> None:
    """总论 7 节 + 各论 69 节 = 76，与教材目录一致。

    各论原来是 68 节，补上了漏掉的「祛暑清热剂」（清络饮）——首版目录把它整节丢了，
    五个祛暑方少一个，地图上「第 5 章 4 节」看着正常，实际少一节。
    """

    sections = [s for c in fdoc["chapters"] for s in c["sections"]]
    assert len(sections) == 76
    general = [s for c in fdoc["chapters"] if c["part"] == "总论" for s in c["sections"]]
    assert len(general) == 7


def test_unsectioned_chapters_are_marked_virtual(fdoc: dict) -> None:
    """总论第三—六章教材里只有"一、二、三"，没有"第一节"，不许造。"""

    virtual = [
        (c["book_no"], c["title"])
        for c in fdoc["chapters"]
        for s in c["sections"]
        if s["virtual"]
    ]
    titles = {t for _, t in virtual}
    assert "方剂的分类" in titles
    assert "方剂的剂型" in titles
    assert "驱虫剂" in titles
    assert len(virtual) == 7


def test_formula_entries_trace_back_to_a_document(fdoc: dict) -> None:
    """方名只能来自**章与节同时给出**的文档，不能是"为了填满而列"的一张表。

    现在接的是《方剂歌诀》教学文档（方歌出处标注为李冀·左铮云《方剂学》2021）。
    这条测试守的是"来源可追溯"：条目数对得上解析结果、每个方名都带考纲等级、
    出处 URL 与 PDF 指纹都在。换成任何别的来源，这些字段就得重新给出来。
    """

    sections = [s for c in fdoc["chapters"] for s in c["sections"]]
    entries = [n for s in sections for n in s["entries"]]
    assert len(entries) == 233
    assert len(set(entries)) == len(entries), "同一首方不该出现在两节"

    importance = {name: lv for s in sections for name, lv in (s.get("importance") or {}).items()}
    assert set(importance) == set(entries), "每个方名都要有考纲等级（掌握/熟悉/了解）"
    assert set(importance.values()) <= {"掌握", "熟悉", "了解"}

    src = fdoc["textbook"]["entries_source"]
    assert src["url"].startswith("https://")
    assert len(src["sha256"]) == 64
    assert "李冀" in src["cites"]

    # 各论每一节都要有方名（含驱虫剂/涌吐剂这种不分节的章）；总论本来就没有方，
    # 那一层是概念点，不许为了"看起来有内容"往总论里塞方名。
    specific = [s for c in fdoc["chapters"] if c["part"] == "各论" for s in c["sections"]]
    general = [s for c in fdoc["chapters"] if c["part"] == "总论" for s in c["sections"]]
    assert all(s["entries"] for s in specific)
    assert all(not s["entries"] for s in general)


def test_formula_shaoyang_section_keeps_its_four_subsections(fdoc: dict) -> None:
    """祛暑剂 4 节：解表/清热/利湿/益气。首版少「祛暑清热剂」，清络饮就无家可归。"""

    ch = next(c for c in fdoc["chapters"] if c["title"] == "祛暑剂")
    assert [s["title"] for s in ch["sections"]] == [
        "祛暑解表剂",
        "祛暑清热剂",
        "祛暑利湿剂",
        "祛暑益气剂",
    ]
    assert [n for s in ch["sections"] for n in s["entries"]] == [
        "香薷散",
        "清络饮",
        "六一散",
        "桂苓甘露散",
        "清暑益气汤",
    ]


# --------------------------------------------------------------------------- 分配


def test_formula_channel_hints_cover_all_chapters(fdoc: dict) -> None:
    keys = [syllabus.chapter_key(c["no"]) for c in fdoc["chapters"]]
    assert hints.missing_hints("formula", keys) == []
    assert set(hints.CHANNEL_HINTS["formula"]) == set(keys)
    # 14 条经都要用到，否则一半经络永远是黑的
    assert len(set(hints.CHANNEL_HINTS["formula"].values())) == 14


def test_formula_assigns_every_section_to_its_own_acupoint(fdb: object) -> None:
    proposal = syllabus.to_proposal(syllabus.load("formula"), subject="formula", db_path=fdb)  # type: ignore[arg-type]
    result, rows = assign_mod.assign_sections(proposal, subject="formula", db_path=fdb)  # type: ignore[arg-type]
    assert result.unmapped == []
    assert len(rows) == 76
    assert len({r["acupoint_id"] for r in rows}) == 76
    assert result.balance.max_ratio <= 0.8


def test_formula_general_chapters_sit_on_conception_vessel(fdb: object) -> None:
    proposal = syllabus.to_proposal(syllabus.load("formula"), subject="formula", db_path=fdb)  # type: ignore[arg-type]
    _, rows = assign_mod.assign_sections(proposal, subject="formula", db_path=fdb)  # type: ignore[arg-type]
    general = [r for r in rows if r["part"] == "总论"]
    assert [r["acupoint_id"] for r in general] == [f"CV{i}" for i in range(1, 8)]


def test_two_layers_do_not_collide_on_section_id(fdb: object) -> None:
    """中药学先落库后，方剂学第一条曾撞 ``UNIQUE constraint failed: map_section.id``。

    根因是小节 id 用了 ``c01.s1`` 这种学科内序号。现在带学科前缀，两层能共存。
    """

    for subject in ("herb", "formula"):
        proposal = syllabus.to_proposal(syllabus.load(subject), subject=subject, db_path=fdb)  # type: ignore[arg-type]
        _, rows = assign_mod.assign_sections(proposal, subject=subject, db_path=fdb)  # type: ignore[arg-type]
        assert all(r["id"].startswith(subject + ":") for r in rows)

    herb = assign_mod.assign_sections(
        syllabus.to_proposal(syllabus.load("herb"), subject="herb", db_path=fdb),  # type: ignore[arg-type]
        layer_id="herb",
        db_path=fdb,  # type: ignore[arg-type]
    )[1]
    form = assign_mod.assign_sections(
        syllabus.to_proposal(syllabus.load("formula"), subject="formula", db_path=fdb),  # type: ignore[arg-type]
        layer_id="formula",
        db_path=fdb,  # type: ignore[arg-type]
    )[1]
    assert not ({r["id"] for r in herb} & {r["id"] for r in form})


# --------------------------------------------------------------------------- 补药脚本


def _make_tree(db_path: object) -> None:
    conn = store.connect(db_path)  # type: ignore[arg-type]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_node (
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            level TEXT NOT NULL CHECK(level IN ('domain','subtype','point')),
            parent_id TEXT REFERENCES knowledge_node(id),
            description TEXT NOT NULL DEFAULT '',
            exam_frequency REAL NOT NULL DEFAULT 0 CHECK(exam_frequency >= 0 AND exam_frequency <= 1),
            sort_hint INTEGER NOT NULL DEFAULT 0,
            extra_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute("INSERT INTO knowledge_node (id,name,level) VALUES ('d01','解表药','domain')")
    conn.execute("INSERT INTO knowledge_node (id,name,level,parent_id) VALUES ('d01.s1','发散风寒药','subtype','d01')")
    # 已有药味：id 故意不按教材位置（p2 在前），验证脚本只改 sort_hint 不改 id
    conn.execute("INSERT INTO knowledge_node (id,name,level,parent_id,sort_hint) VALUES ('d01.s1.p2','麻黄','point','d01.s1',1)")
    conn.commit()
    conn.close()


def test_fill_only_adds_and_never_rewrites_ids(tmp_path: object) -> None:
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "fill_missing_herbs.py"
    spec = importlib.util.spec_from_file_location("fill_missing_herbs", script)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    db = tmp_path / "t.db"  # type: ignore[operator]
    _make_tree(db)

    doc = {
        "chapters": [
            {
                "no": 8,
                "book_no": 8,
                "part": "各论",
                "title": "解表药",
                "sections": [
                    {
                        "no": 1,
                        "title": "发散风寒药",
                        "virtual": False,
                        "entries": ["麻黄", "桂枝", "紫苏叶"],
                    }
                ],
            }
        ]
    }

    conn = store.connect(db)
    try:
        changes, problems = mod.plan(conn, doc)
        assert problems == []
        inserts = [c for c in changes if c["kind"] == "insert"]
        order = [c for c in changes if c["kind"] == "order"]
        # 麻黄已有 → 只调顺序；桂枝/紫苏叶 → 新增
        assert [c["name"] for c in inserts] == ["桂枝", "紫苏叶"]
        assert [(c["id"], c["sort_hint"]) for c in order] == [("d01.s1.p2", 1)]
        assert all(c["id"] != "d01.s1.p2" for c in inserts), "不得复用已有 id"

        mod.apply_changes(conn, changes)
        rows = conn.execute(
            "SELECT name, sort_hint FROM knowledge_node WHERE level='point' ORDER BY sort_hint"
        ).fetchall()
        assert [r[0] for r in rows] == ["麻黄", "桂枝", "紫苏叶"]
    finally:
        conn.close()


def test_local_db_covers_every_textbook_entry() -> None:
    """本机主库应当 442 味全部有真实节点（不是 ``herb:xxx`` 占位）。

    CI 上没有 ``.data/qihuang.db`` 就跳过——这条守的是**数据现状**，不是代码逻辑。
    """

    db = store.DB_PATH
    if not db.exists():
        pytest.skip("本机无主库")
    conn = sqlite3.connect(str(db))
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM knowledge_node WHERE level='point'")}
    except sqlite3.OperationalError:
        pytest.skip("主库还没有知识树")
    finally:
        conn.close()

    doc = syllabus.load("herb")
    missing = [
        n
        for c in doc["chapters"]
        for s in c["sections"]
        for n in s["entries"]
        if n not in have
    ]
    assert missing == [], f"知识树缺 {len(missing)} 味：{missing[:10]}"


def test_book_no_survives_validation(fdb: object) -> None:
    """校验不能把 ``book_no`` 弄丢——这里踩过一次。

    ``_validate_against_syllabus`` 原来用 ``ChapterProposal(...)`` 重新构造章，
    没写出来的字段就悄悄变成默认值：``book_no=0`` → 落库时退回 ``chapter_no``，
    于是「各论第一章 解表剂」在地图上显示成「各论第七章」。校验是通过的，
    因为校验只看结构。现在改用 ``model_copy``，加字段不会再重演。
    """

    import asyncio

    from app.meridian import authoring

    validated = asyncio.run(
        authoring.author_subject("formula", source="syllabus", db_path=fdb)  # type: ignore[arg-type]
    )
    assert validated.ok
    chapter = next(c for c in validated.proposal.chapters if c.title == "解表剂")
    assert (chapter.no, chapter.book_no) == (7, 1)

    _, rows = assign_mod.assign_sections(validated.proposal, subject="formula", db_path=fdb)  # type: ignore[arg-type]
    row = next(r for r in rows if r["title"] == "辛温解表剂")
    assert (row["chapter_no"], row["book_chapter_no"]) == (7, 1)


def test_herb_book_no_equals_chapter_no(fdb: object) -> None:
    """中药学全书连续编号，两套号必须相同——不同就是系数写错了。"""

    import asyncio

    from app.meridian import authoring

    validated = asyncio.run(
        authoring.author_subject("herb", source="syllabus", db_path=fdb)  # type: ignore[arg-type]
    )
    assert validated.ok
    assert all(c.no == c.book_no for c in validated.proposal.chapters)


# --------------------------------------------------------------------------- 知识树导入


def _make_empty_tree(db_path: object) -> None:
    conn = store.connect(db_path)  # type: ignore[arg-type]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_node (
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            level TEXT NOT NULL CHECK(level IN ('domain','subtype','point')),
            parent_id TEXT REFERENCES knowledge_node(id),
            description TEXT NOT NULL DEFAULT '',
            exam_frequency REAL NOT NULL DEFAULT 0 CHECK(exam_frequency >= 0 AND exam_frequency <= 1),
            sort_hint INTEGER NOT NULL DEFAULT 0,
            extra_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.commit()
    conn.close()


def _load_script(name: str):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_formula_tree_comes_from_syllabus(tmp_path: object) -> None:
    """章/节/条目三层都要落到 knowledge_node，id 带学科前缀。

    不带前缀会撞 ``d01.s1.p1``：那是中药学的麻黄，主键冲突会让整棵树建不起来。
    """

    mod = _load_script("import_subject_tree")
    db = tmp_path / "tree.db"  # type: ignore[operator]
    _make_empty_tree(db)

    conn = store.connect(db)
    try:
        inserts, problems = mod.plan(conn, syllabus.load("formula"), subject="formula", prefix="f")
        assert problems == []
        assert sum(1 for n in inserts if n["level"] == "domain") == 27
        assert sum(1 for n in inserts if n["level"] == "subtype") == 76
        # 233 首方 + 总论 7 个概念点
        assert sum(1 for n in inserts if n["level"] == "point") == 240

        mod.apply_changes(conn, inserts)
        row = conn.execute(
            "SELECT id, name, exam_frequency, parent_id FROM knowledge_node WHERE name='四君子汤'"
        ).fetchone()
        # 补益剂是各论第 8 章，内部连续编号是第 14 章（总论 6 章在前）
        assert row[0] == "f14.s1.p1"
        assert row[3] == "f14.s1"
        assert row[2] == 0.85, "掌握方应是最高考频档，映射表写在脚本里"

        # 幂等：再跑一次不应新增
        again, problems2 = mod.plan(conn, syllabus.load("formula"), subject="formula", prefix="f")
        assert again == []
        assert problems2 == []
    finally:
        conn.close()


def test_concept_ticks_are_namespaced_by_subject() -> None:
    """总论那一节的概念点 id 必须带学科：``concept:绪论`` 这种裸名两门学科都会撞。"""

    doc = {
        "textbook": {"edition": "测试", "editor": "测试"},
        "chapters": [
            {
                "no": 1,
                "book_no": 1,
                "part": "总论",
                "title": "绪论",
                "sections": [{"no": 1, "title": "绪论", "entries": [], "virtual": True}],
            }
        ],
    }
    herb = syllabus.to_proposal(doc, subject="herb", db_path=":memory:")
    form = syllabus.to_proposal(doc, subject="formula", db_path=":memory:")
    assert herb.chapters[0].sections[0].node_ids == ["concept:herb:绪论"]
    assert form.chapters[0].sections[0].node_ids == ["concept:formula:绪论"]
