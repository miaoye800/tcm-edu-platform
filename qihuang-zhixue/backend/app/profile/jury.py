"""Three-teacher jury scoring for learning-profile dimensions."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from statistics import median
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import Settings, get_settings
from app.llm.clients import ChatMessage
from app.llm.router import ModelRouter
from app.profile.anchors import SCORE_ANCHOR_INTERVALS
from app.profile.fusion import FusionResult, evidence_ref, fuse_dimension
from app.profile.component_model import COMPONENTS, COMPONENT_RUBRICS, DIMENSION_COMPONENT_LABELS, ComponentScore, deduplicate_component_cards
from app.profile.guardrail import GuardrailWarning, allowed_score_interval, apply_guardrail
from app.profile.schema import DIMENSION_LABELS, DimensionKey, EvidenceCard, evidence_card_to_json_dict

TeacherName = Literal["严师", "宽师", "中立"]
LLMCall = Callable[[Sequence[ChatMessage]], Awaitable[str]]

JURY_TEACHERS: tuple[TeacherName, ...] = ("严师", "宽师", "中立")
JURY_VARIANCE_THRESHOLD = 120.0
JURY_CONFIDENCE_SCALE = 15.0
MOCK_FALLBACK_PREFIX = "[MOCK:"


ANCHOR_SCALES: dict[DimensionKey, str] = {
    "knowledge_base": (
        "知识基础（0-100）：0-20 药名认不全，性味归经空白；21-40 认得常见药名，功效模糊；"
        "41-60 掌握常用药功效，配伍薄弱；61-80 功效+配伍较熟，能应对多数病例；"
        "81-100 配伍加减熟练、剂量精准。基础自评区间："
        + "；".join(
            f"{label} [{interval[0]:g}, {interval[1]:g}]"
            for label, interval in SCORE_ANCHOR_INTERVALS["knowledge_base"]["base_level"].items()
        )
    ),
    "motivation": (
        "动机强度（0-100）：0-20 被动应付，几乎无自驱；21-40 需外部持续推动；"
        "41-60 有意愿，需目标牵引；61-80 主动性强，能自我安排；81-100 高度自驱，主动找挑战。"
        "动机标签区间："
        + "；".join(
            f"{label} [{interval[0]:g}, {interval[1]:g}]"
            for label, interval in SCORE_ANCHOR_INTERVALS["motivation"]["label"].items()
        )
    ),
    "pace": (
        "学习进度节奏（0-100，慢到快）："
        + "；".join(
            f"每日时长 {label} -> [{interval[0]:g}, {interval[1]:g}]"
            for label, interval in SCORE_ANCHOR_INTERVALS["pace"]["daily_minutes"].items()
        )
        + "；"
        + "；".join(
            f"目标周期 {label} -> [{interval[0]:g}, {interval[1]:g}]"
            for label, interval in SCORE_ANCHOR_INTERVALS["pace"]["target_days"].items()
        )
    ),
    "learning_goal": "类别型，不打数值分，只能贴标签：应试提分 / 临床实用 / 兴趣科普 / 学术深造。",
    "learning_style": "类别型，不打数值分，只能贴标签：视觉型 / 文字型 / 记忆(口诀)型。",
    "resource_preference": "类别/分布型，只能使用：知识图谱 / 拟人化短剧 / 汤头歌诀 / 医案文选。",
    "weak_points": "薄弱点列表/热区，必须来自错误证据卡中的类目+知识点标签，带出现次数为强度。",
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
    jury: JuryPanel
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
    # Component scores are kept alongside the legacy aggregate so the profile
    # state can render the collection/scored phase without reconstructing the
    # agent response from mutable evidence cards.
    component_scores: dict[str, float] = Field(default_factory=dict)
    label: str | None = None
    distribution: dict[str, float] = Field(default_factory=dict)
    weak_points: list[str] = Field(default_factory=list)
    variance: float = Field(default=0.0, ge=0.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    radar_score: float | None = Field(default=None, ge=5.0, le=95.0)
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    unknown_reasons: list[str] = Field(default_factory=list)
    confidence_breakdown: dict[str, float] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    narrative: str = ""
    method: Literal["jury", "fusion"] = "jury"
    provenance: str = "jury"
    self_check_sampled: bool = False
    warnings: list[GuardrailWarning] = Field(default_factory=list)


async def score_dimension(
    dimension: DimensionKey,
    evidence_cards: list[EvidenceCard],
    *,
    router: ModelRouter | None = None,
    settings: Settings | None = None,
    force_llm: bool = False,
    llm_call: LLMCall | None = None,
    variance_threshold: float = JURY_VARIANCE_THRESHOLD,
) -> ScoringResult:
    """Score one dimension.

    Without an API key, this skips jury scoring and returns formula-based rules
    scoring, matching the MOCK branch required by the scoring spec.
    """

    cards = [card for card in evidence_cards if card.dimension == dimension]
    active_settings = settings or get_settings()
    if not force_llm and llm_call is None and not _has_llm_key(active_settings):
        fusion = fuse_dimension(dimension, cards)
        return _result_from_fusion(fusion, cards, provenance="no_key_rule_fallback")

    try:
        first = await _call_and_parse_jury(dimension, cards, router=router, settings=active_settings, llm_call=llm_call)
    except ValueError as exc:
        fusion = fuse_dimension(dimension, cards)
        result = _result_from_fusion(fusion, cards, provenance="rule_fallback")
        result.narrative = f"{result.narrative} LLM 调用未返回有效评审团 JSON，已回退规则融合（{exc}）。"
        return result
    if first.variance > variance_threshold:
        try:
            second = await _call_and_parse_jury(
                dimension,
                cards,
                router=router,
                settings=active_settings,
                llm_call=llm_call,
                self_check=True,
            )
        except ValueError as exc:
            fusion = fuse_dimension(dimension, cards)
            result = _result_from_fusion(fusion, cards, provenance="rule_fallback")
            result.narrative = f"{result.narrative} LLM 自检未返回有效评审团 JSON，已回退规则融合（{exc}）。"
            return result
        selected = second if second.variance <= first.variance else first
        selected.self_check_sampled = True
        return _apply_score_guardrail(selected, cards)
    return _apply_score_guardrail(first, cards)


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
    """Aggregate by dropping extremes and taking the middle value."""

    if len(scores) != 3:
        raise ValueError("three-teacher aggregation requires exactly three scores")
    return round(float(median(scores)), 2)


def jury_variance(scores: Sequence[float]) -> float:
    """Population variance of the three teacher scores."""

    if not scores:
        return 0.0
    mean = sum(scores) / len(scores)
    return round(sum((score - mean) ** 2 for score in scores) / len(scores), 2)


def confidence_from_variance(variance: float) -> float:
    """Map three-teacher disagreement to confidence."""

    return round(max(0.0, min(1.0, 1.0 / (1.0 + max(variance, 0.0) / JURY_CONFIDENCE_SCALE))), 4)


def build_jury_messages(
    dimension: DimensionKey,
    evidence_cards: list[EvidenceCard],
    *,
    self_check: bool = False,
) -> list[ChatMessage]:
    """Build the single prompt that asks for all three perspectives at once."""

    label = DIMENSION_LABELS[dimension]
    evidence_json = json.dumps(
        [evidence_card_to_json_dict(card) for card in evidence_cards],
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
    extra = "这是一次自洽补采：请重新独立评估，避免照抄前次答案。" if self_check else ""
    component_instruction = ""
    if any(card.component for card in evidence_cards):
        component_input: list[dict[str, Any]] = []
        for component in COMPONENTS[dimension]:
            cards = [card for card in evidence_cards if card.component == component]
            if not cards:
                continue
            kept, conflicts, dropped_ids = deduplicate_component_cards(cards)
            component_input.append({
                "key": component,
                "weight": COMPONENT_RUBRICS[dimension][component]["weight"],
                "rubric": COMPONENT_RUBRICS[dimension][component],
                "deduplicated_evidence": [
                    {
                        "evidence_id": card.evidence_id,
                        "quote": card.quote,
                        "normalized_claim": card.normalized_claim or card.fact,
                        "evidence_group_id": card.evidence_group_id,
                        "source_turn_ids": card.source_turn_ids,
                        "evidence_type": card.objective_signal.get("evidence_type"),
                        "semantic_relation": card.semantic_relation,
                    }
                    for card in kept
                    if card.semantic_relation != "conflict"
                ],
                "conflicts": [
                    {"evidence_id": card.evidence_id, "quote": card.quote, "reason": card.fact}
                    for card in conflicts
                ],
                "source_groups": sorted({card.evidence_group_id for card in [*kept, *conflicts] if card.evidence_group_id}),
                "deduplicated_evidence_ids": dropped_ids,
            })
        component_instruction = (
            f"本维度组件量表：{json.dumps(COMPONENT_RUBRICS[dimension], ensure_ascii=False)}\n"
            f"组件评分输入：{json.dumps(component_input, ensure_ascii=False)}\n"
            "这是完成评分期：必须逐组件输出 component_scores，每项含 component、score(5..95)、"
            "evidence_refs、reason、conflict_handling；所有冲突证据都必须保留并说明影响。\n"
        )
    schema = {
        "dimension": label,
        "jury": {
            "严师": {"score": 62, "reasoning_chain": ["证据1说明...", "按锚点落在..."], "comment": "简短评语"},
            "宽师": {"score": 72, "reasoning_chain": ["证据2说明..."], "comment": "简短评语"},
            "中立": {"score": 66, "reasoning_chain": ["综合证据..."], "comment": "简短评语"},
        },
        "aggregated_score": 66,
        "variance": 16.7,
        "confidence": 0.45,
        "label": "仅类别维填写已定义标签，数值维可为 null",
        "distribution": {"仅类别维填写": 1.0},
        "weak_points": ["仅薄弱点维填写可追溯标签"],
        "evidence_refs": ["抓药实战#1"],
        "narrative": "自然语言画像叙述",
        "component_scores": [{"component": "组件名", "score": 72, "evidence_refs": ["ev-1"], "reason": "可验证理由", "conflict_handling": "冲突处理"}],
    }
    return [
        {
            "role": "system",
            "content": (
                "你是「岐黄智学」学习画像三师会诊评分器。必须一次性同时输出严师、宽师、中立三视角，"
                "规则层只提供客观证据，你根据锚点量表判断。只输出 JSON，不要 Markdown，不要额外解释。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"评分维度：{label}\n"
                f"锚点量表：{ANCHOR_SCALES[dimension]}\n"
                f"{interval_instruction}"
                f"证据卡：{evidence_json}\n"
                f"{component_instruction}"
                "聚合规则：三师分数去极端取中位；方差越大置信度越低；类别型维度禁造新标签；"
                "薄弱点必须来自错误证据卡的 label/topic/knowledge_point/weak_labels。"
                "每位老师必须给 score、reasoning_chain、comment。类别维还要给 label 和 distribution，薄弱点维还要给 weak_points。\n"
                f"{extra}\n"
                f"输出 JSON 结构示例：{json.dumps(schema, ensure_ascii=False)}"
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
    self_check: bool = False,
) -> ScoringResult:
    messages = build_jury_messages(dimension, evidence_cards, self_check=self_check)
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
            max_tokens=1200,
        )
    if raw.startswith(MOCK_FALLBACK_PREFIX):
        raise ValueError(_mock_fallback_reason(raw))
    return _parse_jury_output(dimension, evidence_cards, raw, self_check_sampled=self_check)


def _parse_jury_output(
    dimension: DimensionKey,
    evidence_cards: list[EvidenceCard],
    raw: str,
    *,
    self_check_sampled: bool = False,
) -> ScoringResult:
    payload = JuryStructuredOutput.model_validate_json(_extract_json_object(raw))
    canonical_by_name = {
        name: key
        for key in COMPONENTS.get(dimension, ())
        for name in (key, DIMENSION_COMPONENT_LABELS[dimension][key])
    }
    if payload.component_scores:
        # Models sometimes echo the human-readable rubric label instead of
        # the stable component key. Normalize both forms before matching;
        # otherwise valid scores are silently discarded and the dimension
        # remains permanently in the collecting phase.
        by_component = {
            canonical_by_name[item.component]: item
            for item in payload.component_scores
            if item.component in canonical_by_name
        }
        for card in evidence_cards:
            scored = by_component.get(card.component or "")
            if scored is not None:
                card.objective_signal = {
                    **card.objective_signal,
                    "component_score": scored.score,
                    "component_score_reason": scored.reason,
                    "component_conflict_handling": scored.conflict_handling,
                }
    fusion = fuse_dimension(dimension, evidence_cards)
    component_scores = {
        canonical_by_name[item.component]: item.score
        for item in payload.component_scores
        if item.component in canonical_by_name
    }
    jury = payload.jury.as_dict()
    scores = [jury[teacher].score for teacher in JURY_TEACHERS]
    aggregated = aggregate_jury_scores(scores)
    variance = jury_variance(scores)
    return ScoringResult(
        dimension=DIMENSION_LABELS[dimension],
        dimension_key=dimension,
        jury=jury,
        aggregated_score=fusion.score if any(card.component for card in evidence_cards) else aggregated,
        component_scores=component_scores,
        # Category distributions are evidence votes, never LLM-created labels.
        label=fusion.label if fusion.label is not None else payload.label,
        distribution=fusion.distribution if fusion.distribution else payload.distribution,
        weak_points=payload.weak_points,
        variance=variance,
        # Jury agreement can only reduce confidence; source diversity and tier
        # remain the responsibility of deterministic fusion.
        confidence=min(fusion.confidence, confidence_from_variance(variance)),
        radar_score=fusion.radar_score,
        coverage=fusion.coverage,
        unknown_reasons=fusion.unknown_reasons,
        confidence_breakdown=fusion.confidence_breakdown,
        evidence_refs=_evidence_refs_from_payload(payload.evidence_refs, evidence_cards),
        narrative=payload.narrative,
        method="jury",
        self_check_sampled=self_check_sampled,
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
    return ScoringResult(
        dimension=fusion.dimension_label,
        dimension_key=fusion.dimension,
        jury={},
        aggregated_score=guarded.score,
        component_scores=component_scores,
        label=guarded.label,
        distribution=guarded.distribution,
        weak_points=guarded.weak_points,
        variance=fusion.variance,
        confidence=fusion.confidence,
        radar_score=fusion.radar_score,
        coverage=fusion.coverage,
        unknown_reasons=fusion.unknown_reasons,
        confidence_breakdown=fusion.confidence_breakdown,
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
    "JURY_TEACHERS",
    "JURY_VARIANCE_THRESHOLD",
    "JurorAssessment",
    "ScoringResult",
    "aggregate_jury_scores",
    "build_jury_messages",
    "confidence_from_variance",
    "jury_variance",
    "score_dimension",
    "score_profile",
]
