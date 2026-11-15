"""把「用户那一整句话」读成一份讲稿施工图。

与 ``app/docs/spec.py``、``app/mindmap/spec.py`` 是同一件事的第三次落地：幻灯工坊
过去也把「这份讲稿长什么样」交给两处关键词规则回答——

- ``ppt/agent.py`` 的大纲提示词把讲授顺序**写死成药材八股**
  （封面 → 导览 → 性味归经 → 功效 → 主治 → 鉴别 → 用量禁忌 → 总结），
  于是给新生做的入门试讲和给临床医生做的用药对比，长出来是同一份；
- ``design.purpose_for_plan_unit`` 用关键词猜版式，猜不中就一律 ``points``，
  一份九页的稿子常常是「封面 + 七页要点 + 收束」——这就是「排版太单一」的根，
  不是渲染层不好看，是**每一页被要求写成同一件事**；
- 方案卡的「目标」是拼接模板（``围绕「{原句}」做一次{受众}演示``），
  「受众」永远是同一个默认值，第一页标题就是用户原句。

所以这里只做四件事：

1. ``compose_deck_spec``：整句话交给模型读——什么场合、给谁、讲多久、几页、
   叙事怎么走、每页是什么角色（版式）、必须讲到什么、需要什么素材。模型管决策。
2. ``baseline_spec``：无模型时的确定性基线，标 ``basis="rules"``，不假装读懂了。
3. ``_diversify``：**版式多样性是硬约束**，不是风格偏好。相邻三页同一种版式就
   按内容形态重排（有数字给数据页、有先后给流程页、有对照给对照页），并写进 notes。
4. ``deck_conformance``：成品出来后如实报「方案要 4 种版式，实际 1 种」
   「第 6–9 页连续四页都是要点」「三页要配图，一张没配上」。
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.structured_output import structured_create
from app.llm.router import ModelRouter
from app.mindmap import catalog
from app.mindmap.catalog import Coverage

from .fontpairs import DEFAULT_TYPE_PAIR, TYPE_PAIR_MENU, coerce_type_pair
from .prompts import DECK_SPEC_SYSTEM_PROMPT

# ── 枚举 ────────────────────────────────────────────────────────────────────

#: 这份讲稿是什么场合。它决定叙事弧与页数预算，比「体裁」更贴用户原话。
DeckGenre = Literal["lecture", "orientation", "brief", "training", "defense", "pitch"]
#: 一页的角色＝版式语法，与 ``design.ARCHETYPES`` 同一套 id（渲染层不用再映射）。
DeckRole = Literal["cover", "agenda", "section", "points", "compare", "matrix", "process", "data", "warning", "summary"]
Density = Literal["airy", "normal", "dense"]

GENRES: Final[tuple[str, ...]] = ("lecture", "orientation", "brief", "training", "defense", "pitch")
ROLES: Final[tuple[DeckRole, ...]] = (
    "cover", "agenda", "section", "points", "compare", "matrix", "process", "data", "warning", "summary",
)
DENSITIES: Final[tuple[str, ...]] = ("airy", "normal", "dense")

GENRE_LABEL: Final[dict[str, str]] = {
    "lecture": "课堂讲授",
    "orientation": "入门宣讲",
    "brief": "工作汇报",
    "training": "技能培训",
    "defense": "答辩",
    "pitch": "路演",
}

#: 场合 → 页数区间。答辩不能讲四十分钟，试讲也不该只放五页。
GENRE_BUDGET: dict[str, tuple[int, int]] = {
    "lecture": (8, 16),
    "orientation": (6, 12),
    "brief": (5, 10),
    "training": (8, 16),
    "defense": (8, 14),
    "pitch": (6, 12),
}

#: 场合 → 叙事弧（中文，给用户看）。不是模板，是「这类场合通常怎么起承转合」。
GENRE_ARC: dict[str, tuple[str, ...]] = {
    "lecture": ("立题", "铺陈概念", "拆解难点", "举例验证", "收束练习"),
    "orientation": ("先勾住人", "说清这门学问是什么", "给一个能记住的抓手", "落到他自己的处境", "鼓励行动"),
    "brief": ("结论先行", "给依据", "说方案", "讲风险", "要决策"),
    "training": ("说目标", "演示一遍", "拆步骤练", "点常见错误", "验收"),
    "defense": ("问题与意义", "方法与数据", "结果", "讨论与局限", "结论"),
    "pitch": ("痛点", "方案", "证据", "壁垒", "诉求"),
}

ROLE_LABEL: Final[dict[str, str]] = {
    "cover": "封面",
    "agenda": "导览",
    "section": "章节",
    "points": "要点",
    "compare": "对照",
    "matrix": "矩阵",
    "process": "流程",
    "data": "数据",
    "warning": "警示",
    "summary": "收束",
}

#: 每种版式**要模型怎么写**。以前提示词只给了 bullets/columns 两个字段，
#: 于是十种版式全都收到同一批要点——版式再多也只能排出「标题 + 五条」。
ROLE_CONTRACT: Final[dict[str, str]] = {
    "cover": "只给 title 与 subtitle（一句副题，说清这场讲给谁、讲完能带走什么），不要 bullets。",
    "agenda": "bullets 写成 3-6 条路线名，每条 14 字以内，像目录不要像句子。",
    "section": "只给 title 与 subtitle（一句过渡），不要 bullets；用来分隔大段落。",
    "points": "bullets 写 3-5 条短句，每条 40 字以内，一条一个意思。",
    "compare": "columns 给两栏（也可以是两组对照要点），栏题 10 字以内，每栏 2-4 条、每条 30 字以内；不要把对照写成两条并列的要点。",
    "matrix": "columns 给三栏，每栏 8 字以内的词，做成一张速查格。",
    "process": "bullets 写成按顺序的步骤（先…再…然后…），3-5 步，每步 12 字以内，能在版面上连成一条线。",
    "data": "bullets 每条写成「数值 + 说明」（如「3~9g，常用剂量」），2-3 条；没有确切数字就不要用这个版式。",
    "warning": "bullets 写 1-2 条红线，每条 36 字以内，是「别这么做」而不是「要注意」。",
    "summary": "bullets 写 3 条回顾 + subtitle 一句定论；这一页要说清「如果只记住一句，是哪句」。",
}

MIN_PAGES: Final[int] = 4
MAX_PAGES: Final[int] = 20
MIN_DURATION: Final[int] = 3
MAX_DURATION: Final[int] = 180

#: 受众默认值。``PptParams`` 的默认就是它——拿它判断「用户有没有说受众」。
DEFAULT_AUDIENCE: Final[str] = "中医药专业本科生"

#: 组织方式 → 基线页序。**无模型时**才用；有模型时页序由模型读句子给。
#: 每一行都刻意给不同的 role：基线也不能是「封面 + 一堆要点」。
_BLUEPRINTS: dict[str, list[tuple[str, str, str]]] = {
    "topic": [
        ("开场：这次讲什么", "交代主题，以及听完能带走什么", "cover"),
        ("十分钟路线", "把要讲的几段先摆出来", "agenda"),
        ("先破一个常见误解", "指出最容易被搞混的一点", "points"),
        ("核心概念与边界", "把关键概念讲清，划出适用范围", "compare"),
        ("从知道到会做", "把结论翻译成能照着走的顺序", "process"),
        ("关键数字与判断标准", "给出能量化的判断依据", "data"),
        ("最容易出错的地方", "点明风险与禁忌", "warning"),
        ("一页记住", "回顾要点，给出一句定论", "summary"),
    ],
    "compare": [
        ("开场：这次讲什么", "交代要比的对象与为什么要比", "cover"),
        ("为什么容易混", "说明混淆的来源，建立比较的必要性", "points"),
        ("比较维度一览", "先给维度，再逐项展开", "matrix"),
        ("逐维对照", "同一维度下并排看差异", "compare"),
        ("各有各的用法", "把差异翻译成选择建议", "compare"),
        ("最容易判错的地方", "指出最常见的误判", "warning"),
        ("一句话记住怎么选", "收束成一条可执行的判断规则", "summary"),
    ],
    "process": [
        ("开场：要解决什么", "先定义任务边界", "cover"),
        ("开始前要准备什么", "材料与前置条件", "agenda"),
        ("按顺序做完", "拆成可执行的步骤", "process"),
        ("每步的判断标准", "做到什么程度算做对", "data"),
        ("容易出错的环节", "点明真正的坑", "warning"),
        ("做完怎么验收", "给出可检验的结果", "summary"),
    ],
    "timeline": [
        ("开场：这条线是怎么来的", "交代要讲的这段时间与为什么值得讲", "cover"),
        ("源流与早期记载", "交代最早可考的出处与语境", "points"),
        ("历代演变", "按时间顺序拆出关键变化", "process"),
        ("转折与分歧", "解释每次变化为什么发生", "compare"),
        ("今天的形态", "落到当代认识或应用", "points"),
        ("时间坐标", "收束时间线，给出可记住的坐标", "summary"),
    ],
    "stage": [
        ("开场：这次要走到哪", "先说清做完要能做到什么", "cover"),
        ("三个阶段", "按阶段排任务", "agenda"),
        ("每个阶段做什么", "逐阶段给出任务与产出", "process"),
        ("怎么判断学会了", "给出可检验的标准", "data"),
        ("最容易走弯路的地方", "点明常见误区", "warning"),
        ("回头看这一路", "收束并给出下一步", "summary"),
    ],
    "case": [
        ("开场：这个案子", "交代背景与要回答的问题", "cover"),
        ("发生了什么", "按时间还原经过", "process"),
        ("当时怎么判断", "把判断依据摊开", "points"),
        ("两种处置的差别", "对比可选路径", "compare"),
        ("事后看哪里可以更好", "指出风险与教训", "warning"),
        ("这个案子教会我们什么", "收束成一条经验", "summary"),
    ],
}

#: 场合 → 组织方式。用户没说怎么排时，按场合的常规来。
_GENRE_ORG: dict[str, str] = {
    "lecture": "topic",
    "orientation": "topic",
    "brief": "topic",
    "training": "process",
    "defense": "topic",
    "pitch": "topic",
}

ORG_LINE: Final[dict[str, str]] = {
    "topic": "按讲授脉络展开：先立题，再分述，最后收束",
    "compare": "按维度并列展开：先给维度，再逐项对照，最后落到怎么选",
    "process": "按操作顺序推进：每步给出动作与判断标准",
    "timeline": "沿时间顺序推进：先交代起点，再讲转折，最后落到今天",
    "stage": "按阶段推进：每阶段给出任务与检验方式",
    "case": "按案情推进：背景 → 经过 → 判断 → 复盘",
}

# ── 枚举线索（只用于基线，模型版不靠它） ────────────────────────────────────

_GENRE_CUES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("orientation", ("新生", "学弟学妹", "宣讲", "招新", "入门", "介绍给", "科普一下", "开学")),
    ("defense", ("答辩", "开题", "毕业课题", "论文汇报")),
    ("pitch", ("路演", "融资", "投资人", "商业计划")),
    ("brief", ("汇报", "述职", "工作总结", "进展", "复盘会")),
    ("training", ("培训", "带教", "工作坊", "实操", "手把手")),
    ("lecture", ("上课", "课堂", "讲授", "试讲", "讲课", "一节课")),
)

_ORG_CUES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("timeline", ("历史", "发展", "演变", "源流", "沿革", "起源", "由来")),
    ("compare", ("对比", "比较", "区别", "异同", "鉴别", "辨析", "vs", "VS")),
    ("stage", ("计划", "复习", "备考", "路线", "安排", "规划")),
    ("process", ("步骤", "流程", "操作", "怎么煎", "如何做", "炮制", "工序")),
    ("case", ("案例", "病案", "医案", "复盘")),
)

#: 版式线索：只在基线 / 重排时用。顺序即优先级。
_ROLE_CUES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("compare", ("对比", "比较", "区别", "异同", "鉴别", "辨析", "不同", "差别", "区分", "vs", "VS")),
    ("matrix", ("矩阵", "维度", "分型", "分类表", "一览", "速查")),
    ("process", ("步骤", "流程", "操作", "炮制", "工序", "先", "再", "然后", "顺序")),
    ("data", ("数据", "统计", "占比", "比例", "数量", "含量", "剂量", "课时", "学分", "年限")),
    ("warning", ("禁忌", "注意", "警示", "慎用", "不良反应", "毒性", "误区", "别", "不要", "风险")),
    ("agenda", ("目录", "导览", "纲要", "路线", "本讲", "议程", "今天讲")),
)

#: 章节页的线索：这些页面只有一句过渡，不该被当成要点页硬塞五条。
_SECTION_HINTS: Final[tuple[str, ...]] = ("第一章", "第二章", "上篇", "下篇", "第一部分", "第二部分", "第一篇", "第二篇")

# ── 数据结构 ────────────────────────────────────────────────────────────────


class MaterialAsk(BaseModel):
    """这一页要什么画面。**只写需求，不写用哪张**——选哪张是 select 的事。

    以前设计单里每页的 ``subject`` 都是 ``topic[:40]``，也就是用户的整句话：
    九页拿着同一句话去搜图，搜不到就成了「素材用不上」。所以这里要求一页一
    主体（「人参饮片」「明代本草彩绘」「水墨远山」），并且说清是证据位还是装饰位。
    """

    model_config = ConfigDict(extra="ignore")

    slot: Literal["figure", "evidence"] = "figure"
    #: 画面要什么。具体名词，不要「相关内容」这种。
    subject: str = Field(default="", max_length=40)
    style: Literal["plate", "line", "photo", "motif", "none"] = "plate"
    priority: Literal["required", "optional"] = "optional"
    why: str = Field(default="", max_length=60)
    #: 检索关键词。subject 是给人看的一句画面描述，keywords 是给库检索的短词
    #: （「人参」「饮片」「炮制」）。只有 subject 时模型容易写出「水墨远山与药材
    #: 剪影叠合，留白为主」这种诗，检索一条都命中不了。
    keywords: list[str] = Field(default_factory=list)

    @field_validator("slot", "style", "priority", mode="before")
    @classmethod
    def _known(cls, value: Any, info: Any) -> str:
        allowed = {
            "slot": ("figure", "evidence"),
            "style": ("plate", "line", "photo", "motif", "none"),
            "priority": ("required", "optional"),
        }[info.field_name]
        text = str(value or "").strip()
        return text if text in allowed else {"slot": "figure", "style": "plate", "priority": "optional"}[info.field_name]

    @field_validator("keywords", mode="before")
    @classmethod
    def _clip_keywords(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:12] for item in value if str(item).strip()][:6]

    @property
    def empty(self) -> bool:
        return not self.subject.strip() or self.style == "none"


# ── 视觉方案：这份稿子长什么样 ──────────────────────────────────────────────


#: 四条主题色板。模型可以在这四条里选，也可以给自定义 palette；
#: 主题只是「没给 palette 时的兜底」，不是设计本身。
THEMES: Final[tuple[str, ...]] = ("paper", "ink", "celadon", "cinnabar")
THEME_TEXT: Final[dict[str, str]] = {
    "paper": "宣纸米白底，墨字，朱砂点",
    "ink": "深墨底，米白字，金线",
    "celadon": "青瓷浅绿底，深绿字",
    "cinnabar": "暖朱砂底，深褐字",
}

#: 一页的构图。这是**给排版层的约束**，不是渲染器分支名：
#: 图在左还是在右、铺满还是留白，由确认过的方案说了算。
COMPOSITIONS: Final[tuple[str, ...]] = (
    "auto", "text-only", "text-image-right", "text-image-left", "full-bleed", "centered", "split",
)
COMPOSITION_LABEL: Final[dict[str, str]] = {
    "auto": "按内容自动",
    "text-only": "纯文字",
    "text-image-right": "左文右图",
    "text-image-left": "左图右文",
    "full-bleed": "满版图压字",
    "centered": "居中极简",
    "split": "上下分栏",
}
IMAGE_TREATMENTS: Final[tuple[str, ...]] = ("plate", "photo", "line", "collage", "none")
IMAGE_TREATMENT_LABEL: Final[dict[str, str]] = {
    "plate": "古籍彩绘 / 版画",
    "photo": "实拍",
    "line": "线描 / 白描",
    "collage": "拼贴（图 + 底纹）",
    "none": "不用图",
}


def _hex(value: Any) -> str:
    """颜色只收 hex。写 ``red`` / ``#f00`` / 空串一律当没给——渲染层只认 #RRGGBB。"""

    text = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text.upper()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", text):
        return "#" + "".join(char * 2 for char in text[1:]).upper()
    return ""


