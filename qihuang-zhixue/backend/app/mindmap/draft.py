"""LLM 直出的导图草案 → 可校验导图数据。

两个刻意的设计：

1. **草案不含 id**。id 由后端按层序确定性分配（``root`` / ``b0`` / ``b0-1``），
   因为前端要靠 id 去重、断线重连时后端会把同一批节点重推一遍。让模型自己
   编 id 的话，同一主题两次生成就是两套 id，去重立刻失效。
2. **草案的校验是宽松的，输出必须严**。``MindmapNode`` 对 topic 是
   「超过 30 字就 raise」，一旦抛出去整棵树会退化成模板。所以这里先收进
   120 字上限的 ``DraftNode``，再由 ``normalize_draft`` 逐条截断、去重、
   剪枝，最后才构造 ``MindmapNode``——保证构造阶段不会失败。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .schemas import Evidence, MindElixirData, MindmapMeta, MindmapNode

#: ``MindmapNode.topic`` 的硬上限。这里必须与 schemas.py 保持一致。
MAX_TOPIC = 30
#: ``MindmapNode.children`` 上限是 12，留 2 个余量给后续手动编辑。
MAX_CHILDREN = 10
#: 单张导图节点总数上限。超过这个数量前端生长队列会明显拖长。
MAX_NODES = 120
#: 每个节点最多引 3 条依据——再多也读不过来，还会把 tooltip 撑爆。
MAX_SOURCE_INDEXES = 3

DEFAULT_DEPTH = 3
MIN_DEPTH = 2
#: 五种结构里最深的是「向右分支」（5 层），再深就只剩缩略图了。
MAX_DEPTH = 5
DEFAULT_BRANCHES = 6
MIN_BRANCHES = 3
MAX_BRANCHES = 8

#: 截断时优先在这些标点处断开，避免把「解表药，用于风寒表证」切成半句。
_CUT_CHARS = "，。；、：（）,.;:()!?！？"
_FALLBACK_TOPIC = "未命名主题"
_PUNCT_ONLY = re.compile(r"[\W_]+", re.UNICODE)


class DraftNode(BaseModel):
    """模型输出的一节。刻意 ``extra="ignore"``：模型常顺手塞 style/color。"""

    model_config = ConfigDict(extra="ignore")

    topic: str = Field(default="", max_length=120)
    source_indexes: list[int] = Field(default_factory=list, max_length=8)
    #: 模型自报的依据等级。允许缺省：缺省时按「有没有引用依据」判定。
    evidence: Evidence | None = None
    children: list["DraftNode"] = Field(default_factory=list, max_length=16)


class MindmapDraft(BaseModel):
    """整张导图的草图。根 topic 允许为空，由调用方用题目兜底。"""

    model_config = ConfigDict(extra="ignore")

    root_topic: str = Field(default="", max_length=120)
    branches: list[DraftNode] = Field(default_factory=list, max_length=10)

    @classmethod
    def from_elixir(cls, data: MindElixirData) -> "MindmapDraft":
        """把确定性模板包装成草案，供无 key 时的 mock_factory 使用。"""

        root = data.nodeData
        return cls(root_topic=root.topic, branches=[_draft_of(node) for node in root.children])


class _Budget:
    """整棵树的节点配额。超配直接放弃该节点，而不是抛错。"""

    __slots__ = ("left",)

    def __init__(self, left: int) -> None:
        self.left = left

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def normalize_draft(
    draft: MindmapDraft,
    *,
    topic: str,
    sources: list[dict[str, str]],
    params: dict[str, Any] | None = None,
) -> MindElixirData:
    """把模型草案收敛成一张可落库、可推流、可渲染的导图。"""

    depth_limit = _int_param(params, "depth", DEFAULT_DEPTH, MIN_DEPTH, MAX_DEPTH)
    branch_limit = _int_param(params, "branches", DEFAULT_BRANCHES, MIN_BRANCHES, MAX_BRANCHES)
    source_count = len(sources)

    root_topic = usable_root_title(draft.root_topic) or question_title(topic) or clean_topic(topic) or _FALLBACK_TOPIC
    budget = _Budget(MAX_NODES - 1)

    children: list[MindmapNode] = []
    if depth_limit > 1:
        seen: set[str] = set()
        for index, raw in enumerate(draft.branches):
            if len(children) >= branch_limit:
                break
            child = _build(
                raw,
                node_id=f"b{index}",
                depth=1,
                direction=index % 2,
                depth_limit=depth_limit,
                source_count=source_count,
                budget=budget,
                siblings=seen,
            )
            if child is not None:
                seen.add(child.topic)
                children.append(child)

    return MindElixirData(nodeData=MindmapNode(id="root", topic=root_topic, children=children))


def _build(
    raw: DraftNode,
    *,
    node_id: str,
    depth: int,
    direction: int,
    depth_limit: int,
    source_count: int,
    budget: _Budget,
    siblings: set[str],
) -> MindmapNode | None:
    topic = clean_topic(raw.topic)
    if not topic or topic in siblings:
        return None
    if not budget.take():
        return None

    children: list[MindmapNode] = []
    if depth + 1 < depth_limit:
        seen: set[str] = set()
        for index, child_raw in enumerate(raw.children):
            if len(children) >= MAX_CHILDREN:
                break
            child = _build(
                child_raw,
                node_id=f"{node_id}-{index}",
                depth=depth + 1,
                # 方向沿父级继承：mind-elixir 的左右分栏是整枝决定的，
                # 子节点各自左右会在连线上打成结。
                direction=direction,
                depth_limit=depth_limit,
                source_count=source_count,
                budget=budget,
                siblings=seen,
            )
            if child is not None:
                seen.add(child.topic)
                children.append(child)

    indexes = _source_indexes(raw.source_indexes, source_count)
    return MindmapNode(
        id=node_id,
        topic=topic,
        direction=direction,
        meta=MindmapMeta(source_indexes=indexes, evidence=evidence_of(raw.evidence, indexes)),
        children=children,
    )


def evidence_of(declared: str | None, indexes: list[int]) -> Evidence:
    """依据等级以**引用事实**为准，模型自报只作补充。

    模型很爱把每个节点都标成 textbook——那等于没有分级。真正的判据是
    「这个节点引用到了资料没有」：引用不到就是推断，这一点不由模型说了算。
    反过来，模型说 ``model`` 但确实引了资料时采信模型（它知道自己是推的）。
    """

    if not indexes:
        return "model"
    if declared in ("textbook", "graph"):
        return declared  # type: ignore[return-value]
    return "textbook"


def _source_indexes(values: list[int], source_count: int) -> list[int]:
    """越界索引静默丢弃——模型偶尔会数错依据条数。"""

    if source_count <= 0:
        return []
    kept = {int(value) for value in values if 0 <= int(value) < source_count}
    return sorted(kept)[:MAX_SOURCE_INDEXES]


#: 提问里表示「请你做一件事」的动词。根节点要的是**被问的那个东西**，
#: 不是这件事本身——「做一个智能体的简介的思维导图」当标题，图上等于没标题。
_TITLE_PREFIXES = (
    "请帮我",
    "帮我",
    "麻烦",
    "请",
    "给我",
    "做一个",
    "做个",
    "做一张",
    "画一个",
    "画一张",
    "画个",
    "生成一张",
    "生成一个",
    "生成",
    "来一个",
    "来个",
    "整理一下",
    "梳理一下",
    "总结一下",
    "整理",
    "梳理",
    "总结",
)
#: 交付物名词。去掉之后剩下的才是主题。
_TITLE_SUFFIXES = (
    "的思维导图",
    "思维导图",
    "的脑图",
    "脑图",
    "的导图",
    "导图",
    "的结构图",
    "结构图",
    "的知识图谱",
    "知识图谱",
    "的图谱",
)
#: 整句只有交付物名词时没有主题可提（见 ``question_title``）。
_DELIVERY_NOUNS = frozenset({"思维导图", "脑图", "导图", "结构图", "知识图谱", "图谱", "图"})
#: 口语化的后缀，改成教材里的说法。
_TITLE_REWRITES = (
    ("的详细介绍", "详解"),
    ("详细介绍", "详解"),
    ("的简介", "简介"),
    ("的概述", "概述"),
    ("的总结", "总结"),
    ("的知识点", "知识点"),
)


def question_title(value: str, *, limit: int = 14) -> str:
    """从提问里提炼一个能当根节点的短标题。

    只做删减与替换，不生成新词——「智能体」这种域外概念我们不知道它是什么，
    硬编一个名词只会更像胡说。删到剩下的那个短语，就是用户想画的那个东西。
    """

    text = collapse_topic(value)
    if not text:
        return ""
    # 整句就是交付物名词（用户只说了「思维导图」三个字）时没有主题可提，
    # 剥后缀只会剩下半个词（「思维」），交给调用方兜底更诚实。
    if text.strip("的 ") in _DELIVERY_NOUNS:
        return ""
    # 反复剥前缀：「请帮我做一个…」要剥两层。
    changed = True
    while changed:
        changed = False
        for prefix in _TITLE_PREFIXES:
            if text.startswith(prefix) and len(text) > len(prefix):
                text = text[len(prefix) :].lstrip("：:，, ")
                changed = True
                break
    for suffix in _TITLE_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)].rstrip("的 ")
            break
    for old, new in _TITLE_REWRITES:
        text = text.replace(old, new)
    # 「做一个X的简介」→「X简介」：去掉中间那个「的」更像标题。
    text = text.replace("的简介", "简介").replace("的详解", "详解")
    text = text.strip(" \t-—·•、，,。：:的")
    if len(text) > limit:
        # 长句（「解表药，分四个分支，每个分支至少三条」）取第一段：
        # 第一段是主题，后面都是对图的要求。
        head = re.split(r"[，。；：、,;:]", text)[0].strip()
        if 2 <= len(head) <= limit + 2:
            text = head
        else:
            text = text[:limit]
    return text


def usable_root_title(value: str) -> str:
    """模型给的标题能不能直接用。

    模型有时把提问原样抄回来（「做一个智能体的简介的思维导图」），有时只给一个字。
    这两种都退回本地提炼：根节点是整张图的锚，抄得越长越不像标题。
    """

    text = clean_topic(value)
    if not 2 <= len(text) <= 16:
        return ""
    if any(word in text for word in ("思维导图", "脑图", "导图")):
        return ""
    return text


def collapse_topic(value: str) -> str:
    """只清洗空白与首尾标点，不截断。保存校验要靠它判断「是否超长」。"""

    text = str(value or "").replace("\u3000", " ").replace("\n", " ")
    text = " ".join(text.split())
    text = text.strip(" \t-—·•、，,。.")
    if not text:
        return ""
    # 纯标点节点在导图里就是一个空框，没有信息量。
    if not _PUNCT_ONLY.sub("", text):
        return ""
    return text


def clean_topic(value: str) -> str:
    text = collapse_topic(value)
    if not text:
        return ""
    if len(text) > MAX_TOPIC:
        text = _truncate(text)
    return text


def _truncate(text: str) -> str:
    window = text[:MAX_TOPIC]
    cut = max((window.rfind(char) for char in _CUT_CHARS), default=-1)
    # 只有标点足够靠后时才用它切，否则「麻黄」这种短词会被切没。
    candidate = window[:cut] if cut >= 6 else window
    candidate = candidate.strip(" \t-—·•、，,。.")
    return candidate[:MAX_TOPIC] or text[:MAX_TOPIC]


def _int_param(params: dict[str, Any] | None, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int((params or {}).get(key))
    except (TypeError, ValueError):
        return default
    return min(max(value, low), high)


def _draft_of(node: MindmapNode) -> DraftNode:
    return DraftNode(
        topic=node.topic,
        source_indexes=list(node.meta.source_indexes),
        evidence=node.meta.evidence,
        children=[_draft_of(child) for child in node.children],
    )


def draft_of_tree(root: MindmapNode) -> MindmapDraft:
    """把一棵已校验的树重新包装成草案。

    用途是「先确定性谋篇、再让模型敷演」：模型拿到的是**已有骨架的草案**，
    它只需要在骨架上补叶子，而不是从零开始编一级分支。
    """

    return MindmapDraft(
        root_topic=root.topic,
        branches=[_draft_of(child) for child in root.children],
    )
