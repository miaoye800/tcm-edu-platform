"""Rule guardrails for profile scoring.

Guardrails use objective signals to bound impossible scores and reject invented
category labels. They do not replace LLM judgment inside the allowed interval.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.profile.fusion import ALLOWED_LABELS
from app.profile.schema import CATEGORY_DIMENSIONS, DIMENSION_LABELS, DimensionKey, EvidenceCard

logger = logging.getLogger(__name__)


class GuardrailWarning(BaseModel):
    """A clamp/filter event that should be auditable."""

    model_config = ConfigDict(frozen=True)

    dimension: DimensionKey
    reason: str
    original: Any = None
    corrected: Any = None
    allowed_min: float | None = None
    allowed_max: float | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class GuardrailResult(BaseModel):
    """Guarded score/label payload."""

    dimension: DimensionKey
    score: float | None = None
    label: str | None = None
    distribution: dict[str, float] = Field(default_factory=dict)
    weak_points: list[str] = Field(default_factory=list)
    warnings: list[GuardrailWarning] = Field(default_factory=list)


def apply_guardrail(
    dimension: DimensionKey,
    *,
    evidence_cards: list[EvidenceCard],
    score: float | None = None,
    label: str | None = None,
    distribution: dict[str, float] | None = None,
    weak_points: list[str] | None = None,
) -> GuardrailResult:
    """Apply score interval, category-label, and weak-point guardrails."""

    warnings: list[GuardrailWarning] = []
    guarded_score = score
    guarded_label = label
    guarded_distribution = dict(distribution or {})
    guarded_weak_points = list(weak_points or [])

    if guarded_score is not None:
        low, high, reasons = allowed_score_interval(dimension, evidence_cards)
        clamped = min(high, max(low, guarded_score))
        if clamped != guarded_score:
            warning = GuardrailWarning(
                dimension=dimension,
                reason="；".join(reasons) or "客观信号与分数矛盾，已按护栏区间纠偏",
                original=guarded_score,
                corrected=clamped,
                allowed_min=low,
                allowed_max=high,
                evidence_refs=_refs(evidence_cards),
            )
            warnings.append(warning)
            _log_warning(warning)
        guarded_score = clamped

    if dimension in CATEGORY_DIMENSIONS or dimension == "motivation":
        cat_result = _guard_category_label(dimension, guarded_label, guarded_distribution, evidence_cards)
        guarded_label = cat_result.label
        guarded_distribution = cat_result.distribution
        warnings.extend(cat_result.warnings)

    if dimension == "weak_points":
        weak_result = _guard_weak_points(guarded_weak_points or _weak_points_from_label(label), evidence_cards)
        guarded_weak_points = weak_result.weak_points
        guarded_label = guarded_weak_points[0] if guarded_weak_points else None
        warnings.extend(weak_result.warnings)

    return GuardrailResult(
        dimension=dimension,
        score=guarded_score,
        label=guarded_label,
        distribution=guarded_distribution,
        weak_points=guarded_weak_points,
        warnings=warnings,
    )


def allowed_score_interval(dimension: DimensionKey, evidence_cards: list[EvidenceCard]) -> tuple[float, float, list[str]]:
    """Return the objective-signal interval for one dimension."""

    low, high = 0.0, 100.0
    reasons: list[str] = []
    if dimension in {"knowledge_base", "pace", "motivation"} and not evidence_cards:
        high = min(high, 50.0)
        reasons.append("缺少可追溯证据卡，不允许给出高分")

    if dimension == "knowledge_base":
        correct, total = _knowledge_correctness(evidence_cards)
        if total > 0:
            rate = correct / total
            if rate == 0:
                high = min(high, 35.0)
                reasons.append("辨药/抓药正确率为 0%，知识基础上限不超过 35")
            if rate == 1:
                low = max(low, 60.0)
                reasons.append("辨药/抓药正确率为 100%，知识基础下限不低于 60")
        if any(card.objective_signal.get("误用毒烈") is True for card in evidence_cards):
            high = min(high, 55.0)
            reasons.append("存在误用毒烈药客观信号，知识基础上限不超过 55")
        if any(card.objective_signal.get("剂量") is False for card in evidence_cards):
            high = min(high, 80.0)
            reasons.append("存在剂量错误客观信号，知识基础上限不超过 80")

    if dimension == "motivation":
        labels = {str(card.objective_signal.get("label") or card.objective_signal.get("motivation") or "") for card in evidence_cards}
        if labels & {"佛系", "被动", "外部推动"}:
            high = min(high, 55.0)
            reasons.append("动机客观标签为佛系/被动，上限不超过 55")
        if any(card.objective_signal.get("accept_challenge") or card.objective_signal.get("challenge") for card in evidence_cards):
            low = max(low, 55.0)
            reasons.append("用户主动接挑战，动机强度下限不低于 55")

    if dimension == "pace":
        interval = _pace_interval(evidence_cards)
        if interval is not None:
            low, high = max(low, interval[0]), min(high, interval[1])
            reasons.append(interval[2])

    if low > high:
        midpoint = round((low + high) / 2, 2)
        reasons.append("客观信号自身冲突，护栏收敛到折中点")
        low = high = midpoint
    return low, high, reasons


def _guard_category_label(
    dimension: DimensionKey,
    label: str | None,
    distribution: dict[str, float],
    evidence_cards: list[EvidenceCard],
) -> GuardrailResult:
    allowed = set(ALLOWED_LABELS.get(dimension, ()))
    if not allowed:
        return GuardrailResult(dimension=dimension, label=label, distribution=distribution)

    warnings: list[GuardrailWarning] = []
    filtered_distribution = {key: value for key, value in distribution.items() if key in allowed}
    if filtered_distribution != distribution:
        warning = GuardrailWarning(
            dimension=dimension,
            reason="类别型维度禁止生成未定义标签，已过滤非法分布项",
            original=distribution,
            corrected=filtered_distribution,
            evidence_refs=_refs(evidence_cards),
        )
        warnings.append(warning)
        _log_warning(warning)
    filtered_distribution = _normalize_distribution(filtered_distribution)

    corrected_label = label if label in allowed else None
    if label is not None and corrected_label is None:
        corrected_label = _best_allowed_label_from_evidence(dimension, evidence_cards) or _best_label(filtered_distribution)
        warning = GuardrailWarning(
            dimension=dimension,
            reason="类别型维度禁止生成未定义标签，已回退到证据支持标签",
            original=label,
            corrected=corrected_label,
            evidence_refs=_refs(evidence_cards),
        )
        warnings.append(warning)
        _log_warning(warning)

    return GuardrailResult(
        dimension=dimension,
        label=corrected_label,
        distribution=filtered_distribution,
        warnings=warnings,
    )


def _guard_weak_points(weak_points: list[str], evidence_cards: list[EvidenceCard]) -> GuardrailResult:
    traceable = _traceable_weak_labels(evidence_cards)
    kept = [item for item in weak_points if item in traceable]
    warnings: list[GuardrailWarning] = []
    if kept != weak_points:
        warning = GuardrailWarning(
            dimension="weak_points",
            reason="薄弱点必须追溯到具体错误证据卡，已移除无证据标签",
            original=weak_points,
            corrected=kept,
            evidence_refs=_refs(evidence_cards),
        )
        warnings.append(warning)
        _log_warning(warning)
    return GuardrailResult(dimension="weak_points", weak_points=kept, warnings=warnings)


def _knowledge_correctness(evidence_cards: list[EvidenceCard]) -> tuple[int, int]:
    correct = 0
    total = 0
    for card in evidence_cards:
        signal = card.objective_signal
        if signal.get("gave_up") or signal.get("abandoned"):
            total += 1
            continue
        if isinstance(signal.get("correct"), bool):
            correct += 1 if signal["correct"] else 0
            total += 1
            continue
        checks: list[bool] = []
        for key in ("对症", "配伍", "剂量"):
            if isinstance(signal.get(key), bool):
                checks.append(bool(signal[key]))
        if isinstance(signal.get("误用毒烈"), bool):
            checks.append(not bool(signal["误用毒烈"]))
        if checks:
            correct += sum(1 for item in checks if item)
            total += len(checks)
    return correct, total


def _pace_interval(evidence_cards: list[EvidenceCard]) -> tuple[float, float, str] | None:
    intervals: list[tuple[float, float, str]] = []
    for card in evidence_cards:
        signal = card.objective_signal
        minutes = _as_float(signal.get("daily_minutes") or signal.get("minutes_per_day"))
        target_days = _as_float(signal.get("target_days") or signal.get("period_days"))
        if minutes is not None:
            if minutes < 15:
                intervals.append((0.0, 30.0, "每日时长 <15min，节奏应落在慢速区间"))
            elif minutes <= 40:
                intervals.append((31.0, 70.0, "每日时长 15-40min，节奏应落在中速区间"))
            else:
                intervals.append((71.0, 100.0, "每日时长 >40min，节奏应落在快速区间"))
        if target_days is not None:
            if target_days <= 14:
                intervals.append((71.0, 100.0, "目标周期紧，节奏应落在快速区间"))
            elif target_days > 60:
                intervals.append((0.0, 30.0, "目标周期宽松，节奏应落在慢速区间"))
    if not intervals:
        return None
    low = max(item[0] for item in intervals)
    high = min(item[1] for item in intervals)
    return low, high, "；".join(item[2] for item in intervals)


def _best_allowed_label_from_evidence(dimension: DimensionKey, evidence_cards: list[EvidenceCard]) -> str | None:
    allowed = set(ALLOWED_LABELS.get(dimension, ()))
    weights: dict[str, float] = {}
    for card in evidence_cards:
        label = card.objective_signal.get("label")
        if isinstance(label, str) and label in allowed:
            weights[label] = weights.get(label, 0.0) + card.weight
    return max(weights.items(), key=lambda item: item[1])[0] if weights else None


def _traceable_weak_labels(evidence_cards: list[EvidenceCard]) -> set[str]:
    labels: set[str] = set()
    for card in evidence_cards:
        signal = card.objective_signal
        if not _is_error_signal(signal):
            continue
        label = signal.get("label") or signal.get("topic") or signal.get("knowledge_point")
        if isinstance(label, str) and label.strip():
            labels.add(label.strip())
        weak_labels = signal.get("weak_labels")
        if isinstance(weak_labels, list):
            labels.update(str(item).strip() for item in weak_labels if str(item).strip())
    return labels


def _is_error_signal(signal: dict[str, Any]) -> bool:
    if signal.get("gave_up") or signal.get("abandoned"):
        return True
    if isinstance(signal.get("correct"), bool):
        return not bool(signal["correct"])
    if isinstance(signal.get("is_correct"), bool):
        return not bool(signal["is_correct"])
    if any(signal.get(key) is False for key in ("对症", "配伍", "剂量")):
        return True
    if signal.get("误用毒烈") is True:
        return True
    weak_labels = signal.get("weak_labels")
    return isinstance(weak_labels, list) and bool(weak_labels)


def _weak_points_from_label(label: str | None) -> list[str]:
    return [label] if label else []


def _normalize_distribution(distribution: dict[str, float]) -> dict[str, float]:
    total = sum(value for value in distribution.values() if value > 0)
    if total <= 0:
        return {}
    return {key: round(max(value, 0.0) / total, 4) for key, value in sorted(distribution.items())}


def _best_label(distribution: dict[str, float]) -> str | None:
    return max(distribution.items(), key=lambda item: item[1])[0] if distribution else None


def _refs(evidence_cards: list[EvidenceCard]) -> list[str]:
    return [f"{card.source}#{index}" for index, card in enumerate(evidence_cards, start=1)]


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _log_warning(warning: GuardrailWarning) -> None:
    logger.warning(
        "profile guardrail corrected %s (%s): %r -> %r",
        DIMENSION_LABELS[warning.dimension],
        warning.reason,
        warning.original,
        warning.corrected,
    )


__all__ = [
    "GuardrailResult",
    "GuardrailWarning",
    "allowed_score_interval",
    "apply_guardrail",
]
