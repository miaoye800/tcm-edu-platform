"""Three-teacher jury scoring for learning-profile dimensions."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from statistics import median
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings, get_settings
from app.llm.clients import ChatMessage
from app.llm.router import ModelRouter
from app.profile.fusion import FusionResult, evidence_ref, fuse_dimension
from app.profile.guardrail import GuardrailWarning, apply_guardrail
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
        "81-100 配伍加减熟练、剂量精准。"
    ),
    "motivation": (
        "动机强度（0-100）：0-20 被动应付，几乎无自驱；21-40 需外部持续推动；"
        "41-60 有意愿，需目标牵引；61-80 主动性强，能自我安排；81-100 高度自驱，主动找挑战。"
    ),
    "pace": (
        "学习进度节奏（0-100，慢到快）：0-30 慢：每天 <15min / 目标周期宽松；"
        "31-70 中：每天 15-40min；71-100 快：每天 >40min / 目标周期紧。"
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
    reasoning_chain: list[str] = Field(default_factory=list)
    comment: str = ""


class ScoringResult(BaseModel):
    """Stable jury/fusion scoring output."""

    model_config = ConfigDict(validate_assignment=True)

    dimension: str
    dimension_key: DimensionKey
    jury: dict[TeacherName, JurorAssessment] = Field(default_factory=dict)
    aggregated_score: float | None = Field(default=None, ge=0.0, le=100.0)
    label: str | None = None
    distribution: dict[str, float] = Field(default_factory=dict)
    variance: float = Field(default=0.0, ge=0.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    narrative: str = ""
    method: Literal["jury", "fusion"] = "jury"
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
        return _result_from_fusion(fusion, cards)

    try:
        first = await _call_and_parse_jury(dimension, cards, router=router, settings=active_settings, llm_call=llm_call)
    except ValueError as exc:
        if llm_call is not None or force_llm:
            raise
        fusion = fuse_dimension(dimension, cards)
        result = _result_from_fusion(fusion, cards)
        result.narrative = f"{result.narrative} LLM 调用未返回有效评审团 JSON，已回退规则融合（{exc}）。"
        return result
    if first.variance > variance_threshold:
        second = await _call_and_parse_jury(
            dimension,
            cards,
            router=router,
            settings=active_settings,
            llm_call=llm_call,
            self_check=True,
        )
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
    extra = "这是一次自洽补采：请重新独立评估，避免照抄前次答案。" if self_check else ""
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
        "evidence_refs": ["抓药实战#1"],
        "narrative": "自然语言画像叙述",
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
                f"证据卡：{evidence_json}\n"
                "聚合规则：三师分数去极端取中位；方差越大置信度越低；类别型维度禁造新标签；"
                "每位老师必须给 score、reasoning_chain、comment。\n"
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
            temperature=0,
            thinking={"type": "enabled"},
            reasoning_effort="high",
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
    payload = _parse_json_object(raw)
    if not isinstance(payload, dict):
        raise ValueError("jury output must be a JSON object")
    jury = _parse_jury(payload.get("jury"))
    scores = [jury[teacher].score for teacher in JURY_TEACHERS]
    aggregated = aggregate_jury_scores(scores)
    variance = jury_variance(scores)
    return ScoringResult(
        dimension=DIMENSION_LABELS[dimension],
        dimension_key=dimension,
        jury=jury,
        aggregated_score=aggregated,
        variance=variance,
        confidence=confidence_from_variance(variance),
        evidence_refs=_evidence_refs_from_payload(payload, evidence_cards),
        narrative=str(payload.get("narrative") or ""),
        method="jury",
        self_check_sampled=self_check_sampled,
    )


def _parse_jury(value: Any) -> dict[TeacherName, JurorAssessment]:
    if not isinstance(value, dict):
        raise ValueError("jury field is required")
    parsed: dict[TeacherName, JurorAssessment] = {}
    for teacher in JURY_TEACHERS:
        raw_item = value.get(teacher)
        if not isinstance(raw_item, dict):
            raise ValueError(f"jury.{teacher} is required")
        reasoning = raw_item.get("reasoning_chain") or raw_item.get("推理链") or raw_item.get("reasoning") or []
        if isinstance(reasoning, str):
            reasoning = [reasoning]
        parsed[teacher] = JurorAssessment(
            score=float(raw_item.get("score")),
            reasoning_chain=[str(item) for item in reasoning],
            comment=str(raw_item.get("comment") or raw_item.get("评语") or ""),
        )
    return parsed


def _apply_score_guardrail(result: ScoringResult, evidence_cards: list[EvidenceCard]) -> ScoringResult:
    original_score = result.aggregated_score
    guarded = apply_guardrail(
        result.dimension_key,
        evidence_cards=evidence_cards,
        score=result.aggregated_score,
        label=result.label,
        distribution=result.distribution,
    )
    result.aggregated_score = guarded.score
    result.label = guarded.label
    result.distribution = guarded.distribution
    result.warnings = guarded.warnings
    if guarded.score != original_score and guarded.score is not None:
        result.narrative = f"{result.narrative} 护栏已将最终分纠偏为 {guarded.score:.1f}。".strip()
    return result


def _result_from_fusion(fusion: FusionResult, evidence_cards: list[EvidenceCard]) -> ScoringResult:
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
    return ScoringResult(
        dimension=fusion.dimension_label,
        dimension_key=fusion.dimension,
        jury={},
        aggregated_score=guarded.score,
        label=guarded.label,
        distribution=guarded.distribution,
        variance=fusion.variance,
        confidence=fusion.confidence,
        evidence_refs=fusion.evidence_refs,
        narrative=narrative,
        method="fusion",
        warnings=guarded.warnings,
    )


def _evidence_refs_from_payload(payload: dict[str, Any], evidence_cards: list[EvidenceCard]) -> list[str]:
    refs = payload.get("evidence_refs")
    if isinstance(refs, list) and all(isinstance(item, str) for item in refs):
        return refs
    return [evidence_ref(card, index) for index, card in enumerate(evidence_cards, start=1)]


def _parse_json_object(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    return json.loads(match.group(0) if match else stripped)


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
