"""学科层：知识树的第三级容器，但**刻意不进库**。

90 个 domain 其实就是六门课的章（中药学 `d*`、方剂学 `f*`、中医基础理论
`b*`、中医诊断学 `z*`、针灸学 `a*`、中医内科学 `n*`），可 `knowledge_node`
只有 domain / subtype / point 三级，**没有任何字段记着"d01 属于中药学"**。
这个缺口在真实使用中直接打穿了规划逻辑，两处都是用户先发现的：

1. **顺序是交织的**。域节点的 `sort_hint` 是**学科内**章号（d01 和 f01 都是 1），
   而 90 个域之间一条先修边都没有，`topo_sort` 就把它们全放进同一层，
   再按 `(sort_hint, id)` 排 → 六门课的第 1 章连在一起、第 2 章连在一起。
   用户看到的是"学完解表药接着学方剂起源"。这不是设计，是排序键的副产物。
2. **"我已经掌握中药学"这句话进不了模型**。模型拿到的是 90 个平铺的章名，
   既不知道哪几章合起来叫中药学，也没有学科级的开关可拨；它只能一章一章地
   猜，而 `min_include` 又要求保住 60% 的章，于是中药学原样留着。

为什么不改库：加一层真实节点要动 `knowledge_node.level` 的 CHECK 约束、全部
954 个点的 `parent_id`、`map_section` 的章号，以及所有按 parent 递归的查询。
而学科是**可以从 id 前缀无损推导**的，且这份推导本身就是教材事实（`d` 就是
中药学）。所以放在这里当纯函数：可读、可测、可审计，换教材只改一张表。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Subject:
    """一门课。``prefix`` 用于从 domain id 推导归属；``aliases`` 用于听懂用户原话。"""

    id: str
    title: str
    prefix: str
    aliases: tuple[str, ...] = ()
    #: 教材里的常见简称。给"已掌握 X"的证据判定用——用户不会写全称。
    short: tuple[str, ...] = ()


SUBJECTS: tuple[Subject, ...] = (
    Subject("basic", "中医基础理论", "b", short=("中基",),
            aliases=("中医基础理论", "中基", "中医基础")),
    Subject("diagnosis", "中医诊断学", "z", short=("中诊",),
            aliases=("中医诊断学", "中诊", "中医诊断")),
    Subject("herb", "中药学", "d", short=("中药",),
            aliases=("中药学", "中药")),
    Subject("formula", "方剂学", "f", short=("方剂",),
            aliases=("方剂学", "方剂")),
    Subject("acup", "针灸学", "a", short=("针灸",),
            aliases=("针灸学", "针灸", "经络腧穴学")),
    Subject("internal", "中医内科学", "n", short=("内科",),
            aliases=("中医内科学", "内科", "中医内科")),
)

BY_ID: dict[str, Subject] = {subject.id: subject for subject in SUBJECTS}
BY_PREFIX: dict[str, Subject] = {subject.prefix: subject for subject in SUBJECTS}

#: 学科级先修顺序（教材与执业大纲的通行编法）。
#:
#: 图里**没有**跨学科的先修边——2174 条边全是域内层级边与"按教材编排顺序"。
#: 也就是说"方剂学要在中药学之后"这件事从来没被写下来过，全靠这条常量表达。
#: 它只决定**学科的先后**，学科内部仍由各自章节的 `sort_hint` 与边决定。
#:
#: 顺序理由：中基给概念（阴阳/藏象/经络），中诊接着教怎么看出证，中药是药性，
#: 方剂是药的组合（必须先有单味药），针灸另成一路但仍以经络腧穴为本，
#: 内科是前面五门的汇合。把内科放最后不是因为它最难，是因为它要调用前五门。
CANONICAL_ORDER: tuple[str, ...] = (
    "basic",
    "diagnosis",
    "herb",
    "formula",
    "acup",
    "internal",
)

#: 中药学总论在库里是独立的域 `general`，id 没有前缀，但它属于中药学
#: （各论的药性分类就是总论的展开）。特例写在这里，不要散在调用方。
_SPECIAL: dict[str, str] = {"general": "herb"}


def subject_of(domain_id: str) -> str:
    """域 → 学科 id。推不出来返回空串（自定义节点、测试图里的别的命名）。

    前缀表比字符串匹配短，也不会把 `d01` 和 `f01` 弄混；`general` 是唯一特例。
    """

    if not domain_id:
        return ""
    special = _SPECIAL.get(domain_id)
    if special:
        return special
    subject = BY_PREFIX.get(domain_id[0])
    return subject.id if subject else ""


def title_of(subject_id: str) -> str:
    subject = BY_ID.get(subject_id)
    return subject.title if subject else "其他"


def aliases_of(subject_id: str) -> tuple[str, ...]:
    subject = BY_ID.get(subject_id)
    if subject is None:
        return ()
    return tuple(dict.fromkeys((subject.title, *subject.aliases, *subject.short)))


def mentions(text: str, subject_id: str) -> bool:
    """用户这句话里有没有点名这门课。**只做确定性匹配，不做语义理解。**

    语义归模型；这里只回答一个事实问题——"他到底说没说过这门课"，
    用来给"已掌握某科"的声明提供可审计的证据。没说过也不算错，
    只是这条声明要另找证据（掌握度 / 已有路线进度）。
    """

    haystack = " ".join(str(text or "").split())
    if not haystack:
        return False
    return any(alias and alias in haystack for alias in aliases_of(subject_id))


def order_key(subject_id: str) -> int:
    """学科在标准顺序里的位次；不认识的排最后，不参与学科级取舍。"""

    try:
        return CANONICAL_ORDER.index(subject_id)
    except ValueError:
        return len(CANONICAL_ORDER)


def group_domains(domain_ids: list[str]) -> dict[str, list[str]]:
    """按学科分组，保持传入顺序（传入方一般已排好拓扑序）。"""

    grouped: dict[str, list[str]] = {}
    for domain_id in domain_ids:
        grouped.setdefault(subject_of(domain_id), []).append(domain_id)
    return grouped


__all__ = [
    "BY_ID",
    "BY_PREFIX",
    "CANONICAL_ORDER",
    "SUBJECTS",
    "Subject",
    "aliases_of",
    "group_domains",
    "mentions",
    "order_key",
    "subject_of",
    "title_of",
]
