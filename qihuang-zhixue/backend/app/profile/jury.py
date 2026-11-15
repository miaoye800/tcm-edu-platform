"""Three-teacher jury scoring for learning-profile dimensions."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import Settings, get_settings
from app.llm.clients import ChatMessage
from app.llm.router import ModelRouter
from app.profile.anchors import resolve_band
from app.profile.fusion import FusionResult, evidence_ref, fuse_dimension
from app.profile.component_model import (
    COMPONENTS,
    COMPONENT_RUBRICS,
    COMPONENT_WEIGHTS,
    DIMENSION_COMPONENT_LABELS,
    TIER_BANDS,
    TIER_ORDER,
    ComponentJuror,
    ComponentScore,
    QualityFactors,
    clamp_to_band,
    deduplicate_component_cards,
    dimension_score,
    geometric_quality,
    quality_tier_of,
)
from app.profile.guardrail import GuardrailWarning, allowed_score_interval, apply_guardrail
from app.profile.schema import DIMENSION_LABELS, DimensionKey, EvidenceCard

TeacherName = Literal["严师", "宽师", "中立"]
LLMCall = Callable[[Sequence[ChatMessage]], Awaitable[str]]

JURY_TEACHERS: tuple[TeacherName, ...] = ("严师", "宽师", "中立")
MOCK_FALLBACK_PREFIX = "[MOCK:"

# The three perspectives are not decoration: each has a stated stance and a
# default landing zone inside the shared band, so the spread comes from how
# generously the same evidence is read rather than from three unrelated numbers.
TEACHER_RULES: dict[TeacherName, dict[str, str]] = {
    "严师": {
        "stance": "证据派",
        "rule": "只认原话直接支撑的部分；表述模糊取下沿；没有行为或实测证据不得进入该档上三分之一；证据冲突时取不利解释。",
        "anchor": "该档下三分之一",
    },
    "宽师": {
        "stance": "潜力派",
        "rule": "有明确意向即算有效；同一证据有多种解释时取有利的一种；不得突破该档上限；不得把没提到当成有。",
        "anchor": "该档上三分之一",
    },
    "中立": {
        "stance": "量表派",
        "rule": "严格按该组件三档描述逐条比对，不额外推断也不额外宽容；分数偏离该档中位必须说明理由。",
        "anchor": "该档中位",
    },
}

TIER_LANDING: dict[str, dict[TeacherName, str]] = {
    "low": {"严师": "5–17", "中立": "17–28", "宽师": "28–40"},
    "medium": {"严师": "40–50", "中立": "50–60", "宽师": "60–70"},
    "high": {"严师": "70–78", "中立": "78–87", "宽师": "87–95"},
}

# Dimension-level scales describe *semantics only*.  Numeric boundaries live in
# TIER_BANDS so the model never receives two conflicting sets of intervals.
ANCHOR_SCALES: dict[DimensionKey, str] = {
    "knowledge_base": (
        "低档（5–40）：药名认不全，性味归经空白，或只认得少数常见药且功效模糊。\n"
        "中档（40–70）：掌握常用药功效，配伍薄弱；或功效较熟但边界不清。\n"
        "高档（70–95）：功效与配伍熟练，剂量精准，能识别易混药与禁忌。"
    ),
    "learning_goal": (
        "低档（5–40）：只有想学中医这类愿望，无对象、无期限、无成果标准。\n"
        "中档（40–70）：有目标类别（考研、考证、兴趣），但缺期限或验收标准，行动零散。\n"
        "高档（70–95）：对象、成果、期限齐全，路径与第一步清楚，且已有实施记录。"
    ),
    "learning_style": (
        "低档（5–40）：说不出偏好，或怎么都行；方法每次都不一样。\n"
        "中档（40–70）：有偏好方向但说不清适用条件，换任务就变。\n"
        "高档（70–95）：偏好明确、能给出比较理由，并在多个任务上稳定执行。"
    ),
    "weak_points": (
        "低档（5–40）：薄弱点说不清，或只是猜测；换个问法就答错。\n"
        "中档（40–70）：能定位一到两个薄弱点，但缺实测支撑，纠错依赖提示。\n"
        "高档（70–95）：薄弱点分布清晰且有实测支撑，能自己发现矛盾并修正。"
    ),
    "pace": (
        "低档（5–40）：没有计划，或计划与可用时间明显不匹配。\n"
        "中档（40–70）：有计划但执行断续，断过之后进度反复归零。\n"
        "高档（70–95）：计划到日到量到验收，持续执行两周以上，断过能接上。"
    ),
    "motivation": (
        "低档（5–40）：被动应付，除了聊天没有任何投入，遇到困难即放弃。\n"
        "中档（40–70）：有意愿但需要外部推动，投入零散，受阻时未验证长期坚持。\n"
        "高档（70–95）：自驱明显，有时间或金钱的实际投入与对外承诺，受阻后仍能坚持。"
    ),
    "resource_preference": (
        "低档（5–40）：资源选择随意，说不出用什么、为什么用。\n"
        "中档（40–70）：有偏好资源，但缺任务匹配理由，也不考虑可获得性。\n"
        "高档（70–95）：资源与任务一一匹配，考虑现实限制，并能说出哪部分有效、据此换过资源。"
    ),
}


class JurorAssessment(BaseModel):
    """One teacher perspective in the jury."""

    model_config = ConfigDict(validate_assignment=True)

    score: float = Field(ge=0.0, le=100.0)
    reasoning_chain: list[str] = Field(min_length=1)
    comment: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def normalize_keys(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if "reasoning_chain" not in payload:
            payload["reasoning_chain"] = payload.get("推理链") or payload.get("reasoning") or payload.get("chain")
        if "comment" not in payload:
            payload["comment"] = payload.get("评语") or payload.get("commentary")
        return payload

    @field_validator("reasoning_chain", mode="before")
    @classmethod
    def normalize_reasoning_chain(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value]
        return value


class JuryPanel(BaseModel):
    """The three mandatory teacher perspectives from one LLM call."""

    model_config = ConfigDict(populate_by_name=True)

    strict_teacher: JurorAssessment = Field(alias="严师")
    lenient_teacher: JurorAssessment = Field(alias="宽师")
    neutral_teacher: JurorAssessment = Field(alias="中立")

    def as_dict(self) -> dict[TeacherName, JurorAssessment]:
        return {
            "严师": self.strict_teacher,
            "宽师": self.lenient_teacher,
            "中立": self.neutral_teacher,
        }


class JuryStructuredOutput(BaseModel):
    """Instructor/Pydantic-enforced structure for jury output."""

    model_config = ConfigDict(extra="ignore")

    dimension: str | None = None
    dimension_tier: Literal["low", "medium", "high"] | None = None
    # 维度级三师不再是必填：现在每个组件都要三师落点，维度级由组件加权推导，
    # 比让模型再给一组"整维印象分"更可追溯。旧输出带 jury 时仍然优先采用。
    jury: JuryPanel | None = None
    label: str | None = None
    distribution: dict[str, float] = Field(default_factory=dict)
    weak_points: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    narrative: str = Field(min_length=1)
    component_scores: list[ComponentScore] = Field(default_factory=list)

    @field_validator("distribution")
    @classmethod
    def validate_distribution(cls, value: dict[str, float]) -> dict[str, float]:
        for label, ratio in value.items():
            if not label:
                raise ValueError("distribution label must be non-empty")
            if ratio < 0:
                raise ValueError("distribution values must be non-negative")
        return value

    @field_validator("weak_points", mode="before")
    @classmethod
    def normalize_weak_points(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value]
        return value or []


class ScoringResult(BaseModel):
    """Stable jury/fusion scoring output."""

    model_config = ConfigDict(validate_assignment=True)

    dimension: str
    dimension_key: DimensionKey
    jury: dict[TeacherName, JurorAssessment] = Field(default_factory=dict)
    aggregated_score: float | None = Field(default=None, ge=0.0, le=100.0)
    # The three perspectives' arithmetic mean.  Kept for transparency only --
    # the authoritative dimension score is the component-based one.
    jury_mean: float | None = Field(default=None, ge=0.0, le=100.0)
    # Component scores are kept alongside the legacy aggregate so the profile
    # state can render the collection/scored phase without reconstructing the
    # agent response from mutable evidence cards.  This is the **effective**
    # set: components rescored this round plus components whose previous score
    # is carried forward because the model returned null for them.
    component_scores: dict[str, float] = Field(default_factory=dict)
    # 本轮模型真正重评的组件，以及沿用上一轮分数的组件。审计里要能区分
    # "这个分是刚评的"和"这个分是第 1 轮评的、这轮没动"。
    rescored_components: list[str] = Field(default_factory=list)
    carried_components: list[str] = Field(default_factory=list)
    # 逐组件三师落点：component -> {严师/宽师/中立: 该师对该组件的判断}。
    # 审计要能回答"这个组件为什么是 58 分"，只有综合分是答不出来的。
    component_jury: dict[str, dict[TeacherName, JurorAssessment]] = Field(default_factory=dict)
    # 每位老师按组件权重合出来的维度分，再取三人均值，就是维度三师均值。
    teacher_dimension_scores: dict[str, float] = Field(default_factory=dict)
    label: str | None = None
    distribution: dict[str, float] = Field(default_factory=dict)
    weak_points: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    radar_score: float | None = Field(default=None, ge=5.0, le=95.0)
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    unknown_reasons: list[str] = Field(default_factory=list)
    confidence_breakdown: dict[str, float] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    narrative: str = ""
    method: Literal["jury", "fusion"] = "jury"
    provenance: str = "jury"
    warnings: list[GuardrailWarning] = Field(default_factory=list)


async def score_dimension(
    dimension: DimensionKey,
    evidence_cards: list[EvidenceCard],
    *,
    router: ModelRouter | None = None,
    settings: Settings | None = None,
    force_llm: bool = False,
    llm_call: LLMCall | None = None,
) -> ScoringResult:
    """Score one dimension.

    Without an API key this returns **no score at all** for both flows.  A
    fabricated fallback score is worse than an empty radar axis: the user cannot
    tell a rule guess from a model judgement, and the two collection paths would
    stop being comparable.
    """

    cards = [card for card in evidence_cards if card.dimension == dimension]
    active_settings = settings or get_settings()
    if not cards:
        # No evidence means no score.  Asking the jury about an empty evidence
        # set only invites fabrication, and the guardrail would then have to
        # undo a number nobody could trace back to a quote.
        fusion = fuse_dimension(dimension, cards)
        return _result_from_fusion(fusion, cards, provenance="no_evidence")
    if not force_llm and llm_call is None and not _has_llm_key(active_settings):
        fusion = fuse_dimension(dimension, cards)
        return _result_from_fusion(fusion, cards, provenance="no_key")

    try:
        return _apply_score_guardrail(
            await _call_and_parse_jury(dimension, cards, router=router, settings=active_settings, llm_call=llm_call),
            cards,
        )
    except ValueError as exc:
        fusion = fuse_dimension(dimension, cards)
        result = _result_from_fusion(fusion, cards, provenance="rule_fallback")
        result.narrative = f"{result.narrative} LLM 调用未返回有效评审团 JSON，已回退规则融合（{exc}）。"
        return result


async def score_profile(
    evidence_cards: list[EvidenceCard],
    *,
    router: ModelRouter | None = None,
    settings: Settings | None = None,
    force_llm: bool = False,
    llm_call: LLMCall | None = None,
) -> dict[DimensionKey, ScoringResult]:
    """Score all seven dimensions."""

    results: dict[DimensionKey, ScoringResult] = {}
    for dimension in DIMENSION_LABELS:
        results[dimension] = await score_dimension(
            dimension,
            evidence_cards,
            router=router,
            settings=settings,
            force_llm=force_llm,
            llm_call=llm_call,
        )
    return results


def aggregate_jury_scores(scores: Sequence[float]) -> float:
    """Average the three perspectives inside the public 5-95 band.

    Median used to throw away the very information the three roles exist to
    produce: the spread between "only what the quote proves" and "what the
    intent could support".  The band is already shared, so a plain mean is both
    faithful and stable.

    The clamp is not cosmetic.  A jury answering 96/99/98 averages 97.67, which
    violates the ``radar_score`` contract and made the whole call fall back to
    rule fusion -- so the guardrail that should correct an unsupported high
    score never ran.
    """

    if len(scores) != 3:
        raise ValueError("three-teacher aggregation requires exactly three scores")
    return round(max(5.0, min(95.0, float(mean(scores)))), 2)


JURY_SIGNAL_WHITELIST: tuple[str, ...] = (
    "label", "selected", "free_text", "correct", "gave_up", "topic",
    "daily_minutes", "target_days", "herb_name", "expected_answer",
    "response_speed", "detail", "question_type", "evidence_type",
)


def _jury_card_payload(card: EvidenceCard) -> dict[str, Any]:
    """Only the fields a teacher may legitimately read.

    Rule-computed values (``answer_quality``, ``weight``, ``difficulty``, raw
    milliseconds) are deliberately excluded: the model copies them straight into
    its score instead of judging the evidence.

    轮次标注与质量分是例外——它们是"这条证据什么时候说的、说得有多实"的
    客观说明，让老师能按时间顺序看演化（同一件事第 1 轮含糊、第 5 轮具体，
    应该往上走分），而不是把五条证据当成并列的一次性陈述。
    """

    signal = card.objective_signal if isinstance(card.objective_signal, dict) else {}
    facts = {key: signal[key] for key in JURY_SIGNAL_WHITELIST if key in signal}
    turns = sorted({int(turn) for turn in (card.source_turn_ids or []) if isinstance(turn, int)})
    raw_quality = card.quality_factors or signal.get("quality_factors") or {}
    quality_score = None
    quality_tier = None
    try:
        if raw_quality:
            factors = QualityFactors.model_validate(raw_quality)
            quality_score = geometric_quality(factors)
            quality_tier = quality_tier_of(quality_score)
    except Exception:  # noqa: BLE001 - 坏结构不能拖垮整次评分
        quality_score = None
    return {
        "evidence_id": card.evidence_id,
        "turn_label": "、".join(f"第{turn}轮" for turn in turns) or "本轮",
        "turn_order": turns[0] if turns else 0,
        "component": card.component,
        "quote": card.quote,
        "normalized_claim": card.normalized_claim or card.fact,
        "evidence_group_id": card.evidence_group_id,
        "source_turn_ids": card.source_turn_ids,
        "evidence_type": signal.get("evidence_type") or getattr(card.tier, "value", str(card.tier)),
        "collection_status": card.collection_status,
        "semantic_relation": card.semantic_relation,
        "quality_factors": card.quality_factors,
        "quality_score": quality_score,
        "quality_tier": quality_tier,
        "objective_facts": facts,
    }


def _ordered_cards(cards: list[EvidenceCard]) -> list[EvidenceCard]:
    """按轮次先后排序：让老师看到的是演化序列，不是抽取顺序。"""

    def key(card: EvidenceCard) -> tuple[int, str]:
        turns = [int(turn) for turn in (card.source_turn_ids or []) if isinstance(turn, int)]
        return (min(turns) if turns else 0, card.evidence_id)

    return sorted(cards, key=key)


def _component_block(dimension: DimensionKey, component: str, cards: list[EvidenceCard]) -> str:
    """One component's own rubric plus its own evidence, and nothing else.

    Keeping siblings out of the block is the whole point: if the model can see
    the "measured_performance" wording while scoring "concept_foundation", the
    two grids collapse into one judgement.
    """

    rubric = COMPONENT_RUBRICS[dimension][component]
    kept, conflicts, _ = deduplicate_component_cards(cards)
    scorable = _ordered_cards([card for card in kept if card.semantic_relation != "conflict"])
    lines = [
        f"【组件：{rubric['label']}（{component}）】",
        f"  这个组件量什么：{rubric['definition']}",
        f"  低档（5–40）：{rubric['low']}",
        f"  中档（40–70）：{rubric['medium']}",
        f"  高档（70–95）：{rubric['high']}",
        f"  高档长这样：{rubric['example_high']}",
        f"  这样不算高档：{rubric['example_not_high']}",
        f"  分数怎么动：{rubric['numeric_hint']}",
    ]
    if scorable:
        lines.append(
            "  本组件证据（已按轮次先后排列；turn_label＝用户第几轮说的，"
            "quality_score＝这条证据本身的质量分（0–1，越低越不该当主要依据），"
            "quality_tier 是它的档位）："
            + json.dumps([_jury_card_payload(card) for card in scorable], ensure_ascii=False)
        )
    else:
        lines.append("  本组件证据：无。本组件 score 必须填 null，并在 missing 里写缺什么。")
    if conflicts:
        lines.append(
            "  本组件冲突证据："
            + json.dumps(
                [{"evidence_id": card.evidence_id, "quote": card.quote, "fact": card.fact} for card in conflicts],
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


def build_jury_messages(
    dimension: DimensionKey,
    evidence_cards: list[EvidenceCard],
) -> list[ChatMessage]:
    """Build the single prompt that asks for all three perspectives at once."""

    label = DIMENSION_LABELS[dimension]
    # 顶层证据清单也按轮次排：模型先读的是这一份，顺序不一致会让它把
    # "第 5 轮才说的"当成最早的信息。
    evidence_json = json.dumps(
        [_jury_card_payload(card) for card in _ordered_cards(evidence_cards)],
        ensure_ascii=False,
        indent=2,
    )
    allowed_low, allowed_high, allowed_reasons = allowed_score_interval(dimension, evidence_cards)
    interval_instruction = ""
    if dimension in {"knowledge_base", "pace", "motivation"} and allowed_reasons:
        interval_instruction = (
            f"本组证据的强制评分区间：[{allowed_low:g}, {allowed_high:g}]。"
            "三位老师都必须在区间内结合证据细节给出具体分，不得机械填写中点。"
            f"区间依据：{'；'.join(allowed_reasons)}。\n"
        )

    teacher_lines = "\n".join(
        f"{name}（{TEACHER_RULES[name]['stance']}）：{TEACHER_RULES[name]['rule']}"
        f"默认落点{TEACHER_RULES[name]['anchor']}。"
        for name in JURY_TEACHERS
    )
    landing_lines = "\n".join(
        f"  {tier}：" + "，".join(f"{name} {TIER_LANDING[tier][name]}" for name in JURY_TEACHERS)
        for tier in TIER_ORDER
    )
    blocks = [
        _component_block(dimension, component, [card for card in evidence_cards if card.component == component])
        for component in COMPONENTS[dimension]
    ]
    component_instruction = (
        "组件评分（逐组件独立判档；同一档之内，严师、宽师、中立各自给落点。每块只看自己的细则）：\n"
        + "\n\n".join(blocks)
        + "\n\n有 scorable 证据的组件必须给 tier，并给三师各自的 score（落在该档区间内）；"
        "没有证据的组件 jury 留空、score 填 null，missing 写缺什么，不要估算，"
        "也不要用同维其它组件的水平去补。所有冲突证据都必须保留并说明影响。\n"
        "本轮证据没变化的组件也可以照旧给分（系统会让新旧分数互相覆盖），"
        "但不要为了凑满五个组件去猜——返回 null 的组件，系统会沿用上一轮分数。\n"
        "每个组件都要给出三师各自的 score 和一句 reason（不超过 25 字，说清命中了细则里的哪一条）；"
        "三个分数允许不同，但都必须落在同一个 tier 的区间内。\n"
        "证据按轮次先后排列：后出现的证据如果让同一件事更具体或更确定，可以在档内往上落点；"
        "quality_score 低的证据（含糊、无细节）不要当主要依据。\n"
        "维度分不用你算：系统先对每个组件取三师落点平均，再按组件固定权重合成维度分。\n"
    )
    schema = {
        "dimension": label,
        "dimension_tier": "<low|medium|high>",
        "label": "<类别维填写已定义标签，否则 null>",
        "distribution": {},
        "weak_points": [],
        "evidence_refs": ["<证据编号>"],
        "narrative": "<自然语言画像叙述>",
        "component_scores": [
            {
                "component": "<组件 key>",
                "tier": "<low|medium|high>",
                "jury": {
                    "严师": {"score": "<该档区间内的数>", "reason": "<≤25字，引用证据里的词>"},
                    "宽师": {"score": "<该档区间内的数>", "reason": "<≤25字>"},
                    "中立": {"score": "<该档区间内的数>", "reason": "<≤25字>"},
                },
                "score": "<无证据填 null>",
                "missing": "<无证据时说明缺什么>",
                "evidence_refs": ["<证据编号>"],
                "conflict_handling": "<冲突处理>",
            }
        ],
    }
    return [
        {
            "role": "system",
            "content": (
                "你是「岐黄智学」学习画像三师会诊评分器。每个组件都要严师、宽师、中立三个视角分别给分。"
                "先判档（low/medium/high），再在该档的数字区间内给分；档位是三师共同的，分歧只体现在档内位置。"
                "规则层只提供客观证据，你只根据三档描述与证据判断。"
                "只输出 JSON，不要 Markdown，不要额外解释。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"评分维度：{label}\n"
                f"维度量表（只用于判 dimension_tier）：\n{ANCHOR_SCALES[dimension]}\n"
                "三档数字区间：low 5–40；medium 40–70；high 70–95。任何分数都必须落在所选档内。\n"
                f"三师默认落点：\n{landing_lines}\n"
                f"三师规则：\n{teacher_lines}\n"
                f"{interval_instruction}"
                f"证据卡（只含可读字段）：{evidence_json}\n"
                f"{component_instruction}"
                "聚合规则：组件分由本地取三师平均，维度分由本地按组件权重合成，你不要输出聚合分、方差或置信度。"
                "类别型维度禁造新标签；"
                "薄弱点必须来自错误证据卡的 label/topic/knowledge_point/weak_labels。\n"
                f"输出 JSON 结构示例（尖括号是占位说明，不是要照抄的数值）：{json.dumps(schema, ensure_ascii=False)}\n"
                # 五个组件各带三师落点，输出量比"整维三师"更大，很容易撞上
                # max_tokens 被截断成半截 JSON。所以逐字段给上限。
                "篇幅上限（超出会被截断，导致整份结果作废）："
                "每个组件里每位老师的 reason 不超过 25 字；"
                "missing 与 conflict_handling 各不超过 30 字；"
                "narrative 不超过 120 字。不要复述量表原文，不要逐条抄证据卡。"
            ),
        },
    ]


async def _call_and_parse_jury(
    dimension: DimensionKey,
    evidence_cards: list[EvidenceCard],
    *,
    router: ModelRouter | None,
    settings: Settings,
    llm_call: LLMCall | None,
) -> ScoringResult:
    messages = build_jury_messages(dimension, evidence_cards)
    if llm_call is not None:
        raw = await llm_call(messages)
    else:
        raw = await (router or ModelRouter(settings=settings)).chat(
            "profile_jury",
            messages,
            response_format={"type": "json_object"},
            temperature=0,
            # 评分结果受结构化 Schema、证据融合和分数护栏约束，默认无需
            # 额外的推理 token。保留模型路由配置，必要时可单独切回 Pro。
            thinking={"type": "disabled"},
            # 1200 装不下「五个组件各带三师落点」：实测四个维度都在 2700–4500
            # 字符处被截成半截 JSON，整份结果作废。2500 仍在下限之下，所以给 4000。
            # 提示词里已按字段限了字数，4000 是上限而非目标。
            max_tokens=4000,
        )
    if raw.startswith(MOCK_FALLBACK_PREFIX):
        raise ValueError(_mock_fallback_reason(raw))
    return _parse_jury_output(dimension, evidence_cards, raw)


def _component_teachers(
    component: str,
    item: ComponentScore,
    tier: str,
) -> dict[TeacherName, JurorAssessment]:
    """把某组件的三师落点收成正式判断，档位统一后再比。"""

    teachers: dict[TeacherName, JurorAssessment] = {}
    for name, assessment in item.jury.items():
        if name not in JURY_TEACHERS or assessment.score is None:
            continue
        score = clamp_to_band(tier, assessment.score)
        reason = assessment.reason.strip() or f"按{TIER_LANDING[tier][name]}落在 {score:g} 分。"
        teachers[name] = JurorAssessment(score=score, reasoning_chain=[reason], comment=reason)
    return teachers


def _teacher_dimension_scores(
    dimension: DimensionKey,
    component_jury: dict[str, dict[TeacherName, JurorAssessment]],
) -> dict[str, float]:
    """每位老师自己那一列，按组件固定权重合成维度分。

    这一步让"三师均值"和"维度分"用同一套权重：先各自加权，再取三人平均，
    而不是拿一个笼统的整维印象分去代表三师。
    """

    weights = COMPONENT_WEIGHTS[dimension]
    result: dict[str, float] = {}
    for name in JURY_TEACHERS:
        pairs = [
            (teachers[name].score, float(weights.get(component, 1.0)))
            for component, teachers in component_jury.items()
            if name in teachers
        ]
        total_weight = sum(weight for _, weight in pairs)
        if not pairs or total_weight <= 0:
            continue
        result[name] = round(sum(score * weight for score, weight in pairs) / total_weight, 2)
    return result


def _derive_dimension_jury(
    dimension: DimensionKey,
    component_jury: dict[str, dict[TeacherName, JurorAssessment]],
    teacher_dimension_scores: dict[str, float],
) -> dict[TeacherName, JurorAssessment]:
    """维度级三师由组件推导，不再额外问模型一遍。"""

    panel: dict[TeacherName, JurorAssessment] = {}
    for name in JURY_TEACHERS:
        score = teacher_dimension_scores.get(name)
        if score is None:
            continue
        chain = [
            f"{DIMENSION_COMPONENT_LABELS[dimension][component]} {teachers[name].score:g} 分"
            for component, teachers in component_jury.items()
            if name in teachers
        ]
        panel[name] = JurorAssessment(
            score=score,
            reasoning_chain=chain[:5] or [f"按组件权重合成 {score:g} 分。"],
            comment=f"由 {len(chain)} 个组件按固定权重合成",
        )
    return panel


def _parse_jury_output(
    dimension: DimensionKey,
    evidence_cards: list[EvidenceCard],
    raw: str,
) -> ScoringResult:
    payload = JuryStructuredOutput.model_validate_json(_extract_json_object(raw))
    canonical_by_name = {
        name: key
        for key in COMPONENTS.get(dimension, ())
        for name in (key, DIMENSION_COMPONENT_LABELS[dimension][key])
    }
    # Models sometimes echo the human-readable rubric label instead of the
    # stable component key.  Normalise both forms before matching; otherwise
    # valid scores are silently discarded and the dimension never leaves the
    # collecting phase.
    by_component = {
        canonical_by_name[item.component]: item
        for item in payload.component_scores
        if item.component in canonical_by_name
    }
    dimension_tier = payload.dimension_tier if payload.dimension_tier in TIER_BANDS else "medium"
    # 上一轮已经评出来的组件分：本轮模型对未涉及组件返回 null 时，这些分数
    # 原样沿用。否则"这轮只重评了 1 个组件"会把维度分打回只剩 1 个组件的
    # 加权均值，分数每轮乱跳。
    carried_scores: dict[str, float] = {}
    for card in evidence_cards:
        component = card.component or ""
        existing = card.objective_signal.get("component_score") if isinstance(card.objective_signal, dict) else None
        if component in COMPONENTS.get(dimension, ()) and isinstance(existing, (int, float)):
            carried_scores[component] = round(float(existing), 2)
    component_scores: dict[str, float] = {}
    component_jury: dict[str, dict[TeacherName, JurorAssessment]] = {}
    # 提示词明确要求「无证据的组件 score 填 null」，但真实模型仍会给一个中性
    # 50 分（实测 learning_goal 的 cross_turn_stability：三师 40/60/50，同时还
    # 写了 missing "缺多轮独立一致表达"）。采信它会把覆盖度从 3/5 抬到 5/5，
    # 维度分凭空多出一个没人能复核的估分。
    no_evidence = {
        component
        for component in COMPONENTS.get(dimension, ())
        if not any(
            card.component == component and card.semantic_relation != "conflict"
            for card in evidence_cards
        )
    }
    ignored_no_evidence: list[str] = []
    for key, item in by_component.items():
        if key in no_evidence and key not in carried_scores:
            ignored_no_evidence.append(key)
            continue
        tier = item.tier if item.tier in TIER_BANDS else dimension_tier
        teachers = _component_teachers(key, item, tier)
        if len(teachers) >= 2:
            # 组件分 = 三师落点平均。判档是三师共同的，分歧只体现在档内位置，
            # 所以平均值本身就落在同一档里，不会出现"严师低档、宽师高档"的
            # 混合档位。
            component_jury[key] = teachers
            component_scores[key] = round(mean([teacher.score for teacher in teachers.values()]), 2)
            continue
        if item.score is None:
            # A null score means "not collected", never "scored zero".
            continue
        component_scores[key] = clamp_to_band(tier, item.score)
    rescored_components = sorted(component_scores)
    carried_components = sorted(set(carried_scores) - set(component_scores))
    for card in evidence_cards:
        component = card.component or ""
        scored = component_scores.get(component)
        if scored is not None:
            item = by_component.get(component)
            # 组件级 reason 模型经常留空（它把理由写进了三师各自的 reason），
            # 审计里就会出现"19.3 分"却没有一句解释。这里用三师理由兜底，
            # 保证每个组件分都能回答"为什么是这个分"。
            reason = (item.reason if item else "") or ""
            if not reason.strip():
                teachers = component_jury.get(component) or {}
                reason = "；".join(
                    f"{name}：{teacher.comment}"
                    for name, teacher in teachers.items()
                    if teacher.comment
                )
            card.objective_signal = {
                **card.objective_signal,
                "component_score": scored,
                # 沿用分的组件本轮没有新理由，保留上一轮写下的那句；
                # 用空值覆盖会让审计变成"有分无据"。
                **(
                    {
                        "component_score_reason": reason or None,
                        "component_conflict_handling": item.conflict_handling if item else None,
                    }
                    if (item is not None or component in component_jury)
                    else {}
                ),
            }
    # 有效分 = 本轮重评的组件 + 沿用的旧分。维度分、覆盖度都按这份算。
    effective_scores: dict[str, float] = {**carried_scores, **component_scores}
    fusion = fuse_dimension(dimension, evidence_cards)
    outcome = dimension_score(dimension, effective_scores)
    teacher_dimension_scores = _teacher_dimension_scores(dimension, component_jury)
    jury = payload.jury.as_dict() if payload.jury else _derive_dimension_jury(dimension, component_jury, teacher_dimension_scores)
    scores = [jury[teacher].score for teacher in JURY_TEACHERS if teacher in jury]
    aggregated = aggregate_jury_scores(scores) if len(scores) == 3 else None
    has_components = any(card.component for card in evidence_cards)
    # Component-based score is authoritative when component cards exist; the
    # three-teacher mean is only a fallback for legacy cards without attribution.
    final_score = outcome["score"] if has_components else aggregated
    unknown_reasons = list(fusion.unknown_reasons)
    if has_components and outcome["score"] is None:
        unknown_reasons.append(
            f"覆盖度 {outcome['coverage']:.0%} 未达 60%，组件已收集 {len(effective_scores)}/5，评分暂缓"
        )
    if carried_components:
        unknown_reasons.append(
            f"本轮重评 {len(rescored_components)} 个组件，其余 {len(carried_components)} 个沿用上一轮分数"
        )
    if ignored_no_evidence:
        labels = "、".join(DIMENSION_COMPONENT_LABELS[dimension][key] for key in ignored_no_evidence)
        unknown_reasons.append(
            f"{labels}这 {len(ignored_no_evidence)} 项本轮没有证据，模型给的估分未采信，按未采集处理"
        )
    return ScoringResult(
        dimension=DIMENSION_LABELS[dimension],
        dimension_key=dimension,
        jury=jury,
        aggregated_score=final_score,
        jury_mean=aggregated,
        # 有效分：本轮重评 + 上轮沿用。维度分就是按这份算出来的。
        component_scores=effective_scores,
        rescored_components=rescored_components,
        carried_components=carried_components,
        component_jury=component_jury,
        teacher_dimension_scores=teacher_dimension_scores,
        # Category distributions are evidence votes, never LLM-created labels.
        label=fusion.label if fusion.label is not None else payload.label,
        distribution=fusion.distribution if fusion.distribution else payload.distribution,
        weak_points=payload.weak_points,
        # Confidence now comes from the evidence formula alone.  Teacher spread
        # is expressed through the band they share, not through a second
        # penalty multiplier.
        confidence=fusion.confidence,
        radar_score=final_score,
        coverage=outcome["coverage"] if has_components else fusion.coverage,
        unknown_reasons=unknown_reasons,
        confidence_breakdown=fusion.confidence_breakdown,
        evidence_refs=_evidence_refs_from_payload(payload.evidence_refs, evidence_cards),
        narrative=payload.narrative,
        method="jury",
    )


def _apply_score_guardrail(result: ScoringResult, evidence_cards: list[EvidenceCard]) -> ScoringResult:
    original_score = result.aggregated_score
    guarded = apply_guardrail(
        result.dimension_key,
        evidence_cards=evidence_cards,
        score=result.aggregated_score,
        label=result.label,
        distribution=result.distribution,
        weak_points=result.weak_points,
    )
    result.aggregated_score = guarded.score
    result.radar_score = guarded.score
    result.label = guarded.label
    result.distribution = guarded.distribution
    result.weak_points = guarded.weak_points
    result.warnings = guarded.warnings
    if guarded.score != original_score and guarded.score is not None:
        result.narrative = f"{result.narrative} 护栏已将最终分纠偏为 {guarded.score:.1f}。".strip()
    return result


def _result_from_fusion(
    fusion: FusionResult,
    evidence_cards: list[EvidenceCard],
    *,
    provenance: str = "fusion",
) -> ScoringResult:
    guarded = apply_guardrail(
        fusion.dimension,
        evidence_cards=evidence_cards,
        score=fusion.score,
        label=fusion.label,
        distribution=fusion.distribution,
        weak_points=[fusion.label] if fusion.dimension == "weak_points" and fusion.label else [],
    )
    narrative = fusion.narrative
    if guarded.score != fusion.score and guarded.score is not None:
        narrative = f"{narrative} 护栏已将最终分纠偏为 {guarded.score:.1f}。"
    component_scores = {
        card.component: float(card.objective_signal["component_score"])
        for card in evidence_cards
        if card.component
        and isinstance(card.objective_signal.get("component_score"), (int, float))
        and 5 <= float(card.objective_signal["component_score"]) <= 95
    }
    no_key = provenance == "no_key"
    if no_key:
        # No model configured -> no score at all.  A rule guess dressed up as a
        # score is indistinguishable from a real judgement downstream.
        # Confidence is NOT zeroed: it measures how much evidence was collected,
        # which is a local fact.  Zeroing it made the whole profile collapse to
        # "待补充" and silently dropped evidence-derived weak points.
        narrative = f"{fusion.dimension_label}未配置评分模型，本次不出分。"
    return ScoringResult(
        dimension=fusion.dimension_label,
        dimension_key=fusion.dimension,
        jury={},
        aggregated_score=None if no_key else guarded.score,
        component_scores={} if no_key else component_scores,
        label=None if no_key else guarded.label,
        distribution={} if no_key else guarded.distribution,
        weak_points=[] if no_key else guarded.weak_points,
        confidence=fusion.confidence,
        radar_score=None if no_key else fusion.radar_score,
        coverage=fusion.coverage,
        unknown_reasons=[*fusion.unknown_reasons, "no_key"] if no_key else fusion.unknown_reasons,
        confidence_breakdown={} if no_key else fusion.confidence_breakdown,
        evidence_refs=fusion.evidence_refs,
        narrative=narrative,
        method="fusion",
        provenance=provenance,
        warnings=guarded.warnings,
    )


def _evidence_refs_from_payload(refs: list[str], evidence_cards: list[EvidenceCard]) -> list[str]:
    fallback = [evidence_ref(card, index) for index, card in enumerate(evidence_cards, start=1)]
    if not refs:
        return fallback
    known = set(fallback)
    filtered = [ref for ref in refs if ref in known]
    return filtered or fallback


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    return match.group(0) if match else stripped


def _has_llm_key(settings: Settings) -> bool:
    return bool(_non_empty(settings.deepseek_api_key) or _non_empty(settings.zhipu_api_key))


def _non_empty(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _mock_fallback_reason(raw: str) -> str:
    match = re.search(r"\[FallbackReason:([^\]]+)\]", raw)
    if match:
        return f"router fell back to mock response after {match.group(1)}"
    return "router fell back to mock response"


__all__ = [
    "ANCHOR_SCALES",
    "JURY_TEACHERS",
    "TEACHER_RULES",
    "TIER_LANDING",
    "JurorAssessment",
    "ScoringResult",
    "aggregate_jury_scores",
    "build_jury_messages",
    "score_dimension",
    "score_profile",
]
