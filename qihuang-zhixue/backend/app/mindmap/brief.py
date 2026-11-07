"""从一句人话里读出「这次到底要什么样的图」。

对话式生成的问题是：用户的要求全写在句子里，而接口只认 depth/branches/style
三个数字。「思维导图必须包含四层」「每个分支至少三条」「要记忆口诀」「面向考研」
这些话以前一律被丢掉——于是图永远长成默认的三层六支，看起来像没听懂。

这里把这些要求抽成 ``GenerationBrief``（生成要点），三处消费：

1. **入参**：层数/分支数/取向直接覆盖接口默认值（用户当场说的话优先）；
2. **提示词**：变成「本次额外要求」硬约束，排在通用规则前面；
3. **兜底**：``include``/``exclude`` 用来校验与修剪确定性骨架的槽位。

判定仍然是确定性的正则，不调模型：这一步一旦不稳，整张图会跟着漂，
而且「为什么这次是四层」必须能给出可复述的理由。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

_CN_NUM: Final[dict[str, int]] = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

#: 「四层」「4 层」「分四层」。六~九层也读，读出来再钳到 5——
#: 读不到就等于没听见，用户会以为图自己做错了。
_DEPTH_RE = re.compile(r"([一二两三四五六七八九1-9])\s*层")
#: 「分四类」「拆成五个分支」「三个部分」。必须带「分支/部分/板块」，否则会把层数当分支数。
_BRANCH_RE = re.compile(r"([三四五六七八3-8])\s*(?:个)?\s*(?:大)?(?:分支|板块|部分)")
#: 「每个分支三条」「每支 2~4 条」。
_LEAF_RE = re.compile(r"每(?:个)?(?:分支|板块|部分|支)[^0-9一二三四五六七八九]{0,6}([2-9一二三四五六七八九])\s*(?:条|个|项)")
#: 「至少三条」「不少于 4 条」。
_LEAF_MIN_RE = re.compile(r"(?:至少|不少于|最少)\s*([2-9一二三四五六七八九])\s*(?:条|个|项)")

#: 要求里出现这些词，就在图上多开（或强化）一条对应的轴。
_INCLUDE_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "mnemonic": ("口诀", "助记", "记忆法", "速记", "歌诀", "背诵"),
    "dosage": ("剂量", "用量", "克数", "几克"),
    "formula": ("方剂", "方子", "代表方", "汤头", "经方"),
    "caution": ("禁忌", "慎用", "十八反", "十九畏", "毒性", "不良反应"),
    "differentiation": ("鉴别", "区别", "异同", "易混", "辨析"),
    "relation": ("配伍", "相须", "相使", "关系", "药对"),
    "case": ("医案", "案例", "病案", "实例"),
    "source": ("出处", "原文", "古籍", "本经", "本草经"),
    "confusion": ("易错", "陷阱", "易错点", "常错"),
    "pathogenesis": ("病机", "机理", "原理"),
    "symptom": ("症状", "主症", "临床表现", "证候"),
    "modern": ("现代研究", "药理", "成分", "实验"),
}

#: 中文标签，写进提示词与对话栏。顺序即展示顺序。
_INCLUDE_LABEL: Final[dict[str, str]] = {
    "mnemonic": "记忆口诀",
    "dosage": "用量",
    "formula": "代表方",
    "caution": "禁忌",
    "differentiation": "鉴别要点",
    "relation": "配伍关系",
    "case": "医案",
    "source": "古籍出处",
    "confusion": "易错点",
    "pathogenesis": "病机",
    "symptom": "主症",
    "modern": "现代研究",
}

_STYLE_TERMS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("exam", ("考点", "考试", "考研", "执医", "应试", "刷题", "怎么考", "重点")),
    ("memory", ("记忆", "背诵", "口诀", "速记", "助记")),
    ("clinic", ("临床", "开方", "用药思路", "辨证思路", "看病")),
    ("intro", ("入门", "零基础", "科普", "通俗", "大白话", "外行")),
    ("compare", ("对比", "比较", "区别", "异同")),
)

_STRUCTURE_TERMS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("fishbone", ("鱼骨", "归因", "因果")),
    ("org-down", ("向下", "树状", "层级", "组织结构", "自上而下")),
    ("mind-both", ("发散", "四周", "环绕", "四面八方")),
    ("logic-right", ("向右", "大纲", "列表式")),
)

#: 取向 → 接口的三种风格。接口只有 exam/understand/compare，其余靠 brief 文字传达。
_STYLE_TO_PARAM: Final[dict[str, str]] = {
    "exam": "exam",
    "memory": "exam",
    "compare": "compare",
    "clinic": "understand",
    "intro": "understand",
}

_STYLE_LABEL: Final[dict[str, str]] = {
    "exam": "应试向",
    "memory": "记忆向",
    "clinic": "临床向",
    "intro": "入门向",
    "compare": "对比向",
}

_STRUCTURE_LABEL: Final[dict[str, str]] = {
    "fishbone": "鱼骨图",
    "org-down": "向下分支",
    "mind-both": "四面发散",
    "logic-right": "向右分支",
}

#: 「再深一层」「详细一点」——没给数，但要得更深。这类词里的「一层」不是层数，
#: 必须先抠掉再跑数字正则，否则「再深一层」会被读成「要 2 层」（正好反了）。
_DEEPER_TERMS: Final[tuple[str, ...]] = ("再深一层", "再深一点", "深一点", "深一些", "更细", "详细一点", "细一点", "展开一点")

_EXCLUDE_RE = re.compile(r"(?:不要|不需要|去掉|别写|不含|不用写|省略)\s*([\u4e00-\u9fff]{2,4})")


def _num(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _CN_NUM.get(token)


@dataclass(frozen=True)
class GenerationBrief:
    """一次生成里「用户额外说的话」。全部可缺省。"""

    depth: int | None = None
    branches: int | None = None
    leaves_min: int | None = None
    style: str | None = None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    structure: str | None = None
    #: 原始句子，写进提示词让模型自己也能读到语气（「必须」「一定」）。
    raw: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def empty(self) -> bool:
        return not any((self.depth, self.branches, self.leaves_min, self.style, self.include, self.exclude, self.structure))

    def labels(self) -> list[str]:
        return [_INCLUDE_LABEL[item] for item in self.include if item in _INCLUDE_LABEL]

    def to_params(self) -> dict[str, object]:
        """覆盖接口入参的那部分。``None`` 表示「用户没说，别动默认值」。"""

        params: dict[str, object] = {}
        if self.depth is not None:
            params["depth"] = self.depth
        if self.branches is not None:
            params["branches"] = self.branches
        if self.style is not None:
            params["style"] = _STYLE_TO_PARAM[self.style]
        if self.structure is not None:
            params["structure"] = self.structure
        return params

    def describe(self) -> str:
        """给对话栏的一句人话，最多四条，长了没人读。"""

        parts: list[str] = []
        if self.depth:
            parts.append(f"{self.depth} 层")
        if self.branches:
            parts.append(f"{self.branches} 个分支")
        if self.leaves_min:
            parts.append(f"每支至少 {self.leaves_min} 条")
        if self.style:
            parts.append(_STYLE_LABEL[self.style])
        labels = self.labels()
        if labels:
            parts.append("含" + "/".join(labels[:4]))
        if self.exclude:
            parts.append("不含" + "/".join(_INCLUDE_LABEL[item] for item in self.exclude[:3]))
        if self.structure:
            parts.append(_STRUCTURE_LABEL[self.structure])
        return " · ".join(parts[:4])

    def prompt_lines(self) -> list[str]:
        """提示词里的「本次额外要求」。逐条编号，便于模型对齐。"""

        lines: list[str] = []
        if self.depth:
            lines.append(f"层数必须是 {self.depth} 层（根算第一层）。")
        if self.branches:
            lines.append(f"一级分支最多 {self.branches} 个。")
        if self.leaves_min:
            lines.append(f"每个一级分支下至少 {self.leaves_min} 个叶子；给不出就宁可合并，不要重复凑数。")
        if self.style == "exam":
            lines.append("写法偏应试：先给考点与易错点，再补解释；能用「A↔B」的地方就用。")
        elif self.style == "memory":
            lines.append("写法偏记忆：条目要短、要能背诵，必要时给口诀式短句。")
        elif self.style == "clinic":
            lines.append("写法偏临床：落到「什么证、什么症、配什么、注意什么」。")
        elif self.style == "intro":
            lines.append("写法偏入门：用常见词，先讲是什么再讲细节，不要堆术语。")
        elif self.style == "compare":
            lines.append("写法偏对比：同一维度下并列双方，不要一边有一边没有。")
        if self.include:
            lines.append("必须覆盖这些内容：" + "、".join(self.labels()) + "。缺哪条就补哪条。")
        if self.exclude:
            lines.append("不要出现这些内容：" + "、".join(_INCLUDE_LABEL[item] for item in self.exclude) + "。")
        return lines


def parse_brief(text: str) -> GenerationBrief:
    """解析一句话里的人为要求。不猜、不补：读不出就是空。"""

    raw = " ".join(str(text or "").split())
    if not raw:
        return GenerationBrief()

    # 数字类正则跑在「抠掉模糊说法」的副本上：『再深一层』里的「一层」是语气，不是层数。
    scan = raw
    wants_deeper = False
    for term in _DEEPER_TERMS:
        if term in scan:
            wants_deeper = True
            scan = scan.replace(term, " ")

    depth = None
    match = _DEPTH_RE.search(scan)
    if match:
        depth = _num(match.group(1))
    if depth is None and wants_deeper:
        # 没说具体几层，只说要更深：给个比默认多一层的 4 层。
        depth = 4

    branches = None
    match = _BRANCH_RE.search(raw)
    if match:
        branches = _num(match.group(1))

    leaves_min = None
    match = _LEAF_RE.search(raw) or _LEAF_MIN_RE.search(raw)
    if match:
        leaves_min = _num(match.group(1))

    style = None
    # 短的先判：「对比」比「区别」更能决定组织方式，而 exam 优先级最高。
    for name, words in _STYLE_TERMS:
        if any(word in raw for word in words):
            style = name
            break

    structure = None
    for name, words in _STRUCTURE_TERMS:
        if any(word in raw for word in words):
            structure = name
            break

    include: list[str] = []
    for name, words in _INCLUDE_TERMS.items():
        if any(word in raw for word in words):
            include.append(name)

    exclude: list[str] = []
    for token in _EXCLUDE_RE.findall(raw):
        for name, words in _INCLUDE_TERMS.items():
            if any(word in token for word in words) and name not in exclude:
                exclude.append(name)

    # 同一件事既说「要」又说「不要」时，以「不要」为准（后半句通常是否定）。
    include = [item for item in include if item not in exclude]

    return GenerationBrief(
        depth=_clamp(depth, 2, 5),
        branches=_clamp(branches, 3, 8),
        leaves_min=_clamp(leaves_min, 2, 8),
        style=style,
        include=tuple(include),
        exclude=tuple(exclude),
        structure=structure,
        raw=raw,
    )


def _clamp(value: int | None, low: int, high: int) -> int | None:
    if value is None:
        return None
    return min(max(value, low), high)
