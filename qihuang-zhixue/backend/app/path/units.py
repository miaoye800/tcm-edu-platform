"""学习单元：把「图里的三层」翻译成「教材里可导航的三层」。

## 为什么需要这一层

知识图只有 ``domain / subtype / point`` 三级，但**这三级在不同学科里指的不是同一件事**：

* 中药学各论 ``d01 解表药``：domain = 章，subtype = 节（发散风寒药），point = 药。
* 中药学总论 ``general``：domain 是**篇级容器**，14 个 subtype 分属教材第一章到第七章，
  章号只藏在 ``extra.chapter_no`` 里。于是"总论"这一个容器顶掉了前七章。
* 中医内科学 ``n02 肺系病证``：domain = 章，subtype = 节（感冒），point 与节同名。
* 中医诊断学 ``z02 望诊``：5 个 subtype 才是真正的节，point 只是同名占位。
* ``d05 化湿药``：章下只有一个同名 subtype，教材里这一章不分节——它是"退化壳"。

规划器此前只认 ``level='domain'``，等于把上述五种结构一律当成"章"。用户看到的
安排表因此同时出现两类错误：篇容器顶掉七章、章名顶掉多个节。这不是某一门课的数据
问题，是**规划单位被绑死在图的一层，而那一层没有固定语义**。

## 判定规则（三层梯子，逐级下降）

1. **有小节用小节**：教材大纲里该节有 ``subsections``（本项目目前没有一本书到这一层，
   规则保留：一旦大纲补上小节，规划单位自动落到小节，不需要改代码）。
2. **无小节但有节用节**：该章在教材里确实分节（``subtype`` 名与章名不同，或不止一个），
   单位就是节。
3. **既无小节也无节才用章**：该章只有一个与章同名的节（``virtual``，即教材该章不分节），
   单位是章本身。

单位名**只写节/小节名，不附加章名**（"发散风寒药"，不写"解表药·发散风寒药"）。
章的信息保留在 ``chapter_title`` 字段里，由界面用分组/缩进表达，而不是拼进标签。

## 教材大纲是本地文件

``data/syllabus/<subject>.json`` 是各科教材的章节目录（出版社、版次、目录来源都写在
``textbook`` 字段里，随仓库走）。这里只读它来回答两个问题：这个节在教材里叫什么、
属于第几章、是不是"该章不分节"。读不到（自定义学科、测试图）就退回**只依据图结构**
的判定，行为与加这一层之前一致。

## 一条不变式：单元集合 == 穴位集合

经脉图里每个"穴位"就是一个节（``map_section``，一穴一节）。规划单元与它必须是同一批：
否则确认完的路线会在地图上点不到（穴位不在路线里），或者地图上的穴位永远点不亮
（路线里没有它）。实测 293 个穴位里有 3 个对不上，全部来自中药学总论那个历史容器
``general.s1 基本理论``——它把教材第四章的"升降浮沉""归经"和第六章的"配伍禁忌"
装在一个没有教材依据的筐里。

所以解析完再做一次**归位**（:meth:`Curriculum._reconcile`）：只补教材里有、当前还没有
单元的节，把名字恰好等于该节名的知识点挪过去。**绝不从已有单元里抢点**，重名的节
（整本书出现两次以上）一律不动——宁可不归位，也不要归错。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.config import BACKEND_ROOT
from app.path.subjects import subject_of, title_of

SYLLABUS_DIR = BACKEND_ROOT / "data" / "syllabus"

#: 单位层级。``chapter`` 只在该章既无小节也无节时出现。
UnitKind = str  # Literal["chapter", "section", "subsection"]，用常量收敛
KIND_CHAPTER = "chapter"
KIND_SECTION = "section"
KIND_SUBSECTION = "subsection"

#: 教材里"成组的条目"（药、方）——它们的子节点是条目而不是小节，不能拿来当单位。
_GROUP_KINDS = frozenset({"herb_group", "formula_group"})


class UnitError(ValueError):
    """单元解析失败。调用方应当退回章级（不能因为一门课的大纲坏了就排不出路线）。"""


@dataclass(frozen=True)
class StudyUnit:
    """一个可取舍、可排课、可点亮穴位的学习单元。"""

    #: 稳定的行标识。单单位的域直接用 ``domain_id``（与历史草稿、老测试兼容），
    #: 多单位的域用 ``subtype_id``（全局唯一）。
    id: str
    domain_id: str
    kind: UnitKind
    title: str
    #: 所属教材章。**不拼进标题**：界面用它分组，标签只写节名。
    chapter_title: str
    chapter_no: int
    section_no: int
    part: str
    subject_id: str
    subject_name: str
    point_ids: tuple[str, ...]
    #: 该章在教材里不分节（节名 == 章名，或大纲标了 virtual）。
    virtual: bool = False
    #: 教材大纲里这个节是不是"成组条目"（药组 / 方剂组）。
    group: str = ""
    #: 排在整份路线里的次序键：学科 → 章号 → 节号 → id。
    order: tuple[int, int, int, str] = (0, 0, 0, "")
    #: 解析过程中的提示（非教材结构、名称重复等）。核对报表会逐条列出。
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def point_count(self) -> int:
        return len(self.point_ids)

    def to_row(self) -> dict[str, Any]:
        """落到草稿 payload 里的形状。字段名与前端 ``DraftStageRow`` 对应。"""

        return {
            "stage_id": self.id,
            "unit_id": self.id,
            "unit_kind": self.kind,
            "unit_title": self.title,
            "domain_id": self.domain_id,
            "name": self.title,
            "chapter_title": self.chapter_title,
            "chapter_no": self.chapter_no,
            "section_no": self.section_no,
            "part": self.part,
            "subject_id": self.subject_id,
            "subject_name": self.subject_name,
            "point_count": self.point_count,
            "point_ids": list(self.point_ids),
            "virtual": self.virtual,
            "group": self.group,
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- 教材大纲


@dataclass(frozen=True)
class OutlineSection:
    title: str
    no: int
    kind: str
    virtual: bool
    subsections: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutlineChapter:
    title: str
    no: int
    book_no: int
    part: str
    sections: tuple[OutlineSection, ...]


@dataclass(frozen=True)
class Outline:
    subject_id: str
    title: str
    textbook: Mapping[str, Any]
    chapters: tuple[OutlineChapter, ...]

    def chapter_by_title(self, title: str) -> OutlineChapter | None:
        clean = _clean(title)
        for chapter in self.chapters:
            if _clean(chapter.title) == clean:
                return chapter
        return None

    def chapter_by_no(self, no: int, *, book: bool = False) -> OutlineChapter | None:
        for chapter in self.chapters:
            if (chapter.book_no if book else chapter.no) == no:
                return chapter
        return None


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split()).replace("　", "")


@lru_cache(maxsize=16)
def _load_outline(path_str: str) -> Outline | None:
    path = Path(path_str)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    chapters: list[OutlineChapter] = []
    for item in raw.get("chapters") or []:
        if not isinstance(item, Mapping):
            continue
        sections = tuple(
            OutlineSection(
                title=_clean(section.get("title")),
                no=int(section.get("no") or 0),
                kind=str(section.get("kind") or ""),
                virtual=bool(section.get("virtual")),
                subsections=tuple(
                    _clean(name)
                    for name in (section.get("subsections") or [])
                    if _clean(name)
                ),
            )
            for section in item.get("sections") or []
            if isinstance(section, Mapping)
        )
        chapters.append(
            OutlineChapter(
                title=_clean(item.get("title")),
                no=int(item.get("no") or 0),
                # **不能用 `or` 兜底**：书上第 0 章是合法值（绪论/总论），
                # `0 or no` 会把它变成 1，界面于是把「绪论」写成「第一章」。
                book_no=int(item["no"] if item.get("book_no") is None else item["book_no"]),
                part=str(item.get("part") or ""),
                sections=sections,
            )
        )
    return Outline(
        subject_id=str(raw.get("subject") or path.stem),
        title=_clean(raw.get("title")),
        textbook=raw.get("textbook") or {},
        chapters=tuple(chapters),
    )


def outline_for(subject_id: str, *, directory: Path = SYLLABUS_DIR) -> Outline | None:
    """取一门课的教材大纲；没有就返回 None（调用方退回只依赖图结构）。"""

    if not subject_id:
        return None
    return _load_outline(str(directory / f"{subject_id}.json"))


# --------------------------------------------------------------------------- 单元解析


def _field(node: Any, name: str, default: Any = None) -> Any:
    """节点可能是 pydantic 模型、ORM dict 或 store 的裸 dict。一处收口。"""

    if isinstance(node, Mapping):
        value = node.get(name, default)
    else:
        value = getattr(node, name, default)
    return default if value is None else value


def _extra(node: Any) -> dict[str, Any]:
    extra = _field(node, "extra", {}) or _field(node, "extra_json", {}) or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except json.JSONDecodeError:
            extra = {}
    return dict(extra) if isinstance(extra, Mapping) else {}


class Curriculum:
    """一棵知识树的「教材视图」。按需解析每个域的单元，结果缓存。

    构造一次、复用多次：规划一次要问几十个域，逐域重建父子索引是纯浪费。
    """

    def __init__(self, nodes: Iterable[Any], *, syllabus_dir: Path = SYLLABUS_DIR) -> None:
        self._nodes: dict[str, Any] = {}
        self._children: dict[str, list[str]] = {}
        for node in nodes:
            node_id = str(_field(node, "id", ""))
            if not node_id:
                continue
            self._nodes[node_id] = node
            parent = _field(node, "parent_id", None)
            if parent:
                self._children.setdefault(str(parent), []).append(node_id)
        self._syllabus_dir = syllabus_dir
        self._raw: dict[str, list[StudyUnit]] = {}
        self._built: list[StudyUnit] | None = None
        self._by_id: dict[str, StudyUnit] = {}
        self._point_unit: dict[str, str] = {}

    # -- 查询 -------------------------------------------------------------

    def domain_ids(self) -> list[str]:
        return [node_id for node_id, node in self._nodes.items() if _field(node, "level") == "domain"]

    def units_of(self, domain_id: str) -> list[StudyUnit]:
        return [unit for unit in self.all_units() if unit.domain_id == domain_id]

    def all_units(self) -> list[StudyUnit]:
        if self._built is None:
            self._built = self._build()
            self._by_id = {unit.id: unit for unit in self._built}
            self._point_unit = {}
            for unit in self._built:
                for point_id in unit.point_ids:
                    self._point_unit.setdefault(point_id, unit.id)
        return self._built

    def unit(self, unit_id: str) -> StudyUnit | None:
        self.all_units()
        return self._by_id.get(unit_id)

    def unit_of_point(self, point_id: str) -> StudyUnit | None:
        self.all_units()
        unit_id = self._point_unit.get(point_id)
        return self._by_id.get(unit_id) if unit_id else None

    def units_for_points(self, point_ids: Iterable[str]) -> list[StudyUnit]:
        wanted = {str(item) for item in point_ids}
        out: list[StudyUnit] = []
        for unit in self.all_units():
            if wanted & set(unit.point_ids):
                out.append(unit)
        return out

    # -- 解析 -------------------------------------------------------------

    def _build(self) -> list[StudyUnit]:
        """整棵树一次性解析，**按学科归位**后再返回。

        归位必须跨域做："教材有这一节、图里没建节点"是**学科级**的缺口，
        逐域判断会拿别的章的节去抢本域的点（实测把中药学`五味`塞进了补虚药）。
        """

        by_subject: dict[str, list[StudyUnit]] = {}
        domain_order: dict[str, int] = {}
        for index, domain_id in enumerate(self.domain_ids()):
            domain_order[domain_id] = index
            units = self._dedupe(self._resolve(domain_id))
            by_subject.setdefault(subject_of(domain_id), []).extend(units)

        out: list[StudyUnit] = []
        for subject_id, units in by_subject.items():
            out.extend(self._reconcile_subject(subject_id, units))
        # 同域内保持教材顺序（章号 → 节号），认不出章节的仍然沉到该域末尾
        out.sort(key=lambda unit: (domain_order.get(unit.domain_id, 999), *unit.order[1:]))
        return out

    @staticmethod
    def _dedupe(units: list[StudyUnit]) -> list[StudyUnit]:
        """同一域内同名同点的重复节（历史遗留）只留第一个，安排表不该出现两行一样的东西。"""

        seen: set[tuple[str, tuple[str, ...]]] = set()
        unique: list[StudyUnit] = []
        for unit in units:
            key = (unit.title, unit.point_ids)
            if key in seen:
                continue
            seen.add(key)
            unique.append(unit)
        return unique

    def _resolve(self, domain_id: str) -> list[StudyUnit]:
        domain = self._nodes.get(domain_id)
        if domain is None or _field(domain, "level") != "domain":
            return []
        subject_id = subject_of(domain_id)
        outline = outline_for(subject_id, directory=self._syllabus_dir)
        domain_name = _clean(_field(domain, "name", domain_id))
        domain_extra = _extra(domain)

        subtypes = [
            node_id
            for node_id in self._children.get(domain_id, [])
            if _field(self._nodes[node_id], "level") == "subtype"
        ]
        points_here = self._descendant_points(domain_id)

        # 章级基线：教材大纲按标题认章；认不到就用图里的 sort_hint。
        chapter = outline.chapter_by_title(domain_name) if outline else None
        chapter_title = chapter.title if chapter else (_clean(domain_extra.get("chapter")) or domain_name)
        chapter_no = (
            chapter.book_no
            if chapter
            else _as_int(domain_extra.get("chapter_no"), _as_int(_field(domain, "sort_hint"), 0))
        )
        part = chapter.part if chapter else str(domain_extra.get("part") or "")
        # 排序键里的章号：教材认得的章按书上次序（绪论是 0，排最前）；
        # 认不出的沉到 900，别因为"章号 0"就把它当第一章。
        chapter_order = chapter.book_no if chapter else 900

        if not subtypes:
            return (
                [
                    self._chapter_unit(
                        domain_id=domain_id,
                        title=chapter_title,
                        chapter_title=chapter_title,
                        chapter_no=chapter_no,
                        section_no=0,
                        part=part,
                        subject_id=subject_id,
                        point_ids=points_here,
                        chapter_order=chapter_order,
                        notes=("这一章在图里没有分节节点，按章排",),
                    )
                ]
                if points_here
                else []
            )

        units: list[StudyUnit] = []
        for subtype_id in self._sorted_subtypes(
            subtypes, outline=outline, chapter_no=chapter_no, chapter_title=chapter_title
        ):
            subtype = self._nodes[subtype_id]
            extra = _extra(subtype)
            point_ids = self._descendant_points(subtype_id)
            if not point_ids:
                continue
            title = _clean(_field(subtype, "name", subtype_id))
            st_chapter_title = _clean(extra.get("chapter")) or chapter_title
            # 章号以**教材**为准：图里的 `extra.chapter_no` 是连续的内部编号，
            # 绪论/总论在书上是不编号的（book_no=0）。照图里的 1 排，界面会把
            # 「绪论」标成「第一章」，而这三本教材都没有"第零章"这回事。
            outline_chapter = outline.chapter_by_title(st_chapter_title) if outline else None
            if outline_chapter is not None:
                st_chapter_title = outline_chapter.title
                st_chapter_no = outline_chapter.book_no
            else:
                st_chapter_no = _as_int(extra.get("chapter_no"), chapter_no)
            st_section_no = _as_int(extra.get("section_no"), _as_int(_field(subtype, "sort_hint"), 0))
            # 书上第 0 章（绪论/总论）排在最前；认不出章节的排到最后。
            chapter_order = st_chapter_no if outline_chapter is not None else 900
            outline_section = self._outline_section(outline, st_chapter_title, title, st_chapter_no, st_section_no)
            if outline_section is not None and outline_section.no:
                # 教材节的先后以**目录**为准，不以图里的 sort_hint 为准。
                # 补虚药那条链上 sort_hint 是乱序的（补气/补阳/补血/补阴），
                # 照它排会让"补血药"排在"补阳药"后面。
                st_section_no = outline_section.no
            kind = outline_section.kind if outline_section else ""
            virtual = bool(outline_section.virtual) if outline_section else title == domain_name
            notes: list[str] = []
            if outline_section is None and outline is not None:
                notes.append("教材目录里没有这一节，按图结构处理")

            subsections = outline_section.subsections if outline_section else ()
            if subsections:
                units.extend(
                    self._subsection_units(
                        domain_id=domain_id,
                        subtype_id=subtype_id,
                        parent_title=title,
                        chapter_title=st_chapter_title,
                        chapter_no=st_chapter_no,
                        section_no=st_section_no,
                        part=part,
                        subject_id=subject_id,
                        point_ids=point_ids,
                        subsections=subsections,
                    )
                )
                continue

            if virtual:
                # 教材该章不分节：单位是章，名字用章名（不是那个占位节名）。
                units.append(
                    self._chapter_unit(
                        domain_id=domain_id,
                        title=st_chapter_title or domain_name,
                        chapter_title=st_chapter_title or domain_name,
                        chapter_no=st_chapter_no,
                        section_no=st_section_no,
                        part=part,
                        subject_id=subject_id,
                        point_ids=point_ids,
                        virtual=True,
                        unit_id=self._unit_id(domain_id, subtype_id, single=len(subtypes) == 1),
                        chapter_order=chapter_order,
                        notes=tuple(notes),
                    )
                )
                continue

            unit_id = self._unit_id(domain_id, subtype_id, single=len(subtypes) == 1)
            units.append(
                StudyUnit(
                    id=unit_id,
                    domain_id=domain_id,
                    kind=KIND_SECTION,
                    title=title,
                    chapter_title=st_chapter_title,
                    chapter_no=st_chapter_no,
                    section_no=st_section_no,
                    part=part,
                    subject_id=subject_id,
                    subject_name=title_of(subject_id),
                    point_ids=tuple(point_ids),
                    virtual=False,
                    group=kind if kind in _GROUP_KINDS else "",
                    order=(0, chapter_order, st_section_no, unit_id),
                    notes=tuple(notes),
                )
            )
        return units

    def _reconcile_subject(self, subject_id: str, units: list[StudyUnit]) -> list[StudyUnit]:
        """把"教材有这一节、但图里没建对应节点"的知识点归位到那一节。

        只在**这一节还没有单元**时动手，且节名必须在整本教材里唯一。动完重排：
        新单元的章号来自教材目录，留在原地的遗留单元仍然沉到末尾。

        两级匹配，先严后宽：先认名字完全相同的点，再对**遗留容器里的**点做"包含"
        匹配，且只有唯一候选时才归位。`性味归经` 这类合称点因此能落到"归经"，
        而 `中药` 这种能对上好几个节名的短名一律不动——宁可不归位，不要归错。
        """

        outline = outline_for(subject_id, directory=self._syllabus_dir)
        if outline is None or not units:
            return units

        index: dict[str, tuple[OutlineChapter, OutlineSection]] = {}
        ambiguous: set[str] = set()
        for chapter in outline.chapters:
            for section in chapter.sections:
                key = _clean(section.title)
                if not key:
                    continue
                if key in index:
                    ambiguous.add(key)
                index[key] = (chapter, section)
        for key in ambiguous:
            index.pop(key, None)

        # **章单位也算覆盖**：教材不分节的那一章，单元名就是章名，
        # 它已经代表那一节了。漏掉它会把这 18 个章级单元整个拆掉重建成同名节级单元，
        # 行 id 从 `f01` 变成 `f01.1.1`，历史草稿立刻对不上。
        covered = {_clean(unit.title) for unit in units}
        missing = {title: item for title, item in index.items() if title not in covered}
        if not missing:
            return units

        chapter_titles = {_clean(chapter.title) for chapter in outline.chapters}

        # 一级：名字完全相同
        target: dict[str, str] = {}  # point_id -> 节名
        for unit in units:
            for point_id in unit.point_ids:
                name = _clean(_field(self._nodes.get(point_id), "name", ""))
                if name in missing:
                    target[point_id] = name

        # 二级：只在"教材里查不到这个容器"的遗留单元里做包含匹配
        for unit in units:
            if _clean(unit.title) in index or _clean(unit.chapter_title) in chapter_titles:
                continue
            for point_id in unit.point_ids:
                if point_id in target:
                    continue
                name = _clean(_field(self._nodes.get(point_id), "name", ""))
                if len(name) < 2:
                    continue
                candidates = [title for title in missing if name in title or title in name]
                if len(candidates) == 1:
                    target[point_id] = candidates[0]

        if not target:
            return units

        created: dict[str, list[str]] = {}
        owners: dict[str, str] = {}
        trimmed: list[StudyUnit] = []
        for unit in units:
            keep = [point_id for point_id in unit.point_ids if point_id not in target]
            for point_id in unit.point_ids:
                if point_id in target:
                    created.setdefault(target[point_id], []).append(point_id)
                    owners.setdefault(target[point_id], unit.domain_id)
            if len(keep) == len(unit.point_ids):
                trimmed.append(unit)
                continue
            if not keep:
                # 整个单元都被归位走了：它本来就没有教材依据，不再占一行
                continue
            trimmed.append(replace(unit, point_ids=tuple(keep)))

        for title, point_ids in created.items():
            chapter, section = missing[title]
            domain_id = owners.get(title, "")
            # 与图里既有节同一套写法（`general.c04.s3`），便于和地图/日志对照
            unit_id = f"{domain_id or subject_id}.c{chapter.book_no:02d}.s{section.no}"
            trimmed.append(
                StudyUnit(
                    id=unit_id,
                    domain_id=domain_id,
                    kind=KIND_SECTION,
                    title=title,
                    chapter_title=chapter.title,
                    chapter_no=chapter.book_no,
                    section_no=section.no,
                    part=chapter.part,
                    subject_id=subject_id,
                    subject_name=title_of(subject_id),
                    point_ids=tuple(point_ids),
                    order=(0, chapter.book_no, section.no, unit_id),
                    notes=("教材有这一节、图里没有对应节点，已按知识点名归位",),
                )
            )
        return sorted(trimmed, key=lambda unit: unit.order)

    def _subsection_units(
        self,
        *,
        domain_id: str,
        subtype_id: str,
        parent_title: str,
        chapter_title: str,
        chapter_no: int,
        section_no: int,
        part: str,
        subject_id: str,
        point_ids: list[str],
        subsections: tuple[str, ...],
    ) -> list[StudyUnit]:
        """大纲声明了小节：按小节切点集。

        切法按**名称匹配**：知识点名以小节名开头/相等就归该小节。匹配不到任何
        小节的知识点留在最后一节，不丢——宁可多带一个，也不要让用户确认完发现少了。
        """

        buckets: dict[str, list[str]] = {name: [] for name in subsections}
        leftovers: list[str] = []
        for point_id in point_ids:
            name = _clean(_field(self._nodes[point_id], "name", ""))
            match = next((item for item in subsections if item and (name.startswith(item) or item.startswith(name))), None)
            if match is None:
                leftovers.append(point_id)
            else:
                buckets[match].append(point_id)
        units: list[StudyUnit] = []
        for index, name in enumerate(subsections, 1):
            bucket = list(buckets[name])
            if index == len(subsections):
                bucket = [*bucket, *leftovers]
            if not bucket:
                continue
            unit_id = f"{subtype_id}#{index}"
            units.append(
                StudyUnit(
                    id=unit_id,
                    domain_id=domain_id,
                    kind=KIND_SUBSECTION,
                    title=name,
                    chapter_title=chapter_title,
                    chapter_no=chapter_no,
                    section_no=section_no,
                    part=part,
                    subject_id=subject_id,
                    subject_name=title_of(subject_id),
                    point_ids=tuple(bucket),
                    order=(0, chapter_no, section_no, unit_id),
                    notes=(f"教材「{parent_title}」下的小节",),
                )
            )
        return units

    def _chapter_unit(
        self,
        *,
        domain_id: str,
        title: str,
        chapter_title: str,
        chapter_no: int,
        section_no: int,
        part: str,
        subject_id: str,
        point_ids: list[str],
        virtual: bool = False,
        unit_id: str | None = None,
        chapter_order: int | None = None,
        notes: tuple[str, ...] = (),
    ) -> StudyUnit:
        return StudyUnit(
            id=unit_id or domain_id,
            domain_id=domain_id,
            kind=KIND_CHAPTER,
            title=title,
            chapter_title=chapter_title,
            chapter_no=chapter_no,
            section_no=section_no,
            part=part,
            subject_id=subject_id,
            subject_name=title_of(subject_id),
            point_ids=tuple(point_ids),
            virtual=virtual,
            order=(0, chapter_order if chapter_order is not None else (chapter_no or 900), section_no, unit_id or domain_id),
            notes=notes,
        )

    def _unit_id(self, domain_id: str, subtype_id: str, *, single: bool) -> str:
        """行标识：单单位的域沿用 ``domain_id``。

        这不是为了好看。历史草稿、已确认的 l1 payload、以及一串调用方都拿
        ``domain_id`` 当行键；一个域只有一个单位时换个新 id 只是制造不兼容。
        多单位的域才需要 subtype id 来区分。
        """

        return domain_id if single else subtype_id

    def _sorted_subtypes(
        self,
        subtypes: list[str],
        *,
        outline: Outline | None,
        chapter_no: int,
        chapter_title: str,
    ) -> list[str]:
        """节序：**章号 → 教材节号 → 图里的 sort_hint → id**。

        课本章号只在"篇容器"（如中药学总论）里才出现在节的 ``extra`` 上；
        教材节号优先于 ``sort_hint``，原因见调用处。认不出教材节时排到最后：
        那是"图里有、教材没有"的遗留节点，放在前面会顶掉真正的第一章。
        """

        def key(subtype_id: str) -> tuple[int, int, int, str]:
            node = self._nodes[subtype_id]
            extra = _extra(node)
            title = _clean(_field(node, "name", subtype_id))
            st_chapter_no = _as_int(extra.get("chapter_no"), chapter_no)
            section = self._outline_section(
                outline,
                _clean(extra.get("chapter")) or chapter_title,
                title,
                st_chapter_no,
                _as_int(extra.get("section_no"), _as_int(_field(node, "sort_hint"), 0)),
            )
            if section is not None:
                section_no = section.no
            elif outline is not None:
                section_no = 900 + _as_int(extra.get("section_no"), _as_int(_field(node, "sort_hint"), 0))
            else:
                section_no = _as_int(extra.get("section_no"), _as_int(_field(node, "sort_hint"), 0))
            return (st_chapter_no if st_chapter_no else 900, section_no, subtype_id)

        return sorted(subtypes, key=key)

    def _outline_section(
        self,
        outline: Outline | None,
        chapter_title: str,
        section_title: str,
        chapter_no: int,
        section_no: int,
    ) -> OutlineSection | None:
        if outline is None:
            return None
        chapter = outline.chapter_by_title(chapter_title)
        if chapter is None and chapter_no:
            chapter = outline.chapter_by_no(chapter_no, book=True) or outline.chapter_by_no(chapter_no)
        if chapter is None:
            return None
        for section in chapter.sections:
            if _clean(section.title) == _clean(section_title):
                return section
        for section in chapter.sections:
            if section_no and section.no == section_no:
                return section
        return None

    def _descendant_points(self, root_id: str) -> list[str]:
        out: list[str] = []
        pending = list(self._children.get(root_id, []))
        while pending:
            node_id = pending.pop()
            node = self._nodes.get(node_id)
            if node is None:
                continue
            if _field(node, "level") == "point":
                out.append(node_id)
            else:
                pending.extend(self._children.get(node_id, []))
        return sorted(out, key=lambda item: (_as_int(_field(self._nodes[item], "sort_hint"), 0), item))


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def resolve_units(nodes: Iterable[Any], domain_id: str, *, syllabus_dir: Path = SYLLABUS_DIR) -> list[StudyUnit]:
    """一次性使用时的便利入口。批量请用 :class:`Curriculum`。"""

    return Curriculum(nodes, syllabus_dir=syllabus_dir).units_of(domain_id)


def order_units(units: Iterable[StudyUnit], *, subject_order: Mapping[str, int] | None = None) -> list[StudyUnit]:
    """整份路线的单元顺序：学科 → 章号 → 节号。"""

    return sorted(
        units,
        key=lambda unit: (
            (subject_order or {}).get(unit.subject_id, 0),
            *unit.order[1:],
        ),
    )


__all__ = [
    "KIND_CHAPTER",
    "KIND_SECTION",
    "KIND_SUBSECTION",
    "SYLLABUS_DIR",
    "Curriculum",
    "Outline",
    "OutlineChapter",
    "OutlineSection",
    "StudyUnit",
    "UnitError",
    "order_units",
    "outline_for",
    "resolve_units",
]
