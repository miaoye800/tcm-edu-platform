"""把「用户那一整句话」读成一份可执行、可展示、可校验的施工图。

这个模块替掉的不是一个 bug，而是一类做法：以前「这次要什么样的图」由三张
关键词表分别回答——``brief`` 读数字、``planning.task_shape`` 选蓝图、
``archetypes`` 定骨架。三处互不相识，于是出现下面这一串**同一个病**的症状：

- 用户说「多分几个层次」——没有数字，三个表都读不到，层数仍是默认 3；
- 用户说「多一些角度对比」——「对比」命中蓝图，得到固定的
  对比维度 / 相同点 / 关键差异 / 选用提示 四支，跟句子里其余要求无关；
- 用户说「并总结学好它们需要怎么做」——这句话没有对应槽位，被整句丢掉；
- 用户在方案面板把层数改成 5——生成读的是原始入参，改的那一版没人看。

病根是**没有人真的去读那句话**。所以这里只做一件事：把整句话交给模型去读，
产出一个 ``MindSpec``（要画什么、给谁、怎么组织、几层、哪几支、必须回答什么），
模型管决策，确定性规则只做三件事：

1. 无模型时的**基线**（``baseline_spec``）：保证没有 key 也能出一份诚实方案；
2. **守门**（``reconcile_spec``）：层数不越过结构可渲染上限、分支去重去兜底词、
   依据按知识库命中重算而不是采信模型自报；
3. **翻译**（``generation_params_from_plan``）：把确认过的那一版方案翻译成生成
   参数——方案是唯一事实源，面板上能手改的每个字段都必须在这里有落点。

第 3 条是这次修复的另一半。方案面板一直能改「目标/受众/组织方式/呈现方式」，
但生成侧只读 ``depth`` 与分支名，四个字段改完等于没改。翻译层把「看得见的字」
与「生成读的键」一一对上，并把期望的层数/支数带进生成，让成品能被核对。
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.agents.structured_output import structured_create
from app.llm.router import ModelRouter

from .brief import GenerationBrief, parse_brief
from .draft import DEFAULT_DEPTH, MAX_DEPTH, MIN_DEPTH, usable_root_title
from .evidence import EvidencePack
from .intents import MapIntent
from .prompts import SPEC_SYSTEM_PROMPT

OrganizationKind = Literal["compare", "taxonomy", "process", "timeline", "concept", "learning", "mixed"]

#: 结构 → 能清楚表达的层数上限。**必须与前端 ``mindmapStructures.maxDepth`` 一致**：
#: 两边不一致时会出现「方案写 5 层、画布按 4 层折」，用户只会看到「说了不算」。
STRUCTURE_CAPACITY: Final[dict[str, int]] = {
    "logic-right": 5,
    "mind-both": 5,
    "org-down": 4,
    "fishbone": 3,
}
#: 能装下最多层的结构。要的层数超出当前结构时改用它，而不是偷偷砍层。
DEEPEST_STRUCTURE: Final[str] = "logic-right"

MAX_BRANCHES = 8
MIN_BRANCHES = 2
MAX_LEAVES_MIN = 6
#: 兜底分支名。这类分支在导图里等于「其他」，读图的人拿不到信息。
_CATCH_ALL: Final[frozenset[str]] = frozenset({"其他", "其它", "综合", "其余", "补充", "小结", "总结", "简介", "概述", "总览"})

_ORGANIZATION: Final[dict[str, tuple[OrganizationKind, str]]] = {
    "comparison": ("compare", "按可比较的维度并列展开，同一维度下双方成对出现，末了给出取舍与学法"),
    "taxonomy": ("taxonomy", "按分类层级展开：先分亚类，再落到具体条目"),
    "reverse": ("taxonomy", "按功效分组，同一功效下并列药名与各自的差别"),
    "filter": ("taxonomy", "按药类分组，组内带性味归经便于二次筛选"),
    "relation": ("taxonomy", "按被约束的一方分组，列出对象与关系类型"),
    "syndrome": ("process", "按证—症—病机—药—方串成一条链"),
    "exam": ("compare", "按考点与易混药对并列，突出辨别依据"),
    "monograph": ("concept", "按语义轴拆：性味归经 / 功效 / 主治 / 配伍 / 鉴别 / 用量注意"),
    "concept": ("concept", "按概念自身的结构拆：是什么 / 由什么组成 / 怎么用 / 有何局限"),
    # 整门课 / 整章：组织方式由教材目录决定，不是由「概念」决定。
    "overview": ("taxonomy", "按教材目录展开：先分篇（总论 / 各论），再落到章与节，章节名照书"),
}

#: 「怎么说这句话」优先于「这句话属于哪一类」。同一味药的「发展历史」与「性味功效」
#: 落的是同一个原型，但组织方式完全不同。
_SHAPE_ORGANIZATION: Final[dict[str, tuple[OrganizationKind, str]]] = {
    "temporal": ("timeline", "沿时间线推进：起源 → 关键阶段 → 推动因素 → 当代形态"),
    "comparison": ("compare", "按可比较的维度并列展开，同一维度下双方成对出现，末了给出取舍与学法"),
    "planning": ("learning", "按阶段与依赖推进，每个阶段给任务与检验方式"),
    "procedure": ("process", "按操作顺序推进，每一步给动作与判断标准"),
}

#: 关键词表只服务于**无模型**的兜底：有模型时结构与层数由模型读句子决定。
#: 这几个蓝本各自代表一种真实的组织方式，不是「四种题型」。
_SHAPE_BRANCHES: Final[dict[str, list[tuple[str, str, list[str]]]]] = {
    "comparison": [
        ("对比维度", "按性味、功效、主治、用法等维度展开", ["性味归经", "功效", "主治", "用法用量"]),
        ("相同点", "共有的功效或适用场景", ["共有功效", "共同适用证"]),
        ("关键差异", "最容易考到、也最容易用错的区别", ["药性差异", "主治偏重"]),
        ("选用提示", "什么情况下用哪个", ["选择依据"]),
    ],
    "temporal": [
        ("起源", "最早可考的时间与出处", []),
        ("关键阶段", "按时间排列的演进节点", []),
        ("推动因素", "每次变化背后的原因", []),
        ("当代形态", "今天的样子与用法", []),
    ],
    "planning": [
        ("学习目标", "学完能做什么", []),
        ("前置基础", "开始前的必备知识", []),
        ("阶段任务", "按阶段拆解的学习内容", []),
        ("检验方式", "怎么知道自己学会了", []),
        ("常见误区", "需要提前避开的地方", []),
    ],
    "procedure": [
        ("适用场景", "什么情况下用这个流程", []),
        ("准备", "需要的材料与前置条件", []),
        ("步骤", "按顺序展开的关键动作", []),
        ("注意事项", "最容易出错的环节", []),
    ],
    "concept": [
        ("核心概念", "它是什么、解决什么问题", []),
        ("构成要素", "由哪些部分组成", []),
        ("运作方式", "各部分怎么配合", []),
        ("应用场景", "用在哪里", []),
        ("局限与争议", "不适用或仍有分歧的地方", []),
    ],
}

#: 「多分几个层次」「更细」这类**没有数字的量**。读不出数不等于没听见：
#: 兜底基线按词性给一个比默认更深的层数，否则用户写了要求却拿到同一张图。
_VAGUE_DEEPER: Final[tuple[str, ...]] = (
    "多分几个层次",
    "多层",
    "层次多",
    "多几个层次",
    "多分几层",
    "分层",
    "更细",
    "详细",
    "展开",
    "深入",
    "细一点",
    "多一些角度",
    "多角度",
    "多方面",
    "多维度",
)
#: 「尽可能细」比「多几层」更进一档。
_VAGUE_DEEPEST: Final[tuple[str, ...]] = ("尽可能", "尽量", "越细越好", "越全越好", "穷尽")

_CN_NUM: Final[dict[str, str]] = {"一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
_DEPTH_TEXT_RE: Final = re.compile(r"([一二两三四五六七八九1-9])\s*层")
_BRANCH_TEXT_RE: Final = re.compile(r"([二三四五六七八2-8])\s*(?:个)?\s*(?:大)?(?:分支|板块|部分|支)")


class BranchSpec(BaseModel):
    """一条一级分支：叫什么、要回答什么、必须覆盖哪些点。"""

    model_config = ConfigDict(extra="ignore")

    label: str = Field(default="", max_length=40)
    purpose: str = Field(default="", max_length=160)
    must_cover: list[str] = Field(default_factory=list, max_length=6)

    @property
    def clean_label(self) -> str:
        return _clean_label(self.label)


class MindSpec(BaseModel):
    """这次到底要什么样的图。方案面板展示它，生成阶段消费它。"""

    model_config = ConfigDict(extra="ignore")

    root_topic: str = Field(default="", max_length=60)
    audience: str = Field(default="", max_length=120)
    organization: str = Field(default="", max_length=300)
    organization_kind: OrganizationKind = "concept"
    #: 最终会用到的层数（已按结构上限夹过）。
    depth: int = Field(default=DEFAULT_DEPTH, ge=MIN_DEPTH, le=MAX_DEPTH)
    #: 用户/模型原本想要的层数。与 ``depth`` 不同时说明被结构上限削过，要出声。
    depth_requested: int | None = Field(default=None, ge=1, le=12)
    leaves_min: int = Field(default=3, ge=2, le=MAX_LEAVES_MIN)
    structure: str = "logic-right"
    branches: list[BranchSpec] = Field(default_factory=list, max_length=MAX_BRANCHES)
    #: 用户明确提的要求，一条一句。「多角度对比」「总结怎么学」都在这里。
    emphasis: list[str] = Field(default_factory=list, max_length=8)
    #: 这张图必须回答的问题，进提示词当硬约束。
    must_answer: list[str] = Field(default_factory=list, max_length=6)
    #: 给用户看的「我这样理解你的要求」，不解释算法。
    interpretation: list[str] = Field(default_factory=list, max_length=6)
    #: rules = 无模型时的确定性基线；model = 模型读出来的。
    basis: Literal["model", "rules"] = "rules"
    notes: list[str] = Field(default_factory=list, max_length=6)

    @property
    def labels(self) -> list[str]:
        return [branch.clean_label for branch in self.branches if branch.clean_label]

    def depth_gap(self) -> str:
        """被结构上限削过层数时给一句人话，没削就是空串。"""

        asked = self.depth_requested
        if asked and asked > self.depth:
            return f"你要 {asked} 层，「{self.structure}」最多画 {self.depth} 层，已按 {self.depth} 层排"
        return ""


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _clean_label(value: str) -> str:
    text = " ".join(str(value or "").split()).strip(" \t-—·•、，,。：:；;")
    return text[:40]


def _clamp(value: int | None, low: int, high: int) -> int | None:
    if value is None:
        return None
    return min(max(int(value), low), high)


def depth_from_text(text: str) -> int | None:
    """从一句人话里读层数。读不出返回 ``None``。

    读的是**方案面板上那行字**，不是原始提问：用户在「呈现方式」里把 3 改成 5，
    那句话就是他最新的意思，生成必须照它来。
    """

    matched = _DEPTH_TEXT_RE.search(str(text or ""))
    if not matched:
        return None
    token = matched.group(1)
    return int(_CN_NUM.get(token, token)) if token.isdigit() or token in _CN_NUM else None


def branches_from_text(text: str) -> int | None:
    matched = _BRANCH_TEXT_RE.search(str(text or ""))
    if not matched:
        return None
    token = matched.group(1)
    return int(_CN_NUM.get(token, token)) if token.isdigit() or token in _CN_NUM else None


def structure_capacity(structure: str) -> int:
    return STRUCTURE_CAPACITY.get(str(structure or ""), MAX_DEPTH)


def fit_structure(structure: str, depth: int) -> tuple[str, bool]:
    """结构装不下这么多层时换一个装得下的。返回（结构, 是否换过）。

    悄悄砍层的代价是「说了 5 层、画出 4 层」，用户不再相信这个功能；
    换结构只是换个画法，内容不变，代价小得多。
    """

    current = str(structure or DEEPEST_STRUCTURE)
    if structure_capacity(current) >= depth:
        return current, False
    if structure_capacity(DEEPEST_STRUCTURE) >= depth:
        return DEEPEST_STRUCTURE, True
    return current, False


def _wants_vague_depth(text: str) -> int | None:
    """没有数字的「要更深」。返回建议层数，没有这类说法就是 ``None``。"""

    content = str(text or "")
    if any(word in content for word in _VAGUE_DEEPEST):
        return MAX_DEPTH
    if any(word in content for word in _VAGUE_DEEPER):
        # 「多分几个层次」至少要比默认的 3 层多一档，否则说了等于没说。
        return min(MAX_DEPTH, DEFAULT_DEPTH + 1)
    return None


def shape_of(text: str) -> str:
    """兜底用的组织形态判断。只在没有模型时决定基线蓝本。"""

    content = str(text or "")
    if any(word in content for word in ("对比", "比较", "区别", "异同", "鉴别", "差异")):
        return "comparison"
    if any(word in content for word in ("历史", "发展", "演变", "沿革", "源流", "起源")):
        return "temporal"
    if any(word in content for word in ("规划", "计划", "路径", "路线", "阶段学习", "备考", "复习计划")):
        return "planning"
    if any(word in content for word in ("步骤", "流程", "操作", "怎么做", "如何做")):
        return "procedure"
    return ""


# ---------------------------------------------------------------------------
# 基线（无模型 / 模型失败时）
# ---------------------------------------------------------------------------


def baseline_spec(
    topic: str,
    pack: EvidencePack | None,
    params: dict[str, Any] | None = None,
    *,
    brief: GenerationBrief | None = None,
    intent: MapIntent | None = None,
) -> MindSpec:
    """确定性基线：真实素材 + 该题型的常规组织方式。

    这份基线不是「正确答案」，而是没有模型时的**诚实答案**：它能让用户看到
    一份完整可确认的方案，但不会假装读懂了句子里所有要求——``basis="rules"``
    会一路传到面板上。
    """

    params = dict(params or {})
    text = " ".join(str(topic or "").split())
    brief = brief or parse_brief(f"{text} {params.get('focus') or ''}".strip())
    intent = intent or (pack.intent if pack is not None else None)
    archetype = getattr(intent, "archetype", "") or "concept"
    degraded = bool(getattr(intent, "degraded", True)) if intent is not None else True

    structure = str(params.get("structure") or "")
    if not structure and intent is not None:
        structure = intent.structure_hint
    structure = structure or DEEPEST_STRUCTURE

    # 层数：明说的数 > 模糊的「多分几层」 > 结构上限内的一档默认
    # 这里读原文的数而不是只读 ``brief.depth``：后者已被钳到 5，"我要六层"
    # 会变成一个没有来源的 5，用户永远不知道自己的数被改了。
    explicit = depth_from_text(text)
    vague = None if explicit is not None else _wants_vague_depth(text)
    requested = explicit or brief.depth or vague or DEFAULT_DEPTH
    if explicit is None and vague is not None:
        # 模糊要求只到结构上限，不要逼着把 5 层塞进鱼骨图。
        requested = min(requested, structure_capacity(structure))
    structure, switched = fit_structure(structure, requested)
    depth = min(requested, structure_capacity(structure), MAX_DEPTH)

    shape = shape_of(text) or ("comparison" if archetype == "comparison" else "")
    slots = [slot for slot in (pack.branch_slots if pack is not None else []) if slot]
    organization_kind, organization = _ORGANIZATION.get(archetype, _ORGANIZATION["concept"])
    if shape in _SHAPE_BRANCHES:
        # 「人参的发展历史」锚到的是单味药，但用户要的是时间线。组织方式与分支
        # 都由这句话的诉求决定，不由它落到哪个原型决定——原型只说明「这是一味药」。
        rows = _SHAPE_BRANCHES[shape]
        organization_kind, organization = _SHAPE_ORGANIZATION.get(shape, (organization_kind, organization))
    elif slots:
        rows = [(slot, "", []) for slot in slots[:MAX_BRANCHES]]
    else:
        rows = _SHAPE_BRANCHES["concept"]

    branches = [BranchSpec(label=label, purpose=purpose, must_cover=list(covers)) for label, purpose, covers in rows]
    # 用户明说「分四支」就按四支给。多给的支在确认前就得砍掉，
    # 否则他会以为「说了四支，面板上却有六支」是没听懂。
    if brief.branches and MIN_BRANCHES <= brief.branches < len(branches):
        branches = branches[: brief.branches]

    audience = str(params.get("audience") or "").strip() or "学习者"
    interpretation = [
        f"主题按「{_clean_label(text)[:24] or '未命名'}」理解。" if text else "主题尚未给出。",
        f"按 {len(branches)} 支、{depth} 层展开：{organization}。",
    ]
    if brief.labels():
        interpretation.append("句子里点到的内容已排进分支或必答项：" + "、".join(brief.labels()[:4]) + "。")
    if degraded:
        interpretation.append("这个主题在本地教材语料里没有对应条目，内容由模型按学科常识整理，不编具体剂量与方名。")

    notes: list[str] = []
    if switched:
        notes.append(f"「{params.get('structure') or structure}」装不下 {requested} 层，已改用「{structure}」。")
    if params.get("brief_note"):
        notes.append(f"原话里的额外要求：{params['brief_note']}")
    notes.append("这一版是按规则拟的：本机没有可用的解读模型，句子里更细的要求未必读全，可直接改方案。")

    return MindSpec(
        root_topic=usable_root_title(text) or _clean_label(text)[:20],
        audience=audience,
        organization=organization,
        organization_kind=organization_kind,
        depth=depth,
        depth_requested=requested,
        leaves_min=brief.leaves_min or (4 if depth >= 4 else 3),
        structure=structure,
        branches=branches[:MAX_BRANCHES],
        emphasis=tuple(brief.labels()),
        must_answer=(),
        interpretation=interpretation,
        basis="rules",
        notes=notes,
    )


# ---------------------------------------------------------------------------
# 模型解读
# ---------------------------------------------------------------------------


class _DraftBranch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str = Field(default="", max_length=40)
    purpose: str = Field(default="", max_length=160)
    must_cover: list[str] = Field(default_factory=list, max_length=6)


class SpecDraft(BaseModel):
    """模型直接产出的部分。依据、层数上限这些由 ``reconcile_spec`` 兜。"""

    model_config = ConfigDict(extra="ignore")

    root_topic: str = Field(default="", max_length=60)
    audience: str = Field(default="", max_length=60)
    organization: str = Field(default="", max_length=200)
    organization_kind: str = Field(default="", max_length=20)
    depth: int = Field(default=0, ge=0, le=12)
    leaves_min: int = Field(default=0, ge=0, le=8)
    structure: str = Field(default="", max_length=20)
    branches: list[_DraftBranch] = Field(default_factory=list, max_length=10)
    emphasis: list[str] = Field(default_factory=list, max_length=8)
    must_answer: list[str] = Field(default_factory=list, max_length=6)
    interpretation: list[str] = Field(default_factory=list, max_length=6)


def _payload(topic: str, pack: EvidencePack | None, params: dict[str, Any], base: MindSpec) -> dict[str, Any]:
    """给模型看的材料摘要。只放能决定结构的东西，不塞整份素材包。"""

    payload: dict[str, Any] = {
        "request": topic,
        "focus": str(params.get("focus") or ""),
        "audience_hint": str(params.get("audience") or ""),
        "structure_hint": base.structure,
        "depth_hint": base.depth,
        "rule_baseline": {
            "branches": [branch.label for branch in base.branches],
            "organization": base.organization,
            "note": "这是规则拟的基线，可能过于套路。用户诉求更具体时按用户的改。",
        },
    }
    if pack is not None:
        payload["evidence"] = {
            "degraded": pack.degraded,
            "intent": pack.intent.label,
            "herbs": pack.herb_names[:6],
            "branch_slots": list(pack.branch_slots)[:8],
            "groups": {key: list(value)[:6] for key, value in list(pack.groups.items())[:6]},
            "notes": list(pack.notes)[:3],
        }
        if pack.catalog:
            # 「库里有什么」是决定分支的最强信号：有这份目录，分支就该照抄目录，
            # 而不是从零编一套。放在 evidence 里而不是 system prompt 里，
            # 因为它是**这次请求**的材料，不是平台的通用规则。
            payload["evidence"]["catalog"] = pack.catalog
    materials = params.get("materials")
    if isinstance(materials, list) and materials:
        payload["user_material"] = [str(item)[:400] for item in materials[:2]]
    return payload


async def compose_spec(
    topic: str,
    pack: EvidencePack | None,
    params: dict[str, Any] | None = None,
    *,
    llm_call: Any = None,
    router: ModelRouter | None = None,
    task_type: str = "generate",
) -> MindSpec:
    """读懂这句话，给出一份施工图。模型优先，失败退回基线。

    这条路径不吞异常也不假装成功：模型没读到就退回 ``baseline_spec`` 并把
    ``basis`` 标成 rules，面板据此告诉用户「这一版是按规则拟的」。
    """

    params = dict(params or {})
    text = " ".join(str(topic or "").split())
    intent = pack.intent if pack is not None else None
    brief = parse_brief(f"{text} {params.get('focus') or ''}".strip())
    base = baseline_spec(text, pack, params, brief=brief, intent=intent)
    if not text:
        return base

    draft: SpecDraft | None = None
    try:
        if llm_call is not None:
            raw = await llm_call(
                [
                    {"role": "system", "content": SPEC_SYSTEM_PROMPT},
                    {"role": "user", "content": _json(_payload(text, pack, params, base))},
                ]
            )
            draft = SpecDraft.model_validate_json(_extract_json(raw))
        else:
            draft = await structured_create(
                task_type=task_type,
                messages=[
                    {"role": "system", "content": SPEC_SYSTEM_PROMPT},
                    {"role": "user", "content": _json(_payload(text, pack, params, base))},
                ],
                response_model=SpecDraft,
                mock_factory=lambda: SpecDraft(),
                router=router or ModelRouter(),
            )
    except Exception:  # noqa: BLE001 - 解读失败必须退回基线，不能拦住出方案
        draft = None

    if draft is None or not _informative(draft):
        # 没有可用输出（无 key 时的空草稿、模型交了白卷）一律退回基线：
        # 空草稿过一遍 reconcile 会把 basis 标成 model，那是在骗用户。
        return base
    return reconcile_spec(draft, base, text)


def _informative(draft: SpecDraft) -> bool:
    """模型这一版有没有真的读进东西。全是默认值就当没读。"""

    return bool(draft.branches or draft.organization or draft.interpretation or draft.depth)


def reconcile_spec(draft: SpecDraft, base: MindSpec, topic: str) -> MindSpec:
    """模型给的东西收进合法区间。

    只做「不许越界」与「不许含糊」，不改写模型对内容的理解：
    分支名与组织方式都照模型给的，只有越界（层数太深、分支太多太碎、
    分支名是「其他」这种兜底词）才动手，并且把动手这件事写进 notes。
    """

    notes: list[str] = []
    interpretation = [_plain(line) for line in draft.interpretation if _plain(line)][:4]

    # 分支：去空、去兜底词、去重
    branches: list[BranchSpec] = []
    seen: set[str] = set()
    for row in draft.branches:
        label = _clean_label(row.label)
        if not label or label in _CATCH_ALL or label in seen:
            continue
        seen.add(label)
        branches.append(
            BranchSpec(
                label=label,
                purpose=_plain(row.purpose)[:160],
                must_cover=[_plain(item)[:40] for item in row.must_cover if _plain(item)][:6],
            )
        )
        if len(branches) >= MAX_BRANCHES:
            break
    if len(branches) < MIN_BRANCHES:
        # 模型把树写塌了：退回基线分支，但保留它读出来的层数与组织方式。
        notes.append("模型给的分支太少，分支按规则基线补齐。")
        branches = list(base.branches)

    # 层数：用户明说的数具有否决权，模型只能更细不能更浅
    explicit = depth_from_text(topic)
    requested = draft.depth or base.depth
    if explicit is not None:
        requested = explicit
    elif base.depth_requested and base.depth_requested > requested and base.basis == "rules":
        requested = base.depth_requested
    structure = draft.structure if draft.structure in STRUCTURE_CAPACITY else base.structure
    structure, switched = fit_structure(structure, requested)
    if switched:
        notes.append(f"「{draft.structure}」装不下 {requested} 层，已改用「{structure}」。")
    depth = min(max(requested, MIN_DEPTH), structure_capacity(structure), MAX_DEPTH)

    kind = draft.organization_kind if draft.organization_kind in _ORGANIZATION_KINDS else base.organization_kind
    leaves_min = _clamp(draft.leaves_min, 2, MAX_LEAVES_MIN) or base.leaves_min

    if not interpretation:
        interpretation = list(base.interpretation)
    interpretation.append(f"按 {len(branches)} 支、{depth} 层展开。")
    gap = f"你要 {requested} 层，最多画 {depth} 层，已按 {depth} 层排。"
    if requested > depth:
        notes.append(gap)

    return MindSpec(
        root_topic=usable_root_title(draft.root_topic) or base.root_topic,
        audience=_one_line(draft.audience)[:60] or base.audience,
        organization=_one_line(draft.organization)[:200] or base.organization,
        organization_kind=kind,
        depth=depth,
        depth_requested=requested,
        leaves_min=leaves_min,
        structure=structure,
        branches=branches,
        emphasis=tuple(_one_line(item)[:60] for item in draft.emphasis if _one_line(item))[:8],
        must_answer=tuple(_one_line(item)[:80] for item in draft.must_answer if _one_line(item))[:6],
        interpretation=interpretation[:6],
        basis="model",
        notes=notes,
    )


_ORGANIZATION_KINDS: Final[frozenset[str]] = frozenset({"compare", "taxonomy", "process", "timeline", "concept", "learning", "mixed"})


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


#: 模型会把内部键名当成话说给用户（「落在 depth=4」「在 must_answer 里点名」）。
#: 面板上这几行是给用户看的，键名必须先换成中文，换不掉就整行丢掉——
#: 用户读到「depth=4」只会觉得这功能在自言自语。
_JARGON: Final[dict[str, str]] = {
    "logic-right": "向右分支",
    "mind-both": "四面发散",
    "org-down": "向下分支",
    "fishbone": "鱼骨图",
    "branch_budget": "分支数",
    "organization_kind": "组织方式",
    "must_answer": "必答项",
    "must_cover": "必须覆盖",
    "leaves_min": "每支条数",
    "depth": "层数",
}
_JARGON_KEY: Final = re.compile(r"[a-z_]{3,}")
_DEPTH_ASSIGN: Final = re.compile(r"层数\s*=\s*(\d+)")


def _plain(value: Any) -> str:
    """把一句可能夹着内部键名的话，改成用户读得懂的话。"""

    text = _one_line(value)
    if not text:
        return ""
    for key, label in _JARGON.items():
        text = text.replace(key, label)
    # 「层数=4」读着还是像代码，改成「4 层」。
    text = _DEPTH_ASSIGN.sub(lambda m: f"{m.group(1)} 层", text)
    if _JARGON_KEY.search(text):
        # 还有英文键名没换掉：这行是内部说法，不如不给用户看。
        return ""
    return " ".join(text.split()).strip(" ：:，,；;")


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _extract_json(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    raise ValueError("解读输出中没有 JSON")


# ---------------------------------------------------------------------------
# 方案 → 生成参数
# ---------------------------------------------------------------------------


def spec_of(plan: dict[str, Any] | None) -> MindSpec | None:
    """从方案里取回 spec。坏数据返回 ``None``：老任务不该因为缺字段生成失败。"""

    raw = (plan or {}).get("spec")
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return MindSpec.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None


def generation_params_from_plan(plan: dict[str, Any] | None, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """把确认过的那一版方案翻译成生成参数。

    这是**唯一**一处把「方案」翻成「生成读的键」的地方。以前这一步不存在，
    于是面板能改的四个字段改完没人生效。三条规则，写在这里就是为了以后加字段
    的人知道该往哪加：

    1. 分支 = 方案里的单元，一支就是一个 unit。用户加一支、删一支都算数；
    2. 层数：面板上「呈现方式」那行字里写的数优先（人写的字最算数），
       其次是 ``spec.depth``，最后才是原始入参；
    3. 受众、组织方式、重点、必答项一律取方案上的那一份——
       哪怕用户在面板里手改过而没有同步到 spec。
    """

    out = dict(base or {})
    spec = spec_of(plan)
    units = [unit for unit in ((plan or {}).get("units") or []) if isinstance(unit, dict) and str(unit.get("label") or "").strip()]
    labels = [_clean_label(str(unit.get("label") or "")) for unit in units]
    labels = [label for label in labels if label]

    depth: int | None = None
    if plan:
        # 1) 面板文字里明写的层数（用户刚改的那一行）
        depth = depth_from_text(str(plan.get("presentation") or ""))
        # 2) 结构化字段
        if depth is None and spec is not None:
            depth = spec.depth
    if depth is None:
        depth = _clamp_any(out.get("depth"), MIN_DEPTH, MAX_DEPTH) or DEFAULT_DEPTH
    structure = str((spec.structure if spec else "") or out.get("structure") or DEEPEST_STRUCTURE)
    structure, _ = fit_structure(structure, depth)
    depth = min(depth, structure_capacity(structure), MAX_DEPTH)
    out["depth"] = depth
    out["structure"] = structure

    branches = len(labels) or (len(spec.labels) if spec else 0)
    if branches:
        out["branches"] = min(max(branches, MIN_BRANCHES), MAX_BRANCHES)
    if spec is not None:
        out["leaves_min"] = spec.leaves_min
        out["emphasis"] = list(spec.emphasis)
        out["must_answer"] = list(spec.must_answer)
        out["spec"] = spec.model_dump(mode="json")
    organization = _one_line((plan or {}).get("organization")) or (spec.organization if spec else "")
    if organization:
        out["organization"] = organization
    audience = _one_line((plan or {}).get("audience")) or (spec.audience if spec else "")
    if audience:
        out["audience"] = audience
    goal = _one_line((plan or {}).get("goal"))
    if goal:
        out["goal"] = goal
    out["expected"] = {
        "depth": depth,
        "branches": len(labels),
        "labels": labels,
        "organization": organization,
        "must_answer": list(spec.must_answer) if spec else [],
        "leaves_min": int(spec.leaves_min) if spec else 0,
    }
    return out


def _clamp_any(value: Any, low: int, high: int) -> int | None:
    try:
        return min(max(int(value), low), high)
    except (TypeError, ValueError):
        return None


def measure_tree(root: dict[str, Any] | None) -> dict[str, Any]:
    """数一棵树真实的层数与每支深度。用于核对「方案说几层，画出来几层」。"""

    if not isinstance(root, dict):
        return {"depth": 0, "branch_depth": {}}
    branch_depth: dict[str, int] = {}
    deepest = 0
    for branch in root.get("children") or []:
        if not isinstance(branch, dict):
            continue
        inner = _subtree_depth(branch)
        branch_depth[str(branch.get("topic") or "")] = inner
        deepest = max(deepest, inner)
    return {"depth": 1 + deepest if root else 0, "branch_depth": branch_depth}


def _subtree_depth(node: dict[str, Any]) -> int:
    children = [child for child in (node.get("children") or []) if isinstance(child, dict)]
    if not children:
        return 1
    return 1 + max(_subtree_depth(child) for child in children)


def conformance(content: dict[str, Any] | None, expected: dict[str, Any] | None) -> dict[str, Any]:
    """成品与方案的差距。空 dict 表示完全对得上。

    这个函数的存在是为了**不再沉默**：层数少了、某支没落地，都要能被说出来，
    而不是让用户自己数。
    """

    expected = expected or {}
    root = (content or {}).get("mindmap_data", {}).get("nodeData") if isinstance(content, dict) else None
    if not root:
        return {}
    measured = measure_tree(root)
    want_depth = int(expected.get("depth") or 0)
    want_labels: Sequence[str] = expected.get("labels") or []
    branches = [str(node.get("topic") or "") for node in (root.get("children") or []) if isinstance(node, dict)]
    shallow = sorted(label for label, deep in (measured.get("branch_depth") or {}).items() if want_depth and deep < want_depth - 1)
    result: dict[str, Any] = {
        "depth": measured["depth"],
        "expected_depth": want_depth,
        "missing": [label for label in want_labels if not any(_similar(label, branch) for branch in branches)][:8],
        "shallow": shallow[:8],
    }
    if want_depth and measured["depth"] < want_depth:
        result["note"] = f"方案定 {want_depth} 层，实际画到 {measured['depth']} 层"
    return result


def _similar(left: str, right: str) -> bool:
    a, b = _clean_label(left), _clean_label(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a
