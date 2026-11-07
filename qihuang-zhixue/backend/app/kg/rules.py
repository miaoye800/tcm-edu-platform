"""P4.5 关系发现闸门 **R001–R011**。

## 这一层解决什么

教材【应用】里到处是这种句子：

    每与羌活、苍术、川芎等祛风湿、止痛药同用

从 442 味药的段落里用正则能召回四千多个「A 配 B」候选。但**候选不是关系**：
抽出来的一千多条里混着「通鼻窍药」「健脾利水的白术」「其他祛风寒药」这种
描述性短语，也有把方剂名（白虎汤）当药名的。直接落库就是给图谱灌噪声——
旧图 915 条「同归经/同功效」相似边占 58%、一条问题都答不了，就是这么来的。

所以中间加一层闸门：**召回只负责「可能有」，闸门负责「能不能进」**。
过不了的进 ``kg_edge_staging`` 并写明原因，不进 ``kg_edge``。

## 十一条闸门的分工

| 编号 | 查什么 | 为什么必须有 |
|---|---|---|
| R001 | 药名能不能解析到**唯一**实体 | 解析不到就是猜，猜出来的边没有意义 |
| R002 | 不能自己配自己 | 「常与同类药同用」容易被切出自身 |
| R003 | 目标药必须有结构化条目 | 没有条目就说不清剂量毒性，边没有用 |
| R004 | 证据必须**逐字**来自该药原文 | 与 G008 同一条红线 |
| R005 | 七情类别不得臆测 | 原文没写「相须」就只能记「配伍」 |
| R006 | 已有同向同类型边不重复建 | 幂等 |
| R007 | 方剂名不能当药名 | 「如白虎汤」不是配伍 |
| R008 | 炮制前缀归一后要留痕 | 制川乌→川乌 是对的，但要看得出改过 |
| R009 | 必须有页码 | 可溯源是 P2 的验收项 |
| R010 | 与 T1 硬约束冲突时不得建 T2 | 十八反/十九畏优先，冲突时宁可不建 |
| R011 | 单味药召回对象数异常要整条拦下 | 正则炸了的时候，症状是「一味药配了 30 味」 |

## 状态只有三种

``pass`` / ``skip`` / ``fail``。``skip`` 不是失败（去重、无条目），但**也不能
伪装成通过**——它和 ``fail`` 一样不进 ``kg_edge``，只是不进「需要人看」的清单。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 七情里可以作为关系类型的词。原文没写这些词，就只能记成「配伍」。
QIQING = ("相须", "相使", "相畏", "相杀", "相恶")

#: 方剂名的结尾。出现在目标药名位置上就是解析错了。
FORMULA_SUFFIX = ("汤", "散", "丸", "饮", "膏", "丹", "煎", "方")

#: 炮制/修治前缀。剥掉之后还能解析到实体，才算归一成功。
PREP_PREFIX = ("生", "制", "炒", "煅", "醋", "酒", "蜜", "盐", "姜", "炭", "焦", "煨")

#: 一味药最多能配多少味——**熔断阈值**，不是业务上限。
#:
#: 实测 442 味药的召回结果：最多的是黄连 38 个对象（它出现在泻心汤、白头翁汤、
#: 左金丸等十几个方里，38 是真的），其次昆布 35、金银花 31。所以上限设 45：
#: 正常数据永远碰不到，一旦碰到就说明正则把整段方剂组成吃进来了。
MAX_PARTNERS = 45


@dataclass
class RelationCandidate:
    """一条待判的配伍候选。字段都是**事实**，不含判断。"""

    source_name: str
    target_name: str
    #: 该药这一段的完整原文，R004 拿它做子串校验。
    source_quote: str = ""
    #: 候选所在的原句，作为边的 evidence.quote。
    quote: str = ""
    field: str = ""
    page: int | None = None
    relation_type: str = "配伍"
    #: 解析结果，由调用方填。解析不到就是 None——**不要填个近似值糊过去**。
    source_id: str | None = None
    target_id: str | None = None
    #: 目标药在 data/herbs 里有没有结构化条目。
    target_has_monograph: bool = True
    #: 库里已有同向同类型边。
    already_exists: bool = False
    #: 这对药落在十八反/十九畏里。
    t1_conflict: bool = False
    #: 这味药这次一共召回了几条候选。
    partner_count: int = 0


@dataclass(frozen=True)
class RuleHit:
    code: str
    status: str  # pass / skip / fail
    reason: str


@dataclass
class Verdict:
    """一条候选的判决。``status`` 取最坏的那一档。"""

    hits: list[RuleHit] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(hit.status == "fail" for hit in self.hits):
            return "fail"
        if any(hit.status == "skip" for hit in self.hits):
            return "skip"
        return "pass"

    @property
    def failed(self) -> list[str]:
        return [hit.code for hit in self.hits if hit.status == "fail"]

    def describe(self) -> str:
        bad = [hit for hit in self.hits if hit.status != "pass"]
        return "；".join(f"{hit.code} {hit.reason}" for hit in bad)


def _squash(text: str) -> str:
    return "".join((text or "").split())


def _strip_prep(name: str) -> str:
    for prefix in PREP_PREFIX:
        if name.startswith(prefix) and len(name) > len(prefix) + 1:
            return name[len(prefix):]
    return name


def evaluate(candidate: RelationCandidate) -> Verdict:
    """跑完 R001–R011。全部通过才返回 ``status == "pass"``。"""

    verdict = Verdict()

    def add(code: str, status: str, reason: str) -> None:
        verdict.hits.append(RuleHit(code, status, reason))

    # ---- R001 药名解析 ----
    if not candidate.source_id:
        add("R001", "fail", f"源药名「{candidate.source_name}」解析不到实体")
    if not candidate.target_id:
        add("R001", "fail", f"药名「{candidate.target_name}」解析不到实体")
    else:
        add("R001", "pass", "")

    # ---- R002 不能自配 ----
    if candidate.source_id and candidate.source_id == candidate.target_id:
        add("R002", "fail", "自己配自己")
    else:
        add("R002", "pass", "")

    # ---- R003 目标药要有结构化条目 ----
    #
    # 只在药名解析成功时才判。解析不到已经被 R001 判死了，这里再报一次
    # 只会让报告里 R003 的数字虚高，看着像「358 味药没有条目」。
    if not candidate.target_id:
        add("R003", "pass", "")
    elif not candidate.target_has_monograph:
        add("R003", "skip", f"「{candidate.target_name}」没有结构化条目，这条边说不清剂量毒性")
    else:
        add("R003", "pass", "")

    # ---- R004 证据逐字来自原文 ----
    quote = _squash(candidate.quote)
    if not quote:
        add("R004", "fail", "没有证据原句")
    elif quote not in _squash(candidate.source_quote):
        add("R004", "fail", "证据原句不在该药教材原文里")
    else:
        add("R004", "pass", "")

    # ---- R005 七情类别不得臆测 ----
    if candidate.relation_type in QIQING:
        if candidate.relation_type not in candidate.quote:
            add("R005", "fail", f"标成「{candidate.relation_type}」但原文没写这个词")
        else:
            add("R005", "pass", "")
    elif candidate.relation_type != "配伍":
        add("R005", "fail", f"未知的关系类型「{candidate.relation_type}」")
    else:
        add("R005", "pass", "")

    # ---- R006 不重复建边 ----
    if candidate.already_exists:
        add("R006", "skip", "库里已有同向同类型边")
    else:
        add("R006", "pass", "")

    # ---- R007 方剂名不能当药名 ----
    if candidate.target_name.endswith(FORMULA_SUFFIX):
        add("R007", "fail", f"「{candidate.target_name}」是方剂名，不是药名")
    else:
        add("R007", "pass", "")

    # ---- R008 炮制前缀归一留痕 ----
    stripped = _strip_prep(candidate.target_name)
    if stripped != candidate.target_name:
        add("R008", "pass", f"「{candidate.target_name}」按「{stripped}」归一")
    else:
        add("R008", "pass", "")

    # ---- R009 必须有页码 ----
    if not isinstance(candidate.page, int) or candidate.page <= 0:
        add("R009", "fail", "没有页码，无法回溯")
    else:
        add("R009", "pass", "")

    # ---- R010 T1 优先 ----
    if candidate.t1_conflict:
        add("R010", "fail", "这对药在十八反/十九畏里，T1 硬约束优先，不再建 T2 配伍边")
    else:
        add("R010", "pass", "")

    # ---- R011 召回数量异常 ----
    if candidate.partner_count > MAX_PARTNERS:
        add(
            "R011",
            "fail",
            f"「{candidate.source_name}」一次召回 {candidate.partner_count} 个配伍对象，"
            f"超过上限 {MAX_PARTNERS}，多半是把方剂组成当成配伍了",
        )
    else:
        add("R011", "pass", "")

    return verdict


#: 闸门说明书。报告里直接引用，避免「代码改了文档没改」。
DESCRIBE = {
    "R001": "药名解析到唯一实体",
    "R002": "不能自己配自己",
    "R003": "目标药有结构化条目",
    "R004": "证据逐字来自原文",
    "R005": "七情类别不臆测",
    "R006": "不重复建边",
    "R007": "方剂名不当药名",
    "R008": "炮制前缀归一留痕",
    "R009": "有页码可回溯",
    "R010": "T1 硬约束优先",
    "R011": "召回数量不异常",
}