class DeckPalette(BaseModel):
    """自定义配色。给不出来就整条不用，回落主题——不许半套颜色。"""

    model_config = ConfigDict(extra="ignore")

    bg: str = ""
    fg: str = ""
    muted: str = ""
    accent: str = ""
    line: str = ""

    @field_validator("bg", "fg", "muted", "accent", "line", mode="before")
    @classmethod
    def _clip_hex(cls, value: Any) -> str:
        return _hex(value)

    @property
    def usable(self) -> bool:
        # 至少要有底与字，否则画出来是黑底黑字或者白底白字。
        return bool(self.bg and self.fg)


class LookBackground(BaseModel):
    """背景要什么。可以是程序纹样，也可以是库里的一张实拍（中药房、宣纸、石墙）。"""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["motif", "asset", "solid"] = "motif"
    #: 母题 id（``motifs.MOTIFS`` 里有的），渲染层会参数化生成。
    motif: str = "paper-plain"
    #: 若 kind=asset：要一张什么背景（「中药房内景」「宣纸纹理」「青砖墙」）。
    subject: str = Field(default="", max_length=40)
    keywords: list[str] = Field(default_factory=list)
    #: 压暗程度。满版图压字时必须给，否则字看不清。
    veil: float = 0.0
    tone: Literal["bg", "fg"] = "bg"

    @field_validator("kind", mode="before")
    @classmethod
    def _known_kind(cls, value: Any) -> str:
        text = str(value or "").strip()
        return text if text in ("motif", "asset", "solid") else "motif"

    @field_validator("motif", mode="before")
    @classmethod
    def _known_motif(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "paper-plain"
        from .motifs import by_id

        return text if by_id(text) is not None else "paper-plain"

    @field_validator("veil", mode="before")
    @classmethod
    def _clip_veil(cls, value: Any) -> float:
        try:
            return max(0.0, min(0.85, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @field_validator("keywords", mode="before")
    @classmethod
    def _clip_words(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:12] for item in value if str(item).strip()][:6]


class LookOrnament(BaseModel):
    """角饰要什么：一款母题，或者素材库里的一张角花图，或者**不要角花**。

    「每个 PPT 四个角都开同一朵花」是上一版的直接观感。现在角花是方案里的
    一个可选项：模型读了用户的话，决定要不要、要哪一种、画在哪些角。
    """

    model_config = ConfigDict(extra="ignore")

    #: 母题 id 或 ``none``。``none`` 表示这一份稿子不用角花。
    motif: str = "none"
    #: 若母题给不出、或用户要「真的纹样」：去库里找一张角花/纹样。
    subject: str = Field(default="", max_length=40)
    keywords: list[str] = Field(default_factory=list)
    #: 画在哪些角。空列表＝四角。
    corners: list[str] = Field(default_factory=list)
    #: 给用户看的一句：「封面用折枝，正文页不挂角花」。
    ask: str = Field(default="", max_length=80)

    @field_validator("motif", mode="before")
    @classmethod
    def _known_motif(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "none"
        if text == "none":
            return "none"
        from .motifs import by_id

        return text if by_id(text) is not None else "none"

    @field_validator("corners", mode="before")
    @classmethod
    def _clip_corners(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        allowed = {"tl", "tr", "bl", "br"}
        return [str(item).strip() for item in value if str(item).strip() in allowed][:4]

    @field_validator("keywords", mode="before")
    @classmethod
    def _clip_words(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:12] for item in value if str(item).strip()][:6]


class DeckLook(BaseModel):
    """整份稿子的美术方向。**模型读用户的话定，用户确认后才执行。**

    以前「长什么样」由主题名决定：paper → 宣纸 + 卷草角花 + 宋楷，于是
    所有讲稿的封面都是同一张纸、同一个角花。现在这一整块进方案卡，
    用户可以看见、可以改，排版层按它执行。

    2026-11 母版路线：模型的主职责收敛为「选母版（master_id）+ 色板变体
    （accent_variant）+ mood/rhythm」；theme/background/ornament/type_pair
    在 master_id 命中时由母版回填（模型显式给的字段才覆盖母版默认），
    palette 的语义变为「对母版色板的覆盖」。
    """

    model_config = ConfigDict(extra="ignore")

    #: 版式母版 id（masters.MASTERS 的键）。空 = 走旧 LLM 自由排版管线。
    master_id: str = Field(default="", max_length=32)
    #: 母版色板变体序号（clamp 到母版的变体数内）。
    accent_variant: int = Field(default=0, ge=0, le=3)
    #: 一句话气质，给用户看：「静气、古籍感、留白多，不要卡通」。
    mood: str = Field(default="", max_length=80)
    theme: str = "paper"
    palette: DeckPalette | None = None
    background: LookBackground = Field(default_factory=LookBackground)
    ornament: LookOrnament = Field(default_factory=LookOrnament)
    type_pair: str = DEFAULT_TYPE_PAIR
    image_treatment: str = "plate"
    #: 版式节奏：一句话说清整份稿子怎么变（「封面满版，正文左文右图，章节居中」）。
    rhythm: str = Field(default="", max_length=120)

    @field_validator("master_id", mode="before")
    @classmethod
    def _clip_master(cls, value: Any) -> str:
        return str(value or "").strip()[:32]

    @field_validator("accent_variant", mode="before")
    @classmethod
    def _clamp_variant(cls, value: Any) -> int:
        # 宽松读：模型给字符串数字、负数、越界都夹回，不 raise。
        try:
            return max(0, min(3, int(value)))
        except (TypeError, ValueError):
            return 0

    @field_validator("theme", mode="before")
    @classmethod
    def _known_theme(cls, value: Any) -> str:
        text = str(value or "").strip()
        return text if text in THEMES else "paper"

    @field_validator("type_pair", mode="before")
    @classmethod
    def _known_pair(cls, value: Any) -> str:
        # 旧 id（serif-song 等）在这里映射到新表，回放老方案不至于整体回落默认。
        return coerce_type_pair(value)

    @field_validator("image_treatment", mode="before")
    @classmethod
    def _known_treatment(cls, value: Any) -> str:
        text = str(value or "").strip()
        return text if text in IMAGE_TREATMENTS else "plate"


class DeckPageSpec(BaseModel):
    """一页：叫什么、什么角色、要解决什么、必须讲到什么、需要什么素材。

    **字段一律宽松读**：这份数据会从浏览器回传（用户在面板上改过），用严格的
    ``max_length`` 会让一次手改把整份施工图判非法——``deck_spec_of`` 只能返回
    ``None``，于是版式退回关键词、核对丢方案，而用户什么都没做错。
    越界的值在这里截断即可，不要 raise。
    """

    model_config = ConfigDict(extra="ignore")

    heading: str = Field(default="", max_length=40)
    role: DeckRole = "points"
    goal: str = Field(default="", max_length=160)
    must_cover: list[str] = Field(default_factory=list)
    density: Density = "normal"
    material: MaterialAsk | None = None
    #: 这一页怎么摆。排版层按它排，不再一律「左文右图 + 四角角花」。
    composition: str = "auto"

    @field_validator("composition", mode="before")
    @classmethod
    def _known_composition(cls, value: Any) -> str:
        text = str(value or "").strip()
        return text if text in COMPOSITIONS else "auto"

    @field_validator("role", mode="before")
    @classmethod
    def _known_role(cls, value: Any) -> str:
        text = str(value or "").strip()
        return text if text in ROLES else "points"

    @field_validator("density", mode="before")
    @classmethod
    def _known_density(cls, value: Any) -> str:
        text = str(value or "").strip()
        return text if text in DENSITIES else "normal"

    @field_validator("heading", mode="before")
    @classmethod
    def _clip_heading(cls, value: Any) -> str:
        return str(value or "")[:40]

    @field_validator("goal", mode="before")
    @classmethod
    def _clip_goal(cls, value: Any) -> str:
        return str(value or "")[:160]

    @field_validator("must_cover", mode="before")
    @classmethod
    def _clip_covers(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:40] for item in value if str(item).strip()][:6]

    @property
    def clean_heading(self) -> str:
        return _clean_heading(self.heading)


class DeckSpec(BaseModel):
    """这份讲稿到底要讲成什么样。方案卡展示它，设计单与生成消费它。"""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    subtitle: str = ""
    genre: DeckGenre = "lecture"
    audience: str = ""
    #: 场合，一句人话（「新生入学第一周的入门试讲」）。
    occasion: str = ""
    organization: str = ""
    organization_kind: str = "topic"
    #: 讲完希望听众记住的那一句。方案卡「目标」那一栏就是它。
    thesis: str = ""
    #: 这份稿子**做什么用**——「让新生对中药学有整体认识，并愿意按一条路径开始学」。
    #: 与 thesis 分开：thesis 是讲完记住的论点，goal 是做这份 PPT 的目的。
    #: 混成一个字段时，卡片第一栏写的是一句演讲词，用户读不出「你要做什么」。
    goal: str = ""
    arc: list[str] = Field(default_factory=list)
    target_pages: int = 9
    duration_min: int = 15
    pages: list[DeckPageSpec] = Field(default_factory=list)
    theme: str = "paper"
    #: 视觉方案：配色、背景、角饰、字体、配图处理、版式节奏。
    look: DeckLook = Field(default_factory=DeckLook)
    #: 用户明确提的要求，一条一句。「激励他们适应大学生活」就在这儿。
    emphasis: list[str] = Field(default_factory=list)
    interpretation: list[str] = Field(default_factory=list)
    basis: Literal["model", "rules"] = "rules"
    notes: list[str] = Field(default_factory=list)

    @field_validator("goal", mode="before")
    @classmethod
    def _clip_goal(cls, value: Any) -> str:
        return str(value or "")[:200]

    @field_validator("look", mode="before")
    @classmethod
    def _loose_look(cls, value: Any) -> Any:
        # 浏览器回传的 look 可能是半截的（只改了 theme）；缺的字段由默认值补，
        # 不认识的值由各字段自己的宽松验证器回落，不要在这里 raise。
        if value is None:
            return {}
        return value

    # 这份数据会从浏览器回传（方案卡上直接编辑），一次手改不该让整份施工图
    # 判非法：deck_spec_of 一旦返回 None，设计单就退回关键词猜版式、核对条
    # 也丢了方案承诺。所以越界一律截断/夹回/回落，不要 raise。
    @field_validator("title", "subtitle", "audience", "occasion",
                     "organization", "thesis", "theme", "organization_kind",
                     mode="before")
    @classmethod
    def _clip_text(cls, value: Any, info: Any) -> str:
        limits = {
            "title": 40, "subtitle": 60, "audience": 60, "occasion": 60,
            "organization": 300, "thesis": 300, "theme": 16,
            "organization_kind": 20,
        }
        return str(value or "")[: limits[info.field_name]]

    @field_validator("genre", mode="before")
    @classmethod
    def _known_genre(cls, value: Any) -> str:
        text = str(value or "").strip()
        return text if text in GENRES else "lecture"

    @field_validator("arc", "emphasis", "interpretation", "notes", mode="before")
    @classmethod
    def _clip_lists(cls, value: Any, info: Any) -> list[str]:
        limits = {"arc": 6, "emphasis": 8, "interpretation": 6, "notes": 6}
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value
                if str(item).strip()][: limits[info.field_name]]

    @field_validator("target_pages", "duration_min", mode="before")
    @classmethod
    def _clamp_ints(cls, value: Any, info: Any) -> int:
        lo, hi = {
            "target_pages": (MIN_PAGES, MAX_PAGES),
            "duration_min": (MIN_DURATION, MAX_DURATION),
        }[info.field_name]
        try:
            return max(lo, min(hi, int(value)))
        except (TypeError, ValueError):
            return 9 if info.field_name == "target_pages" else 15

    @field_validator("pages", mode="before")
    @classmethod
    def _clip_pages(cls, value: Any) -> list[Any]:
        if not isinstance(value, list):
            return []
        return list(value)[:MAX_PAGES]

    @property
    def headings(self) -> list[str]:
        return [page.clean_heading for page in self.pages if page.clean_heading]

    @property
    def roles(self) -> list[str]:
        return [page.role for page in self.pages]

    @property
    def genre_label(self) -> str:
        return GENRE_LABEL.get(self.genre, "课堂讲授")

    @property
    def variety(self) -> int:
        return len(set(self.roles))


class _DraftPage(BaseModel):
    """模型给的一页。``points`` 是旧版大纲的字段，录制回放里还留着。"""

    model_config = ConfigDict(extra="ignore")

    heading: str = Field(default="", max_length=60)
    title: str = Field(default="", max_length=60)
    role: str = Field(default="", max_length=20)
    goal: str = Field(default="", max_length=240)
    must_cover: list[str] = Field(default_factory=list, max_length=8)
    points: list[str] = Field(default_factory=list, max_length=8)
    density: str = Field(default="", max_length=12)
    material: str = Field(default="", max_length=80)
    image: str = Field(default="", max_length=80)
    #: 这一页怎么摆（见 COMPOSITIONS）。模型可以不给，不给就按内容自动。
    composition: str = Field(default="", max_length=20)
    #: 素材检索词。material 是给人看的画面描述，keywords 是给库检索的短词。
    keywords: list[str] = Field(default_factory=list, max_length=8)

    @property
    def label(self) -> str:
        return self.heading or self.title

    @property
    def covers(self) -> list[str]:
        return [*self.must_cover, *self.points]


class DeckSpecDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(default="", max_length=80)
    subtitle: str = Field(default="", max_length=80)
    genre: str = Field(default="", max_length=20)
    audience: str = Field(default="", max_length=60)
    occasion: str = Field(default="", max_length=80)
    organization: str = Field(default="", max_length=300)
    organization_kind: str = Field(default="", max_length=20)
    thesis: str = Field(default="", max_length=400)
    goal: str = Field(default="", max_length=300)
    arc: list[str] = Field(default_factory=list, max_length=8)
    target_pages: int = Field(default=0, ge=0, le=40)
    duration_min: int = Field(default=0, ge=0, le=300)
    pages: list[_DraftPage] = Field(default_factory=list, max_length=30)
    #: 视觉方案。宽松收：模型给半截也比退回模板好，缺的字段由 reconcile 补。
    look: dict[str, Any] = Field(default_factory=dict)
    emphasis: list[str] = Field(default_factory=list, max_length=8)
    interpretation: list[str] = Field(default_factory=list, max_length=8)


# ── 小工具 ──────────────────────────────────────────────────────────────────


def _clean_heading(value: str) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"^#{1,6}\s*", "", text)
    # 模型爱在标题前自报版式：「封面：中药学入门」。
    text = re.sub(r"^(封面|导览|目录|纲要|章节|总结|小结|收束|结尾)[:：\s]+", "", text)
    return text.strip(" \t-—·•、，,。：:；;#")[:30]


