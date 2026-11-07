"""8 类问法路由：把一句人话映射成「该查什么 + 该长成什么形状」。

这个模块存在的唯一理由是修掉一个具体的病：原来的检索只认单味药，
「解表药」「桂枝汤」「十八反」全部退化成「知识要点 → 待补充资料」。
问题不在模型，在于没人告诉它这是一道**分类题 / 关系题 / 反查题**。

所以这里只做两件事，都不碰模型：

1. **判定问法**（``kind``）——决定用哪套素材来源与哪一种结构原型；
2. **锚定实体**（``anchors``）——从问句里认出药/类/方/证/功效/性味归经。

判定是**确定性规则**，不是模型分类。理由：这一步一旦不稳，下游的素材、
结构、prompt 全部跟着漂；而问法本身在中文里是有稳定句式的
（「…的区别」「哪些药能…」「入…经的…药」「十八反」）。规则跑得住，
也方便写测试断言——「为什么这条被当成对比题」永远有答案。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Final, Literal

from app.kb.ingest import load_formulas, load_herbs, load_syndromes
from app.kb.models import Herb

IntentKind = Literal[
    "monograph",  # 单味药
    "taxonomy",  # 分类章节
    "comparison",  # 对比鉴别
    "reverse",  # 功效反查
    "filter",  # 性味归经筛选
    "relation",  # 关系禁忌
    "syndrome",  # 证治对应
    "exam",  # 应试专题
    "concept",  # 域外兜底
]

#: 问法 → 结构原型。原型决定 prompt 的槽位清单与确定性兜底的骨架。
ARCHETYPE: Final[dict[str, str]] = {
    "monograph": "monograph",
    "taxonomy": "taxonomy",
    "comparison": "comparison",
    "reverse": "reverse",
    "filter": "filter",
    "relation": "relation",
    "syndrome": "syndrome",
    "exam": "exam",
    "concept": "concept",
}

#: 问法 → 首选结构（前端四种之一）。只是建议，用户随时可换。
STRUCTURE_HINT: Final[dict[str, str]] = {
    "monograph": "logic-right",
    "taxonomy": "org-down",
    "comparison": "logic-right",
    "reverse": "org-down",
    "filter": "org-down",
    "relation": "logic-right",
    "syndrome": "fishbone",
    "exam": "logic-right",
    "concept": "logic-right",
}

#: 中文问法里能稳定识别的信号词。命中的词会被记进 ``signals``，便于解释与测试。
_PATTERNS: Final[dict[str, tuple[str, ...]]] = {
    "comparison": ("区别", "鉴别", "异同", "对比", "比较", "有何不同", "差异", "异同点", "vs", "VS", "VS.", "怎么分"),
    "relation": ("十八反", "十九畏", "相须", "相使", "相恶", "相畏", "相杀", "配伍", "同用", "合用", "配什么", "配用", "反药", "禁忌"),
    "exam": ("易混", "易错", "考点", "常考", "高频", "真题", "辨析", "应试", "怎么考"),
    "reverse": ("哪些药", "哪些中药", "什么药", "有何药", "用什么药", "有哪些药", "的药有", "能治", "可治"),
    "filter": ("入", "归经", "性温", "性寒", "性平", "性凉", "性热", "味甘", "味辛", "味苦", "味酸", "味咸", "温性", "寒性", "平性"),
    "taxonomy": ("分类", "包括", "有哪些", "组成", "目录", "章节", "概览", "总结"),
}

#: 域外兜底的兜底：这些词基本不会是中药学教材内的题目。
_DEGRADED_HINTS: Final[tuple[str, ...]] = (
    "十二经脉",
    "经络循行",
    "气血津液",
    "藏象",
    "六淫",
    "卫气营血",
    "三焦辨证",
)

_NATURE_WORDS: Final[tuple[tuple[str, str], ...]] = (
    ("大寒", "大寒"),
    ("微寒", "微寒"),
    ("寒", "寒"),
    ("凉", "凉"),
    ("微温", "微温"),
    ("温", "温"),
    ("热", "热"),
    ("平", "平"),
)
_FLAVOR_WORDS: Final[tuple[str, ...]] = ("辛", "甘", "苦", "酸", "咸", "淡", "涩")


@dataclass(frozen=True)
class Anchors:
    """问句里认出来的实体。全是**正名**，不是问句原文。"""

    herbs: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    subcategories: tuple[str, ...] = ()
    syndromes: tuple[str, ...] = ()
    formulas: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    meridians: tuple[str, ...] = ()
    natures: tuple[str, ...] = ()
    flavors: tuple[str, ...] = ()
    relations: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not any(
            (
                self.herbs,
                self.categories,
                self.subcategories,
                self.syndromes,
                self.formulas,
                self.effects,
                self.meridians,
                self.natures,
                self.flavors,
                self.relations,
            )
        )


@dataclass(frozen=True)
class MapIntent:
    """一次导图请求的「题意」。"""

    topic: str
    kind: IntentKind
    archetype: str
    structure_hint: str
    anchors: Anchors = field(default_factory=Anchors)
    signals: tuple[str, ...] = ()
    #: True = 语料外，整棵树只能标 model。前端据此在对话里明说。
    degraded: bool = False

    @property
    def label(self) -> str:
        return _KIND_LABEL[self.kind]

    def describe(self) -> str:
        """给对话栏用的一句话。不解释算法，只说结论。"""

        if self.degraded:
            return "该主题不在中药学教材语料内，以下内容由模型整理，建议对照教材核对"
        return f"按「{self.label}」组织"


_KIND_LABEL: Final[dict[str, str]] = {
    "monograph": "单味药",
    "taxonomy": "分类章节",
    "comparison": "对比鉴别",
    "reverse": "功效反查",
    "filter": "性味归经筛选",
    "relation": "关系禁忌",
    "syndrome": "证治对应",
    "exam": "应试专题",
    "concept": "概念梳理",
}


# ---------------------------------------------------------------------------
# 知识库索引（进程内缓存）
#
# 索引只读、且来源是 16 个 JSON 文件，重复加载一次要几十毫秒；
# 分类/锚定每条请求都会走一遍，所以必须缓存。知识库变更后由
# ``reset_intent_index()`` 清掉（与 resource_context.reset_herb_index 同步）。
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _herb_pairs() -> tuple[tuple[str, str], ...]:
    """(别名, 正名) 列表，按别名长度降序。

    长名优先是必须的：「炙甘草」不能被当成「甘草」，「川贝母」不能被「贝母」
    抢先匹配。按长度降序 + 逐条子串判断，等价于最长匹配。
    """

    pairs: dict[str, str] = {}
    for herb in load_herbs():
        pairs.setdefault(herb.name, herb.name)
        for alias in herb.aliases:
            if alias and alias not in pairs:
                pairs[alias] = herb.name
    return tuple(sorted(pairs.items(), key=lambda item: (-len(item[0]), item[0])))


@lru_cache(maxsize=1)
def _category_names() -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for herb in load_herbs():
        seen.setdefault(str(herb.category), None)
        if herb.subcategory:
            seen.setdefault(herb.subcategory, None)
    return tuple(sorted(seen, key=len, reverse=True))


@lru_cache(maxsize=1)
def _effect_terms() -> tuple[str, ...]:
    """全部功效词（去重），按长度降序。用于「功效反查」的锚定。"""

    terms: dict[str, None] = {}
    for herb in load_herbs():
        for effect in herb.effects:
            if effect:
                terms.setdefault(effect, None)
    return tuple(sorted(terms, key=lambda item: (-len(item), item)))


@lru_cache(maxsize=1)
def _meridian_names() -> tuple[str, ...]:
    names: dict[str, None] = {}
    for herb in load_herbs():
        for meridian in herb.meridians:
            names.setdefault(meridian, None)
    return tuple(sorted(names, key=len, reverse=True))


@lru_cache(maxsize=1)
def _syndrome_names() -> tuple[str, ...]:
    return tuple(sorted((name for name in load_syndromes() if name), key=len, reverse=True))


@lru_cache(maxsize=1)
def _formula_names() -> tuple[str, ...]:
    names: dict[str, None] = {}
    for key, item in load_formulas().items():
        names.setdefault(str(item.get("name") or key), None)
    return tuple(sorted(names, key=len, reverse=True))


def reset_intent_index() -> None:
    for func in (_herb_pairs, _category_names, _effect_terms, _meridian_names, _syndrome_names, _formula_names):
        func.cache_clear()


# ---------------------------------------------------------------------------
# 锚定
# ---------------------------------------------------------------------------


def _find_all(text: str, names: tuple[str, ...], limit: int = 6) -> tuple[str, ...]:
    """在文本里找出出现的名字，长名优先、不重叠。

    先用长名占位，避免「甘草」把「炙甘草」的一半吃掉。
    """

    taken = [False] * len(text)
    found: list[str] = []
    for name in names:
        if len(found) >= limit or not name:
            continue
        start = 0
        while True:
            index = text.find(name, start)
            if index < 0:
                break
            end = index + len(name)
            if not any(taken[index:end]):
                for pos in range(index, end):
                    taken[pos] = True
                found.append(name)
                break
            start = index + 1
    return tuple(found)


def _herbs_in(text: str) -> tuple[str, ...]:
    """药名与别名统一换成正名。

    别名表里既有正名也有别名（``_herb_pairs`` 的 key），所以一趟匹配就够；
    以前分两趟写，结果「麻黄与炙甘草」只认出了麻黄——正名一命中就早退了。
    """

    alias_map = dict(_herb_pairs())
    names = _find_all(text, tuple(alias_map), limit=8)
    return tuple(dict.fromkeys(alias_map[name] for name in names if name in alias_map))


def _effects_in(text: str) -> tuple[str, ...]:
    """功效词锚定。

    两段式：先找知识库里**原样存在**的功效词（长词优先），
    找不到再退回 2~4 字滑窗——「安神」「化痰」这类单看不是完整功效，
    但确实是学生提问时会用的词。
    """

    exact = _find_all(text, _effect_terms(), limit=6)
    if exact:
        return exact
    candidates: list[str] = []
    for size in (4, 3, 2):
        for index in range(0, max(len(text) - size + 1, 0)):
            token = text[index : index + size]
            if all("\u4e00" <= char <= "\u9fff" for char in token):
                candidates.append(token)
    hits = [token for token in candidates if any(token in effect for effect in _effect_terms())]
    return tuple(dict.fromkeys(hits))[:3]


def _meridians_in(text: str) -> tuple[str, ...]:
    if "经" not in text and "归" not in text:
        return ()
    names = [name for name in _meridian_names() if name in text]
    return tuple(dict.fromkeys(names))


def _natures_in(text: str) -> tuple[str, ...]:
    """必须带「性」字才算性味。

    只看单个字会误判：「发散风寒药」里的「寒」说的是病因，不是药性。
    写成「性寒」「寒性」就没有歧义——学生确实是这么问的。
    """

    found: list[str] = []
    for word, label in _NATURE_WORDS:
        if f"性{word}" in text or f"{word}性" in text:
            found.append(label)
    return tuple(dict.fromkeys(found))


def _flavors_in(text: str) -> tuple[str, ...]:
    if "味" not in text and not any(f"性{flavor}" in text for flavor in _FLAVOR_WORDS):
        return ()
    return tuple(flavor for flavor in _FLAVOR_WORDS if f"{flavor}味" in text or f"味{flavor}" in text)


def anchor(text: str) -> Anchors:
    """从问句里抽取实体。纯字符串匹配，不调模型、不查向量库。"""

    herbs = _herbs_in(text)
    categories = _find_all(text, _category_names(), limit=3)
    syndromes = _find_all(text, _syndrome_names(), limit=2)
    formulas = _find_all(text, _formula_names(), limit=2)
    relations = tuple(word for word in _PATTERNS["relation"] if word in text and word in ("十八反", "十九畏", "相须", "相使", "相恶", "相畏", "相杀"))
    return Anchors(
        herbs=herbs,
        categories=categories,
        syndromes=syndromes,
        formulas=formulas,
        effects=_effects_in(text),
        meridians=_meridians_in(text),
        natures=_natures_in(text),
        flavors=_flavors_in(text),
        relations=relations,
    )


def _looks_like_reverse(text: str) -> bool:
    return any(word in text for word in _PATTERNS["reverse"])


def _signals(text: str, keys: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(word for word in keys if word in text))


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------


def classify(topic: str) -> MapIntent:
    """判定问法。顺序即优先级，理由写在每条注释里。"""

    text = " ".join(str(topic or "").split())
    if not text:
        return MapIntent(topic="", kind="concept", archetype="concept", structure_hint="logic-right", degraded=True)

    anchors = anchor(text)
    found: dict[str, tuple[str, ...]] = {}

    # 1) 对比：有「区别/鉴别」这类词就算，哪怕只点了一味药
    #    （另一味从同类鉴别边里找，比让模型硬编一个强）。
    found["comparison"] = _signals(text, _PATTERNS["comparison"])
    if found["comparison"]:
        return _make(text, "comparison", anchors, found)

    # 2) 关系禁忌：十八反/十九畏/相须…是**确定的关系类型**，命中就是关系题，
    #    不该落到单味药——「十八反有哪些」的答案是 68 条边，不是甘草的性味。
    relation_signal = _signals(text, ("十八反", "十九畏", "相须", "相使", "相恶", "相畏", "相杀", "反药"))
    if relation_signal:
        return _make(text, "relation", anchors, {**found, "relation": relation_signal})
    # 「桂枝配白芍」：点了两味药又说「配」，问的是关系而不是各自性味。
    # 关系类型要真的写进 anchors，不能只写在 signals 里——素材包是按
    # anchors.relations 决定去查哪种边的，只写 signals 它会默认去查十八反。
    if "配" in text and len(anchors.herbs) >= 2:
        anchors = replace(anchors, relations=("配伍",))
        return _make(text, "relation", anchors, {**found, "relation": ("配伍",)})

    # 3) 证治：问句里出现标准证名，就按证组织（药 + 方 + 症）。
    #    必须排在「哪些药」之前：「风寒表证用什么药」的骨架是证，不是功效反查。
    if anchors.syndromes:
        return _make(text, "syndrome", anchors, found)

    # 4) 应试专题：易混/考点类，先于反查——都是「列一批药」，但组织方式不同。
    exam_signal = _signals(text, _PATTERNS["exam"])
    if exam_signal:
        return _make(text, "exam", anchors, {**found, "exam": exam_signal})

    # 5) 功效反查：「哪些药能安神」。要求真的锚到了功效词，否则是句客套话。
    if _looks_like_reverse(text) and anchors.effects:
        return _make(text, "reverse", anchors, {**found, "reverse": _signals(text, _PATTERNS["reverse"])})

    # 6) 性味归经筛选：「入肺经的温性药」。至少锚到归经/性/味之一才算。
    if (anchors.meridians or anchors.natures or anchors.flavors) and ("药" in text or "中药" in text):
        return _make(text, "filter", anchors, {**found, "filter": ()})

    # 7) 分类章节：命中真实的知识树节点名（22 域 / 48 亚类），或明确说「分类/包括」。
    taxonomy_signal = _signals(text, _PATTERNS["taxonomy"])
    if anchors.categories or taxonomy_signal:
        return _make(text, "taxonomy", anchors, {**found, "taxonomy": taxonomy_signal})

    # 8) 单味药：只点了药名，没有别的意图。
    if anchors.herbs:
        return _make(text, "monograph", anchors, found)

    # 9) 域外兜底：诚实降级，不伪装成教材。
    return MapIntent(
        topic=text,
        kind="concept",
        archetype="concept",
        structure_hint="logic-right",
        anchors=anchors,
        signals=_signals(text, _DEGRADED_HINTS),
        degraded=True,
    )


def _make(text: str, kind: IntentKind, anchors: Anchors, found: dict[str, tuple[str, ...]]) -> MapIntent:
    signals = tuple(word for words in found.values() for word in words)
    return MapIntent(
        topic=text,
        kind=kind,
        archetype=ARCHETYPE[kind],
        structure_hint=STRUCTURE_HINT[kind],
        anchors=anchors,
        signals=signals,
        degraded=False,
    )


def herb_objects(names: tuple[str, ...]) -> list[Herb]:
    """按正名取药材条目（保持传入顺序）。"""

    index = {herb.name: herb for herb in load_herbs()}
    return [index[name] for name in names if name in index]


def compare_pair(intent: MapIntent) -> tuple[str, str] | None:
    """对比题的两味药。不足两味时用同类鉴别边补一个最常被放在一起比的。"""

    herbs = list(intent.anchors.herbs)
    if len(herbs) >= 2:
        return herbs[0], herbs[1]
    if not herbs:
        return None
    from app.kg.traverse import neighbors  # 局部 import：避免与 kg 包形成循环

    try:
        subgraph = neighbors(_herb_entity_id(herbs[0]), hops=1, limit=60)
    except Exception:  # noqa: BLE001 - 图未建好时退回单药
        return None
    for edge in subgraph.edges:
        if edge.relation_type not in ("同类鉴别", "相须", "相使", "配伍"):
            continue
        other = edge.target_id if edge.source_id == _herb_entity_id(herbs[0]) else edge.source_id
        name = _display(other)
        if name and name != herbs[0]:
            return herbs[0], name
    return None


def _herb_entity_id(name: str) -> str:
    from app.kg.entities import resolve

    entity = resolve(name)
    return entity.id if entity is not None else name


def _display(entity_id: str) -> str:
    from app.kg.entities import display_name

    try:
        return display_name(entity_id)
    except Exception:  # noqa: BLE001
        return ""

