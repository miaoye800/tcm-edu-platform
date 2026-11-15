"""把「用户那一整句话」读成一份可执行、可展示、可核对的写作施工图。

这个模块替掉的是同一类做法：以前「这篇文档长什么样」由两处关键词规则回答——
``docs.agent._KIND_RULES`` 认体裁、``resources.planning._SHAPE_BLUEPRINTS`` 挑章节。
两处互不相识，于是出现下面这一串**同一个病**的症状：

- 用户说「我过四级又要学中药学，应该怎么做」——命中「计划」二字，于是不管他还说了
  什么，都长出「目标与现状 / 前置准备 / 阶段路线 / 练习与自测 / 调整与复盘」五节；
- 方案卡上的「目标」是一句拼接模板（``写一份关于「{原句}」的{组织方式}``），
  「受众」永远是「通用读者」——它们不是读出来的，是拼出来的；
- 标题是原句加个后缀（``…我应该怎么做·规划``），把用户的话当成了书名；
- 生成侧读 ``design``，方案侧写 ``units``，两边各算各的，面板上改的篇幅到不了正文。

病根还是**没有人真的去读那句话**。所以这里只做三件事：

1. ``compose_doc_spec``：把整句话交给模型读，产出 ``DocSpec``（什么体裁、给谁、
   怎么排、分几节、每节该长什么样、必须覆盖什么）。模型管决策。
2. ``baseline_spec``：无模型时的确定性基线，**标 ``basis="rules"``**。它不假装
   读懂了，方案卡上会写「按规则拟的初稿」。
3. ``doc_params_from_plan`` / ``doc_conformance``：把确认过的那一版翻译成生成参数，
   并在成品出来后如实报「方案要 3 张表，实际 0 张」「§3 整节都是段落」。
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.structured_output import structured_create
from app.llm.router import ModelRouter
from app.mindmap import catalog
from app.mindmap.catalog import ChapterInfo, Coverage, SubjectInfo

from .prompts import DOC_SPEC_SYSTEM_PROMPT

# ── 枚举 ────────────────────────────────────────────────────────────────────

DocGenre = Literal["explain", "compare", "guide", "plan", "review", "case", "qa", "minutes", "cheatsheet"]
OrgKind = Literal["topic", "compare", "process", "timeline", "stage", "qa", "case", "review", "minutes"]
#: 一节该长什么样。不写它，模型就只会交自然段。
SectionForm = Literal["prose", "table", "steps", "checklist", "qa", "mixed"]
#: 全文级的排版要素。成品出来后按它逐项核对。
Device = Literal["table", "checklist", "steps", "timeline", "callout", "summary", "glossary"]

GENRES: Final[tuple[str, ...]] = (
    "explain", "compare", "guide", "plan", "review", "case", "qa", "minutes", "cheatsheet",
)
ORG_KINDS: Final[tuple[str, ...]] = (
    "topic", "compare", "process", "timeline", "stage", "qa", "case", "review", "minutes",
)
FORMS: Final[tuple[str, ...]] = ("prose", "table", "steps", "checklist", "qa", "mixed")
DEVICES: Final[tuple[str, ...]] = ("table", "checklist", "steps", "timeline", "callout", "summary", "glossary")

GENRE_LABEL: Final[dict[str, str]] = {
    "explain": "讲解",
    "compare": "对比分析",
    "guide": "操作指南",
    "plan": "计划",
    "review": "综述",
    "case": "案例",
    "qa": "答疑",
    "minutes": "纪要",
    "cheatsheet": "速查表",
}

#: 体裁 → 目标字数区间。讲义比答疑长，纪要比综述短，这是写作者的常识而不是偏好。
GENRE_BUDGET: dict[str, tuple[int, int]] = {
    "explain": (1600, 3200),
    "compare": (1500, 3000),
    "guide": (1200, 2600),
    "plan": (1400, 2800),
    "review": (3000, 6000),
    "case": (1500, 3200),
    "qa": (1200, 2800),
    "minutes": (800, 1800),
    "cheatsheet": (800, 2000),
}

GENRE_ORG: Final[dict[str, str]] = {
    "explain": "topic",
    "compare": "compare",
    "guide": "process",
    "plan": "stage",
    "review": "review",
    "case": "case",
    "qa": "qa",
    "minutes": "minutes",
    "cheatsheet": "topic",
}

#: 组织方式 → 给用户看的一句话。**这里是中文，不是键名**：方案卡直接展示它。
ORG_LINE: Final[dict[str, str]] = {
    "topic": "按主题分块：是什么 → 由什么构成 → 怎么用 → 注意什么",
    "compare": "按比较维度并列展开，先给维度再逐项对照",
    "process": "按操作顺序推进，每步给出动作与判断标准",
    "timeline": "沿时间顺序推进，先交代起点，再讲转折，最后落到今天",
    "stage": "按阶段推进，每阶段给出任务、产出与检验方式",
    "qa": "按问题组织，一问一节，先答结论再解释",
    "case": "按医案叙事推进：案情 → 辨证 → 处置 → 复盘",
    "review": "按研究脉络组织：缘起 → 观点分歧 → 证据 → 待解问题",
    "minutes": "按会议议程组织：会议信息 → 议题结论 → 待办",
}

#: 体裁线索。顺序有意义：纪要先于案例，计划先于指南（「怎么学」是计划不是步骤）。
_GENRE_CUES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("minutes", ("会议纪要", "会议记录", "纪要", "会议内容")),
    ("case", ("医案", "病案", "病例", "个案", "案例", "验案")),
    ("review", ("综述", "研究进展", "文献回顾", "学术史")),
    ("plan", ("计划", "规划", "路线", "安排", "备考", "复习", "怎么学", "学习路径", "时间表", "几个月", "几周")),
    ("compare", ("对比", "比较", "区别", "异同", "鉴别", "哪个好", "怎么选", "差别")),
    ("cheatsheet", ("速查", "一览表", "清单", "口诀", "卡片", "备忘")),
    ("guide", ("步骤", "流程", "教程", "怎么做", "如何", "操作", "上手", "指南", "注意事项")),
    ("qa", ("答疑", "问答", "常见问题", "一问一答")),
)

#: 骨架：(标题, 这一节解决什么, 呈现形态)。**只在无模型时用**，且方案卡会标 rules。
_SKELETON: Final[dict[str, tuple[tuple[str, str, str], ...]]] = {
    "topic": (
        ("先说清楚它是什么", "一句话定义，再给两三个同义说法或别称", "prose"),
        ("它由哪些部分构成", "拆结构，用一张表把构成与各自作用摆出来", "table"),
        ("为什么重要：解决什么问题", "讲清用途与价值，别喊口号", "prose"),
        ("怎么用：典型场景", "给有先后顺序的做法", "steps"),
        ("常见误区与边界", "列 3-4 条最容易错的地方", "checklist"),
        ("小结", "回收主线，给一张速记表", "table"),
    ),
    "compare": (
        ("为什么容易混", "点出混淆的来源，别急着列表", "prose"),
        ("核心差异", "先给一句判断，再给维度", "prose"),
        ("逐维对照", "同一维度下并排看两者", "table"),
        ("各自适用场景", "把差异翻译成选择建议", "table"),
        ("选择建议与记忆抓手", "给可执行的判断规则", "checklist"),
    ),
    "process": (
        ("适用场景与前置条件", "什么情况下按这个流程做", "prose"),
        ("准备清单", "材料、工具、前置知识", "checklist"),
        ("操作步骤", "按顺序拆成可执行的步骤，每步含判断标准", "steps"),
        ("关键细节与常见错误", "最容易失败的几处", "table"),
        ("验收标准", "怎么算做对了", "checklist"),
    ),
    "timeline": (
        ("源流与早期记载", "交代最早可考的出处与语境", "prose"),
        ("历代演变", "按朝代或时期梳理关键变化", "table"),
        ("转折与分歧", "说明演变中的关键节点与不同说法", "prose"),
        ("当代认识", "落到今天的教材表述或研究结论", "prose"),
        ("小结", "回收时间线，指出仍有争议之处", "prose"),
    ),
    "stage": (
        ("目标与现状", "明确终点与起点，给一张现状自查表", "table"),
        ("前置准备", "开始前需要补齐的基础", "checklist"),
        ("阶段路线", "分阶段列出任务、产出与时间刻度", "table"),
        ("练习与自检", "每个阶段怎么确认学会了", "checklist"),
        ("调整与复盘", "进度落后时怎么改", "prose"),
    ),
    "qa": (
        ("这些问题从哪来", "把零散的疑问归成几类", "prose"),
        ("逐条回答", "一问一节，先答结论再解释", "qa"),
        ("容易误解的地方", "把常见错解与正解并排放", "table"),
        ("再追问一层", "答完以后最自然的两个追问", "qa"),
        ("小结", "收束成三句话", "prose"),
    ),
    "case": (
        ("案情", "时间、人物、主诉、舌脉，按接诊顺序写", "prose"),
        ("辨证思路", "从症到证的推理链", "steps"),
        ("处置与依据", "方药、剂量与出处", "table"),
        ("复盘", "哪些判断是关键，哪些可商榷", "prose"),
        ("可迁移的经验", "下次遇到同类情况先看什么", "checklist"),
    ),
    "review": (
        ("缘起与范围", "这个问题是怎么被提出来的", "prose"),
        ("主要观点与分歧", "把不同说法并排放", "table"),
        ("证据强度", "哪些有教材或研究支撑，哪些还只是经验", "table"),
        ("尚待解决的问题", "把空白点列出来", "checklist"),
        ("小结", "收束，不站队但要指出方向", "prose"),
    ),
    "minutes": (
        ("会议信息", "时间、地点、参会人、议题清单", "table"),
        ("议题与结论", "一条议题一段结论", "prose"),
        ("待办与责任人", "谁在什么时候做什么", "checklist"),
        ("分歧与待定", "没有当场定下来的事", "prose"),
    ),
}

#: 单味药主题的骨架。药材条目本身就是按这个结构组织的，照它写才对得上教材。
_MONOGRAPH: Final[tuple[tuple[str, str, str], ...]] = (
    ("这味药是什么", "来源、类别与一句话定位", "prose"),
    ("性味归经", "药性与归经，落到它为什么能治这些病", "table"),
    ("功效与主治", "功效逐条对应主治证候", "table"),
    ("配伍与方剂", "常用配伍与代表方", "table"),
    ("用量与使用注意", "剂量、煎服法与禁忌", "checklist"),
    ("相似药鉴别", "与最易混的几味怎么分", "table"),
    ("小结", "三句话记住这味药", "prose"),
)

_GENRE_DEVICES: Final[dict[str, tuple[str, ...]]] = {
    "explain": ("table", "callout", "summary"),
    "compare": ("table", "checklist"),
    "guide": ("steps", "checklist", "callout"),
    "plan": ("table", "checklist", "timeline"),
    "review": ("table", "callout"),
    "case": ("table", "callout"),
    "qa": ("callout", "summary"),
    "minutes": ("table", "checklist"),
    "cheatsheet": ("table", "summary"),
}

MAX_SECTIONS = 10
MIN_SECTIONS = 3
MAX_WORDS = 12000
MIN_WORDS = 400


# ── 模型 ────────────────────────────────────────────────────────────────────


class DocSectionSpec(BaseModel):
    """一节：叫什么、解决什么、必须覆盖什么、该长什么样。"""

    model_config = ConfigDict(extra="ignore")

    heading: str = Field(default="", max_length=40)
    goal: str = Field(default="", max_length=160)
    must_cover: list[str] = Field(default_factory=list, max_length=6)
    form: SectionForm = "prose"

    @property
    def clean_heading(self) -> str:
        return _clean_heading(self.heading)


class DocSpec(BaseModel):
    """这篇文档到底要写成什么样。方案卡展示它，生成阶段消费它。"""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(default="", max_length=40)
    subtitle: str = Field(default="", max_length=60)
    genre: DocGenre = "explain"
    audience: str = Field(default="", max_length=60)
    organization: str = Field(default="", max_length=300)
    organization_kind: OrgKind = "topic"
    #: 这份文档要交付的判断，也是方案卡上「目标」那一栏。
    thesis: str = Field(default="", max_length=300)
    target_words: int = Field(default=2200, ge=MIN_WORDS, le=MAX_WORDS)
    sections: list[DocSectionSpec] = Field(default_factory=list, max_length=MAX_SECTIONS)
    devices: list[Device] = Field(default_factory=list, max_length=6)
    #: 用户明确提的要求，一条一句。「又要过四级」「只有三个月」都在这里。
    emphasis: list[str] = Field(default_factory=list, max_length=8)
    #: 给用户看的「我这样理解你的要求」，不解释算法。
    interpretation: list[str] = Field(default_factory=list, max_length=6)
    #: rules = 无模型时的确定性基线；model = 模型读出来的。
    basis: Literal["model", "rules"] = "rules"
    notes: list[str] = Field(default_factory=list, max_length=6)

    @property
    def headings(self) -> list[str]:
        return [section.clean_heading for section in self.sections if section.clean_heading]

    @property
    def genre_label(self) -> str:
        return GENRE_LABEL.get(self.genre, "讲解")

    def word_range(self) -> tuple[int, int]:
        return int(self.target_words * 0.8), int(self.target_words * 1.2)


class _DraftSection(BaseModel):
    """模型直接给的节。``points`` 是旧版 DocPlan 的字段，录制回放里还留着。"""

    model_config = ConfigDict(extra="ignore")

    heading: str = Field(default="", max_length=60)
    goal: str = Field(default="", max_length=240)
    must_cover: list[str] = Field(default_factory=list, max_length=8)
    points: list[str] = Field(default_factory=list, max_length=8)
    form: str = Field(default="", max_length=20)

    @property
    def covers(self) -> list[str]:
        return [*self.must_cover, *self.points]


class DocSpecDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(default="", max_length=80)
    subtitle: str = Field(default="", max_length=80)
    genre: str = Field(default="", max_length=20)
    audience: str = Field(default="", max_length=60)
    organization: str = Field(default="", max_length=300)
    organization_kind: str = Field(default="", max_length=20)
    thesis: str = Field(default="", max_length=400)
    target_words: int = Field(default=0, ge=0, le=20000)
    sections: list[_DraftSection] = Field(default_factory=list, max_length=14)
    devices: list[str] = Field(default_factory=list, max_length=8)
    emphasis: list[str] = Field(default_factory=list, max_length=8)
    interpretation: list[str] = Field(default_factory=list, max_length=8)


# ── 小工具 ──────────────────────────────────────────────────────────────────


def _clean_heading(value: str) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = text.strip(" \t-—·•、，,。：:；;#")
    text = re.sub(r"^(第[一二三四五六七八九十\d]+[节章部分]+)[、.．:：\s]*", r"\1 ", text)
    return text[:30]


def _num(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _clamp(value: int, low: int, high: int) -> int:
    return min(max(int(value), low), high)


def words_from_text(text: Any) -> int | None:
    """从「约 2600 字」这类人话里读篇幅。用户手打的数最算数。"""

    matched = re.search(r"约?\s*(\d{3,5})\s*字", str(text or ""))
    return int(matched.group(1)) if matched else None


def genre_of(text: str) -> str:
    """这句话最像哪一种交付物。空串表示看不出来（交给模型）。"""

    content = str(text or "")
    for genre, cues in _GENRE_CUES:
        if any(cue in content for cue in cues):
            return genre
    return ""


#: 旧名，内部调用点还在用。
_genre_from_text = genre_of


#: 用户原话里这些词说明「这不是个标题」。基线取标题时要剥掉。
_REQUEST_NOISE: Final = re.compile(
    r"^(?:那么|然后|现在|我们|我|你|想要?|需要|准备|打算|请|帮我|帮忙|麻烦|给我|能不能|可以|应该)+"
)
_TAIL_NOISE: Final = re.compile(r"(?:应该)?(?:怎么办|怎么做|如何是好|才好|呢|吗|吧|啊)[。！？!?]*$")


def _first_clause(text: str) -> str:
    """取第一段能当主题的短句。整句太长时不能直接拿来当标题。"""

    body = str(text or "").strip()
    for chunk in re.split(r"[，,；;。！？!?\n]", body):
        piece = _TAIL_NOISE.sub("", _REQUEST_NOISE.sub("", chunk.strip())).strip()
        # 「制定一份计划」这种以动词开头的短语不是主题，跳过看下一段。
        if len(piece) < 4 or re.match(r"^(制定|写|做|准备|安排|生成|整理|给出|设计)", piece):
            continue
        return piece
    return _TAIL_NOISE.sub("", _REQUEST_NOISE.sub("", body)).strip()


#: 组织方式的线索。**只影响基线**：无模型时才用它挑骨架，模型读到什么都照模型。
_ORG_CUES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("timeline", ("历史", "发展", "演变", "沿革", "源流", "起源", "流传", "变迁")),
)


def organization_of(text: str) -> str:
    """这句话自己说了要按什么顺序排（时间 / 阶段…）。空串表示没明说。"""

    content = str(text or "")
    for org, cues in _ORG_CUES:
        if any(cue in content for cue in cues):
            return org
    return ""


def _topic_core(text: str) -> str:
    """把「麻黄与桂枝的区别」压成「麻黄与桂枝」这种能当标题的核心词。"""

    core = _first_clause(text)
    core = re.sub(r"(?:之间)?(?:的区别|的异同|的差别|有何不同|对比|比较|鉴别|怎么选|哪个好)$", "", core).strip()
    return core[:18]


def _baseline_title(topic: str, genre: str, org: str, coverage: Coverage | None, docs: list[Any]) -> str:
    """基线标题：宁可朴素，也不能是用户原句。"""

    label = GENRE_LABEL.get(genre, "讲解")
    if coverage is not None and coverage.chapter_info is not None:
        chapter = coverage.chapter_info
        base = chapter.title
        return base if genre == "explain" else f"{base}·{label}"
    if coverage is not None and coverage.subjects:
        subject = coverage.subjects[0]
        if genre == "plan":
            return f"{subject.title}学习计划"
        if genre == "cheatsheet":
            return f"{subject.title}速查"
        if genre == "explain" and org == "topic":
            return f"{subject.title}通览"
        # 指南 / 对比 / 答疑这类问的是具体做法，用句子里的主题比用学科名准。
    if docs and org == GENRE_ORG.get(genre):
        name = str(docs[0].get("name") or "").strip()
        if name:
            return {
                "plan": f"{name}学习计划",
                "compare": f"{name}鉴别要点",
                "guide": f"{name}使用指南",
                "case": f"{name}临床应用",
                "review": f"{name}研究综述",
                "qa": f"{name}常见问题",
                "cheatsheet": f"{name}速查",
            }.get(genre, f"{name}：性味归经与临床应用")
    core = _topic_core(topic) or "未命名文稿"
    return core if genre == "explain" else f"{core}（{label}）"


def _catalog_sections(coverage: Coverage, limit: int = 8) -> list[tuple[str, str, str]]:
    """整门课 / 某一章 → 章节名就是节标题。库里怎么分，文档就怎么分。"""

    if coverage.chapter is not None:
        subject, chapter = coverage.chapter
        rows = [
            (section.title, f"教材第{chapter.no}章「{chapter.title}」的这一节", "prose")
            for section in chapter.sections
            if section.title
        ]
        return rows[:limit]
    subject = coverage.subjects[0]
    rows: list[tuple[str, str, str]] = []
    for part in subject.parts():
        chapters = subject.chapters_of(part)
        if not chapters:
            continue
        # 篇下面的章不多时逐章展开；21 类药那种一次列不完，改成一节表格。
        if len(chapters) <= max(limit - 1, 3):
            for chapter in chapters[: max(limit - 1, 3)]:
                rows.append((chapter.title, f"教材第{chapter.no}章，{len(chapter.sections)} 节", "prose"))
        else:
            rows.append((f"{part}：{len(chapters)} 章", f"这一篇共 {len(chapters)} 章，按类摆成一张表", "table"))
        if len(rows) >= limit:
            break
    return rows[:limit]


def _chapter_covers(coverage: Coverage | None, limit: int = 4) -> list[str]:
    if coverage is None:
        return []
    if coverage.chapter is not None:
        # ``coverage.chapter`` 是 (学科, 章) 的二元组，别直接当 ChapterInfo 用。
        chapter = coverage.chapter[1]
        return [section.title for section in chapter.sections if section.title][:limit]
    subject = coverage.subjects[0]
    return [chapter.title for chapter in subject.chapters if chapter.title][:limit]


# ── 基线 ────────────────────────────────────────────────────────────────────


def baseline_spec(
    topic: str,
    *,
    coverage: Coverage | None = None,
    docs: list[Any] | None = None,
    params: dict[str, Any] | None = None,
) -> DocSpec:
    """无模型时的确定性方案。诚实标 rules——它没有读懂那句话，只是没瞎编。"""

    params = dict(params or {})
    docs = list(docs or [])
    text = f"{topic} {params.get('brief_note') or ''}".strip()
    genre = genre_of(text) or "explain"
    # 用户自己说了「按历史讲」，就按时间排——这与体裁是两件事。
    org = organization_of(text) or GENRE_ORG[genre]

    rows: list[tuple[str, str, str]]
    if genre in ("plan", "cheatsheet") and coverage is not None:
        rows = list(_SKELETON[org])
        rows.insert(2, ("要过完的教材范围", "把库里的章按篇排进来，而不是只写「学教材」", "table"))
    elif org == "timeline":
        # 先说清楚时间线，比套「是什么/为什么/怎么用」更贴用户的原话。
        rows = list(_SKELETON["timeline"])
    elif genre == "explain" and coverage is not None:
        rows = _catalog_sections(coverage)
    elif genre == "explain" and docs:
        rows = list(_MONOGRAPH)
    else:
        rows = list(_SKELETON[org])
    rows = rows[:MAX_SECTIONS] or list(_SKELETON["topic"])

    sections: list[DocSectionSpec] = []
    covers = _chapter_covers(coverage)
    for heading, goal, form in rows:
        must = covers[:4] if ("教材范围" in heading or heading.startswith("阶段路线")) else []
        sections.append(
            DocSectionSpec(
                heading=_clean_heading(heading),
                goal=goal,
                must_cover=must,
                form=form,  # type: ignore[arg-type]
            )
        )

    low, high = GENRE_BUDGET.get(genre, (1500, 3000))
    words = _num(params.get("target_words")) or (low + high) // 2
    title = _baseline_title(topic, genre, org, coverage, docs)
    return DocSpec(
        title=title,
        subtitle=GENRE_LABEL[genre],
        genre=genre,  # type: ignore[arg-type]
        audience=str(params.get("audience") or "").strip() or ("中医专业学生" if (docs or coverage) else "通用读者"),
        organization=ORG_LINE[org],
        organization_kind=org,  # type: ignore[arg-type]
        thesis=f"把「{topic.strip()[:40]}」写成一份{GENRE_LABEL[genre]}",
        target_words=_clamp(words, MIN_WORDS, MAX_WORDS),
        sections=sections,
        devices=list(_GENRE_DEVICES.get(genre, ("table",))),  # type: ignore[arg-type]
        basis="rules",
    )


# ── 读整句 ──────────────────────────────────────────────────────────────────


def _payload(
    topic: str,
    *,
    base: DocSpec,
    coverage: Coverage | None,
    docs: list[Any],
    params: dict[str, Any],
    materials: list[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request": topic,
        "focus": str(params.get("focus") or ""),
        "audience_hint": str(params.get("audience") or ""),
        "word_hint": str(params.get("target_words") or ""),
        "rule_baseline": {
            "title": base.title,
            "genre": base.genre,
            "sections": base.headings,
            "note": "这是规则拟的基线，可能过于套路。用户诉求更具体时按用户的改。",
        },
        # 「库里有什么」要一直给：用户抱怨过「从来不用知识库」，只给命中的学科
        # 会让模型以为库里只有那一门课。
        "library": catalog.digest(coverage),
    }
    if coverage is not None:
        payload["matched"] = {
            "subjects": [subject.title for subject in coverage.subjects],
            "chapter": coverage.chapter_info.title if coverage.chapter_info else "",
            "note": "这句话落在本库的这门课里：章节名与术语照用，计划类文档要把这些章节排进阶段。",
        }
    if docs:
        payload["herbs"] = [_doc_digest(doc) for doc in docs[:3]]
    if materials:
        payload["user_material"] = [str(item)[:400] for item in materials[:3]]
    return payload


def _doc_digest(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        return {}
    return {
        "name": doc.get("name", ""),
        "category": f"{doc.get('category', '')}·{doc.get('subcategory', '')}".strip("·"),
        "nature": doc.get("nature", ""),
        "flavor": doc.get("flavor", []),
        "meridians": doc.get("meridians", []),
        "effects": list(doc.get("effects") or [])[:8],
        "indications": [item.get("syndrome", "") for item in doc.get("indications") or [] if isinstance(item, dict)][:5],
        "dosage": str((doc.get("dosage") or {}).get("text", "")),
        "cautions": list(doc.get("cautions") or [])[:4],
    }


def _informative(draft: DocSpecDraft) -> bool:
    """模型这一版有没有真的读进东西。全是默认值就当没读。"""

    return bool([item for item in draft.sections if item.heading.strip()] or draft.title or draft.interpretation)


async def compose_doc_spec(
    topic: str,
    *,
    coverage: Coverage | None = None,
    docs: list[Any] | None = None,
    params: dict[str, Any] | None = None,
    materials: list[str] | None = None,
    llm_call: Any = None,
    router: ModelRouter | None = None,
    task_type: str = "generate",
) -> DocSpec:
    """读懂这句话，给出一份施工图。模型优先，失败退回基线。"""

    params = dict(params or {})
    docs = list(docs or [])
    text = " ".join(str(topic or "").split())
    base = baseline_spec(text, coverage=coverage, docs=docs, params=params)
    if not text:
        return base

    messages = [
        {"role": "system", "content": DOC_SPEC_SYSTEM_PROMPT},
        {"role": "user", "content": _json(_payload(text, base=base, coverage=coverage, docs=docs, params=params, materials=list(materials or [])))},
    ]
    draft: DocSpecDraft | None = None
    try:
        if llm_call is not None:
            raw = await llm_call(messages)
            draft = DocSpecDraft.model_validate_json(_extract_json(raw))
        else:
            draft = await structured_create(
                task_type=task_type,
                messages=messages,
                response_model=DocSpecDraft,
                mock_factory=lambda: DocSpecDraft(),
                router=router or ModelRouter(),
            )
    except Exception:  # noqa: BLE001 - 解读失败必须退回基线，不能拦住出方案
        draft = None

    if draft is None or not _informative(draft):
        return base
    return reconcile_doc_spec(draft, base, text)


def reconcile_doc_spec(draft: DocSpecDraft, base: DocSpec, topic: str) -> DocSpec:
    """把模型给的收进合法区间。

    只做「不许越界」与「不许含糊」，不改写模型对内容的理解：标题、体裁、章节
    都照模型给的，只有越界（节太多、篇幅离谱、标题就是原句）才动手，并写进 notes。
    """

    notes: list[str] = []
    genre = draft.genre if draft.genre in GENRES else base.genre
    org = draft.organization_kind if draft.organization_kind in ORG_KINDS else GENRE_ORG[genre]

    sections: list[DocSectionSpec] = []
    seen: set[str] = set()
    for item in draft.sections:
        heading = _clean_heading(item.heading)
        if not heading or heading in seen:
            continue
        seen.add(heading)
        covers = [str(point).strip()[:40] for point in item.covers if str(point).strip()][:5]
        form = item.form if item.form in FORMS else "prose"
        sections.append(DocSectionSpec(heading=heading, goal=str(item.goal or "").strip()[:160], must_cover=covers, form=form))  # type: ignore[arg-type]
    if len(sections) < MIN_SECTIONS:
        notes.append(f"模型给的章节不足 {MIN_SECTIONS} 节，按完整的通用骨架补齐")
        sections = list(base.sections)
    if len(sections) > MAX_SECTIONS:
        notes.append(f"章节压到 {MAX_SECTIONS} 节以内")
        sections = sections[:MAX_SECTIONS]

    low, high = GENRE_BUDGET.get(genre, (1500, 3000))
    words = _num(draft.target_words) or base.target_words
    if words < low // 2 or words > high * 2:
        notes.append(f"篇幅按「{GENRE_LABEL[genre]}」的常规定在 {low}-{high} 字")
        words = _clamp(words, low, high)

    devices = [item for item in dict.fromkeys(draft.devices) if item in DEVICES][:4]
    if not devices:
        devices = list(_GENRE_DEVICES.get(genre, ("table",)))

    accepted = _accept_title(draft.title, base.title, topic)
    if not accepted:
        # 模型把原句当标题、或者标题里带「请/帮我/写一份」，都当没给。
        notes.append("模型给的标题不像书名（或就是原句），按主题另起了一个")
        title = base.title
    else:
        title = accepted
        if draft.title.strip() != title:
            notes.append("标题不像书名，按主题另起了一个")

    interpretation = [_plain(line) for line in draft.interpretation if _plain(line)][:4]
    return DocSpec(
        title=title[:40],
        subtitle=str(draft.subtitle or base.subtitle)[:60],
        genre=genre,  # type: ignore[arg-type]
        audience=(str(draft.audience or "").strip() or base.audience)[:60],
        organization=(str(draft.organization or "").strip() or ORG_LINE[org])[:300],
        organization_kind=org,  # type: ignore[arg-type]
        thesis=(str(draft.thesis or "").strip() or base.thesis)[:300],
        target_words=_clamp(words, MIN_WORDS, MAX_WORDS),
        sections=sections,
        devices=devices,  # type: ignore[arg-type]
        emphasis=[str(item)[:40] for item in draft.emphasis if str(item).strip()][:6],
        interpretation=interpretation,
        basis="model",
        notes=notes,
    )


def _accept_title(title: str, fallback: str, topic: str) -> str:
    """标题不许是用户原句，也不许带「·规划」这种后缀。"""

    text = " ".join(str(title or "").split()).strip(" 。.，,；;：:")
    if not text:
        return ""
    # 模型照抄了原句，或者把原句截了一段当标题。
    if text == topic.strip() or (len(text) >= 8 and text in topic):
        return ""
    if re.search(r"(请|帮我|帮忙|麻烦|给我|生成|写一份|写一篇|做个|制作)", text):
        return ""
    if len(text) > 30:
        return ""
    # 「…·规划」「…-讲解」是旧实现的机械拼接，别再让它活下来。
    suffix = re.search(r"[·\-—_]\s*(讲解|对比分析|操作指南|计划|规划|综述|案例|答疑|纪要|速查表|文稿)$", text)
    if suffix:
        stripped = text[: suffix.start()].strip()
        return stripped if len(stripped) >= 4 else ""
    return text or fallback


#: 解读里出现这些，说明模型在跟用户说内部话，不是在说人话。
_JARGON: Final[tuple[str, ...]] = (
    "must_cover", "must_answer", "organization_kind", "target_words", "doc_kind", "section",
    "genre", "form", "depth", "payload", "json", "basis", "规则引擎",
)


def _plain(value: Any) -> str:
    """解读只用人的说法。带内部键名的整行丢弃，不做半截替换。"""

    text = " ".join(str(value or "").split()).strip(" 。.，,；;")
    if not text:
        return ""
    lowered = text.lower()
    if any(token in lowered for token in _JARGON):
        return ""
    if re.search(r"[a-z_]{3,}\s*[=:：]", lowered):
        return ""
    return text[:120]


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _extract_json(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("{"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    raise ValueError("施工图输出中没有 JSON")


# ── 方案 → 生成参数（唯一翻译层） ────────────────────────────────────────────


def doc_spec_of(plan: dict[str, Any] | None) -> DocSpec | None:
    """从方案里取出施工图。老方案（没有 spec）返回 ``None``。"""

    if not isinstance(plan, dict):
        return None
    raw = plan.get("spec")
    if not isinstance(raw, dict) or not raw:
        return None
    # 导图的 spec 存在同一个键里，别把它当文稿施工图。
    if "sections" not in raw and "target_words" not in raw:
        return None
    try:
        spec = DocSpec.model_validate(raw)
    except Exception:  # noqa: BLE001 - 坏数据不该让生成炸掉
        return None
    if not spec.sections and not spec.title:
        return None
    return spec


def doc_params_from_plan(plan: dict[str, Any] | None, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """方案 → 生成参数。**唯一一处**翻译，且必须排在设计单之后。

    方案卡上能手改的每个字段都要在这里有落点，否则就是「改了不生效」：
    篇幅读「呈现方式」里的数字（用户手打的最算数），章节读 units，
    体裁/受众/组织方式读 spec。
    """

    out = dict(base or {})
    spec = doc_spec_of(plan)
    if spec is None:
        return out

    plan_map = plan or {}
    words = words_from_text(plan_map.get("presentation")) or spec.target_words
    spec = spec.model_copy(update={"target_words": _clamp(words, MIN_WORDS, MAX_WORDS)})
    units = [unit for unit in plan_map.get("units") or [] if isinstance(unit, dict) and str(unit.get("label") or "").strip()]
    if units:
        # units 是用户在卡片上真正改的东西：加减一节、改标题都在这里。
        merged: list[DocSectionSpec] = []
        for index, unit in enumerate(units):
            label = _clean_heading(str(unit.get("label") or ""))
            if not label:
                continue
            old = spec.sections[index] if index < len(spec.sections) else None
            detail = [item.strip() for item in re.split(r"[；;、,，]", str(unit.get("detail") or "")) if item.strip()]
            merged.append(
                DocSectionSpec(
                    heading=label,
                    goal=str(unit.get("intent") or (old.goal if old else ""))[:160],
                    must_cover=(detail or (old.must_cover if old else []))[:5],
                    form=(old.form if old else "prose"),
                )
            )
        if merged:
            spec = spec.model_copy(update={"sections": merged})

    out["doc_spec"] = spec.model_dump(mode="json")
    out["doc_genre"] = spec.genre
    out["target_words"] = spec.target_words
    out["expected"] = {
        "title": spec.title,
        "genre": spec.genre,
        "target_words": spec.target_words,
        "sections": spec.headings,
        "forms": {section.clean_heading: section.form for section in spec.sections},
        "devices": list(spec.devices),
    }
    return out


# ── 成品核对 ────────────────────────────────────────────────────────────────

#: 块类型 → 它算哪一种「结构化」。正文全落在 paragraph 上就是「整段整段」。
_BLOCK_DEVICE: Final[dict[str, str]] = {
    "table": "table",
    "todo": "checklist",
    "bulleted": "checklist",
    "numbered": "steps",
    "callout": "callout",
    "quote": "callout",
    "code": "glossary",
    "toggle": "summary",
}


def measure_doc(blocks: list[dict[str, Any]], spec: DocSpec | None = None) -> dict[str, Any]:
    """量一量这篇稿子的结构。只报事实，不打分。"""

    headings: list[str] = []
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    words = 0
    long_paragraphs = 0
    devices: set[str] = set()

    for block in blocks or []:
        kind = str(block.get("type") or "")
        text = str(block.get("text") or "")
        if kind in ("heading1", "heading2"):
            current = {"heading": text.strip()[:60], "structured": 0, "paragraphs": 0}
            sections.append(current)
            headings.append(current["heading"])
            continue
        if kind == "heading3":
            if current is not None:
                current["structured"] += 1
            devices.add("summary")
            continue
        device = _BLOCK_DEVICE.get(kind)
        if device:
            devices.add(device)
            if current is not None:
                current["structured"] += 1
        if kind == "paragraph":
            length = len(re.sub(r"\s+", "", text))
            words += length
            if length >= 200:
                long_paragraphs += 1
            if current is not None:
                current["paragraphs"] += 1
        elif kind.startswith("heading"):
            words += len(re.sub(r"\s+", "", text))
        elif kind == "table":
            # 表格的字在 props 的单元格里；text 存的是带竖线的原始行，数它会虚高。
            cells: list[str] = []
            for row in (block.get("props") or {}).get("rows") or []:
                cells.extend(str(cell) for cell in row)
            words += len(re.sub(r"\s+", "", "".join(cells)))
        elif kind in ("todo", "numbered", "bulleted", "callout", "quote", "toggle"):
            # 清单、步骤、提示块里的字也是用户看到的字，不数它们会让
            # 结构化文档显得比实际短——补篇幅的闸门会因此错杀合格的扩写。
            words += len(re.sub(r"\s+", "", text))
            for child in block.get("children") or []:
                words += len(re.sub(r"\s+", "", str(child.get("text") or "")))

    dense = [row["heading"] for row in sections if row["structured"] == 0 and row["paragraphs"] >= 2]
    planned = spec.headings if spec is not None else []
    matched = [label for label in planned if any(_same_heading(label, heading) for heading in headings)]
    return {
        "sections": len(sections),
        "headings": headings[:20],
        "planned": len(planned),
        "matched": len(matched),
        "missing": [label for label in planned if label not in matched][:8],
        "words": words,
        "dense": dense[:8],
        "long_paragraphs": long_paragraphs,
        "devices": sorted(devices),
        "section_rows": [{**row, "heading": row["heading"][:40]} for row in sections[:16]],
    }


def doc_conformance(blocks: list[dict[str, Any]], spec: DocSpec | None) -> dict[str, Any]:
    """方案 vs 成品。差距如实报，不假装达标。"""

    stats = measure_doc(blocks, spec)
    expected_words = spec.target_words if spec is not None else 0
    wanted = list(spec.devices) if spec is not None else []
    present = set(stats["devices"])
    missing_devices = [device for device in wanted if not _device_hit(device, present)]
    return {
        "sections": stats["sections"],
        "planned": stats["planned"],
        "matched": stats["matched"],
        "missing": stats["missing"],
        "words": stats["words"],
        "expected_words": expected_words,
        "dense": stats["dense"],
        "long_paragraphs": stats["long_paragraphs"],
        "devices": stats["devices"],
        "devices_missing": missing_devices,
    }


def _device_hit(device: str, present: set[str]) -> bool:
    """排版要素的判据要宽松一点：清单写成 ``- `` 而不是 ``- [ ]`` 也算有清单。"""

    if device == "timeline":
        return "table" in present
    if device in present:
        return True
    if device == "summary":
        return "table" in present or "summary" in present
    if device == "glossary":
        return "table" in present or "glossary" in present
    return False


def _same_heading(left: str, right: str) -> bool:
    a = re.sub(r"[\s　·、，,：:；;（）()【】\[\]]", "", str(left or ""))
    b = re.sub(r"[\s　·、，,：:；;（）()【】\[\]]", "", str(right or ""))
    if not a or not b:
        return False
    return a == b or a in b or b in a


# 供方案侧使用：把 units 与 spec 对齐后写回（保持与导图同一套收口语义）。
def align_spec_with_units(spec: DocSpec, units: list[dict[str, Any]]) -> DocSpec:
    rows: list[DocSectionSpec] = []
    for index, unit in enumerate(units):
        label = _clean_heading(str(unit.get("label") or ""))
        if not label:
            continue
        old = spec.sections[index] if index < len(spec.sections) else None
        detail = [item.strip() for item in re.split(r"[；;、,，]", str(unit.get("detail") or "")) if item.strip()]
        rows.append(
            DocSectionSpec(
                heading=label,
                goal=str(unit.get("intent") or (old.goal if old else ""))[:160],
                must_cover=(detail or (old.must_cover if old else []))[:5],
                form=(old.form if old else "prose"),
            )
        )
    return spec.model_copy(update={"sections": rows}) if rows else spec


__all__ = [
    "DocSpec",
    "DocSectionSpec",
    "DocGenre",
    "ORG_LINE",
    "GENRE_BUDGET",
    "GENRE_LABEL",
    "baseline_spec",
    "compose_doc_spec",
    "reconcile_doc_spec",
    "doc_spec_of",
    "doc_params_from_plan",
    "doc_conformance",
    "measure_doc",
    "words_from_text",
    "align_spec_with_units",
    "ChapterInfo",
    "SubjectInfo",
]
