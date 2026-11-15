"""教材目录：地图结构的**唯一事实源**。

地图上「章 / 节 / 穴位」的层级必须能一指头戳到教材某一页。这个模块读
``backend/data/syllabus/<subject>.json``（由 ``scripts/build_syllabus.py``
从教材条目与印刷目录生成），把它转成造图用的 ``StructureProposal``。

**这里不再读 ``knowledge_node`` 去反推结构。** 上一版那样做，结果是把
《中药学》总论压成「1 章 1 节 3 个点」，教材前 7 章整个消失；同时给 9 个
本来不分节的章硬造了同名假节。目录错位会让后面的规划、分配、渲染全部
建立在错的前提下——所以宁可让这是一份独立的数据文件，也不要再从别处推导。

知识点命名规则（软引用，前端按前缀取展示名）：

* ``herb:麻黄`` / ``formula:麻黄汤`` —— 教材条目，未进知识树时走这个
* ``<node_id>``  —— 已进知识树且 ``user_path_state`` 有引用，保留原 id 以免丢进度
* ``concept:<学科>:四气`` —— 总论概念点，教材这一节就是这一条
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from app.meridian import store
from app.meridian.schema import ChapterProposal, SectionProposal, StructureProposal

SYLLABUS_DIR = Path(__file__).resolve().parents[2] / "data" / "syllabus"

#: 总论里节名与既有知识树节点名不一致、但语义确为一处的，在这里显式对上。
#: 「性味归经」这个旧节点把性味与归经揉在一起，教材已拆成四气 / 五味 / 归经
#: 三节；挂到「归经」是字面最接近的落点，不能让它掉进虚构节点里——
#: ``user_path_state`` 有 45 条进度指着它。
GENERAL_NODE_ALIASES: dict[str, str] = {
    "归经": "性味归经",
}

#: 软引用前缀。条目/概念还没进知识树时，用 ``<学科>:<名字>`` 顶上，
#: 前端按前缀取展示名。**必须带学科**：``concept:绪论`` 这种裸名字在两门学科
#: 里都会出现，而 ``map_point_slot.node_id`` 是全局主键，撞了就是整层重建失败。
ENTRY_PREFIXES: dict[str, str] = {"herb": "herb", "formula": "formula"}


def entry_prefix(subject: str = "herb") -> str:
    return ENTRY_PREFIXES.get(subject, subject)


class SyllabusError(RuntimeError):
    """目录文件缺失或与既定契约不符。"""


def path_for(subject: str = "herb") -> Path:
    return SYLLABUS_DIR / f"{subject}.json"


def load(subject: str = "herb") -> dict[str, Any]:
    p = path_for(subject)
    if not p.exists():
        raise SyllabusError(
            f"缺少教材目录 {p}。先跑 `python scripts/build_syllabus.py --subject {subject}`。"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def available() -> list[str]:
    """已生成目录的学科。**别用 SUBJECT_META 判断能不能造图**——
    那边声明的是"打算做哪些学科"（方剂学也在），这边才是"教材目录到齐了"。"""

    if not SYLLABUS_DIR.exists():
        return []
    return sorted(p.stem for p in SYLLABUS_DIR.glob("*.json"))


def digest(doc: dict[str, Any]) -> str:
    """目录指纹。教材目录一变，基于它造的地图就该重建。"""

    raw = json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def chapter_key(no: int) -> str:
    return f"c{no:02d}"


def section_key(no: int, sec_no: int) -> str:
    return f"c{no:02d}.s{sec_no}"


def _book_no(chapter: dict[str, Any]) -> int:
    """书上印的章号；**0 是合法值**（中医诊断学/针灸学的绪论、中医内科学的总论）。

    不能写 ``ch.get("book_no") or ch["no"]``：0 是假值，会被兜底成 1，
    地图上于是把「绪论」标成「第一章」，而三本教材都没有「第零章」这回事。
    """

    raw = chapter.get("book_no")
    return int(chapter["no"]) if raw is None else int(raw)


def _tree_points(db_path: Path | str, subject: str | None = None) -> dict[str, str]:
    """知识点名 → 既有节点 id。用于保住已有学习进度。

    新库可能还没建 ``knowledge_node``（只种了穴位名录就能跑地图）。这时不能
    抛异常——地图的章节结构来自教材，本来就不依赖知识树；拿不到旧 id 就一律
    走 ``herb:`` / ``concept:`` 前缀。

    **按学科收窄**：``绪论``、``概述``、``总论`` 这类名字在每门课里都有，
    只按名字建表会让后建的学科把前面的节点顶掉，两个学科的「绪论」指向同一个
    ``b01`` 节点——地图上看不出来，进度和错题却串了学科。所以先取本学科自己的
    节点（``extra_json.subject`` 相符），再退回旧数据（中药学那棵树没写 subject）。
    """

    conn = store.connect(Path(db_path))
    try:
        rows = conn.execute(
            "SELECT id, name, extra_json FROM knowledge_node WHERE level='point'"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()

    scoped: dict[str, str] = {}
    legacy: dict[str, str] = {}
    for nid, name, extra in rows:
        try:
            meta = json.loads(extra or "{}")
        except (TypeError, ValueError):
            meta = {}
        if subject and isinstance(meta, dict) and meta.get("subject") == subject:
            scoped[str(name)] = str(nid)
        else:
            legacy.setdefault(str(name), str(nid))
    for name, nid in legacy.items():
        scoped.setdefault(name, nid)
    return scoped


def to_proposal(
    doc: dict[str, Any],
    *,
    db_path: Path | str = store.DB_PATH,
    subject: str = "herb",
) -> StructureProposal:
    """把目录转成结构提案。

    ``point_count`` 与 ``node_ids`` 必须同步——前端拿前者画环、拿后者列清单，
    两个数对不上就是「点了环形进度说有 35 味，点开只有 12 味」。
    """

    tree = _tree_points(db_path, subject)
    prefix = entry_prefix(subject)
    chapters: list[ChapterProposal] = []
    for ch in doc["chapters"]:
        sections: list[SectionProposal] = []
        for sec in ch["sections"]:
            node_ids: list[str] = []
            for name in sec["entries"]:
                node_ids.append(tree.get(name, f"{prefix}:{name}"))
            if not sec["entries"]:
                # 总论：节名本身即知识点；旧节点同名或别名的优先沿用
                target = GENERAL_NODE_ALIASES.get(sec["title"], sec["title"])
                node_ids = [tree.get(target, f"concept:{prefix}:{sec['title']}")]
            sections.append(
                SectionProposal(
                    key=section_key(ch["no"], sec["no"]),
                    chapter_key=chapter_key(ch["no"]),
                    title=sec["title"],
                    point_count=len(node_ids),
                    node_ids=node_ids,
                    virtual=bool(sec.get("virtual")),
                )
            )
        chapters.append(
            ChapterProposal(
                key=chapter_key(ch["no"]),
                title=ch["title"],
                no=ch["no"],
                book_no=_book_no(ch),
                part=ch["part"],
                sections=sections,
            )
        )

    textbook = doc["textbook"]
    return StructureProposal(
        subject=subject,  # type: ignore[arg-type]
        edition=f"{textbook['edition']}（{textbook['editor']}）",
        source="textbook_toc",
        chapters=chapters,
        input_hash=digest(doc),
    )


def outline(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """给前端 / 文档用的扁平目录：一行一个穴位单元。"""

    out: list[dict[str, Any]] = []
    for ch in doc["chapters"]:
        for sec in ch["sections"]:
            out.append(
                {
                    "chapter_no": ch["no"],
                    "book_chapter_no": _book_no(ch),
                    "chapter_key": chapter_key(ch["no"]),
                    "part": ch["part"],
                    "chapter_title": ch["title"],
                    "section_no": sec["no"],
                    "section_key": section_key(ch["no"], sec["no"]),
                    "section_title": sec["title"],
                    "virtual": bool(sec.get("virtual")),
                    "entry_count": len(sec["entries"]),
                    "entries": list(sec["entries"]),
                }
            )
    return out


def stats(doc: dict[str, Any]) -> dict[str, Any]:
    chapters = doc["chapters"]
    secs = [s for c in chapters for s in c["sections"]]
    return {
        "textbook": doc["textbook"]["title"],
        "edition": doc["textbook"]["edition"],
        "chapters": len(chapters),
        "general_chapters": sum(1 for c in chapters if c["part"] == "总论"),
        "specific_chapters": sum(1 for c in chapters if c["part"] == "各论"),
        "sections": len(secs),
        "real_sections": sum(1 for s in secs if not s.get("virtual")),
        "virtual_sections": sum(1 for s in secs if s.get("virtual")),
        "entries": sum(len(s["entries"]) for s in secs),
        "digest": digest(doc),
    }


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="查看教材目录事实源")
    ap.add_argument("--subject", default="herb")
    ap.add_argument("--chapters", action="store_true", help="打印完整章节目录")
    args = ap.parse_args(argv)

    doc = load(args.subject)
    for k, v in stats(doc).items():
        print(f"{k:<20}{v}")
    if args.chapters:
        print()
        for row in outline(doc):
            virt = "·虚拟" if row["virtual"] else ""
            print(
                f"  {row['part']} 第{row['chapter_no']:>2}章 "
                f"{row['chapter_title']:<12} 第{row['section_no']}节 "
                f"{row['section_title']:<12} {row['entry_count']:>3} 味{virt}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
