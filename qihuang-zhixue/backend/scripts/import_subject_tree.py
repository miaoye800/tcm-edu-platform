"""按教材目录把一门学科的知识树建出来（domain → subtype → point）。

为什么要这一步
--------------
上一版方剂学只有「章 / 节」两层结构，每节的知识点是一个挂在 ``concept:`` 上的
占位名——点开穴位看到的是「辛温解表剂」这五个字，不是麻黄汤、桂枝汤。
地图再准，点进去没有东西也等于没有。

而知识树是**规划器 / 错题 / 题库**共用的那一份：只有它长出了方名节点，
学习计划才排得出先后，R3「方剂组成依赖」才有端点点可连。

怎么建才不出错
--------------
1. **结构只来自教材目录**（``data/syllabus/<subject>.json``），不读模型输出。
   章 → ``domain``、节 → ``subtype``、条目 → ``point``，一一对应。
2. **id 带学科前缀**（``f01.s1.p1``）。``d01.s1.p1`` 已经在中药学里用掉了，
   而 ``knowledge_node.id`` 是全局主键，不带前缀第二门学科必然撞。
3. **已有的不动**。中药学那棵树是旧脚本种的（含用户进度），这里只对
   ``--subject`` 指定的前缀做增量；同名但 id 不同不合并（合并会悄悄丢进度）。
4. **不分节的章也有内容**。驱虫剂、涌吐剂教材不分节，目录里是 ``virtual`` 节，
   条目照挂在它下面，不为了层级好看再造一层。
5. **考频不编**。条目带的 ``importance``（掌握★ / 熟悉* / 了解▲）映射成
   ``exam_frequency``，映射表写死在 ``IMPORTANCE_FREQ`` 里并注明依据；
   没有等级的走 ``DEFAULT_EXAM_FREQ``，``extra_json`` 里写明是默认值。

用法::

    python scripts/import_subject_tree.py --subject formula --dry-run
    python scripts/import_subject_tree.py --subject formula --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.meridian import store, syllabus  # noqa: E402

#: 学科 → 知识树 id 前缀。中药学历史数据已经是 ``d``（drug），不改；
#: 方剂学用 ``f``（formula），中医基础理论用 ``b``（basic），
#: 中医诊断学 ``z``（zhen）、针灸学 ``a``（acup）、中医内科学 ``n``（neike）。
#: 新学科接入时在这里加一行，不要复用已有前缀。
SUBJECT_PREFIX: dict[str, str] = {
    "herb": "d",
    "formula": "f",
    "basic": "b",
    "diagnosis": "z",
    "acup": "a",
    "internal": "n",
}

#: 考纲等级 → exam_frequency。依据：掌握方是必考点，熟悉方常考，了解方偶尔出现。
#: 这是一份**映射规则**而不是统计值，写在这里是为了以后能一眼看出数值从哪来；
#: 真实考频要等 ``exam_point`` 有足够样本后重新标定。
IMPORTANCE_FREQ: dict[str, float] = {"掌握": 0.85, "熟悉": 0.70, "了解": 0.50}

#: 没有等级信息时的默认值（与补药脚本一致：现有知识点的众数 0.60）。
DEFAULT_EXAM_FREQ = 0.60


def node_id(prefix: str, chapter_no: int, section_no: int | None = None, point_no: int | None = None) -> str:
    base = f"{prefix}{chapter_no:02d}"
    if section_no is not None:
        base = f"{base}.s{section_no}"
    if point_no is not None:
        base = f"{base}.p{point_no}"
    return base


def plan(conn: sqlite3.Connection, doc: dict[str, Any], *, subject: str, prefix: str) -> tuple[list[dict], list[str]]:
    """算出要插哪些节点。纯读，不写。"""

    existing = {
        str(row[0]): (str(row[1]), str(row[2] or ""))
        for row in conn.execute("SELECT id, name, parent_id FROM knowledge_node")
    }
    inserts: list[dict[str, Any]] = []
    problems: list[str] = []

    def add(node: dict[str, Any]) -> None:
        nid = node["id"]
        if nid in existing:
            name, parent = existing[nid]
            if name != node["name"] or parent != (node.get("parent_id") or ""):
                problems.append(
                    f"{nid} 已存在且内容不同：库里「{name}」/ 目录「{node['name']}」，"
                    "需要人工确认后再迁（脚本不覆盖）"
                )
            return
        inserts.append(node)
        existing[nid] = (node["name"], node.get("parent_id") or "")

    for chapter in doc["chapters"]:
        ch_id = node_id(prefix, chapter["no"])
        add(
            {
                "id": ch_id,
                "name": chapter["title"],
                "level": "domain",
                "parent_id": None,
                "sort_hint": int(chapter["no"]),
                "exam_frequency": DEFAULT_EXAM_FREQ,
                "extra_json": {
                    "source": "textbook_toc",
                    "subject": subject,
                    "part": chapter["part"],
                    "chapter_no": chapter["no"],
                    # 0 是合法章号（绪论/总论），不能用 or 兜底
                    "book_chapter_no": (
                        int(chapter["no"])
                        if chapter.get("book_no") is None
                        else int(chapter["book_no"])
                    ),
                },
            }
        )
        for sec in chapter["sections"]:
            sec_id = node_id(prefix, chapter["no"], sec["no"])
            add(
                {
                    "id": sec_id,
                    "name": sec["title"],
                    "level": "subtype",
                    "parent_id": ch_id,
                    "sort_hint": int(sec["no"]),
                    "exam_frequency": DEFAULT_EXAM_FREQ,
                    "extra_json": {
                        "source": "textbook_toc",
                        "subject": subject,
                        "chapter_no": chapter["no"],
                        "section_no": sec["no"],
                        "virtual": bool(sec.get("virtual")),
                    },
                }
            )
            importance = sec.get("importance") or {}
            names = list(sec["entries"])
            if not names:
                # 总论那一节本身就是知识点（「方剂的分类」没有子条目）。
                names = [sec["title"]]
            for idx, name in enumerate(names, start=1):
                level = importance.get(name)
                freq = IMPORTANCE_FREQ.get(level or "", DEFAULT_EXAM_FREQ)
                add(
                    {
                        "id": node_id(prefix, chapter["no"], sec["no"], idx),
                        "name": name,
                        "level": "point",
                        "parent_id": sec_id,
                        "sort_hint": idx,
                        "exam_frequency": freq,
                        "extra_json": {
                            "source": "textbook_entry" if sec["entries"] else "textbook_concept",
                            "subject": subject,
                            "chapter_no": chapter["no"],
                            "chapter": chapter["title"],
                            "section": sec["title"],
                            "importance": level,
                            "exam_frequency_source": (
                                f"importance={level}" if level else f"default_{DEFAULT_EXAM_FREQ}"
                            ),
                        },
                    }
                )
    return inserts, problems


def apply_changes(conn: sqlite3.Connection, inserts: list[dict[str, Any]]) -> int:
    conn.executemany(
        """
        INSERT INTO knowledge_node
            (id, name, level, parent_id, description, exam_frequency, sort_hint, extra_json)
        VALUES (?,?,?,?,'',?,?,?)
        """,
        [
            (
                n["id"],
                n["name"],
                n["level"],
                n["parent_id"],
                float(n["exam_frequency"]),
                int(n["sort_hint"]),
                json.dumps(n["extra_json"], ensure_ascii=False),
            )
            for n in inserts
        ],
    )
    conn.commit()
    return len(inserts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按教材目录建学科知识树")
    ap.add_argument("--subject", default="formula", choices=sorted(SUBJECT_PREFIX))
    ap.add_argument("--db", default=None)
    ap.add_argument("--apply", action="store_true", help="写库（默认只预览）")
    args = ap.parse_args(argv)

    db = Path(args.db) if args.db else store.DB_PATH
    store.init_db(db)
    doc = syllabus.load(args.subject)
    prefix = SUBJECT_PREFIX[args.subject]

    with closing(store.connect(db)) as conn:
        inserts, problems = plan(conn, doc, subject=args.subject, prefix=prefix)
        covered = sum(1 for n in inserts if n["level"] == "point")
        print(
            f"{doc['title']}：新增 章 {sum(1 for n in inserts if n['level'] == 'domain')} · "
            f"节 {sum(1 for n in inserts if n['level'] == 'subtype')} · "
            f"知识点 {covered}"
        )
        if problems:
            print(f"⚠ {len(problems)} 处冲突，未覆盖：")
            for p in problems[:8]:
                print("   ", p)
        if not args.apply:
            print("（预览）加 --apply 写库")
            return 1 if problems else 0
        n = apply_changes(conn, inserts)
        print(f"已写入 {n} 个节点")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
