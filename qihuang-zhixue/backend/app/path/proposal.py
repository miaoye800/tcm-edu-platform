"""取舍提案：模型能改什么，内核怎么验。

三层分工（计划 §2.2）：

* **硬约束**（内核，不可谈）—— strict 先修边、总时长、考点覆盖、``general`` 必学、
  上限裁剪保护。破了这些不是“个性化”，是学崩。
* **取舍提案**（模型，结构化 JSON）—— 学哪些、跳过哪些、顺序偏好、逐点掌握度、
  资源形式，理由必须点名画像维度与数值。
* **校验兜底**（内核）—— 逐条验，非法项进 ``rejected[]``；整单非法的退回重试，
  仍失败回规则底稿并标 ``fallback``。

判断依据不是“信不信任模型”，而是**错误代价不对称**：让模型在“错了也不崩”的区域
（砍哪几章）自由，在“一错就崩”的区域（先修边、考点覆盖）只给建议。

为什么提案模型用 ``extra="ignore"``、而其它契约用 ``extra="forbid"``：提案是**模型
输出**，多写一个键应该被丢掉，而不是让整次规划退回规则底稿。契约的严格性该用在
“我们自己产出的结构”上，不用在“别人嘴上说的话”上。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.path.constants import (
    CONFIDENCE_THRESHOLD,
    MAX_TARGET_MASTERY,
    MIN_INCLUDE_DOMAINS,
    MIN_TARGET_MASTERY,
    OVER_PRUNE_RATIO,
)
from app.path.subjects import CANONICAL_ORDER, order_key, title_of

ProposalCode = Literal[
    "proposal.order_mismatch",
    "proposal.strict_conflict",
    "proposal.missing_prereq",
    "proposal.exam_coverage",
    "proposal.over_pruned",
    "proposal.mastery_range",
    "proposal.untrusted_basis",
    "proposal.unknown_target",
    "proposal.subject_dropped",
    "proposal.untrusted_mastery",
]

#: 整单拒的码。其余码只丢一条，决策仍然成立。
FATAL_CODES: frozenset[str] = frozenset(
    {"proposal.order_mismatch", "proposal.exam_coverage", "proposal.over_pruned"}
)


class ProposalModel(BaseModel):
    """面向模型输出的宽松解析：多余的键丢弃，缺的键取默认值。"""

    model_config = ConfigDict(extra="ignore")


class Exclusion(ProposalModel):
    domain_id: str = ""
    reason: str = ""


class Coverage(ProposalModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[Exclusion] = Field(default_factory=list)

    @field_validator("exclude", mode="before")
    @classmethod
    def _coerce_exclude(cls, value: Any) -> Any:
        """模型常把 exclude 写成 ``["d10"]``；两种写法都收，不能因此整单作废。"""

        if not isinstance(value, list):
            return []
        return [{"domain_id": item, "reason": ""} if isinstance(item, str) else item for item in value]


class RationaleBasis(ProposalModel):
    dimension: str = ""
    value: Any = None
    confidence: float = 0.0


class Rationale(ProposalModel):
    claim: str = ""
    basis: list[RationaleBasis] = Field(default_factory=list)


class NodeOverride(ProposalModel):
    target_mastery: float | None = None
    resource_hint: str | None = None
    #: ``True`` = 这一点已有基础，不进路线；确认时标成"已掌握（免修）"。
    skip: bool = False
    #: 跳过 / 降难的理由。原样带给用户，别在呈现层重新编。
    reason: str = ""


class SubjectDecision(ProposalModel):
    """学科级取舍。这是模型**必须表态**的一层：学、跳过、还是只学一部分。

    为什么要有这一层而不是继续让它一章一章地勾：六门课、90 个章节点摆在一个
    prompt 里，模型很容易"看着还行就都留着"；而用户的原话（"已经掌握中药学"）
    本来就是学科粒度的。让它先在 6 个学科上表态，再在章上做微调，才是
    和用户同一层的对话。
    """

    subject_id: str = ""
    decision: Literal["study", "skip", "partial"] = "study"
    reason: str = ""


class UnitSkip(ProposalModel):
    """跳过一个**单元**（小节 / 节 / 章）。

    与 ``skip_chapters`` 的分工：那一个是"这一整章不学"，这一个是"这一章还学，
    但其中某几节不学"。期末考试从来不是"整章不考"，而是"这章里只考那两节"——
    没有这个粒度，模型只能整章砍（砍 3 章），剩下的 62 节一节不少地压给用户，
    他看到的还是"有很多不需要的"。
    """

    unit_id: str = ""
    reason: str = ""


class PointSkip(ProposalModel):
    """单点跳过。稀疏给，只用来表达"这一条我确实不用学"。"""

    node_id: str = ""
    reason: str = ""


class PlanProposal(ProposalModel):
    """模型这一轮的取舍提案。字段不全也要能解析，缺的部分交给校验判。"""

    subjects: list[SubjectDecision] = Field(default_factory=list)
    subject_order: list[str] = Field(default_factory=list)
    skip_chapters: list[Exclusion] = Field(default_factory=list)
    #: 章内某个小节/节不学。比 skip_points 粗、比 skip_chapters 细——期末应考最常用的一档。
    skip_units: list[UnitSkip] = Field(default_factory=list)
    skip_points: list[PointSkip] = Field(default_factory=list)
    #: 旧形状。保留是为了录制回放（``LLM_MODE=replay``）里的历史响应仍能解析。
    coverage: Coverage = Field(default_factory=Coverage)
    order: list[str] = Field(default_factory=list)
    depth_overrides: dict[str, NodeOverride] = Field(default_factory=dict)
    resource_overrides: dict[str, NodeOverride] = Field(default_factory=dict)
    rationale: list[Rationale] = Field(default_factory=list)
    confidence: float = 0.0

    @field_validator("skip_units", mode="before")
    @classmethod
    def _coerce_skip_units(cls, value: Any) -> Any:
        """模型也可能把 skip_units 写成 ``["d10.s2"]``，同样收下。"""

        if not isinstance(value, list):
            return []
        return [{"unit_id": item, "reason": ""} if isinstance(item, str) else item for item in value]

    @field_validator("skip_chapters", mode="before")
    @classmethod
    def _coerce_skip_chapters(cls, value: Any) -> Any:
        """模型常把 skip_chapters 写成 ``["d10"]``；两种写法都收，不能因此整单作废。"""

        if not isinstance(value, list):
            return []
        return [{"domain_id": item, "reason": ""} if isinstance(item, str) else item for item in value]

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> "PlanProposal":
        """把模型吐的 JSON 收成提案。

        兼容旧的 ``{ordered_domain_ids, suggestion}`` 形状：那是上一版 prompt 的
        输出结构，录制回放（``LLM_MODE=replay``）里还留着。旧形状等于“全量覆盖 +
        模型给的排序”，语义上没有取舍，按此还原即可。
        """

        raw = dict(payload or {})
        if not raw.get("coverage") and isinstance(raw.get("ordered_domain_ids"), list):
            include = [str(item) for item in raw["ordered_domain_ids"]]
            raw["coverage"] = {"include": include}
            raw.setdefault("order", include)
            claim = str(raw.get("suggestion") or "").strip()
            if claim:
                raw.setdefault("rationale", [{"claim": claim, "basis": []}])
        return cls.model_validate(raw)


@dataclass(frozen=True)
class ProposalScope:
    """校验需要的全部事实；由 planner 从 PlanningContext 一次性算出。"""

    domain_ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    strict_edges: tuple[tuple[str, str], ...]
    hot_domain_ids: frozenset[str]
    layer_of: Mapping[str, int]
    breadth: str
    dimension_confidence: Mapping[str, float]
    #: 域 → 学科。空映射时全部按"未归类"处理，行为与加学科层之前一致。
    subject_of: Mapping[str, str] = field(default_factory=dict)
    #: 学科内**已排好**的章序列。模型只给学科的先后，章序仍由拓扑事实决定，
    #: 免得它为了"把内科提前"顺手打乱章内的教材顺序。
    within_subject: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: 学科 → 掌握度覆盖比例（BKT 实测），用于校验"已掌握"的声明是否有据。
    subject_mastered: Mapping[str, float] = field(default_factory=dict)
    #: 学科 → 已有路线的完成比例（user_path_state），同上。
    subject_learned: Mapping[str, float] = field(default_factory=dict)
    #: 用户原话。学科级"已掌握"的第一顺位证据就是它。
    request_text: str = ""
    #: 用户点名要学的学科（本次的硬边界）。空 = 没点名，全图参与取舍。
    #: 呈现层用它回答"这份计划为什么只有中药学"——不能只让用户自己数章节。
    requested_subjects: tuple[str, ...] = ()

    @property
    def subjects(self) -> tuple[str, ...]:
        """图里真实出现的学科，按标准教材顺序。未归类的域不进这个清单。"""

        seen = {self.subject_of.get(domain_id, "") for domain_id in self.domain_ids}
        return tuple(
            sorted(
                (subject for subject in seen if subject),
                key=order_key,
            )
        )

    def domains_of(self, subject_id: str) -> tuple[str, ...]:
        return tuple(
            domain_id
            for domain_id in self.domain_ids
            if self.subject_of.get(domain_id, "") == subject_id
        )

    @property
    def min_include(self) -> int:
        """不许把课删光：max(6, 60% 域总数)，但不超过域总数本身。

        **分母要在"仍然在学的范围"里算**。用户说"我已经掌握中药学、想学其余五门"
        时，中药学那 22 章本来就不在本次规划范围内，把它算进分母会让下限虚高
        16 章，模型只能把已经学过的东西重新排进来凑数——那正是"不要局部修补"
        所指的症状。豁免哪些域由 ``validate_proposal`` 按证据判定后传入。
        """

        return _floor(len(self.domain_ids))


def _floor(in_scope: int) -> int:
    floor = max(MIN_INCLUDE_DOMAINS, math.ceil(OVER_PRUNE_RATIO * in_scope))
    return min(max(in_scope, 0), floor)


class RejectedItem(ProposalModel):
    code: ProposalCode
    target: str = ""
    message: str = ""


class ProposalVerdict(ProposalModel):
    """校验结果。``accepted=False`` 表示整单不可用，需要重试或回落规则底稿。"""

    accepted: bool
    order: list[str] = Field(default_factory=list)
    overrides: dict[str, NodeOverride] = Field(default_factory=dict)
    rejected: list[RejectedItem] = Field(default_factory=list)
    auto_added: list[str] = Field(default_factory=list)
    rationale: list[Rationale] = Field(default_factory=list)
    untrusted_dimensions: list[str] = Field(default_factory=list)
    reason: str = ""
    #: 本次判为"已有基础、整科不进范围"的学科。呈现层用它解释"为什么没有中药学"。
    exempt_subjects: list[str] = Field(default_factory=list)
    #: 上面那些学科覆盖到的具体章。有了它，呈现层才能把"整科跳过"摊到每一行。
    exempt_domains: list[str] = Field(default_factory=list)
    #: 被跳过的章 → 理由。用户点开"跳过了哪些"时要能逐条答上来。
    skipped_chapters: dict[str, str] = Field(default_factory=dict)
    #: 被跳过的**单元** → 理由（章还在学，但其中这几节不学）。
    skipped_units: dict[str, str] = Field(default_factory=dict)


def validate_proposal(proposal: PlanProposal, scope: ProposalScope) -> ProposalVerdict:
    """逐条校验提案，返回可执行的顺序 + 逐点覆盖 + 全部拒收记录。

    顺序上**先学科、后章节**：学科级取舍决定"本次规划的范围"，范围定了才有
    "保住了几成"这个问题。把两件事搅在一起算，会出现"用户说学过中药学、
    系统却因为总量不够把中药学补回来"的荒谬结果。
    """

    rejected: list[RejectedItem] = []
    known_domains = set(scope.domain_ids)

    # ── ① 学科级取舍：整科跳过必须有证据 ────────────────────────────────
    exempt, subject_rejected = _subject_decisions(proposal, scope)
    rejected.extend(subject_rejected)

    # ── ② 本次规划的范围 ────────────────────────────────────────────────
    explicit = [str(item) for item in proposal.coverage.include]
    if (
        not explicit
        and not proposal.subjects
        and not proposal.subject_order
        and not proposal.skip_chapters
        and not proposal.skip_points
    ):
        # 一个"什么都没说"的提案不是提案。放行它等于把"模型没答"伪装成
        # "模型决定全学"——用户会以为这份路线经过判断，其实一个字都没经过。
        message = "提案没有表达任何取舍（既没有学科取舍，也没有章节或方向清单）"
        rejected.append(RejectedItem(code="proposal.over_pruned", message=message))
        return ProposalVerdict(accepted=False, rejected=rejected, reason=message)
    if explicit:
        # 旧形状 / 模型主动列清单：以它为准，但已豁免的学科不能借尸还魂。
        deduped = list(dict.fromkeys(explicit))
        for unknown in [item for item in deduped if item not in known_domains]:
            rejected.append(
                RejectedItem(
                    code="proposal.unknown_target",
                    target=unknown,
                    message="知识图里没有这个方向，已忽略",
                )
            )
        candidate = [item for item in deduped if item in known_domains and item not in exempt]
    else:
        candidate = [item for item in scope.domain_ids if item not in exempt]

    # ── ③ 章级跳过 ──────────────────────────────────────────────────────
    skipped_chapters: set[str] = set()
    chapter_reasons: dict[str, str] = {}
    for item in proposal.skip_chapters:
        domain_id = str(item.domain_id or "")
        if domain_id not in known_domains:
            rejected.append(
                RejectedItem(
                    code="proposal.unknown_target",
                    target=domain_id,
                    message="知识图里没有这一章，跳过项已忽略",
                )
            )
            continue
        skipped_chapters.add(domain_id)
        if item.reason:
            chapter_reasons[domain_id] = item.reason
    if skipped_chapters:
        candidate = [item for item in candidate if item not in skipped_chapters]

    include = candidate

    # ── ④ 上限裁剪保护：一破就整单拒 ────────────────────────────────────
    # 先查"整科被悄悄抹掉"，再查"总量不够"：前者是结构错（用户会问"我说要学
    # 方剂学，路线里怎么没有"），后者只是量的问题。报后者会掩盖前者。
    fatal = _silent_subject_drop(include, scope, exempt)
    code: ProposalCode = "proposal.subject_dropped"
    if not fatal:
        fatal = _hard_limit_violation(include, scope, exempt)
        code = "proposal.over_pruned"
    if fatal:
        rejected.append(RejectedItem(code=code, message=fatal))
        return ProposalVerdict(accepted=False, rejected=rejected, reason=fatal)

    # ── ⑤ 顺序 ──────────────────────────────────────────────────────────
    if proposal.subject_order or proposal.subjects:
        order = _subject_blocked_order(include, proposal.subject_order, scope)
        if order is None:
            message = "学科顺序里出现了知识图中没有的学科"
            rejected.append(RejectedItem(code="proposal.order_mismatch", message=message))
            return ProposalVerdict(accepted=False, rejected=rejected, reason=message)
    else:
        order = _normalized_order(proposal.order, include)
        if order is None:
            message = "order 必须是 include 的排列（缺项或多出未选中的域）"
            rejected.append(RejectedItem(code="proposal.order_mismatch", message=message))
            return ProposalVerdict(accepted=False, rejected=rejected, reason=message)

    # ── 前置补全：被排掉的域若被某个入选项 strict 依赖，必须补回来 ──────
    include_set = set(include)
    auto_added: list[str] = []
    changed = True
    while changed:
        changed = False
        for source, target in scope.strict_edges:
            if target in include_set and source not in include_set:
                include_set.add(source)
                auto_added.append(source)
                changed = True
    for domain_id in auto_added:
        rejected.append(
            RejectedItem(
                code="proposal.missing_prereq",
                target=domain_id,
                message=f"{domain_id} 是已选方向的前置，已自动补回",
            )
        )
        order.insert(_insertion_index(order, domain_id, scope.strict_edges), domain_id)

    # ── 顺序：strict 边不得倒置，层号不得回退 ────────────────────────────
    order, conflicts = _repair_order(order, scope)
    for source, target in conflicts:
        rejected.append(
            RejectedItem(
                code="proposal.strict_conflict",
                target=f"{source}->{target}",
                message=f"{source} 必须在 {target} 之前，已按拓扑修正",
            )
        )

    # ── 考点覆盖：考点导向下不许整域跳过含高频考点的方向 ────────────────
    if scope.breadth == "考点导向":
        dropped = sorted(
            domain_id
            for domain_id in scope.hot_domain_ids
            if domain_id not in include_set and domain_id not in exempt
        )
        if dropped:
            message = f"{'、'.join(dropped)} 含高频考点（考点频率≥0.8），考点导向下不能整域跳过"
            rejected.append(RejectedItem(code="proposal.exam_coverage", target="、".join(dropped), message=message))
            return ProposalVerdict(accepted=False, rejected=rejected, reason=message)

    # ── 单元级跳过：章还在学，但其中某几节不学 ──────────────────────
    # 不做存在性校验：校验器手里没有教材层级（那是 Curriculum 的事），
    # 编造的 unit_id 到不了任何一行，draft 层按单元表过滤时自然消失。
    skipped_units: dict[str, str] = {}
    for item in proposal.skip_units:
        unit_id = str(item.unit_id or "").strip()
        if unit_id:
            skipped_units[unit_id] = str(item.reason or "").strip()

    overrides = _validated_overrides(proposal, scope, rejected)
    rationale, untrusted_dimensions = _split_rationale(proposal, scope, rejected)

    return ProposalVerdict(
        accepted=True,
        order=order,
        overrides=overrides,
        rejected=rejected,
        auto_added=auto_added,
        rationale=rationale,
        untrusted_dimensions=untrusted_dimensions,
        exempt_subjects=sorted({scope.subject_of.get(d, "") for d in exempt} - {""}),
        exempt_domains=sorted(exempt),
        skipped_chapters=chapter_reasons,
        skipped_units=skipped_units,
    )


def _subject_decisions(
    proposal: PlanProposal,
    scope: ProposalScope,
) -> tuple[set[str], list[RejectedItem]]:
    """把学科级取舍翻成"本次不学的域集合"，并给"已掌握"的声明找出处。

    证据三条，命中一条即可（顺序即优先级，报告里取第一条命中的写成理由）：

    1. **用户原话点名了这门课**（"已经掌握中药学"）。这是最强证据：他自己说的。
    2. **BKT 掌握度覆盖 ≥ 60%**：做过题、测得出来的，不是猜的。
    3. **已有路线里该科完成 ≥ 60%**：之前学过并点过完成。

    三条都不成立时**不整科跳过**，而是降级成单条拒收：这条声明保留在
    ``rejected[]`` 里给用户看，学科仍然回到本次范围。这样"模型自作主张砍掉
    一门课"不会静默发生，而"用户确实学过"也不会被规则挡住。
    """

    exempt: set[str] = set()
    rejected: list[RejectedItem] = []
    seen: set[str] = set()

    for item in proposal.subjects:
        subject_id = str(item.subject_id or "").strip()
        if not subject_id:
            continue
        if subject_id in seen:
            continue
        seen.add(subject_id)
        if subject_id not in scope.subjects:
            rejected.append(
                RejectedItem(
                    code="proposal.unknown_target",
                    target=subject_id,
                    message="知识图里没有这门学科，取舍已忽略",
                )
            )
            continue
        if item.decision != "skip":
            continue

        evidence = _mastery_evidence(subject_id, scope, item.reason)
        if evidence is None:
            rejected.append(
                RejectedItem(
                    code="proposal.untrusted_mastery",
                    target=subject_id,
                    message=(
                        f"没有证据表明已经学过{title_of(subject_id)}"
                        f"（原话未提及、掌握度与路线进度都不足）→ 该科仍在本次范围内"
                    ),
                )
            )
            continue
        exempt.update(scope.domains_of(subject_id))
    return exempt, rejected


def _evidence_ratio(subject_id: str, scope: ProposalScope) -> float:
    return max(
        float(scope.subject_mastered.get(subject_id, 0.0)),
        float(scope.subject_learned.get(subject_id, 0.0)),
    )


def _mastery_evidence(subject_id: str, scope: ProposalScope, reason: str) -> str | None:
    """返回证据描述；``None`` 表示查无实据。"""

    from app.path.subjects import mentions  # 局部导入：避免 dataclass 与模块初始化互相拉扯

    if mentions(scope.request_text, subject_id):
        return f"用户原话提到「{title_of(subject_id)}」"
    if mentions(reason, subject_id):
        # 模型在 reason 里复述用户说过的话也算——但只认学科名字本身，
        # 不接受"看起来学过"这种自由心证。
        return f"取舍理由引用了「{title_of(subject_id)}」"
    ratio = _evidence_ratio(subject_id, scope)
    if ratio >= OVER_PRUNE_RATIO:
        return f"掌握度/路线进度覆盖 {ratio:.0%}"
    return None


def _silent_subject_drop(include: list[str], scope: ProposalScope, exempt: set[str]) -> str:
    """一门课在被点名"学"的情况下，一章都没留下——这不是取舍，是漏了。

    允许整科跳过，但必须走 ``subjects[].decision="skip"`` 那条明路（带理由、
    过证据），不能在章级微调里悄悄把它抹掉。否则用户会看到"我说要学方剂学，
    结果路线里没有方剂学"而没有任何解释。
    """

    kept = {scope.subject_of.get(domain_id, "") for domain_id in include}
    exempt_subjects = {scope.subject_of.get(domain_id, "") for domain_id in exempt}
    dropped = [
        subject_id
        for subject_id in scope.subjects
        if subject_id not in exempt_subjects
        and subject_id not in kept
        and scope.domains_of(subject_id)
    ]
    if not dropped:
        return ""
    names = "、".join(title_of(item) for item in dropped)
    return f"{names} 一章都没留下：整科跳过要写进 subjects[].decision 并给理由，不能只删章"


def _subject_blocked_order(
    include: list[str],
    subject_order: list[str],
    scope: ProposalScope,
) -> list[str] | None:
    """按学科分块铺平：同一门课的章必须连在一起，块内保持拓扑/教材顺序。

    这是"学科跳跃交织"的正面解法。章序**不交给模型**：它只要排出六门课
    谁先谁后，块内沿用 ``within_subject``（拓扑层 → 教材章号），
    既不会打乱教材顺序，也不会出现"第 1 章 → 第 1 章 → 第 2 章"的往返跑。
    """

    present = set(include)
    known = set(scope.subjects)
    requested = [str(item) for item in subject_order]
    unknown = [item for item in requested if item and item not in known]
    if unknown:
        return None
    blocks: list[str] = []
    for subject_id in requested:
        if subject_id not in blocks:
            blocks.append(subject_id)
    # 模型没点到的学科按教材顺序收尾：漏说一个学科不该让整单作废，
    # 但也不能因为没提到就消失（``_silent_subject_drop`` 已经挡住整科归零）。
    for subject_id in scope.subjects:
        if subject_id not in blocks:
            blocks.append(subject_id)
    blocks.extend(sorted({scope.subject_of.get(d, "") for d in include} - set(blocks) - {""}))

    order: list[str] = []
    for subject_id in blocks:
        for domain_id in scope.within_subject.get(subject_id, ()):
            if domain_id in present:
                order.append(domain_id)
    # 未归类的域（自定义 / 标签命名）按 scope 顺序补在末尾，别丢。
    for domain_id in include:
        if domain_id not in order:
            order.append(domain_id)
    return order if set(order) == present else None


def _hard_limit_violation(include: list[str], scope: ProposalScope, exempt: set[str]) -> str:
    if not include:
        return "提案没有保留任何方向"
    # 总论必学——但只在中药学仍在范围内时成立。用户说"已掌握中药学"之后，
    # 还咬着"总论必学"就是拿规则去否掉他自己给出的前提。
    # 注意 ``exempt`` 里装的是**域 id**（general / d01 …），不是学科 id。
    if (
        "general" in scope.domain_ids
        and "general" not in include
        and "general" not in exempt
    ):
        return "总论是必学内容，不能跳过"
    in_scope = len([domain_id for domain_id in scope.domain_ids if domain_id not in exempt])
    minimum = _floor(in_scope)
    if len(include) < minimum:
        return f"只保留 {len(include)}/{in_scope} 个方向，低于下限 {minimum}（不可把课删光）"
    return ""


def _normalized_order(order: list[str], include: list[str]) -> list[str] | None:
    """order 必须是 include 的排列；空 order 按 include 顺序补全（不算违规）。"""

    items = [str(item) for item in order]
    if not items:
        return list(include)
    deduped = list(dict.fromkeys(items))
    if len(deduped) != len(include) or set(deduped) != set(include):
        return None
    return deduped


def _insertion_index(order: list[str], domain_id: str, edges: tuple[tuple[str, str], ...]) -> int:
    """把自动补回的前置塞到它最早的下游项之前。"""

    dependents = [order.index(target) for source, target in edges if source == domain_id and target in order]
    return min(dependents) if dependents else len(order)


def _repair_order(
    order: list[str], scope: ProposalScope
) -> tuple[list[str], list[tuple[str, str]]]:
    """按层号稳定重排，再逐条消掉 strict 倒置；返回修正结果与被修正的相邻对。

    "层号不得回退"和"strict 边不得倒置"是两条独立的规矩：后者一定有边可查，
    前者不一定——模型完全可以把第 1 层的域排到第 0 层后面而一条 strict 边都不碰。
    只查边会漏掉这种情况，所以这里两种都查。
    """

    present = set(order)
    edges = [(source, target) for source, target in scope.strict_edges if source in present and target in present]
    position = {domain_id: index for index, domain_id in enumerate(order)}
    violations: list[tuple[str, str]] = [
        (source, target) for source, target in edges if position[source] > position[target]
    ]
    for index in range(len(order) - 1):
        current, following = order[index], order[index + 1]
        if scope.layer_of.get(current, 0) > scope.layer_of.get(following, 0):
            # 同一条错位被"strict 倒置"和"层号回退"各抓一次，只报一次：
            # 报两遍会让用户以为模型犯了两处错。
            if (current, following) in violations or (following, current) in violations:
                continue
            violations.append((current, following))
    if not violations:
        return list(order), []

    original_index = {domain_id: index for index, domain_id in enumerate(order)}
    repaired = sorted(order, key=lambda domain_id: (scope.layer_of.get(domain_id, 0), original_index[domain_id]))
    # 层内理论上不会再有 strict 倒置（strict 边一定跨层），这里再兜一遍：
    # 万一上游图把两条 strict 边连到同一层，也不能把倒置放出去。
    for _ in range(len(repaired)):
        moved = False
        for source, target in edges:
            if repaired.index(source) > repaired.index(target):
                repaired.remove(source)
                repaired.insert(repaired.index(target), source)
                moved = True
        if not moved:
            break
    return repaired, violations


def _validated_overrides(
    proposal: PlanProposal,
    scope: ProposalScope,
    rejected: list[RejectedItem],
) -> dict[str, NodeOverride]:
    """合并 depth_overrides 与 resource_overrides，丢掉越界与不存在的目标。"""

    known_nodes = set(scope.node_ids)
    merged: dict[str, NodeOverride] = {}

    def merge(node_id: str, item: NodeOverride, *, allow_mastery: bool) -> None:
        if node_id not in known_nodes:
            rejected.append(
                RejectedItem(
                    code="proposal.unknown_target",
                    target=node_id,
                    message="知识图里没有这个知识点，覆盖已忽略",
                )
            )
            return
        target = item.target_mastery if allow_mastery else None
        if target is not None and not (MIN_TARGET_MASTERY <= float(target) <= MAX_TARGET_MASTERY):
            rejected.append(
                RejectedItem(
                    code="proposal.mastery_range",
                    target=node_id,
                    message=(
                        f"target_mastery={target} 超出 "
                        f"[{MIN_TARGET_MASTERY}, {MAX_TARGET_MASTERY}]，回落到规则值"
                    ),
                )
            )
            target = None
        hint = (item.resource_hint or "").strip() or None
        if target is None and hint is None:
            return
        existing = merged.get(node_id) or NodeOverride()
        merged[node_id] = NodeOverride(
            target_mastery=target if target is not None else existing.target_mastery,
            resource_hint=hint or existing.resource_hint,
        )

    for node_id, item in proposal.depth_overrides.items():
        merge(node_id, item, allow_mastery=True)
    for node_id, item in proposal.resource_overrides.items():
        merge(node_id, item, allow_mastery=False)
    for item in proposal.skip_points:
        node_id = str(item.node_id or "")
        if node_id not in known_nodes:
            rejected.append(
                RejectedItem(
                    code="proposal.unknown_target",
                    target=node_id,
                    message="知识图里没有这个知识点，跳过项已忽略",
                )
            )
            continue
        existing = merged.get(node_id) or NodeOverride()
        merged[node_id] = NodeOverride(
            target_mastery=existing.target_mastery,
            resource_hint=existing.resource_hint,
            skip=True,
            reason=(item.reason or existing.reason or "").strip(),
        )
    return merged


def _split_rationale(
    proposal: PlanProposal,
    scope: ProposalScope,
    rejected: list[RejectedItem],
) -> tuple[list[Rationale], list[str]]:
    """引用了低置信度维度的理由不可采信：**决策保留、理由丢弃**。

    项目已有 ``CONFIDENCE_THRESHOLD = 0.5`` 的门控传统；模型不能拿一个 0.31 置信度的
    “薄弱点”当理由去砍三章内容。注意比的是**画像里实测的置信度**，不是模型自报的那个
    数字 —— 后者想写多少写多少。
    """

    trusted: list[Rationale] = []
    untrusted_dimensions: list[str] = []
    for item in proposal.rationale:
        # ``request`` 不是画像维度，是用户自己那句话。它是**最强的证据**，
        # 不参与置信度门控——否则冷启动（七个维度全是"保守默认"）时，
        # 模型每一句解释都会被丢掉，用户只能看到"已改标规则默认"。
        weak = sorted(
            {
                basis.dimension
                for basis in item.basis
                if basis.dimension
                and basis.dimension != "request"
                and scope.dimension_confidence.get(basis.dimension, 0.0) < CONFIDENCE_THRESHOLD
            }
        )
        if not weak:
            trusted.append(item)
            continue
        untrusted_dimensions.extend(weak)
        rejected.append(
            RejectedItem(
                code="proposal.untrusted_basis",
                target="、".join(weak),
                message=(
                    f"理由引用了置信度<{CONFIDENCE_THRESHOLD}的维度，"
                    "决策保留、理由改标“规则默认”"
                ),
            )
        )
    return trusted, sorted(set(untrusted_dimensions))


__all__ = [
    "FATAL_CODES",
    "Coverage",
    "Exclusion",
    "NodeOverride",
    "PlanProposal",
    "PointSkip",
    "ProposalCode",
    "ProposalScope",
    "ProposalVerdict",
    "Rationale",
    "RationaleBasis",
    "RejectedItem",
    "SubjectDecision",
    "validate_proposal",
]
