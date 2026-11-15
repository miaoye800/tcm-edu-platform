"""把教材里有、知识树里没有的药味补成 ``knowledge_node``。

为什么要补
----------
《中药学》第10版各论有 442 味，知识树只收了 205 味（46%）。地图的章节已经按教材
排好了，穴位也占上了，但点进去有一半是空的——因为那些药在树里根本不存在。
「点了穴位却没有内容」比"少画几个穴位"更伤，所以补齐。

怎么补才不出错
--------------
1. **父节点不猜**：教材的「节」与知识树 ``subtype`` 是 47:47 一一对应的（节名完全相同），
   按节名精确取父节点。对不上就报错停下，不 fallback 到"挂到章下面"。
2. **旧 id 一个不动**：已有 205 味的 id 被 ``user_path_state`` / ``mastery_events`` 引用，
   改 id 等于把学习进度抹掉。新药另起 id，旧药只改 ``sort_hint``（教材顺序）。
3. **顺序按教材**：``sort_hint`` 取该节内的教材排列序号，地图上从左到右就是书里从上到下。
   旧数据的 sort_hint 是按另一套顺序排的（会出现"柴胡"排在"葛根"后面），一并纠正。
4. **考点频率不编**：新节点 ``exam_frequency`` 取语料众数 0.60（"普通"档），
   并在 ``extra_json`` 里写明来源是默认值，不是真实考频。造一个 0.85 会让规划器
   把 237 味全当重点，那比留空更糟。

用法::

    python scripts/fill_missing_herbs.py            # 只预览，不写盘
    python scripts/fill_missing_herbs.py --apply    # 写库
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.meridian import store, syllabus  # noqa: E402

#: 新节点的默认考频。取现有 209 个知识点的众数（0.60），代表"普通考点"。
#: 真实考频要靠 ``exam_point`` 统计，这里不编。
DEFAULT_EXAM_FREQ = 0.60


def _subtype_by_name(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT id, name FROM knowledge_node WHERE level='subtype'").fetchall()
    return {str(name): str(nid) for nid, name in rows}


def _points_by_name(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    rows = conn.execute(
        "SELECT id, name, parent_id FROM knowledge_node WHERE level='point'"
    ).fetchall()
    return {str(name): (str(nid), str(par or "")) for nid, name, par in rows}


def plan(conn: sqlite3.Connection, doc: dict) -> tuple[list[dict], list[str]]:
    """算出要插什么、要改什么。纯读，不写。"""

    subs = _subtype_by_name(conn)
    points = _points_by_name(conn)
    used_ids = {str(r[0]) for r in conn.execute("SELECT id FROM knowledge_node")}

    inserts: list[dict] = []
    problems: list[str] = []

    for chapter in doc["chapters"]:
        if chapter["part"] != "各论":
            continue
        for sec in chapter["sections"]:
            # 不分节的章（virtual）也照样有药味——「化湿药」一章就是 8 味，
            # 只是教材没再分节。**别因为 virtual 就跳过**，那会漏掉一整章的药。
            if not sec["entries"]:
                continue
            subtype_id = subs.get(sec["title"])
            if not subtype_id:
                problems.append(f"第{chapter['no']}章 {sec['title']}：知识树里找不到同名 subtype")
                continue

            counter = 0
            for idx, name in enumerate(sec["entries"], start=1):
                hit = points.get(name)
                if hit:
                    # 已存在：只纠正排序，id 与父子关系一律不动
                    inserts.append(
                        {"kind": "order", "id": hit[0], "sort_hint": idx, "name": name,
                         "section": sec["title"]}
                    )
                    continue
                while True:
                    counter += 1
                    new_id = f"{subtype_id}.p{counter}"
                    if new_id not in used_ids:
                        break
                used_ids.add(new_id)
                inserts.append(
                    {
                        "kind": "insert",
                        "id": new_id,
                        "name": name,
                        "parent_id": subtype_id,
                        "sort_hint": idx,
                        "section": sec["title"],
                        "chapter_no": chapter["no"],
                        "book_chapter_no": (
                            chapter["no"]
                            if chapter.get("book_no") is None
                            else chapter["book_no"]
                        ),
                    }
                )
    return inserts, problems


def apply_changes(conn: sqlite3.Connection, changes: list[dict]) -> tuple[int, int]:
    fresh = [c for c in changes if c["kind"] == "insert"]
    order = [c for c in changes if c["kind"] == "order"]

    conn.executemany(
        """
        INSERT INTO knowledge_node
            (id, name, level, parent_id, description, exam_frequency, sort_hint, extra_json)
        VALUES (?,?,?,?,'',?,?,?)
        """,
        [
            (
                c["id"],
                c["name"],
                "point",
                c["parent_id"],
                DEFAULT_EXAM_FREQ,
                c["sort_hint"],
                json.dumps(
                    {
                        "source": "textbook_entry",
                        "textbook": "中药学(第10版)·钟赣生",
                        "chapter_no": c["chapter_no"],
                        "section": c["section"],
                        "exam_frequency_source": f"default_{DEFAULT_EXAM_FREQ}（未接入真实考频）",
                    },
                    ensure_ascii=False,
                ),
            )
            for c in fresh
        ],
    )
    # 旧节点只改 sort_hint（教材顺序），id / parent 不动
    conn.executemany(
        "UPDATE knowledge_node SET sort_hint=? WHERE id=?",
        [(c["sort_hint"], c["id"]) for c in order],
    )
    conn.commit()
    return len(fresh), len(order)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="补齐教材有、知识树没有的药味")
    ap.add_argument("--subject", default="herb", choices=["herb"])
    ap.add_argument("--db", default=None)
    ap.add_argument("--apply", action="store_true", help="写库（默认只预览）")
    args = ap.parse_args(argv)

    db = Path(args.db) if args.db else store.DB_PATH
    store.init_db(db)
    doc = syllabus.load(args.subject)

    conn = store.connect(db)
    try:
        changes, problems = plan(conn, doc)
        fresh = [c for c in changes if c["kind"] == "insert"]
        order = [c for c in changes if c["kind"] == "order"]

        total = sum(len(s["entries"]) for c in doc["chapters"] if c["part"] == "各论"
                    for s in c["sections"])
        print(f"教材各论条目 {total} 味 · 知识树已有 {len(order)} · 待补 {len(fresh)}")

        by_section: dict[str, int] = {}
        for c in fresh:
            by_section[c["section"]] = by_section.get(c["section"], 0) + 1
        if by_section:
            print("\n待补最多的节：")
            for name, n in sorted(by_section.items(), key=lambda kv: -kv[1])[:10]:
                print(f"  {name:<14} +{n}")

        if problems:
            print("\n⚠ 结构对不上，先修这些再写库：", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1

        if not args.apply:
            print("\n（预览模式，未写库。加 --apply 执行）")
            return 0

        n_ins, n_ord = apply_changes(conn, changes)
        print(f"\n已插入 {n_ins} 个知识点，纠正 {n_ord} 个排序")
        left = conn.execute("SELECT COUNT(*) FROM knowledge_node WHERE level='point'").fetchone()[0]
        print(f"知识点总数 {left}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
