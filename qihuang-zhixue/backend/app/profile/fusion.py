"""Weighted evidence fusion for learning-profile scoring.

The rule layer never invents a subjective judgment. It only converts objective
signals on EvidenceCard records into numeric evidence values or category votes,
then applies the scoring formula from ``画像评分细则.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from app.profile.schema import CATEGORY_DIMENSIONS, DIMENSION_LABELS, DimensionKey, EvidenceCard

HALF_LIFE_DAYS: dict[DimensionKey, float | None] = {
    "knowledge_base": 30.0,
    "learning_goal": None,
    "learning_style": None,
    "weak_points": 30.0,
    "pace": 30.0,
    "motivation": 30.0,
    "resource_preference": None,
}

ALLOWED_LABELS: dict[DimensionKey, tuple[str, ...]] = {
    "learning_goal": ("应试提分", "临床实用", "兴趣科普", "学术深造"),
    "learning_style": ("视觉型", "文字型", "记忆(口诀)型"),
    "motivation": ("内驱", "外驱", "内外混合", "被动"),
    "resource_preference": ("知识图谱", "拟人化短剧", "汤头歌诀", "医案文选"),
}

DEFAULT_CONFIDENCE_WEIGHT_K = 3.0
NUMERIC_VARIANCE_NORMALIZER = 2500.0


@dataclass(frozen=True)
class FusionResult:
    """Result of formula-based scoring for a single profile dimension."""

    dimension: DimensionKey
    dimension_label: str
    score: float | None
    label: str | None
    distribution: dict[str, float]
    confidence: float
    variance: float
    evidence_refs: list[str]
    narrative: str
    method: str = "fusion"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        """Return a stable JSON-compatible dict for demos and API callers."""

        return {
            "dimension": self.dimension_label,
            "dimension_key": self.dimension,
            "method": self.method,
            "score": self.score,
            "label": self.label,
            "distribution": self.distribution,
            "confidence": self.confidence,
            "variance": self.variance,
            "evidence_refs": self.evidence_refs,
            "narrative": self.narrative,
            "metadata": self.metadata,
        }


def fuse_profile(
    evidence_cards: list[EvidenceCard],
    *,
    now: datetime | None = None,
    confidence_weight_k: float = DEFAULT_CONFIDENCE_WEIGHT_K,
) -> dict[DimensionKey, FusionResult]:
    """Fuse all seven dimensions from evidence cards."""

    return {
        dimension: fuse_dimension(
            dimension,
            evidence_cards,
            now=now,
            confidence_weight_k=confidence_weight_k,
        )
        for dimension in DIMENSION_LABELS
    }


def fuse_dimension(
    dimension: DimensionKey,
    evidence_cards: list[EvidenceCard],
    *,
    now: datetime | None = None,
    confidence_weight_k: float = DEFAULT_CONFIDENCE_WEIGHT_K,
) -> FusionResult:
    """Fuse one dimension using the weighted + confidence + decay formula."""

    cards = [card for card in evidence_cards if card.dimension == dimension]
    if dimension in CATEGORY_DIMENSIONS or dimension == "weak_points":
        return _fuse_label_dimension(dimension, cards, now=now, confidence_weight_k=confidence_weight_k)
    if dimension == "motivation":
        return _fuse_motivation(cards, now=now, confidence_weight_k=confidence_weight_k)
    return _fuse_numeric_dimension(dimension, cards, now=now, confidence_weight_k=confidence_weight_k)


def score_from_evidence_value(card: EvidenceCard) -> float | None:
    """Map an objective signal to a 0-100 evidence value when possible."""

    signal = card.objective_signal
    explicit = _first_numeric(signal, ("value", "score", "evidence_value", "numeric_value"))
    if explicit is not None:
        return _clamp_score(explicit)

    if card.dimension == "knowledge_base":
        return _knowledge_value(signal)
    if card.dimension == "pace":
        return _pace_value(signal)
    if card.dimension == "motivation":
        return _motivation_value(signal)
    return None


def decay_factor(
    card: EvidenceCard,
    *,
    now: datetime | None = None,
    half_life_days: float | None = None,
) -> float:
    """Return ``0.5 ** (Delta t / half-life)``; category dimensions do not decay."""

    half_life = HALF_LIFE_DAYS.get(card.dimension) if half_life_days is None else half_life_days
    if half_life is None:
        return 1.0
    if half_life <= 0:
        raise ValueError("half_life_days must be positive")

    current = _aware_datetime(now or datetime.now(UTC))
    timestamp = _aware_datetime(card.timestamp)
    delta_days = max((current - timestamp).total_seconds(), 0.0) / 86400.0
    return 0.5 ** (delta_days / half_life)


def confidence_from_weight_and_variance(
    total_weight: float,
    variance: float,
    *,
    k: float = DEFAULT_CONFIDENCE_WEIGHT_K,
    variance_normalizer: float = NUMERIC_VARIANCE_NORMALIZER,
) -> float:
    """Apply ``min(1, sum(weight) / K) * (1 - normalized_variance)``."""

    if k <= 0:
        raise ValueError("k must be positive")
    evidence_factor = min(1.0, max(total_weight, 0.0) / k)
    normalized_variance = _clamp01(variance / variance_normalizer if variance_normalizer > 0 else 0.0)
    return round(_clamp01(evidence_factor * (1.0 - normalized_variance)), 4)


def evidence_ref(card: EvidenceCard, index: int) -> str:
    """Build the traceable evidence ref used by jury and fusion outputs."""

    return f"{card.source}#{index}"


def _fuse_numeric_dimension(
    dimension: DimensionKey,
    cards: list[EvidenceCard],
    *,
    now: datetime | None,
    confidence_weight_k: float,
) -> FusionResult:
    weighted_values: list[tuple[float, float, float]] = []
    for card in cards:
        value = score_from_evidence_value(card)
        if value is None:
            continue
        decay = decay_factor(card, now=now)
        weighted_values.append((value, card.weight, decay))

    numerator = sum(value * weight * decay for value, weight, decay in weighted_values)
    denominator = sum(weight * decay for _, weight, decay in weighted_values)
    score = round(numerator / denominator, 2) if denominator > 0 else None
    variance = _weighted_variance(weighted_values, score) if score is not None else 0.0
    total_weight = sum(card.weight for card in cards if score_from_evidence_value(card) is not None)
    confidence = confidence_from_weight_and_variance(total_weight, variance, k=confidence_weight_k)
    return FusionResult(
        dimension=dimension,
        dimension_label=DIMENSION_LABELS[dimension],
        score=score,
        label=None,
        distribution={},
        confidence=confidence,
        variance=round(variance, 2),
        evidence_refs=[evidence_ref(card, index) for index, card in enumerate(cards, start=1)],
        narrative=_numeric_narrative(dimension, score, confidence),
        metadata={"weighted_denominator": round(denominator, 4), "formula": "score_D=sum(value*weight*decay)/sum(weight*decay)"},
    )


def _fuse_motivation(
    cards: list[EvidenceCard],
    *,
    now: datetime | None,
    confidence_weight_k: float,
) -> FusionResult:
    numeric = _fuse_numeric_dimension("motivation", cards, now=now, confidence_weight_k=confidence_weight_k)
    distribution, label, label_weight, label_variance = _label_distribution("motivation", cards, now=now)
    total_weight = max(sum(card.weight for card in cards), label_weight)
    confidence = confidence_from_weight_and_variance(
        total_weight,
        max(numeric.variance, label_variance),
        k=confidence_weight_k,
    )
    return FusionResult(
        dimension="motivation",
        dimension_label=DIMENSION_LABELS["motivation"],
        score=numeric.score,
        label=label,
        distribution=distribution,
        confidence=confidence,
        variance=max(numeric.variance, round(label_variance, 2)),
        evidence_refs=numeric.evidence_refs,
        narrative=_motivation_narrative(numeric.score, label, confidence),
        metadata=numeric.metadata,
    )


def _fuse_label_dimension(
    dimension: DimensionKey,
    cards: list[EvidenceCard],
    *,
    now: datetime | None,
    confidence_weight_k: float,
) -> FusionResult:
    distribution, label, total_weight, variance = _label_distribution(dimension, cards, now=now)
    confidence = confidence_from_weight_and_variance(
        total_weight,
        variance,
        k=confidence_weight_k,
        variance_normalizer=1.0,
    )
    return FusionResult(
        dimension=dimension,
        dimension_label=DIMENSION_LABELS[dimension],
        score=None,
        label=label,
        distribution=distribution,
        confidence=confidence,
        variance=round(variance, 4),
        evidence_refs=[evidence_ref(card, index) for index, card in enumerate(cards, start=1)],
        narrative=_label_narrative(dimension, label, confidence),
        metadata={"formula": "category_vote_distribution"},
    )


def _label_distribution(
    dimension: DimensionKey,
    cards: list[EvidenceCard],
    *,
    now: datetime | None,
) -> tuple[dict[str, float], str | None, float, float]:
    weights: dict[str, float] = {}
    for card in cards:
        decay = decay_factor(card, now=now)
        for label, vote_weight in _label_votes_from_signal(dimension, card.objective_signal).items():
            weights[label] = weights.get(label, 0.0) + card.weight * decay * vote_weight

    total_weight = sum(weights.values())
    if total_weight <= 0:
        return {}, None, 0.0, 0.0

    distribution = {label: round(weight / total_weight, 4) for label, weight in sorted(weights.items())}
    label = max(weights.items(), key=lambda item: item[1])[0]
    concentration = max(distribution.values()) if distribution else 0.0
    disagreement = 1.0 - concentration
    return distribution, label, total_weight, disagreement


def _label_votes_from_signal(dimension: DimensionKey, signal: dict[str, Any]) -> dict[str, float]:
    allowed = set(ALLOWED_LABELS.get(dimension, ()))
    votes: dict[str, float] = {}

    label = signal.get("label")
    if isinstance(label, str) and (not allowed or label in allowed):
        votes[label] = votes.get(label, 0.0) + 1.0

    if dimension == "weak_points":
        weak_labels = signal.get("weak_labels")
        if isinstance(weak_labels, list):
            for item in weak_labels:
                if isinstance(item, str) and item.strip():
                    votes[item.strip()] = votes.get(item.strip(), 0.0) + 1.0
        label_from_fact = signal.get("knowledge_point") or signal.get("topic")
        if isinstance(label_from_fact, str) and label_from_fact.strip():
            votes[label_from_fact.strip()] = votes.get(label_from_fact.strip(), 0.0) + 1.0

    raw_votes = signal.get("votes")
    if isinstance(raw_votes, dict):
        for raw_label, raw_count in raw_votes.items():
            if not isinstance(raw_label, str) or (allowed and raw_label not in allowed):
                continue
            count = _as_float(raw_count)
            if count is not None and count > 0:
                votes[raw_label] = votes.get(raw_label, 0.0) + count

    raw_distribution = signal.get("distribution")
    if isinstance(raw_distribution, dict):
        for raw_label, raw_ratio in raw_distribution.items():
            if not isinstance(raw_label, str) or (allowed and raw_label not in allowed):
                continue
            ratio = _as_float(raw_ratio)
            if ratio is not None and ratio > 0:
                votes[raw_label] = votes.get(raw_label, 0.0) + ratio

    return votes


def _knowledge_value(signal: dict[str, Any]) -> float | None:
    accuracy = _first_numeric(signal, ("accuracy", "correct_rate"))
    if accuracy is not None:
        return _clamp_score(accuracy * 100 if accuracy <= 1 else accuracy)

    if bool(signal.get("gave_up") or signal.get("abandoned")):
        return 20.0
    if isinstance(signal.get("correct"), bool):
        return 100.0 if signal["correct"] else 0.0
    if isinstance(signal.get("is_correct"), bool):
        return 100.0 if signal["is_correct"] else 0.0

    checks: list[bool] = []
    for key in ("对症", "配伍", "剂量"):
        if isinstance(signal.get(key), bool):
            checks.append(bool(signal[key]))
    if isinstance(signal.get("误用毒烈"), bool):
        checks.append(not bool(signal["误用毒烈"]))
    if checks:
        return round(sum(1 for item in checks if item) / len(checks) * 100, 2)

    if signal.get("prior"):
        prior = _first_numeric(signal, ("prior_score", "base_score"))
        return _clamp_score(prior) if prior is not None else 50.0
    return None


def _pace_value(signal: dict[str, Any]) -> float | None:
    minutes = _first_numeric(signal, ("daily_minutes", "minutes_per_day"))
    target_days = _first_numeric(signal, ("target_days", "period_days"))
    values: list[float] = []
    if minutes is not None:
        if minutes < 15:
            values.append(20.0)
        elif minutes <= 40:
            values.append(55.0)
        else:
            values.append(85.0)
    if target_days is not None:
        if target_days <= 14:
            values.append(85.0)
        elif target_days <= 45:
            values.append(55.0)
        else:
            values.append(25.0)
    return round(sum(values) / len(values), 2) if values else None


def _motivation_value(signal: dict[str, Any]) -> float | None:
    explicit = _first_numeric(signal, ("motivation_score", "strength"))
    if explicit is not None:
        return _clamp_score(explicit)
    label = str(signal.get("label") or signal.get("motivation") or "")
    if label in {"被动", "佛系", "外部推动"}:
        return 35.0
    if label == "外驱":
        return 50.0
    if label == "内外混合":
        return 65.0
    if label == "内驱":
        return 75.0
    if bool(signal.get("accept_challenge") or signal.get("challenge")):
        return 80.0
    return None


def _weighted_variance(weighted_values: list[tuple[float, float, float]], mean: float | None) -> float:
    if mean is None:
        return 0.0
    denominator = sum(weight * decay for _, weight, decay in weighted_values)
    if denominator <= 0:
        return 0.0
    return sum(((value - mean) ** 2) * weight * decay for value, weight, decay in weighted_values) / denominator


def _numeric_narrative(dimension: DimensionKey, score: float | None, confidence: float) -> str:
    if score is None:
        return f"{DIMENSION_LABELS[dimension]}暂无足够客观证据，暂不出分。"
    return f"{DIMENSION_LABELS[dimension]}按证据加权融合为 {score:.1f} 分，当前置信度 {confidence:.2f}。"


def _motivation_narrative(score: float | None, label: str | None, confidence: float) -> str:
    if score is None and label is None:
        return "兴趣与动机暂无足够客观证据。"
    label_part = f"，动机标签偏向{label}" if label else ""
    score_part = f"动机强度 {score:.1f} 分" if score is not None else "动机强度待补证"
    return f"{score_part}{label_part}，当前置信度 {confidence:.2f}。"


def _label_narrative(dimension: DimensionKey, label: str | None, confidence: float) -> str:
    if label is None:
        return f"{DIMENSION_LABELS[dimension]}暂无足够客观证据。"
    return f"{DIMENSION_LABELS[dimension]}按证据投票偏向“{label}”，当前置信度 {confidence:.2f}。"


def _first_numeric(signal: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _as_float(signal.get(key))
        if value is not None:
            return value
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _clamp_score(value: float) -> float:
    return min(100.0, max(0.0, float(value)))


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


__all__ = [
    "ALLOWED_LABELS",
    "DEFAULT_CONFIDENCE_WEIGHT_K",
    "FusionResult",
    "HALF_LIFE_DAYS",
    "confidence_from_weight_and_variance",
    "decay_factor",
    "evidence_ref",
    "fuse_dimension",
    "fuse_profile",
    "score_from_evidence_value",
]
