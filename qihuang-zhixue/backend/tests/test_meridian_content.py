"""「点开穴位有没有内容」的回归：正文回填与总论补节点。

P1 把章节结构铺到穴位上之后，地图能点、点开是空的——``knowledge_node.description``
1453 个节点全是空串。这批测试守四件事：

1. 正文**只从教材条目取**，条目里没有的字段不许拿模型补一句；
2. 同名节点（``经络系统的组成`` 在树里有两个）**跳过不写**，写错比不写更糟；
3. 总论补节点**不重复建**：``性味归经`` 已被 45 条进度引用，再建一个「归经」
   会让同一处知识出现两个节点，进度看起来像丢了一半；
4. 方剂学的正文是**方歌**，解析器必须把歌诀正文和出处一起留下。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

from app.meridian import store

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _tree_db(tmp_path: Path) -> sqlite3.Connection:
    """只要 knowledge_node + map_point_slot 两张表，够跑 plan 系列。"""

    db = tmp_path / "content.db"
    store.init_db(db)
    conn = store.connect(db)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS knowledge_node (
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            level TEXT NOT NULL CHECK(level IN ('domain','subtype','point')),
            parent_id TEXT REFERENCES knowledge_node(id),
            description TEXT NOT NULL DEFAULT '',
            exam_frequency REAL NOT NULL DEFAULT 0,
            sort_hint INTEGER NOT NULL DEFAULT 0,
            extra_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    conn.commit()
    return conn


def _node(conn: sqlite3.Connection, nid: str, name: str, level: str = "point", parent: str = "") -> None:
    conn.execute(
        "INSERT INTO knowledge_node (id, name, level, parent_id) VALUES (?,?,?,?)",
        (nid, name, level, parent or None),
    )


def _entry(name: str, **fields: str) -> dict:
    return {
        "name": name,
        "chapter": "解表药",
        "chapter_no": "第八章",
        "section": "发散风寒药",
        "page": 148,
        "fields": fields,
    }


# --------------------------------------------------------------------------- 中药正文


def test_content_comes_from_textbook_fields(tmp_path: Path) -> None:
    mod = _load_script("backfill_point_content")
    conn = _tree_db(tmp_path)
    try:
        _node(conn, "d01.s1.p1", "麻黄")
        doc = {"source": "《中药学》第十版", "entries": [_entry("麻黄", 药性="辛、微苦，温。", 功效="发汗解表，宣肺平喘。")]}
        updates, problems = mod.plan_contents(conn, doc)
    finally:
        conn.close()

    assert problems == []
    assert len(updates) == 1
    row = updates[0]
    assert row["description"] == "辛、微苦，温。发汗解表，宣肺平喘。"
    meta = json.loads(row["extra_json"])
    assert meta["content"]["功效"] == "发汗解表，宣肺平喘。"
    assert meta["content_source"]["page"] == 148
    assert row["replaced_empty"] is True


def test_field_alias_is_normalized(tmp_path: Path) -> None:
    """教材里有 2 条写成「性味」而不是「药性」，归一到同一键，前端不用写两套取值。"""

    mod = _load_script("backfill_point_content")
    conn = _tree_db(tmp_path)
    try:
        _node(conn, "d01.s1.p1", "某药")
        doc = {"entries": [_entry("某药", 性味="苦，寒。", 功效="清热。")]}
        updates, _ = mod.plan_contents(conn, doc)
    finally:
        conn.close()

    assert updates, "性味 必须被识别为 药性，否则这条会只剩功效"
    assert json.loads(updates[0]["extra_json"])["content"]["药性"] == "苦，寒。"


def test_duplicate_names_are_skipped_not_guessed(tmp_path: Path) -> None:
    """两个同名节点时宁可留空——写进哪一个都可能是别人的内容。"""

    mod = _load_script("backfill_point_content")
    conn = _tree_db(tmp_path)
    try:
        _node(conn, "b01.s1.p1", "经络系统的组成")
        _node(conn, "z01.s1.p1", "经络系统的组成")
        doc = {"entries": [_entry("经络系统的组成", 药性="x。", 功效="y。")]}
        updates, problems = mod.plan_contents(conn, doc)
    finally:
        conn.close()

    assert updates == []
    assert any("同名节点" in p for p in problems)


def test_unknown_name_is_reported(tmp_path: Path) -> None:
    mod = _load_script("backfill_point_content")
    conn = _tree_db(tmp_path)
    try:
        doc = {"entries": [_entry("不存在的药", 药性="x。", 功效="y。")]}
        updates, problems = mod.plan_contents(conn, doc)
    finally:
        conn.close()

    assert updates == []
    assert any("没有同名知识点" in p for p in problems)


# --------------------------------------------------------------------------- 总论节点


def _general_doc() -> dict:
    return {
        "chapters": [
            {
                "no": 4,
                "part": "总论",
                "title": "中药的性能",
                "sections": [
                    {"no": 1, "title": "四气", "entries": []},
                    {"no": 4, "title": "归经", "entries": []},
                ],
            }
        ]
    }


def test_general_section_gets_a_real_point_node(tmp_path: Path) -> None:
    """``concept:四气`` 查不到节点，地图上就取不到名字与正文。补成真实节点。"""

    mod = _load_script("backfill_point_content")
    conn = _tree_db(tmp_path)
    try:
        _node(conn, "general", "中药学总论", level="domain")
        inserts, remap, _ = mod.plan_general_nodes(conn, _general_doc())
    finally:
        conn.close()

    point = [n for n in inserts if n["level"] == "point"]
    # 库里没有「性味归经」时，「归经」也要有自己的节点（c04.s4）
    assert [n["id"] for n in point] == ["general.c04.s1.p1", "general.c04.s4.p1"]
    assert remap["concept:四气"] == "general.c04.s1.p1"
    assert remap["concept:herb:四气"] == "general.c04.s1.p1"
    # 节点必须挂在本章的 subtype 下，不能直接挂 domain
    sub = {n["id"]: n["parent_id"] for n in inserts}
    assert sub["general.c04.s1"] == "general"
    assert sub["general.c04.s1.p1"] == "general.c04.s1"


def test_existing_point_is_reused_not_duplicated(tmp_path: Path) -> None:
    """「归经」在树里已有节点（性味归经），不许再建一个同名 point。"""

    mod = _load_script("backfill_point_content")
    conn = _tree_db(tmp_path)
    try:
        _node(conn, "general.p1", "性味归经")
        inserts, remap, _ = mod.plan_general_nodes(conn, _general_doc())
    finally:
        conn.close()

    assert [n["id"] for n in inserts if n["level"] == "point"] == ["general.c04.s1.p1"]
    assert remap["concept:归经"] == "general.p1"


# --------------------------------------------------------------------------- 方剂歌诀


def test_formula_verse_becomes_description(tmp_path: Path) -> None:
    mod = _load_script("backfill_point_content")
    conn = _tree_db(tmp_path)
    try:
        _node(conn, "f01.s1.p1", "麻黄汤")
        doc = {
            "source": {"title": "方剂歌诀", "url": "https://example.org/x.pdf"},
            "chapters": [
                {
                    "title": "解表剂",
                    "sections": [
                        {
                            "title": "辛温解表剂",
                            "entries": [
                                {
                                    "name": "麻黄汤",
                                    "importance": "掌握",
                                    "gejue": "麻黄汤中臣桂枝，杏仁甘草四般施。",
                                    "cite": "李冀.方剂学[M].2021:30.",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        updates, problems = mod.plan_formula_contents(conn, doc)
    finally:
        conn.close()

    assert problems == []
    assert updates[0]["description"].startswith("麻黄汤中臣桂枝")
    meta = json.loads(updates[0]["extra_json"])
    assert meta["content"]["考纲等级"] == "掌握"
    assert "2021" in meta["content"]["出处"]


def test_gejue_parser_keeps_verse_and_cite() -> None:
    """歌诀只取方名等于把 46 页资料当名单用；出处要能翻回原书。"""

    mod = _load_script("parse_formula_gejue")
    text = "\n".join(
        [
            "第一章 解表剂",
            "第一节 辛温解表剂",
            "麻黄汤★",
            "麻黄汤中臣桂枝，杏仁甘草四般施，",
            "发汗解表宣肺气，伤寒表实无汗宜。",
            "（李冀,左铮云.方剂学[M].北京:中国中医药出版社,2021:30.）",
        ]
    )
    chapters, orphans = mod.parse(text)
    assert orphans == []
    entry = chapters[0]["sections"][0]["entries"][0]
    assert entry["name"] == "麻黄汤"
    assert entry["verse"] == [
        "麻黄汤中臣桂枝，杏仁甘草四般施，",
        "发汗解表宣肺气，伤寒表实无汗宜。",
    ]
    assert "中国中医药出版社" in entry["cite"]