def _num(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def genre_of(text: str) -> str:
    for genre, words in _GENRE_CUES:
        if _has(text, words):
            return genre
    return ""


def organization_of(text: str) -> str:
    for org, words in _ORG_CUES:
        if _has(text, words):
            return org
    return ""


def role_of(text: str) -> str:
    for role, words in _ROLE_CUES:
        if _has(text, words):
            return role
    return ""


def pages_from_text(text: str) -> int | None:
    """从用户原话里读「讲几分钟 / 要几页」。读不到就返回 None。"""

    pages = re.search(r"(\d+)\s*(?:页|幻灯片|张)", text)
    if pages:
        return _clamp(int(pages.group(1)), MIN_PAGES, MAX_PAGES)
    return None


def minutes_from_text(text: str) -> int | None:
    match = re.search(r"(\d+)\s*(?:分钟|min)", text, re.IGNORECASE)
    if match:
        return _clamp(int(match.group(1)), MIN_DURATION, MAX_DURATION)
    hours = re.search(r"([0-9]+(?:\.[0-9])?)\s*(?:个?小时|学时)", text)
    if hours:
        return _clamp(int(float(hours.group(1)) * 60), MIN_DURATION, MAX_DURATION)
    return None


def _strip_request(text: str) -> str:
    """把「我想/帮我/给我」这类祈使开头剥掉，留下要做的那个东西。"""

    value = " ".join(str(text or "").split())
    value = re.sub(r"^(请|麻烦)?(你)?(帮我|给我|替我|我想|我要|我需要|想要|希望)\s*", "", value)
    # 「给新生学弟学妹讲解一份…」：先剥「给某人讲」，再剥「讲解一份」。
    # 两个动词的先后不能反，否则「讲解」会先被「讲」吃掉，留下一个「解」字。
    value = re.sub(r"^(给|向|对)[^，,。；;]{1,20}?(讲解|介绍|讲述|汇报|分享|说明|讲)", "", value)
    value = re.sub(r"^(做|写|生成|制作|来|讲解|介绍|讲述|汇报|分享|说明)\s*(一份|一个|一版|一页|一节|套|一下)?\s*", "", value)
    value = re.sub(r"^(一份|一个|一版|一页|一节|套)\s*", "", value)
    return value.strip(" 。，,：:")


def _topic_core(text: str) -> str:
    """一句话的「核心名词」。标题不能是原句，基线得有个能当书名的东西。"""

    value = _strip_request(text)
    value = re.sub(r"^(关于|围绕|针对)\s*", "", value)
    # 「讲解一份中药学的入门试讲」这种，取第一段像标题的部分。
    value = re.split(r"[，,。；;！!？?]", value)[0]
    value = re.sub(r"^(给|向|对)\s*[^，,]{1,20}?(讲|介绍|说明|汇报|分享)", "", value)
    value = re.sub(r"^(讲解|介绍|讲述|汇报|分享|说明)(一份|一个|一下)?", "", value)
    value = re.sub(r"(的)?(试讲|讲解|介绍|汇报|分享|宣讲|讲稿|演示|课件|幻灯片|PPT|ppt)$", "", value)
    value = re.sub(r"的(?=(入门|概论|基础|概览|通识|导读|第一课|入门课))", "", value)
    return value.strip(" 。，,：:「」\"'")[:28]


def _accept_title(title: str, fallback: str, topic: str) -> str:
    """标题不许是用户原句，也不许带「·讲稿」这种后缀。"""

    text = " ".join(str(title or "").split()).strip(" 「」\"'")
    if not text:
        return ""
    if len(text) > 30:
        return ""
    if re.search(r"(请|帮我|给我|我想|生成|写一份|做一份)", text):
        return ""
    if re.sub(r"[\s，,。；;：:！!？?、「」\"']", "", text) == re.sub(r"[\s，,。；;：:！!？?、「」\"']", "", topic):
        return ""  # 就是原句
    if re.search(r"·\s*(讲稿|演示|幻灯片|PPT|ppt)$", text):
        return ""
    return text


def _plain(value: Any) -> str:
    """解读句：去掉内部键名，也不许把算法词念给用户。"""

    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if re.search(r"[a-z_]{4,}", text):
        banned = ("must_cover", "organization_kind", "target_pages", "role", "basis", "duration_min", "material", "density")
        if any(word in text for word in banned):
            return ""
    text = text.lstrip("-•· ").strip()
    return text[:120]


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _extract_json(text: str) -> str:
    """剥掉围栏。``structured_output`` 不处理 ```json，这里兜一层。"""

    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return raw[start : end + 1]
    return raw


# ── 基线 ────────────────────────────────────────────────────────────────────


def _catalog_pages(coverage: Coverage, limit: int = 10) -> list[tuple[str, str, str]]:
    """整门课的讲稿：章节名就是页标题，页数不够时把相关的章合并。

    与文稿同一原则：库里有真实章名，就不要用「核心内容」这种空壳。
    """

    subject = coverage.subjects[0]
    chapters = list(subject.chapters)
    if not chapters:
        return []
    rows: list[tuple[str, str, str]] = [("开场：这门课讲什么", f"交代《{subject.title}》的框架与学法", "cover")]
    for index, chapter in enumerate(chapters[: max(limit - 2, 2)]):
        entries = "、".join(entry for section in chapter.sections[:2] for entry in section.entries[:3])
        goal = f"第 {chapter.no} 章：{entries}" if entries else f"第 {chapter.no} 章"
        rows.append((chapter.title, goal[:160], role_of(f"{chapter.title} {entries}") or "points"))
    rows.append(("一页记住这门课", "把框架收成一张图，给出下一步从哪学起", "summary"))
    return rows


def baseline_spec(
    topic: str,
    *,
    coverage: Coverage | None = None,
    docs: list[Any] | None = None,
    params: dict[str, Any] | None = None,
    shape: str = "",
) -> DeckSpec:
    """无模型时的确定性方案。诚实标 rules——它没读懂那句话，只是没瞎编。"""

    params = dict(params or {})
    docs = list(docs or [])
    text = f"{topic} {params.get('brief_note') or ''}".strip()
    genre = genre_of(text) or ("lecture" if (coverage or docs) else "orientation")
    org = organization_of(text) or shape or _GENRE_ORG.get(genre, "topic")
    if org not in _BLUEPRINTS:
        org = "topic"

    if org == "topic" and coverage is not None:
        rows = _catalog_pages(coverage)
    else:
        rows = list(_BLUEPRINTS[org])
    if not rows:
        rows = list(_BLUEPRINTS["topic"])

    pages: list[DeckPageSpec] = []
    for heading, goal, role in rows:
        pages.append(
            DeckPageSpec(
                heading=_clean_heading(heading),
                role=role,  # type: ignore[arg-type]
                goal=goal[:160],
                must_cover=[],
                density="airy" if role in ("cover", "section") else "normal",
                material=_baseline_material(role, heading),
            )
        )

    low, high = GENRE_BUDGET.get(genre, (8, 14))
    pages_target = _num(params.get("page_count")) or pages_from_text(text) or len(pages)
    duration = minutes_from_text(text) or max(MIN_DURATION, round(len(pages) * 1.8))
    title = _topic_core(topic) or "本次讲稿"
    pages = _cover_heading(pages, title)
    # 基线也要有构图节奏：封面右图、章节居中、正文有图才给图位。
    pages = [page.model_copy(update={"composition": _composition_for(page)}) for page in pages]
    return DeckSpec(
        title=title[:40],
        subtitle=GENRE_LABEL[genre],
        genre=genre,  # type: ignore[arg-type]
        audience=_audience_hint(params) or DEFAULT_AUDIENCE,
        occasion=GENRE_LABEL[genre],
        organization=ORG_LINE[org],
        organization_kind=org,
        thesis=f"讲清「{title}」，让听众带走一条能用的判断",
        arc=list(GENRE_ARC.get(genre, ())),
        target_pages=_clamp(pages_target, MIN_PAGES, MAX_PAGES) if pages_target else _clamp(len(pages), low, high),
        duration_min=duration,
        pages=pages,
        theme=str(params.get("theme") or "paper"),
        goal=f"让{_audience_hint(params) or DEFAULT_AUDIENCE}听懂「{title}」并知道下一步怎么做",
        look=_baseline_look(genre, base_theme=str(params.get("theme") or "")),
        basis="rules",
    )


def _audience_hint(params: dict[str, Any]) -> str:
    """``PptParams`` 的受众有默认值，等于默认值就当用户没说。"""

    value = str(params.get("audience") or "").strip()
    return "" if value == DEFAULT_AUDIENCE else value


def _cover_heading(pages: list[DeckPageSpec], title: str) -> list[DeckPageSpec]:
    """封面那一页的标题就是讲稿标题。

    以前封面单元写的是用户原句（``topic[:80]``），于是成品第一页顶着一句
    「我想给新生学弟学妹讲解一份中药学…」。文案给得出标题时，别拿原句当书名。
    """

    if not pages:
        return pages
    if pages[0].role != "cover":
        pages = [DeckPageSpec(heading=title or "开场", role="cover", goal="交代主题与这次要解决的问题", density="airy"), *pages]
        return pages[:MAX_PAGES]
    if title and (not pages[0].heading.strip() or pages[0].heading.startswith(("开场", "标题", "封面"))):
        pages[0] = pages[0].model_copy(update={"heading": title[:30]})
    return pages


def _baseline_material(role: str, heading: str) -> MaterialAsk | None:
    """基线的素材需求：只给封面与章节页要主图，正文页不硬配图。

    「正文页都配一张图」是版式单调的另一半原因：图位永远要图，就永远搜同一句。
    """

    if role in ("cover", "section"):
        return MaterialAsk(slot="figure", subject=_clean_heading(heading)[:40] or "主题", style="plate", priority="optional")
    return None


#: 无模型时的视觉方案。**场合不同，长得就该不同**——这是基线，也是「不许所有
#: 稿子一个样」的最小证据：汇报用黑体深色、试讲用宣纸折枝、答辩用线描。
#: master_id 由场合绑默认母版（masters.GENRE_DEFAULT_MASTER 的同款映射）。
_BASELINE_LOOK: Final[dict[str, dict[str, Any]]] = {
    "orientation": {
        "master_id": "xuan-songkai",
        "mood": "亲切、不端着，第一眼要让人觉得这门学问和自己有关",
        "theme": "paper",
        "background": {"kind": "motif", "motif": "paper-plain"},
        "ornament": {"motif": "corner-branch", "corners": ["tl", "br"]},
        "type_pair": "serif-full",
        "image_treatment": "plate",
        "rhythm": "封面留白 + 主图，正文左文右图，章节居中，收束只留一句",
    },
    "lecture": {
        "master_id": "xuan-songkai",
        "mood": "安静、学术，版心稳，装饰少",
        "theme": "paper",
        "background": {"kind": "motif", "motif": "paper-chain"},
        "ornament": {"motif": "corner-fret", "corners": ["tl", "tr"]},
        "type_pair": "serif-full",
        "image_treatment": "plate",
        "rhythm": "章节页分隔段落，内容页交替「要点 / 对照 / 流程」",
    },
    "brief": {
        "master_id": "modern-sans",
        "mood": "干净、结论先行，不要装饰",
        "theme": "ink",
        "background": {"kind": "solid", "tone": "bg"},
        "ornament": {"motif": "none"},
        "type_pair": "sans-full",
        "image_treatment": "photo",
        "rhythm": "结论页 → 依据页 → 方案页，页页短句",
    },
    "training": {
        "master_id": "celadon-wenkai",
        "mood": "清楚、可照做，步骤要看得见",
        "theme": "celadon",
        "background": {"kind": "motif", "motif": "paper-plain"},
        "ornament": {"motif": "none"},
        "type_pair": "sans-full",
        "image_treatment": "photo",
        "rhythm": "目标 → 演示 → 分步练 → 常见错误 → 验收",
    },
    "defense": {
        "master_id": "ink-gold",
        "mood": "克制、证据说话，图表为主",
        "theme": "paper",
        "background": {"kind": "solid", "tone": "bg"},
        "ornament": {"motif": "none"},
        "type_pair": "serif-full",
        "image_treatment": "line",
        "rhythm": "问题 → 方法 → 结果 → 讨论，页页带一张图或表",
    },
    "pitch": {
        "master_id": "ink-gold",
        "mood": "有张力，深色底把重点压出来",
        "theme": "ink",
        "background": {"kind": "motif", "motif": "paper-mottle"},
        "ornament": {"motif": "none"},
        "type_pair": "sans-full",
        "image_treatment": "collage",
        "rhythm": "痛点满版图 → 方案 → 证据 → 壁垒 → 诉求",
    },
}


def _apply_master_defaults(merged: dict[str, Any], master: Any, *, given: set[str] | None = None) -> None:
    """master_id 命中时，把母版的默认设计系统写进 look。

    ``given`` 是**模型显式给了**的字段名集合——只有这些字段保留原值，
    其余（theme/type_pair/background/ornament）一律以母版默认为准。
    旧基线里那些按 genre 猜的值不该挡住母版：母版才是设计系统本身。
    """
    spec = master.spec
    provided = given or set()
    merged["master_id"] = spec.id
    variants = spec.palette_variants
    merged["accent_variant"] = max(0, min(int(merged.get("accent_variant") or 0), len(variants) - 1))
    deco = spec.decoration()
    ornament = deco.get("ornament") or {}
    ground = deco.get("ground") or {}
    if "theme" not in provided:
        merged["theme"] = spec.theme
    if "type_pair" not in provided:
        merged["type_pair"] = spec.type_pair
    if "background" not in provided:
        if ground.get("motif"):
            merged["background"] = {"kind": "motif", "motif": ground["motif"]}
        elif ground.get("subject") or ground.get("keywords"):
            # 「真纹样底纹」组：背景走素材检索（剪纸/回纹方连铺底）。
            merged["background"] = {
                "kind": "asset",
                "subject": str(ground.get("subject") or ""),
                "keywords": list(ground.get("keywords") or []),
                "veil": 0.34,
            }
        else:
            merged["background"] = dict(spec.background)
    if "ornament" not in provided:
        if ornament.get("motif") and ornament["motif"] != "none":
            merged["ornament"] = {"motif": ornament["motif"], "corners": []}
        elif ornament.get("subject") or ornament.get("keywords"):
            merged["ornament"] = {
                "motif": "none",
                "subject": str(ornament.get("subject") or ""),
                "keywords": list(ornament.get("keywords") or []),
            }
        else:
            merged["ornament"] = {"motif": "none"}


def _baseline_look(genre: str, *, base_theme: str = "") -> DeckLook:
    data = dict(_BASELINE_LOOK.get(genre, _BASELINE_LOOK["lecture"]))
    from .masters import by_id as master_by_id

    master = master_by_id(data.get("master_id"))
    if master is not None:
        _apply_master_defaults(data, master)
    if base_theme in THEMES:
        data["theme"] = base_theme
    return DeckLook.model_validate(data)


def _composition_for(page: DeckPageSpec) -> str:
    """规则版的构图：由这一页的角色与有没有图决定，十页不该全是一个摆法。"""

    has_image = bool(page.material and not page.material.empty)
    if page.role == "cover":
        return "text-image-right" if has_image else "centered"
    if page.role in ("section", "summary"):
        return "centered"
    if page.role in ("agenda", "process", "data"):
        return "text-only"
    if page.role in ("compare", "matrix"):
        return "text-only"
    return "text-image-right" if has_image else "text-only"


def _reconcile_look(raw: Any, base: DeckLook, *, genre: str = "") -> tuple[DeckLook, list[str]]:
    """把模型给的视觉方案收进合法范围。只拦「画不出来」的，不改它的审美。

    母版路线下的三条硬规矩：
    - ``master_id`` 不认识 → 按场合回落默认母版，写 note（不许 raise）；
    - master_id 命中时，模型**没显式给**的 theme/type_pair/background/ornament
      由母版回填——模型给过的（代表用户明说的「深墨底」「不要角花」）保留；
    - 背景若声称用库图（kind=asset），必须有 subject 或 keywords，
      否则选材阶段无从检索；角饰 motif=none 且没给 subject ＝ 明确不要角花，
      **不许回落成默认角花**。
    """

    notes: list[str] = []
    if not isinstance(raw, dict):
        return base, notes
    merged: dict[str, Any] = base.model_dump()
    for key in ("mood", "theme", "type_pair", "image_treatment", "rhythm"):
        if str(raw.get(key) or "").strip():
            merged[key] = raw[key]
    given: set[str] = {key for key in ("mood", "theme", "type_pair", "image_treatment", "rhythm") if str(raw.get(key) or "").strip()}
    if isinstance(raw.get("palette"), dict):
        palette = DeckPalette.model_validate(raw["palette"])
        merged["palette"] = palette.model_dump() if palette.usable else None
        if not palette.usable:
            notes.append("配色没给全（至少要有底色与字色），按母版色板走")
    if isinstance(raw.get("background"), dict):
        background = LookBackground.model_validate(raw["background"])
        if background.kind == "asset" and not (background.subject or background.keywords):
            background = background.model_copy(update={"kind": "motif", "motif": "paper-plain"})
            notes.append("背景说要图却没说什么图，改回纸纹")
        elif background.kind == "asset" and background.veil <= 0:
            # 满版图不压暗就把字压没了。这不是审美，是可用性。
            background = background.model_copy(update={"veil": 0.42})
        merged["background"] = background.model_dump()
        given.add("background")
    if isinstance(raw.get("ornament"), dict):
        ornament = LookOrnament.model_validate(raw["ornament"])
        if ornament.motif == "none" and not (ornament.subject or ornament.keywords):
            notes.append("角饰：这份稿子不挂角花")
        merged["ornament"] = ornament.model_dump()
        given.add("ornament")

    # 母版：模型选的优先；不认识回落场合默认。命中后回填没显式给的字段。
    from .masters import by_id as master_by_id, default_master_for

    raw_master = str(raw.get("master_id") or "").strip()
    master = master_by_id(raw_master) if raw_master else None
    if raw_master and master is None:
        notes.append(f"母版「{raw_master[:24]}」不在目录里，按场合用默认母版")
        master = master_by_id(default_master_for(genre))
    elif master is None:
        master = master_by_id(merged.get("master_id") or default_master_for(genre))
    if master is not None:
        if str(raw.get("accent_variant") or "").strip() or isinstance(raw.get("accent_variant"), int):
            try:
                merged["accent_variant"] = int(raw.get("accent_variant") or 0)
            except (TypeError, ValueError):
                merged["accent_variant"] = 0
        _apply_master_defaults(merged, master, given=given)
    return DeckLook.model_validate(merged), notes


# ── 读整句 ──────────────────────────────────────────────────────────────────


def _payload(
    topic: str,
    *,
    base: DeckSpec,
    coverage: Coverage | None,
    docs: list[Any],
    params: dict[str, Any],
    materials: list[str],
    channels_digest: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request": topic,
        "page_hint": str(params.get("page_count") or ""),
        "audience_hint": _audience_hint(params),
        "theme_hint": str(params.get("theme") or ""),
        "role_menu": [{"role": role, "label": ROLE_LABEL[role], "write": ROLE_CONTRACT[role]} for role in ROLES],
        # 母版菜单：模型的主职责就是从这里选一套设计系统（id/label/tagline/
        # 场合/色板变体）。几何不问模型——选了系统，剩下的是填内容。
        "master_menu": _master_menu(),
        # 视觉方案的词汇表：主题、构图、配图处理、母题。模型要用**这些词**写 look，
        # 写别的（「中国风」「高级感」）渲染层没法执行。
        "theme_menu": [{"theme": theme, "text": THEME_TEXT[theme]} for theme in THEMES],
        "composition_menu": [{"composition": item, "label": COMPOSITION_LABEL[item]} for item in COMPOSITIONS],
        "image_treatment_menu": [{"treatment": item, "label": IMAGE_TREATMENT_LABEL[item]} for item in IMAGE_TREATMENTS],
        "motif_menu": _motif_menu(),
        "type_pair_menu": TYPE_PAIR_MENU,
        "rule_baseline": {
            "title": base.title,
            "genre": base.genre,
            "pages": [page.clean_heading for page in base.pages],
            "look": base.look.model_dump(mode="json"),
            "note": "这是规则拟的基线，可能过于套路。用户诉求更具体时按用户的改。",
        },
        # 「库里有什么」要一直给：只给命中的学科，模型会以为库里只有那一门课。
        "library": catalog.digest(coverage),
    }
    if coverage is not None and coverage.chapter_info is not None:
        chapter = coverage.chapter_info
        payload["matched"] = {
            "subject": coverage.chapter_subject.title if coverage.chapter_subject else "",
            "chapter": chapter.title,
            "sections": [section.title for section in chapter.sections[:8]],
            "note": "这句话落在本库的这门课这一章里：术语与章名照用库里的写法。",
        }
    if docs:
        payload["herbs"] = [
            {
                "name": doc.get("name", ""),
                "nature": doc.get("nature", ""),
                "flavor": doc.get("flavor", []),
                "meridians": doc.get("meridians", []),
                "effects": list(doc.get("effects") or [])[:6],
                "dosage": str((doc.get("dosage") or {}).get("text", "")),
                "cautions": list(doc.get("cautions") or [])[:3],
            }
            for doc in docs[:3]
            if isinstance(doc, dict)
        ]
    if channels_digest:
        # 素材库清单直接进提示词：模型要先知道「有哪些图库、各自能给什么」，
        # 才谈得上「自己挑需要的素材」。以前这段只在排版阶段才有。
        payload["assets_library"] = channels_digest
    if materials:
        payload["user_material"] = [str(item)[:400] for item in materials[:3]]
    return payload


def _master_menu() -> list[dict[str, Any]]:
    """母版目录的模型版（masters.menu 的薄封装，避免顶层循环 import）。"""

    from .masters import menu

    return menu()


def _motif_menu() -> list[dict[str, Any]]:
    """母题目录的模型版：id、族、什么时候用。参数不给——那是渲染细节。"""

    from .motifs import MOTIFS

    return [
        {
            "id": motif.id,
            "label": motif.label,
            "family": motif.family,
            "slot": motif.slot,
            "themes": list(motif.themes),
            "when": motif.note,
        }
        for motif in MOTIFS
    ]


def _informative(draft: DeckSpecDraft) -> bool:
    return bool([page for page in draft.pages if page.label.strip()] or draft.title or draft.interpretation)


async def compose_deck_spec(
    topic: str,
    *,
    coverage: Coverage | None = None,
    docs: list[Any] | None = None,
    params: dict[str, Any] | None = None,
    materials: list[str] | None = None,
    shape: str = "",
    channels_digest: str = "",
    llm_call: Any = None,
    router: ModelRouter | None = None,
    task_type: str = "generate",
) -> DeckSpec:
    """读懂这句话，给出一份讲稿施工图。模型优先，失败退回基线。"""

    params = dict(params or {})
    docs = list(docs or [])
    text = " ".join(str(topic or "").split())
    base = baseline_spec(text, coverage=coverage, docs=docs, params=params, shape=shape)
    if not text:
        return base

    messages = [
        {"role": "system", "content": DECK_SPEC_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _json(
                _payload(
                    text,
                    base=base,
                    coverage=coverage,
                    docs=docs,
                    params=params,
                    materials=list(materials or []),
                    channels_digest=channels_digest,
                )
            ),
        },
    ]
    draft: DeckSpecDraft | None = None
    try:
        if llm_call is not None:
            raw = await llm_call(messages)
            draft = DeckSpecDraft.model_validate_json(_extract_json(raw))
        else:
            draft = await structured_create(
                task_type=task_type,
                messages=messages,
                response_model=DeckSpecDraft,
                mock_factory=lambda: DeckSpecDraft(),
                router=router or ModelRouter(),
            )
    except Exception:  # noqa: BLE001 - 解读失败必须退回基线，不能拦住出方案
        draft = None

    if draft is None or not _informative(draft):
        return base
    return reconcile_deck_spec(draft, base, text)


def reconcile_deck_spec(draft: DeckSpecDraft, base: DeckSpec, topic: str) -> DeckSpec:
    """把模型给的收进合法区间，并**保证版式不单调**。

    只做「不许越界」与「不许含糊」，不改写模型对内容的理解：标题、场合、页序
    都照模型给的。只有越界（页太多、时长离谱、标题是原句）与**连续同版式**才动手，
    并写进 notes（「四页都是要点」是版式缺陷，不是风格差异）。
    """

    notes: list[str] = []
    genre = draft.genre if draft.genre in GENRES else base.genre
    org = draft.organization_kind if draft.organization_kind in ORG_LINE else base.organization_kind

    pages: list[DeckPageSpec] = []
    seen: set[str] = set()
    for item in draft.pages:
        heading = _clean_heading(item.label)
        if not heading or heading in seen:
            continue
        seen.add(heading)
        role = item.role if item.role in ROLES else (role_of(f"{heading} {item.goal}") or "points")
        covers = [str(point).strip()[:40] for point in item.covers if str(point).strip()][:5]
        density = item.density if item.density in DENSITIES else ("airy" if role in ("cover", "section") else "normal")
        pages.append(
            DeckPageSpec(
                heading=heading,
                role=role,  # type: ignore[arg-type]
                goal=str(item.goal or "").strip()[:160],
                must_cover=covers,
                density=density,  # type: ignore[arg-type]
                material=_parse_material(item, heading, role),
                composition=item.composition if item.composition in COMPOSITIONS else "auto",
            )
        )
    if len(pages) < MIN_PAGES:
        notes.append(f"模型给的页数不足 {MIN_PAGES} 页，按完整的通用骨架补齐")
        pages = list(base.pages)
    if len(pages) > MAX_PAGES:
        notes.append(f"页数压到 {MAX_PAGES} 页以内")
        pages = pages[:MAX_PAGES]

    pages, diversity_note = _diversify(pages)
    if diversity_note:
        notes.append(diversity_note)

    low, high = GENRE_BUDGET.get(genre, (8, 14))
    pages_target = _num(draft.target_pages) or len(pages)
    pages_target = _clamp(pages_target, MIN_PAGES, MAX_PAGES)
    if len(pages) != pages_target:
        # 页数写「12 页」而只给了 8 页，卡片第一眼就自相矛盾：以实际页数为准。
        if abs(len(pages) - pages_target) >= 2:
            notes.append(f"页数以实际列出的 {len(pages)} 页为准")
        pages_target = len(pages)
    if pages_target < low // 2 or pages_target > high * 2:
        pages_target = _clamp(pages_target, low, high)

    duration = _num(draft.duration_min) or base.duration_min
    if duration < MIN_DURATION or duration > MAX_DURATION:
        duration = _clamp(duration, MIN_DURATION, MAX_DURATION)
        notes.append(f"时长按每页约 1.5 分钟定在 {duration} 分钟")

    arc = [str(item).strip()[:12] for item in draft.arc if str(item).strip()][:5]
    if not arc:
        arc = list(GENRE_ARC.get(genre, ()))

    accepted = _accept_title(draft.title, base.title, topic)
    if not accepted:
        notes.append("模型给的标题不像讲稿名（或就是原句），按主题另起了一个")
        title = base.title
    else:
        title = accepted
        if draft.title.strip() != title:
            notes.append("标题不像讲稿名，按主题另起了一个")

    interpretation = [_plain(line) for line in draft.interpretation if _plain(line)][:4]
    pages = _cover_heading(pages, title)
    look, look_notes = _reconcile_look(draft.look, base.look, genre=genre)
    notes.extend(look_notes)
    goal = " ".join(str(draft.goal or "").split())[:200] or base.goal
    return DeckSpec(
        title=title[:40],
        subtitle=str(draft.subtitle or base.subtitle)[:60],
        genre=genre,  # type: ignore[arg-type]
        audience=(str(draft.audience or "").strip() or base.audience)[:60],
        occasion=(str(draft.occasion or "").strip() or base.occasion)[:60],
        organization=(str(draft.organization or "").strip() or ORG_LINE.get(org, ORG_LINE["topic"]))[:300],
        organization_kind=org,
        thesis=(str(draft.thesis or "").strip() or base.thesis)[:300],
        goal=goal,
        arc=arc,
        target_pages=pages_target,
        duration_min=duration,
        pages=pages,
        theme=look.theme,
        look=look,
        emphasis=[str(item)[:40] for item in draft.emphasis if str(item).strip()][:6],
        interpretation=interpretation,
        basis="model",
        notes=notes,
    )


def _parse_material(item: _DraftPage, heading: str, role: str) -> MaterialAsk | None:
    """模型可以用一句「人参饮片实拍」写素材需求，也可以不写。"""

    raw = f"{item.material} {item.image}".strip()
    keywords = [str(word).strip()[:12] for word in item.keywords if str(word).strip()][:6]
    if not raw and not keywords:
        return _baseline_material(role, heading)
    slot = "evidence" if _has(raw, ("实拍", "证据", "原植物", "药材图", "饮片")) else "figure"
    style = "photo" if _has(raw, ("实拍", "照片", "photo")) else ("line" if _has(raw, ("线描", "白描", "剪影")) else "plate")
    return MaterialAsk(
        slot=slot,  # type: ignore[arg-type]
        subject=raw[:40] or "".join(keywords)[:40],
        style=style,  # type: ignore[arg-type]
        priority="required" if _has(raw, ("必须", "一定要")) else "optional",
        why="这一页要用画面说清主体",
        keywords=keywords,
    )


#: 相邻同版式超过这个数就算「单调」。三页连着要点在放映时是灾难：
#: 听众看不出页面换了，讲者也失去了节奏。
MAX_RUN: Final[int] = 2


def _diversify(pages: list[DeckPageSpec]) -> tuple[list[DeckPageSpec], str]:
    """版式去单调：连续三页同版式时，按内容形态给其中一页换角色。

    只换「呈现方式」，不动标题与必讲点——这一页讲什么由用户的话决定，
    怎么讲由版式决定，两件事分开才不会把用户的诉求改掉。
    """

    if len(pages) < 4:
        return pages, ""
    # 内容形态优先级：有对照给对照，有先后给流程，有数字给数据，有风险给警示。
    fallback_order = ("compare", "process", "data", "warning", "matrix")
    changed = 0
    seen_roles: set[str] = set(page.role for page in pages)
    run = 1
    for index in range(1, len(pages)):
        current = pages[index]
        if current.role == pages[index - 1].role and current.role in ("points", "compare", "process", "data", "warning", "matrix"):
            run += 1
        else:
            run = 1
        if run <= MAX_RUN:
            continue
        if current.role in ("cover", "section", "summary", "agenda"):
            continue
        text = f"{current.heading} {current.goal} {' '.join(current.must_cover)}"
        hinted = role_of(text)
        candidate = hinted if hinted in ROLES and hinted != current.role else ""
        if not candidate:
            candidate = next((role for role in fallback_order if role not in seen_roles and role != current.role), "")
        if not candidate:
            continue
        pages[index] = current.model_copy(update={"role": candidate})
        seen_roles.add(candidate)
        changed += 1
        run = 1
    if not changed:
        return pages, ""
    return pages, f"版式去单调：{changed} 页按内容形态换成了对照 / 流程 / 数据 / 警示，避免整份都是要点页"


# ── 方案 ⇄ 生成 ─────────────────────────────────────────────────────────────


def deck_spec_of(plan: dict[str, Any] | None) -> DeckSpec | None:
    """从方案里取出施工图。老方案（没有 spec）返回 ``None``。"""

    if not isinstance(plan, dict):
        return None
    raw = plan.get("spec")
    if not isinstance(raw, dict) or not raw:
        return None
    # 导图有 branches、文稿有 sections，别把它们的 spec 当讲稿施工图。
    if "pages" not in raw and "target_pages" not in raw:
        return None
    if "sections" in raw or "branches" in raw:
        return None
    try:
        spec = DeckSpec.model_validate(raw)
    except Exception:  # noqa: BLE001 - 坏数据不该让生成炸掉
        return None
    if not spec.pages and not spec.title:
        return None
    return spec


def align_deck_with_units(spec: DeckSpec, units: list[dict[str, Any]]) -> DeckSpec:
    """让施工图的页与方案卡上的「页面」逐项对齐。

    用户在卡片上加一页、删一页、改页名，改的是 ``units``。版式（role）与素材
    需求按**位置**沿用——改页名不该把这一页的对照表变回要点页。
    """

    rows = [unit for unit in units or [] if isinstance(unit, dict) and str(unit.get("label") or "").strip()]
    if not rows:
        return spec
    pages: list[DeckPageSpec] = []
    for index, unit in enumerate(rows):
        label = _clean_heading(str(unit.get("label") or ""))
        if not label:
            continue
        old = spec.pages[index] if index < len(spec.pages) else None
        detail = [item.strip() for item in re.split(r"[；;、,，]", str(unit.get("detail") or "")) if item.strip()]
        pages.append(
            DeckPageSpec(
                heading=label,
                role=(old.role if old else ("cover" if index == 0 else "points")),
                goal=str(unit.get("intent") or (old.goal if old else ""))[:160],
                must_cover=(detail or (old.must_cover if old else []))[:5],
                density=(old.density if old else "normal"),
                material=(old.material if old else None),
                composition=(old.composition if old else "auto"),
            )
        )
    return spec.model_copy(update={"pages": pages, "target_pages": len(pages)})


def deck_params_from_plan(plan: dict[str, Any] | None, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """方案 → 生成参数。**唯一一处**翻译。

    方案卡上能手改的每个字段都要在这里有落点，否则就是「改了不生效」：
    页数读「呈现方式」里的「共 N 页」（用户手打的最算数），每页读 units，
    场合/受众/叙事弧读 spec。
    """

    out = dict(base or {})
    spec = deck_spec_of(plan)
    if spec is None:
        return out
    plan_map = plan or {}
    count = _pages_from_presentation(plan_map.get("presentation"))
    units = [unit for unit in plan_map.get("units") or [] if isinstance(unit, dict)]
    if units:
        spec = align_deck_with_units(spec, units)
    if count:
        spec = spec.model_copy(update={"target_pages": _clamp(count, MIN_PAGES, MAX_PAGES)})
    out["deck_spec"] = spec.model_dump(mode="json")
    out["page_count"] = spec.target_pages
    out["expected"] = {
        "title": spec.title,
        "pages": spec.headings,
        "roles": spec.roles,
        "variety": spec.variety,
        "target_pages": spec.target_pages,
        "materials": [page.material.subject for page in spec.pages if page.material and not page.material.empty],
    }
    return out


def _pages_from_presentation(text: Any) -> int | None:
    match = re.search(r"共\s*(\d+)\s*页", str(text or ""))
    return int(match.group(1)) if match else None


# ── 成品核对 ────────────────────────────────────────────────────────────────


def deck_conformance(content: dict[str, Any] | None, spec: DeckSpec | None) -> dict[str, Any]:
    """量一量这份稿子的版式与配图。只报事实，不打分。

    「排版单一」是用户的直接感受，所以这里必须能给出可核对的数字：
    用了几种版式、最长连续同类是几页、方案要的版式缺了哪些、几页该有图却没图。
    """

    slides = [slide for slide in ((content or {}).get("slides") or []) if isinstance(slide, dict)]
    roles = [str(slide.get("purpose") or slide.get("kind") or "points") for slide in slides]
    longest = 1
    run = 1
    for index in range(1, len(roles)):
        run = run + 1 if roles[index] == roles[index - 1] else 1
        longest = max(longest, run)
    thin = [
        str(slide.get("title") or "")[:30]
        for slide in slides
        if str(slide.get("purpose") or "") not in ("cover", "section", "agenda")
        and not (slide.get("blocks") or slide.get("bullets") or slide.get("columns"))
    ]
    expected_roles = list(dict.fromkeys(spec.roles)) if spec else []
    used = list(dict.fromkeys(roles))
    # 只有 figure / evidence 槽、且**真的画在纸上**才算「这页配了图」。
    # 两个坑都踩过：底纹与角饰也是 asset_refs（九页全算配图，稿头写成「9/2 页配图」），
    # 图位引用也可能有而版面没给它位置（纯文字页）。有 scene 就以 scene 里的
    # asset 元素为准，没有 scene（旧稿）才退回看引用。
    def _has_figure(slide: dict[str, Any]) -> bool:
        drawn = {
            str(element.get("asset") or "")
            for element in ((slide.get("scene") or {}).get("elements") or [])
            if element.get("type") == "asset"
        }
        for ref in slide.get("asset_refs") or []:
            if ref.get("slot") not in ("figure", "evidence"):
                continue
            if ref.get("result") not in ("hit", "substitute", "parametric"):
                continue
            if not (ref.get("url") or ref.get("file")):
                continue
            if drawn and str(ref.get("asset_id") or "") not in drawn:
                continue
            return True
        return False

    with_figure = sum(1 for slide in slides if _has_figure(slide))
    figure_pages = len([page for page in (spec.pages if spec else []) if page.material and not page.material.empty and page.material.slot == "figure"])
    return {
        "pages": len(slides),
        "expected_pages": spec.target_pages if spec else 0,
        "roles_used": used,
        "role_variety": len(used),
        "longest_run": longest if slides else 0,
        "missing_roles": [role for role in expected_roles if role not in used][:6],
        "thin_pages": thin[:5],
        "with_figure": with_figure,
        "figure_pages": figure_pages,
        # 一条可读的结论，前端直接显示，不必自己拼。
        "monotone": bool(slides) and (longest > MAX_RUN or (len(slides) >= 6 and len(used) <= 2)),
    }
