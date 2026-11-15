"""把「穴位点开是空的」这件事补上：知识点补正文、总论补节点。

为什么要补
----------
P1 把 28 章 / 65 节铺到了 362 个穴位上，结构是对的，但 ``knowledge_node.description``
**1453 个节点全是空串**。地图能点、点开没内容，等于给了一张门票却进不了展厅。

内容从哪来
----------
只用已经入库的教材条目 ``data/curated/textbook_entries.json``（442 味，《中药学》
第十版，钟赣生）。每味药有完整的「药性 / 功效 / 应用 / 用法用量 / 使用注意」。
**不编**：条目里没有的字段就不写，也不拿模型补一句"具有清热解毒之效"。

写进哪
------
``description`` 放「药性。功效。」（卡片摘要，约 30 字）；
``extra_json.content`` 放全部字段原文（详情页照录）；
``extra_json.content_source`` 记页码与章节，能一页页翻回去核对。

为什么总论要单独补节点
----------------------
中药学总论 18 节里 15 节在知识树里只有 subtype、没有 point（树是旧种子建的，
早期把总论压成了「1 章 1 节 3 个点」）。于是地图刻度挂的是 ``concept:四气``
这类软引用——**查不到节点，也就取不到名字和内容**。
这里按教材目录补成 ``general.c04.s1.p1`` 这种真实节点，把旧软引用迁过去。

用法::

    python scripts/backfill_point_content.py            # 只预览
    python scripts/backfill_point_content.py --apply    # 写库
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.meridian import store, syllabus  # noqa: E402

ENTRIES_PATH = ROOT / "data" / "curated" / "textbook_entries.json"
FORMULA_ENTRIES_PATH = ROOT / "data" / "curated" / "formula_entries.json"

#: 教材条目里的字段顺序就是详情页的显示顺序。``性味`` 是「药性」的异名（2 条），
#: 归一到同一键，否则前端要写两套取值逻辑。
FIELD_ORDER: tuple[str, ...] = ("药性", "功效", "应用", "用法用量", "使用注意", "鉴别用药")
FIELD_ALIASES: dict[str, str] = {"性味": "药性"}

#: 摘要取这两段。``应用`` 最长 1087 字（大黄），放进列表会把卡片撑开。
SUMMARY_FIELDS: tuple[str, ...] = ("药性", "功效")


def _clean(text: str) -> str:
    """去掉教材扫描留下的软换行与多余空白，保留原有的「1. 2. 3.」编号。"""

    return re.sub(r"[ \t\u3000]+", " ", text.replace("\r", "")).strip()


def load_entries(path: Path = ENTRIES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_fields(raw: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or not value.strip():
            continue
        out[FIELD_ALIASES.get(key, key)] = _clean(value)
    return {k: out[k] for k in FIELD_ORDER if k in out}


def summarize(fields: dict[str, str]) -> str:
    """卡片摘要 = 药性 + 功效。两段都以句号结尾，直接相接。"""

    parts = [fields[k].rstrip("。；; ") + "。" for k in SUMMARY_FIELDS if fields.get(k)]
    return "".join(parts)


# --------------------------------------------------------------------------- 总论补节点


def _point_by_name(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM knowledge_node WHERE level='point' AND name=? ORDER BY id LIMIT 1",
        (name,),
    ).fetchone()
    return str(row[0]) if row else None


def plan_general_nodes(
    conn: sqlite3.Connection, doc: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    """总论每一节都要有真实 point 节点。

    返回 (要插入的节点, 软引用→真实 id 的迁移表, 问题列表)。
    已经在树里同名存在的**不新建**——``general.p1``「性味归经」被 45 条进度引用，
    再建一个「归经」会让同一处知识出现两个节点。
    """

    inserts: list[dict[str, Any]] = []
    remap: dict[str, str] = {}
    problems: list[str] = []

    for ch in doc["chapters"]:
        if ch["part"] != "总论":
            continue
        for sec in ch["sections"]:
            title = sec["title"]
            target = syllabus.GENERAL_NODE_ALIASES.get(title, title)
            existing = _point_by_name(conn, target)
            for soft in (f"concept:{title}", f"concept:herb:{title}"):
                if existing:
                    remap[soft] = existing
            if existing:
                continue

            sub_id = f"general.c{int(ch['no']):02d}.s{int(sec['no'])}"
            point_id = f"{sub_id}.p1"
            inserts.append(
                {
                    "id": sub_id,
                    "name": title,
                    "level": "subtype",
                    "parent_id": "general",
                    "description": "",
                    "exam_frequency": 0.6,
                    "sort_hint": int(sec["no"]),
                    "extra_json": json.dumps(
                        {
                            "source": "syllabus_general",
                            "subject": "herb",
                            "chapter_no": ch["no"],
                            "chapter": ch["title"],
                            "section_no": sec["no"],
                            "note": "总论该节即一个概念知识点，由教材目录补建",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            inserts.append(
                {
                    "id": point_id,
                    "name": title,
                    "level": "point",
                    "parent_id": sub_id,
                    "description": "",
                    "exam_frequency": 0.6,
                    "sort_hint": 1,
                    "extra_json": json.dumps(
                        {
                            "source": "syllabus_general",
                            "subject": "herb",
                            "chapter_no": ch["no"],
                            "chapter": ch["title"],
                            "section_no": sec["no"],
                            "exam_frequency_source": "default_0.6",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            for soft in (f"concept:{title}", f"concept:herb:{title}"):
                remap[soft] = point_id
            problems.append(f"总论第{ch['no']}章 第{sec['no']}节「{title}」：新建节点 {point_id}")

    # 目录里没有的总论节（例如旧数据残留）也要报告，避免"补完了但漏一节"
    known = {
        syllabus.GENERAL_NODE_ALIASES.get(s["title"], s["title"])
        for c in doc["chapters"]
        if c["part"] == "总论"
        for s in c["sections"]
    }
    orphans = conn.execute(
        "SELECT node_id FROM map_point_slot WHERE node_id LIKE 'concept:%'"
    ).fetchall()
    for (nid,) in orphans:
        name = str(nid).rsplit(":", 1)[-1]
        if name not in known:
            problems.append(f"总论软引用 {nid} 不在教材目录里，未处理")
    return inserts, remap, problems


# --------------------------------------------------------------------------- 内容


def plan_contents(
    conn: sqlite3.Connection, entries_doc: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """按药名把教材条目写进同名 point。返回 (更新列表, 问题列表)。

    **只按名字匹配，不按章节**：树的章号（d01 = 解表药，各论第八章）与教材章号
    （c08）本来就不是一套编号，按章号取会全错一格。名字在教材内唯一（442 味无重名），
    ``knowledge_node.name`` 也够用。
    """

    rows = conn.execute(
        "SELECT id, name, description, extra_json FROM knowledge_node WHERE level='point'"
    ).fetchall()
    by_name: dict[str, list[tuple[str, str, str]]] = {}
    for nid, name, desc, extra in rows:
        by_name.setdefault(str(name), []).append((str(nid), str(desc or ""), str(extra or "{}")))

    updates: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: set[str] = set()

    for entry in entries_doc["entries"]:
        name = str(entry["name"])
        fields = normalize_fields(entry.get("fields") or {})
        if not fields:
            problems.append(f"{name}：条目没有任何字段正文")
            continue
        hits = by_name.get(name, [])
        if not hits:
            problems.append(f"{name}：知识树里没有同名知识点")
            continue
        if len(hits) > 1:
            # 同名节点会让人点开看到别人的内容，比不填更糟。宁可不写。
            problems.append(f"{name}：知识树里有 {len(hits)} 个同名节点，跳过以免写错")
            continue
        nid, old_desc, old_extra = hits[0]
        if nid in seen:
            continue
        seen.add(nid)

        try:
            meta = json.loads(old_extra or "{}")
        except (TypeError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}

        summary = summarize(fields)
        meta.update(
            {
                "subject": "herb",
                "content": fields,
                "content_source": {
                    "title": entries_doc.get("source", ""),
                    "chapter": entry.get("chapter", ""),
                    "section": entry.get("section", ""),
                    "page": entry.get("page"),
                },
            }
        )
        updates.append(
            {
                "id": nid,
                "name": name,
                "description": summary,
                "extra_json": json.dumps(meta, ensure_ascii=False),
                "replaced_empty": not old_desc.strip(),
            }
        )

    return updates, problems


# --------------------------------------------------------------------------- 方剂学：方歌


def plan_formula_contents(
    conn: sqlite3.Connection, doc: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """方剂学的正文 = 方歌。

    方剂没有「药性/功效」那种字段，但每首方都有一首方歌，且出处可核对。
    方歌本身就是医学生要背的东西——拿它当 ``description`` 比写一句
    "本方具有解表散寒之效"有用得多，而且**不用编**。
    """

    rows = conn.execute(
        "SELECT id, name, description FROM knowledge_node WHERE level='point'"
    ).fetchall()
    by_name: dict[str, list[tuple[str, str]]] = {}
    for nid, name, desc in rows:
        by_name.setdefault(str(name), []).append((str(nid), str(desc or "")))

    updates: list[dict[str, Any]] = []
    problems: list[str] = []
    for chapter in doc["chapters"]:
        for sec in chapter["sections"]:
            for entry in sec["entries"]:
                name = str(entry["name"])
                hits = by_name.get(name, [])
                if not hits:
                    problems.append(f"{name}：知识树里没有同名方剂节点")
                    continue
                if len(hits) > 1:
                    problems.append(f"{name}：知识树里有 {len(hits)} 个同名节点，跳过")
                    continue
                nid, old_desc = hits[0]
                gejue = str(entry.get("gejue") or "").strip()
                if not gejue:
                    problems.append(f"{name}：文档里没有歌诀正文")
                    continue
                meta = {
                    "subject": "formula",
                    "content": {
                        "歌诀": gejue,
                        "出处": entry.get("cite") or "",
                        "考纲等级": entry.get("importance") or "",
                    },
                    "content_source": {
                        "title": (doc.get("source") or {}).get("title", ""),
                        "url": (doc.get("source") or {}).get("url", ""),
                        "chapter": chapter["title"],
                        "section": sec["title"],
                    },
                }
                updates.append(
                    {
                        "id": nid,
                        "name": name,
                        "description": gejue,
                        "extra_json": json.dumps(meta, ensure_ascii=False),
                        "replaced_empty": not old_desc.strip(),
                    }
                )
    return updates, problems


# --------------------------------------------------------------------------- 写库


def _apply_general(conn: sqlite3.Connection, inserts: list[dict], remap: dict[str, str]) -> int:
    conn.executemany(
        """
        INSERT INTO knowledge_node
            (id, name, level, parent_id, description, exam_frequency, sort_hint, extra_json)
        VALUES (:id, :name, :level, :parent_id, :description, :exam_frequency, :sort_hint, :extra_json)
        ON CONFLICT(id) DO NOTHING
        """,
        inserts,
    )
    moved = 0
    for soft, real in remap.items():
        cur = conn.execute(
            "UPDATE OR REPLACE map_point_slot SET node_id=? WHERE node_id=?", (real, soft)
        )
        moved += cur.rowcount
    return moved


def _apply_contents(conn: sqlite3.Connection, updates: list[dict]) -> int:
    """只覆盖空描述或本次内容更长的旧描述；**别人写过的正文不覆盖**。"""

    changed = 0
    for row in updates:
        cur = conn.execute(
            "UPDATE knowledge_node SET description=?, extra_json=? WHERE id=? "
            "AND (description IS NULL OR description='' OR length(description) < length(?))",
            (row["description"], row["extra_json"], row["id"], row["description"]),
        )
        changed += cur.rowcount
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="补知识点正文 + 总论节点")
    ap.add_argument("--db", default=None)
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只预览）")
    args = ap.parse_args(argv)

    db = Path(args.db) if args.db else store.DB_PATH
    store.init_db(db)
    entries_doc = load_entries()
    formula_doc = load_entries(FORMULA_ENTRIES_PATH)
    doc = syllabus.load("herb")

    conn = store.connect(db)
    try:
        general, remap, p1 = plan_general_nodes(conn, doc)
        herb_updates, p2 = plan_contents(conn, entries_doc)
        formula_updates, p3 = plan_formula_contents(conn, formula_doc)
        updates = herb_updates + formula_updates

        print(f"总论补节点：{sum(1 for n in general if n['level'] == 'point')} 节 / {len(general)} 行")
        print(f"软引用迁移：{len(remap)} 条规则")
        print(
            f"内容回填：{len(updates)} 条（中药 {len(herb_updates)} 味 · "
            f"方剂 {len(formula_updates)} 首 · 空描述 {sum(1 for u in updates if u['replaced_empty'])} 条）"
        )
        for msg in (p1 + p2 + p3)[:20]:
            print("  ·", msg)
        if len(p1) + len(p2) + len(p3) > 20:
            print(f"  …… 另 {len(p1) + len(p2) + len(p3) - 20} 条")

        if not args.apply:
            print("\n预览模式，未写库。加 --apply 生效。")
            return 0

        moved = _apply_general(conn, general, remap)
        changed = _apply_contents(conn, updates)
        conn.commit()
        print(f"\n已写入：节点 {len(general)} 行、刻度迁移 {moved} 条、正文更新 {changed} 条")
        left = conn.execute(
            "SELECT COUNT(*) FROM map_point_slot p LEFT JOIN knowledge_node k ON k.id=p.node_id "
            "WHERE k.id IS NULL"
        ).fetchone()[0]
        empty = conn.execute(
            "SELECT COUNT(*) FROM knowledge_node WHERE level='point' AND "
            "COALESCE(TRIM(description),'')=''"
        ).fetchone()[0]
        print(f"剩余悬空刻度 {left} 个 · 仍无正文的 point {empty} 个")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
