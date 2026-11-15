"""教材核对：把"教材目录 / 知识图 / 穴位"三份数据摊在一张表上，供人逐条对。

## 为什么需要它

规划单位、穴位、知识点三者必须说同一件事。以前它们各说各的，而且**没有一处能对**：

* 教材大纲 ``data/syllabus/<subject>.json`` 是人工录的章节目录（带出版社、版次、目录来源）；
* 知识图 ``knowledge_node`` 是另一个人按考点建的，节点名与教材节名**不保证一致**；
* 穴位 ``map_section`` 是按教材目录造的，一穴一节。

三者一有偏差，用户看到的就是"这一节在教材里明明有，地图上没有""穴位名和教材对不上"。
所以这里生成两类产物，都是给人看的：

1. ``data/textbook/<subject>.md`` —— 逐章逐节的对账表：教材第几章第几节 / 穴位 / 代号 /
   知识点数 / 知识点名。**打印出来对着书翻**，一行一行勾。
2. ``audit()`` 的差异清单 —— 机器能判定的偏差（教材有节无单元、单元有节无教材、
   一穴无点、点无穴位、同名节落在同一章、章号对不上…），每条带 ``code`` 与可执行建议。

## 判定的边界

只报**能确定**的偏差。"这一节的知识点够不够全"要靠人判断，这里不猜。
每个 code 都对应一次具体的修补动作，不给"建议人工检查"这种空话。
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.path.store import DB_PATH, list_nodes
from app.path.subjects import CANONICAL_ORDER, SUBJECTS, title_of
from app.path.units import SYLLABUS_DIR, Curriculum, Outline, outline_for
from app.profile.store import connect

#: 对账表落到这里。和 ``syllabus`` 分开放：一份是数据源，一份是给人看的产物。
TEXTBOOK_DIR = Path(__file__).resolve().parents[2] / "data" / "textbook"


@dataclass
class Issue:
    code: str
    subject: str
    message: str
    #: 可直接执行的修补动作（改哪个文件、跑哪个脚本）
    action: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "subject": self.subject, "message": self.message, "action": self.action, **self.detail}


@dataclass
class AcupointRow:
    """对账表的一行：教材的一节，及其穴位、知识点。"""

    subject: str
    subject_name: str
    chapter_no: int
    chapter_title: str
    part: str
    section_no: int
    section_title: str
    virtual: bool
    unit_kind: str
    unit_id: str
    point_ids: tuple[str, ...]
    point_names: tuple[str, ...]
    acupoint_code: str = ""
    acupoint_name: str = ""
    section_id: str = ""
    in_textbook: bool = True
    in_graph: bool = True


def _section_rows(db_path: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """``map_section`` 的穴位信息，按 (学科, 节名) 分组。

    一个节名可能对应多行（基本理论的"概述"在第四章、第六章各一个），所以存成列表，
    由 :func:`_pick_section` 按教材章号挑。**不能只按名字取第一行**：`概述` 会拿到
    错误的章号，然后报一条不存在的偏差。
    """

    sql = """
        SELECT sec.id, sec.layer_id, sec.title, sec.chapter_no, sec.book_chapter_no,
               sec.section_no, sec.chapter_title, sec.part, sec.virtual, sec.point_count,
               a.code AS acupoint_code, a.name AS acupoint_name
        FROM map_section sec
        LEFT JOIN meridian_acupoint a ON a.id = sec.acupoint_id
    """
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    try:
        with closing(connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(sql):
                item = dict(row)
                out.setdefault((str(item["layer_id"]), str(item["title"])), []).append(item)
    except sqlite3.Error:
        return {}
    return out


def _book_no_of(item: dict[str, Any]) -> int:
    """书上的章号。**0 是合法值**（绪论/总论），不能拿 `or` 兜底成别的数。"""

    raw = item.get("book_chapter_no")
    return -1 if raw is None else int(raw)


def _pick_section(candidates: Sequence[dict[str, Any]], *, chapter_no: int, section_no: int) -> dict[str, Any] | None:
    """从同名的几行里挑出教材这一章那一节；一行都没有就返回 None。

    先按书上章号（``book_chapter_no``）对，再按节号对。都对不上就退回第一行——
    这时报的偏差才是真的（章号/节序和教材不一致），而不是挑错了行。
    """

    if not candidates:
        return None
    for item in candidates:
        if _book_no_of(item) == chapter_no and int(item.get("section_no") or 0) == section_no:
            return item
    for item in candidates:
        if int(item.get("section_no") or 0) == section_no:
            return item
    return candidates[0]


def build_index(*, subject: str = "", db_path: Path = DB_PATH, syllabus_dir: Path = SYLLABUS_DIR) -> list[AcupointRow]:
    """把一门课（或全部）的教材章节点摊平成对账行。

    以**教材大纲**为骨架：教材里有的节一定出现在表里，哪怕图里还没建节点
    （那正是 `in_graph=False` 要暴露的）。
    """

    nodes = list_nodes(db_path=db_path)
    curriculum = Curriculum(nodes, syllabus_dir=syllabus_dir)
    units = curriculum.all_units()
    names = {str(node["id"]): str(node.get("name") or "") for node in nodes}
    sections = _section_rows(db_path)

    unit_index: dict[str, list[Any]] = {}
    for unit in units:
        key = f"{unit.subject_id}|{unit.title}|{unit.chapter_no}|{unit.section_no}"
        unit_index.setdefault(key, []).append(unit)
    # 章号/节号可能对不上（图里用 sort_hint，教材用书上次序），退一步只按名字找
    by_title: dict[tuple[str, str], list[Any]] = {}
    for unit in units:
        by_title.setdefault((unit.subject_id, unit.title), []).append(unit)

    subjects = [subject] if subject else list(CANONICAL_ORDER)
    rows: list[AcupointRow] = []
    for subject_id in subjects:
        outline = outline_for(subject_id, directory=syllabus_dir)
        if outline is None:
            continue
        for chapter in outline.chapters:
            for section in chapter.sections:
                key = f"{subject_id}|{section.title}|{chapter.book_no}|{section.no}"
                matched = unit_index.get(key) or by_title.get((subject_id, section.title)) or []
                unit = matched[0] if matched else None
                point_ids = tuple(unit.point_ids) if unit else ()
                acu = _pick_section(
                    sections.get((subject_id, section.title), []),
                    chapter_no=chapter.book_no,
                    section_no=section.no,
                ) or {}
                rows.append(
                    AcupointRow(
                        subject=subject_id,
                        subject_name=title_of(subject_id),
                        chapter_no=chapter.book_no,
                        chapter_title=chapter.title,
                        part=chapter.part,
                        section_no=section.no,
                        section_title=section.title,
                        virtual=section.virtual,
                        unit_kind=unit.kind if unit else "",
                        unit_id=unit.id if unit else "",
                        point_ids=point_ids,
                        point_names=tuple(names.get(point_id, point_id) for point_id in point_ids),
                        acupoint_code=str(acu.get("acupoint_code") or ""),
                        acupoint_name=str(acu.get("acupoint_name") or ""),
                        section_id=str(acu.get("id") or ""),
                        in_textbook=True,
                        in_graph=unit is not None,
                    )
                )

        # 图里有、教材没有的节（遗留节点）也要列出来，否则它永远藏在报表外
        covered = {(chapter.book_no, section.title) for chapter in outline.chapters for section in chapter.sections}
        for unit in units:
            if unit.subject_id != subject_id or (unit.chapter_no, unit.title) in covered:
                continue
            if any(row.section_title == unit.title and row.subject == subject_id for row in rows):
                continue
            rows.append(
                AcupointRow(
                    subject=subject_id,
                    subject_name=title_of(subject_id),
                    chapter_no=unit.chapter_no,
                    chapter_title=unit.chapter_title,
                    part=unit.part,
                    section_no=unit.section_no,
                    section_title=unit.title,
                    virtual=unit.virtual,
                    unit_kind=unit.kind,
                    unit_id=unit.id,
                    point_ids=unit.point_ids,
                    point_names=tuple(names.get(point_id, point_id) for point_id in unit.point_ids),
                    in_textbook=False,
                    in_graph=True,
                )
            )
    return rows


def audit(*, db_path: Path = DB_PATH, syllabus_dir: Path = SYLLABUS_DIR) -> list[Issue]:
    """能确定对不上的地方。每条都带一个具体动作，不给"请人工检查"。"""

    issues: list[Issue] = []
    rows = build_index(db_path=db_path, syllabus_dir=syllabus_dir)
    nodes = list_nodes(db_path=db_path)
    sections = _section_rows(db_path)

    # ① 教材有这一节、规划里没有单元
    for row in rows:
        if not row.in_graph:
            issues.append(
                Issue(
                    code="section_without_unit",
                    subject=row.subject,
                    message=f"教材「{row.chapter_title}」第 {row.section_no} 节「{row.section_title}」在图里没有对应节点",
                    action="运行 python -m app.meridian.seed --subject <学科> 补建该节节点，或在 syllabus 里确认节名",
                )
            )

    # ② 图里有、教材没有
    for row in rows:
        if not row.in_textbook:
            issues.append(
                Issue(
                    code="unit_without_section",
                    subject=row.subject,
                    message=f"图里的「{row.section_title}」（{row.unit_id}）在教材目录里查不到",
                    action="在该学科 syllabus 里补这一节，或确认这是否是旧版教材遗留",
                )
            )

    # ③ 教材有这一节、穴位表没有（地图上点不到）
    for row in rows:
        if row.in_textbook and not row.acupoint_code:
            issues.append(
                Issue(
                    code="section_without_acupoint",
                    subject=row.subject,
                    message=f"「{row.section_title}」没有对应的穴位（map_section 缺这一行）",
                    action="运行 python -m app.meridian.seed 重建该学科的穴位映射",
                )
            )

    # ④ 同一学科内同名节落在同一章（界面无法区分）
    by_title: dict[tuple[str, str], list[int]] = {}
    for row in rows:
        by_title.setdefault((row.subject, row.section_title), []).append(row.chapter_no)
    for (subject_id, title), chapter_nos in by_title.items():
        if len(chapter_nos) > 1 and len(set(chapter_nos)) < len(chapter_nos):
            issues.append(
                Issue(
                    code="duplicate_section_title",
                    subject=subject_id,
                    message=f"{title_of(subject_id)} 有 {len(chapter_nos)} 个「{title}」落在同一章里",
                    action="给其中一个节改用教材上的实际节名（不改名的话界面上两个标签一模一样）",
                )
            )

    # ⑤ 穴位表里的章号与教材对不上（书上是第几章 vs 表里写的第几章）
    for row in rows:
        if not row.in_textbook or not row.section_id:
            continue
        acu = _pick_section(
            sections.get((row.subject, row.section_title), []),
            chapter_no=row.chapter_no,
            section_no=row.section_no,
        )
        if acu is None:
            continue
        book_no = _book_no_of(acu)
        if book_no != row.chapter_no:
            issues.append(
                Issue(
                    code="acupoint_chapter_mismatch",
                    subject=row.subject,
                    message=f"{title_of(row.subject)}「{row.section_title}」教材是第 {row.chapter_no} 章，穴位表写的是第 {book_no} 章",
                    action="以教材目录为准修正 map_section.book_chapter_no（重跑 python -m app.meridian.seed --subject <学科>）",
                    detail={"section_id": acu.get("id"), "textbook": row.chapter_no, "table": book_no},
                )
            )

    # ⑥ 知识点重复挂到两个穴位上
    slots: dict[str, list[str]] = {}
    try:
        with closing(connect(db_path)) as conn:
            for node_id, section_id in conn.execute("SELECT node_id, section_id FROM map_point_slot"):
                slots.setdefault(str(node_id), []).append(str(section_id))
    except sqlite3.Error:
        slots = {}
    for node_id, section_ids in slots.items():
        if len(set(section_ids)) > 1:
            issues.append(
                Issue(
                    code="point_on_two_sections",
                    subject="",
                    message=f"知识点 {node_id} 同时挂在 {len(set(section_ids))} 个穴位上",
                    action="map_point_slot 一条点只能有刻度；删掉多余那行",
                    detail={"node_id": node_id, "sections": sorted(set(section_ids))},
                )
            )

    # ⑦ 教材有节、但穴位表整本书都没有这个节名（地图上点不到）
    for row in rows:
        if not row.in_textbook or not row.section_title:
            continue
        if not sections.get((row.subject, row.section_title)):
            issues.append(
                Issue(
                    code="section_missing_on_map",
                    subject=row.subject,
                    message=f"教材「{row.section_title}」在穴位表里没有对应穴位，地图上点不到",
                    action="重跑 python -m app.meridian.seed --subject <学科> 重建穴位映射",
                )
            )

    point_ids = {str(node["id"]) for node in nodes if node.get("level") == "point"}
    names = {str(node["id"]): str(node.get("name") or "") for node in nodes}

    # ⑧ 知识点在图上没有刻度：学完也点不亮，用户会以为"系统没记住我学过"
    for point_id in sorted(point_ids - set(slots)):
        issues.append(
            Issue(
                code="point_without_slot",
                subject="",
                message=f"知识点「{names.get(point_id, point_id)}」（{point_id}）在图上没有刻度",
                action="若它是被拆分/合并掉的旧条目（如「贝母」拆成川贝母、浙贝母），停用该节点；否则补 map_point_slot 一行",
                detail={"node_id": point_id},
            )
        )

    # ⑨ 穴位上写的知识点数与实际刻度数不一致（刻度条上的数字会跟着错）
    actual: dict[str, int] = {}
    for section_ids in slots.values():
        for section_id in set(section_ids):
            actual[section_id] = actual.get(section_id, 0) + 1
    for items in sections.values():
        for item in items:
            expected = int(item.get("point_count") or 0)
            found = actual.get(str(item["id"]), 0)
            if expected != found:
                issues.append(
                    Issue(
                        code="section_point_count_stale",
                        subject=str(item["layer_id"]),
                        message=f"「{item['title']}」写着 {expected} 个知识点，实际挂了 {found} 个刻度",
                        action="重跑 python -m app.meridian.seed --subject <学科> 让 point_count 与刻度对齐",
                        detail={"section_id": item["id"], "expected": expected, "found": found},
                    )
                )

    for row in rows:
        unknown = [point_id for point_id in row.point_ids if point_id not in point_ids]
        if unknown:
            issues.append(
                Issue(
                    code="unit_points_missing",
                    subject=row.subject,
                    message=f"「{row.section_title}」引用了图里不存在的知识点：{', '.join(unknown[:5])}",
                    action="清理 user_path_plan / knowledge_node 里的悬空引用",
                    detail={"unit_id": row.unit_id, "points": unknown},
                )
            )

    issues.sort(key=lambda item: (item.subject, item.code, item.message))
    return issues


def _minutes(row: AcupointRow) -> str:
    return str(len(row.point_ids))


def render_markdown(subject: str, rows: Sequence[AcupointRow], *, outline: Outline | None = None) -> str:
    """一份能打印出来对着教材勾的对账表。``outline`` 不给就按默认目录读。"""

    subject_rows = [row for row in rows if row.subject == subject]
    name = title_of(subject)
    outline = outline or outline_for(subject)
    textbook = (outline.textbook if outline else {}) or {}
    lines: list[str] = [f"# {name} · 教材对照表", ""]
    if textbook:
        lines.append("## 教材版本")
        for key, label in (("title", "书名"), ("edition", "版次"), ("editor", "主编"), ("publisher", "出版社"), ("toc_source", "目录来源")):
            if textbook.get(key):
                lines.append(f"- **{label}**：{textbook[key]}")
        lines.append("")

    total_points = sum(len(row.point_ids) for row in subject_rows)
    total_chapters = len({row.chapter_no for row in subject_rows})
    lines.append(f"共 **{total_chapters}** 章、**{len(subject_rows)}** 节、**{total_points}** 个知识点。")
    lines.append("")
    lines.append("| 章 | 节 | 穴位 | 代号 | 知识点数 | 知识点 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for row in subject_rows:
        chapter = f"第 {row.chapter_no} 章 {row.chapter_title}" if row.chapter_no else f"（章号待定）{row.chapter_title}"
        points = "、".join(row.point_names) if row.point_names else "—"
        kind = "章" if row.unit_kind == "chapter" else "节"
        section = f"{row.section_title}（不分节，按{kind}）" if row.virtual else row.section_title
        flag = "" if row.in_textbook else " ⚠️ 教材无"
        lines.append(
            f"| {chapter} | {section}{flag} | {row.acupoint_name or '—'} | {row.acupoint_code or '—'} | {_minutes(row)} | {points} |"
        )
    lines.append("")
    lines.append("> 核对方法：对着教材目录逐行看「章 / 节」两列。节名、章号、节序对得上，就说明层级没错；")
    lines.append("> 知识点数对不上时，先确认是教材里确实有这么多内容，还是图里漏建了。")
    return "\n".join(lines)


def write_textbook(directory: Path = TEXTBOOK_DIR, *, db_path: Path = DB_PATH, syllabus_dir: Path = SYLLABUS_DIR) -> list[Path]:
    """生成六份 Markdown 对账表，返回写出的文件路径。"""

    rows = build_index(db_path=db_path, syllabus_dir=syllabus_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for subject in SUBJECTS:
        subject_rows = [row for row in rows if row.subject == subject.id]
        if not subject_rows:
            continue
        outline = outline_for(subject.id, directory=syllabus_dir)
        path = directory / f"{subject.id}.md"
        path.write_text(render_markdown(subject.id, subject_rows, outline=outline), encoding="utf-8")
        written.append(path)
    return written


def summary(issues: Iterable[Issue]) -> dict[str, int]:
    return dict(Counter(issue.code for issue in issues))


def fix_map_chapters(*, db_path: Path = DB_PATH, syllabus_dir: Path = SYLLABUS_DIR, dry_run: bool = False) -> list[str]:
    """把穴位表里与教材不一致的**书上章号**改回教材的值。

    只改 ``book_chapter_no`` 一列。``chapter_no`` 是连续内部编号，另有用途，
    不动；``acupoint_id`` / 刻度更不碰——重跑 seed 会重排穴位，那是另一件事。

    典型场景：`绪论` 在书上不编号（0），旧版建表时被兜底成 1，界面上就成了"第一章"。
    """

    changes: list[str] = []
    rows = build_index(db_path=db_path, syllabus_dir=syllabus_dir)
    sections = _section_rows(db_path)
    updates: list[tuple[int, str]] = []
    for row in rows:
        if not row.in_textbook or not row.section_id:
            continue
        acu = _pick_section(
            sections.get((row.subject, row.section_title), []),
            chapter_no=row.chapter_no,
            section_no=row.section_no,
        )
        if acu is None or _book_no_of(acu) == row.chapter_no:
            continue
        updates.append((row.chapter_no, str(acu["id"])))
        changes.append(
            f"{title_of(row.subject)}「{row.section_title}」：book_chapter_no {acu.get('book_chapter_no')} → {row.chapter_no}"
        )
    if dry_run or not updates:
        return changes
    with closing(connect(db_path)) as conn:
        for chapter_no, section_id in updates:
            conn.execute("UPDATE map_section SET book_chapter_no = ? WHERE id = ?", (chapter_no, section_id))
        conn.commit()
    return changes


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="生成教材对照表并列出层级偏差")
    parser.add_argument("--write", action="store_true", help="写出 data/textbook/*.md")
    parser.add_argument("--json", action="store_true", help="把差异清单以 JSON 打印")
    parser.add_argument("--fix-map-chapters", action="store_true", help="把穴位表的书上章号改回教材值")
    parser.add_argument("--dry-run", action="store_true", help="配合 --fix-map-chapters：只打印不改库")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.write:
        for path in write_textbook():
            print(f"已生成 {path}")

    if args.fix_map_chapters:
        changes = fix_map_chapters(dry_run=args.dry_run)
        for line in changes:
            print(("（预览）" if args.dry_run else "已修正 ") + line)
        if not changes:
            print("穴位表的章号与教材一致，无需修正")

    issues = audit()
    if args.json:
        print(json.dumps([issue.to_dict() for issue in issues], ensure_ascii=False, indent=2))
    else:
        counts = summary(issues)
        print(f"差异 {len(issues)} 条：" + ("、".join(f"{code}×{count}" for code, count in sorted(counts.items())) or "无"))
        for issue in issues[:20]:
            print(f"  [{issue.code}] {issue.message}")
            if issue.action:
                print(f"      → {issue.action}")
        if len(issues) > 20:
            print(f"  …还有 {len(issues) - 20} 条，用 --json 看全量")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "TEXTBOOK_DIR",
    "AcupointRow",
    "Issue",
    "audit",
    "build_index",
    "main",
    "render_markdown",
    "summary",
    "write_textbook",
]
