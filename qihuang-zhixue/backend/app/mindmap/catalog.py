"""库情：这个知识库里到底有什么。

导图工坊最后剩下的那个毛病在这里。模型从不知道库里有什么，系统也从不去查——
「中药学介绍」被判成「教材语料之外」，提示词里写着「没有任何检索资料」，
整棵树 24 个节点全标成「推断」。可库里明明有《中药学》第 10 版 28 章 65 节、
442 味药，这门课本身就在库里。

所以这个模块只做一件事：把「库里有什么」变成一份**既进得了提示词、也搭得起
骨架**的清单，并且能回答「这句话落在库里的哪一门课、哪一章」。

事实源是 ``data/syllabus/<subject>.json``（教材目录）。``app.meridian.syllabus``
已经用同一份文件造学习地图，两边不能各读一套——**目录是教材的复印件**，
而从知识树反推出来的只是别人对教材的理解（``syllabus.py`` 的模块注释记着
上一次那样做把总论压成了 1 章 1 节 3 个点）。

判据的分工要说清楚，否则又会变成两套答案：

* 这里只回答「**有没有**」与「**叫什么**」——哪些学科、多少章、章叫什么、
  这句话命中哪一章。这些是确定性事实。
* 「**该怎么组织**」仍然由模型读整句话决定（``spec.compose_spec``）。
  库里有的名字就照用，库里没有的才推断——这是本模块存在的全部意义。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from app.kb.ingest import load_formulas, load_herbs, load_syndromes
from app.meridian import syllabus

#: 学科的日常叫法。**长词在前**：「中医基础理论」必须先于「中基」被认出来。
#:
#: 「中药」「方剂」这类既是学科名又是普通词的叫法也收进来，因为这里只用来判断
#: 「这句话在不在库里、库里有什么」，不用来决定分支——分支由模型读句子决定。
#: 判成「在库里」最坏的结果是多给一份目录材料，判成「不在库里」的代价却是整棵树
#: 被打成推断，两害相权，宁可宽一点。
SUBJECT_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "herb": ("中药学", "中药学概论", "中药"),
    "formula": ("方剂学", "方剂学概论", "方剂"),
    "basic": ("中医基础理论", "中医学基础理论", "中医学基础", "中医基础", "中基"),
    "diagnosis": ("中医诊断学", "中医诊断", "中诊"),
    "acup": ("经络腧穴学", "针灸学", "经络腧穴", "针灸"),
    "internal": ("中医内科学", "中医内科", "内科"),
}

#: 学科名的最短匹配长度。「内科」这种两字词在「中医内科」里也会命中，
#: 指向同一门课，不算误判。
_MIN_ALIAS = 2


@dataclass(frozen=True)
class SectionInfo:
    """教材的一节。``entries`` 是这一节下列出的条目（药名 / 方名 / 概念点）。"""

    title: str
    entries: tuple[str, ...]


@dataclass(frozen=True)
class ChapterInfo:
    no: int
    part: str
    title: str
    sections: tuple[SectionInfo, ...]

    @property
    def section_count(self) -> int:
        return len(self.sections)

    @property
    def entry_count(self) -> int:
        return sum(len(section.entries) for section in self.sections)


@dataclass(frozen=True)
class SubjectInfo:
    key: str
    title: str
    edition: str
    editor: str
    chapters: tuple[ChapterInfo, ...]

    @property
    def section_count(self) -> int:
        return sum(chapter.section_count for chapter in self.chapters)

    @property
    def entry_count(self) -> int:
        return sum(chapter.entry_count for chapter in self.chapters)

    def parts(self) -> list[str]:
        """篇名，按目录出现顺序去重（总论 / 各论 / 绪论）。"""

        seen: dict[str, None] = {}
        for chapter in self.chapters:
            seen.setdefault(chapter.part, None)
        return list(seen)

    def chapters_of(self, part: str) -> list[ChapterInfo]:
        return [chapter for chapter in self.chapters if chapter.part == part]

    def chapter_named(self, text: str) -> ChapterInfo | None:
        """这句话点到了哪一章。长名优先，避免「中药的配伍」被「中药的性能」之类抢先。"""

        best: ChapterInfo | None = None
        for chapter in self.chapters:
            if len(chapter.title) < 3 or chapter.title not in text:
                continue
            if best is None or len(chapter.title) > len(best.title):
                best = chapter
        return best


@dataclass(frozen=True)
class Catalog:
    """整个知识库的「门牌」。"""

    subjects: tuple[SubjectInfo, ...]
    herb_count: int
    formula_count: int
    syndrome_count: int

    def by_key(self, key: str) -> SubjectInfo | None:
        return next((item for item in self.subjects if item.key == key), None)

    @property
    def empty(self) -> bool:
        return not self.subjects


@dataclass(frozen=True)
class Coverage:
    """一句话落在库里的位置。``chapter`` 非空表示点到的是某一章，应当往下沉一层。"""

    subjects: tuple[SubjectInfo, ...]
    chapter: tuple[SubjectInfo, ChapterInfo] | None = None

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(subject.key for subject in self.subjects)

    @property
    def chapter_subject(self) -> SubjectInfo | None:
        return self.chapter[0] if self.chapter else None

    @property
    def chapter_info(self) -> ChapterInfo | None:
        return self.chapter[1] if self.chapter else None


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    """把 6 份教材目录读成一份门牌。缺文件就明说空，不编。"""

    subjects: list[SubjectInfo] = []
    for key in syllabus.available():
        try:
            doc = syllabus.load(key)
        except Exception:  # noqa: BLE001 - 目录文件坏掉不该让整条链路起不来
            continue
        textbook = doc.get("textbook") or {}
        chapters: list[ChapterInfo] = []
        for chapter in doc.get("chapters") or []:
            if not isinstance(chapter, dict):
                continue
            sections: list[SectionInfo] = []
            for section in chapter.get("sections") or []:
                if not isinstance(section, dict):
                    continue
                sections.append(
                    SectionInfo(
                        title=str(section.get("title") or ""),
                        entries=tuple(str(item) for item in (section.get("entries") or []) if str(item).strip()),
                    )
                )
            chapters.append(
                ChapterInfo(
                    no=int(chapter.get("no") or 0),
                    part=str(chapter.get("part") or ""),
                    title=str(chapter.get("title") or ""),
                    sections=tuple(sections),
                )
            )
        subjects.append(
            SubjectInfo(
                key=key,
                title=str(doc.get("title") or key),
                edition=str(textbook.get("edition") or ""),
                editor=str(textbook.get("editor") or ""),
                chapters=tuple(chapters),
            )
        )
    return Catalog(
        subjects=tuple(subjects),
        herb_count=len(load_herbs()),
        formula_count=len(load_formulas()),
        syndrome_count=len(load_syndromes()),
    )


def reset_catalog_cache() -> None:
    """知识库/教材目录变更后清掉缓存与名字索引。"""

    load_catalog.cache_clear()
    _known_names.cache_clear()


# ---------------------------------------------------------------------------
# 识别：这句话落在哪
# ---------------------------------------------------------------------------


def subjects_in(text: str) -> tuple[SubjectInfo, ...]:
    """句子里点了哪些学科。按别名的长度降序，先认长名。

    「中药和方剂学的对比」会同时认出中药学与方剂学——这正是我们要的：
    对比题的两边都该拿得到目录。
    """

    content = str(text or "")
    if not content:
        return ()
    catalog = load_catalog()
    hits: dict[str, int] = {}
    for key, aliases in SUBJECT_ALIASES.items():
        for alias in aliases:
            if len(alias) >= _MIN_ALIAS and alias in content:
                hits[key] = max(hits.get(key, 0), len(alias))
    ordered = sorted(hits, key=lambda key: (-hits[key], key))
    return tuple(subject for subject in (catalog.by_key(key) for key in ordered) if subject is not None)


def match(text: str, *, chapter: bool = True) -> Coverage | None:
    """这句话落在库里的哪里。完全对不上返回 ``None``。"""

    subjects = subjects_in(text)
    if not subjects:
        return None
    if chapter:
        content = str(text or "")
        for subject in subjects:
            found = subject.chapter_named(content)
            if found is not None:
                return Coverage(subjects=(subject,), chapter=(subject, found))
    return Coverage(subjects=subjects)


# ---------------------------------------------------------------------------
# 给用户 / 给模型看的「库情」
# ---------------------------------------------------------------------------


def coverage_line() -> str:
    """一句话说清本库覆盖什么。用于**真降级**时的说明——不许再含糊。"""

    catalog = load_catalog()
    if catalog.empty:
        return "本地还没有教材目录"
    titles = "、".join(f"《{subject.title}》" for subject in catalog.subjects)
    return (
        f"{titles} {len(catalog.subjects)} 门课的教材目录，"
        f"以及 {catalog.herb_count} 味药、{catalog.formula_count} 首方、{catalog.syndrome_count} 个证候的条目"
    )


def titles_line() -> str:
    """只报学科名。降级说明要短，塞进对话卡的一句话里。"""

    catalog = load_catalog()
    return "、".join(f"《{subject.title}》" for subject in catalog.subjects)


def _chapter_line(subject: SubjectInfo, part: str) -> str:
    chapters = subject.chapters_of(part)
    if not chapters:
        return ""
    numbers = f"第{chapters[0].no}–{chapters[-1].no}章" if len(chapters) > 1 else f"第{chapters[0].no}章"
    titles = "／".join(chapter.title for chapter in chapters)
    return f"{part}（{numbers}，{len(chapters)} 章）：{titles}"


def digest(coverage: Coverage | None = None, *, full_subjects: int = 2) -> str:
    """「库里有什么」的一段话，直接进提示词。

    命中学科的那几门给到**章标题**——模型要照抄目录就得先看得见目录；
    没命中的只报规模，免得把六门课的目录全塞进提示词。
    """

    catalog = load_catalog()
    if catalog.empty:
        return ""
    matched = set(coverage.keys) if coverage else set()
    lines = [
        f"本库共 {len(catalog.subjects)} 门课、{sum(s.section_count for s in catalog.subjects)} 节、"
        f"{sum(s.entry_count for s in catalog.subjects)} 个教材条目，"
        f"另有 {catalog.herb_count} 味药、{catalog.formula_count} 首方、{catalog.syndrome_count} 个证候。"
    ]
    shown = 0
    for subject in catalog.subjects:
        header = f"【{subject.title}】{subject.edition}" + (f"（{subject.editor}）" if subject.editor else "")
        # 命中的学科展开到章；没命中的只报规模。命中为空（没有具体指向）时
        # 按顺序展开前几门——那是「这门库里都有什么」的自我介绍场景。
        expanded = subject.key in matched or (not matched and shown < full_subjects)
        if not expanded:
            lines.append(f"{header} · {len(subject.chapters)} 章 {subject.section_count} 节 {subject.entry_count} 条（本次未展开）")
            continue
        shown += 1
        lines.append(f"{header} · {len(subject.chapters)} 章 {subject.section_count} 节 {subject.entry_count} 条")
        for part in subject.parts():
            line = _chapter_line(subject, part)
            if line:
                lines.append(f"  {line}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 给骨架 / 材料用的结构
# ---------------------------------------------------------------------------


def branch_slots(coverage: Coverage) -> list[str]:
    """一级分支的建议槽位：真实存在的篇名，或点到某章时用它的节名。

    只给「库里确实这么分」的东西。点不到章就给篇（总论 / 各论），
    不给「概念界定 / 组成 / 应用」这类与教材无关的通用骨架。
    """

    if coverage.chapter is not None:
        sections = [section.title for section in coverage.chapter[1].sections if section.title]
        if len(sections) >= 2:
            return sections[:8]
        return []
    slots: list[str] = []
    for subject in coverage.subjects:
        for part in subject.parts():
            if part and part not in slots:
                slots.append(part)
    return slots[:8]


def toc_rows(coverage: Coverage, *, limit: int) -> list[dict[str, str]]:
    """教材目录行，结构与 ``knowledge_node`` 一致（id/name/level/parent_id）。

    点到某章时往下沉一层：这一章的节与条目才是材料。
    """

    rows: list[dict[str, str]] = []
    if coverage.chapter is not None:
        subject, chapter = coverage.chapter
        parent = f"{subject.key}.c{chapter.no:02d}"
        for section in chapter.sections:
            rows.append({"id": f"{parent}.{section.title}", "name": section.title, "level": "subtype", "parent_id": parent})
            if len(rows) >= limit:
                return rows
            for entry in section.entries:
                rows.append({"id": f"{parent}.{section.title}.{entry}", "name": entry, "level": "point", "parent_id": f"{parent}.{section.title}"})
                if len(rows) >= limit:
                    return rows
        return rows
    # 两门课一起问时，章要先均分名额。按学科顺序一路填到底，第二个学科
    # 会一章都拿不到——「中药和方剂学的对比」于是只有方剂学的目录。
    quota = max(limit // len(coverage.subjects), 8)
    for subject in coverage.subjects:
        for chapter in subject.chapters[:quota]:
            parent = f"{subject.key}.{chapter.part}"
            rows.append({"id": f"{subject.key}.c{chapter.no:02d}", "name": chapter.title, "level": "domain", "parent_id": parent})
    for subject in coverage.subjects:
        for chapter in subject.chapters:
            for section in chapter.sections:
                if len(rows) >= limit:
                    return rows
                rows.append({"id": f"{subject.key}.c{chapter.no:02d}.{section.title}", "name": section.title, "level": "subtype", "parent_id": f"{subject.key}.c{chapter.no:02d}"})
    return rows


def groups(coverage: Coverage, *, limit: int) -> dict[str, list[str]]:
    """「某个名字下面有这些条目」。给骨架按分支名取回真实子项用。

    **放不下就不给**：`limit` 是渲染上限，硬塞进来会被下游静默截断，
    用户看到的是「各论只有 10 章」——少掉的那 11 章不会有任何提示。
    与其悄悄丢，不如不给，让模型按材料自己分组。
    """

    result: dict[str, list[str]] = {}
    if coverage.chapter is not None:
        subject, chapter = coverage.chapter
        names = [section.title for section in chapter.sections if section.title]
        if 0 < len(names) <= limit:
            result[chapter.title] = names
        for section in chapter.sections:
            entries = list(section.entries)
            if 0 < len(entries) <= limit:
                result[section.title] = entries
        return result
    for subject in coverage.subjects:
        for part in subject.parts():
            names = [chapter.title for chapter in subject.chapters_of(part)]
            if 0 < len(names) <= limit:
                result[part] = names
        for chapter in subject.chapters:
            names = [section.title for section in chapter.sections if section.title]
            if 0 < len(names) <= limit:
                result[chapter.title] = names
    return result


def source_ref(coverage: Coverage) -> dict[str, str]:
    """教材目录本身作为一条可引用的出处。

    这条引用不是说「内容来自某一页」，而是「结构来自这本书的目录」——
    对于整门课的概览，目录就是依据。
    """

    subject = coverage.subjects[0]
    edition = "、".join(filter(None, [subject.edition, subject.editor]))
    if coverage.chapter is not None:
        chapter = coverage.chapter[1]
        return {
            "name": chapter.title,
            "type": "教材目录",
            "ref": f"《{subject.title}》{edition}·第{chapter.no}章 {chapter.title}",
            "quote": "／".join(section.title for section in chapter.sections)[:120],
        }
    return {
        "name": subject.title,
        "type": "教材目录",
        "ref": f"《{subject.title}》{edition}·目录",
        "quote": "；".join(
            f"{part}：{'／'.join(chapter.title for chapter in subject.chapters_of(part))}" for part in subject.parts()
        )[:200],
    }


# ---------------------------------------------------------------------------
# 名字核对：这个词库里到底有没有
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _known_names() -> frozenset[str]:
    """库里真实存在的名字集合。用来**逐节点**判依据，而不是整棵树一刀切。

    只收正名与教材条目名，不做模糊匹配——模糊匹配会把「与中医学关系」这种
    模型自己攒的短语也算成教材有的，那是另一种骗人。
    """

    names: set[str] = set()
    for subject in load_catalog().subjects:
        names.add(subject.title)
        names.update(subject.parts())
        for chapter in subject.chapters:
            names.add(chapter.title)
            for section in chapter.sections:
                names.add(section.title)
                names.update(section.entries)
    for herb in load_herbs():
        names.add(herb.name)
        names.update(alias for alias in herb.aliases if alias)
    for key, item in load_formulas().items():
        names.add(str(item.get("name") or key))
    names.update(load_syndromes())
    return frozenset(name for name in names if name)


def knows(name: str) -> bool:
    """这个名字库里有没有。"""

    return str(name or "").strip() in _known_names()


#: 节点里并列多个条目时的分隔符。「四气、五味、升降浮沉、归经」是一个节点，
#: 但它列的四个都是书上的节——这种情况按并列逐项核，不能整条判成推断。
_NODE_SPLIT: Final = re.compile(r"[、／/，,；;]+")


def covers_node(topic: str) -> bool:
    """一个节点名算不算「书上有」。

    精确命中优先；否则拆开并列写法逐项核——**要求每一项都是书上的**，
    有一项不在就是模型自己攒的，不能因为前半句是真的就整条放过。
    """

    text = " ".join(str(topic or "").split())
    if not text:
        return False
    names = _known_names()
    if text in names:
        return True
    parts = [part.strip() for part in _NODE_SPLIT.split(text) if part.strip()]
    if len(parts) < 2:
        return False
    return all(part in names for part in parts)


def covers_text(text: str) -> bool:
    """这句话有没有点到本库的某门课。根节点标题用它判。"""

    return bool(subjects_in(text))
