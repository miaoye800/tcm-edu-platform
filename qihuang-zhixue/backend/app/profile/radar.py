"""Profile radar payload conversion for the new Profile schema."""

from __future__ import annotations

from typing import Any

from app.profile.component_model import score_interval
from app.profile.schema import DIMENSION_LABELS, Profile

LOW_CONFIDENCE_THRESHOLD = 0.5

RADAR_LABELS: dict[str, str] = {
    "knowledge_base": "掌握程度",
    "learning_goal": "目标锚定度",
    "learning_style": "学习方式偏向",
    "weak_points": "掌握稳定度",
    "pace": "节奏可持续度",
    "motivation": "投入驱动力",
    "resource_preference": "资源适配度",
}

RADAR_METRICS: dict[str, str] = {
    "knowledge_base": "当前掌握估计",
    "learning_goal": "目标锚定估计",
    "learning_style": "方式偏向估计",
    "weak_points": "掌握稳定估计",
    "pace": "可持续度估计",
    "motivation": "投入驱动估计",
    "resource_preference": "资源适配估计",
}


def to_radar(profile: Profile) -> dict[str, Any]:
    """Convert a seven-dimension profile to a frontend-friendly radar payload."""

    axes = [_axis_payload(profile, key, label) for key, label in DIMENSION_LABELS.items()]
    provenance = {axis["key"]: axis["provenance"] for axis in axes}
    return {
        "dimensions": [axis["label"] for axis in axes],
        "values": [axis["value"] for axis in axes],
        "confidences": [axis["confidence"] for axis in axes],
        "provenance": provenance,
        "axes": axes,
    }


def _axis_payload(profile: Profile, key: str, source_label: str) -> dict[str, Any]:
    dimension = getattr(profile, key)
    confidence = _confidence(dimension)
    coverage = _coverage(dimension)
    value, kind = _axis_value(key, dimension)
    raw_label = _raw_label(key, dimension)
    lit = _is_lit(dimension)
    status = _status(lit)
    interval = _score_interval(dimension, coverage, confidence)
    pending_verification = any(
        getattr(card, "claim_status", None) == "pending"
        for card in (getattr(dimension, "evidence", None) or [])
    )
    provenance = _dimension_provenance(dimension)
    return {
        "key": key,
        "label": RADAR_LABELS.get(key, source_label),
        "source_label": source_label,
        "sourceLabel": source_label,
        "metric": RADAR_METRICS.get(key, "派生值" if kind == "derived" else "数值分"),
        "value": value,
        "raw_label": raw_label,
        "raw": raw_label,
        "confidence": confidence,
        "coverage": coverage,
        "score_interval": interval,
        "lit": lit,
        "status": status,
        "pending_verification": pending_verification,
        "provenance": provenance,
        "anchor": _anchor_text(key, value),
        "confidence_status": "已取证" if confidence >= LOW_CONFIDENCE_THRESHOLD else ("已收集到" if lit else "待取证"),
        "confidenceStatus": "已取证" if confidence >= LOW_CONFIDENCE_THRESHOLD else ("已收集到" if lit else "待取证"),
        "low_confidence": confidence < LOW_CONFIDENCE_THRESHOLD,
        "lowConfidence": confidence < LOW_CONFIDENCE_THRESHOLD,
        "kind": kind,
        "placeholder": _is_placeholder(value, confidence, dimension),
        "scoring_phase": getattr(dimension, "scoring_phase", "scored"),
        "component_collected": _component_count(dimension)[0],
        "component_total": _component_count(dimension)[1],
        "pending_components": list(getattr(dimension, "pending_components", []) or []),
    }


def _coverage(dimension: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(getattr(dimension, "coverage", 0.0) or 0.0))), 4)
    except (TypeError, ValueError):
        return 0.0


def _score_interval(dimension: Any, coverage: float, confidence: float) -> dict[str, float] | None:
    """The honest error bar: wide when components are missing or evidence is weak."""

    score = getattr(dimension, "score", None)
    if score is None:
        return None
    try:
        low, high = score_interval(float(score), coverage, confidence)
    except (TypeError, ValueError):
        return None
    return {"low": low, "high": high}


def _dimension_provenance(dimension: Any) -> str:
    cards = getattr(dimension, "evidence", None) or []
    explicit = [
        str(card.objective_signal.get("scoring_provenance"))
        for card in cards
        if card.objective_signal.get("scoring_provenance")
    ]
    if explicit:
        return explicit[-1]
    methods = {getattr(card, "extraction_method", "legacy") for card in cards}
    if "no_key_rule_fallback" in methods:
        return "no_key_rule_fallback"
    if "rule_fallback" in methods:
        return "rule_fallback"
    return "fusion" if cards else "unknown"


def _axis_value(key: str, dimension: Any) -> tuple[int, str]:
    radar_score = getattr(dimension, "radar_score", None)
    if radar_score is not None:
        return _clamp_score(radar_score, evidence_bearing=True), "agent_estimate"
    if key in {"learning_goal", "learning_style", "resource_preference"}:
        return _clamp_score(getattr(dimension, "derived_score", None), evidence_bearing=bool(getattr(dimension, "evidence", None))), "derived"
    if key == "weak_points":
        return _weak_point_heat(dimension), "hotspot"
    return _clamp_score(getattr(dimension, "score", None), evidence_bearing=bool(getattr(dimension, "evidence", None))), "numeric"


def _weak_point_heat(dimension: Any) -> int:
    items = getattr(dimension, "items", []) or []
    if not items:
        return _clamp_score(getattr(dimension, "score", None), evidence_bearing=bool(getattr(dimension, "evidence", None)))
    total = sum(float(getattr(item, "strength", 1) or 1) for item in items)
    return _clamp_score(total * 25, evidence_bearing=True)


def _raw_label(key: str, dimension: Any) -> str:
    if key == "weak_points":
        items = getattr(dimension, "items", []) or []
        labels = [str(getattr(item, "label", "")).strip() for item in items]
        return "、".join(label for label in labels if label) or "待补充"
    label = getattr(dimension, "label", None)
    if label:
        return str(label)
    score = getattr(dimension, "score", None)
    if score is not None:
        return f"{_clamp_score(score, evidence_bearing=True):g} 分"
    return "待补充"


def _anchor_text(key: str, score: int) -> str:
    if key == "knowledge_base":
        if score <= 20:
            return "药名、性味与归经线索仍待建立"
        if score <= 40:
            return "能识别常见概念，但功效辨析还需补证"
        if score <= 60:
            return "常用药功效已有基础，配伍辨析待强化"
        if score <= 80:
            return "功效与配伍较熟，可进入病例迁移"
        return "配伍加减与剂量判断已较稳定"

    if key == "motivation":
        if score <= 20:
            return "当前动机线索较少，适合先用短目标牵引"
        if score <= 40:
            return "需要外部节奏推动与即时反馈"
        if score <= 60:
            return "有学习意愿，可通过阶段目标稳定投入"
        if score <= 80:
            return "主动性较强，适合安排挑战任务"
        return "高度自驱，可进入拓展型任务"

    if key == "pace":
        if score <= 30:
            return "慢节奏：适合小步复盘"
        if score <= 70:
            return "中节奏：可保持日常练习"
        return "快节奏：适合密集冲刺与回顾"

    if key == "weak_points":
        return "由错题、问答和开药模拟共同取证"
    return "由交互选择与资源使用分布派生"


def _confidence(dimension: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(getattr(dimension, "confidence", 0.0) or 0.0))), 4)
    except (TypeError, ValueError):
        return 0.0


def _clamp_score(value: Any, *, evidence_bearing: bool = False) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not evidence_bearing:
        return round(max(0.0, min(100.0, numeric)), 1)
    # Profiles are useful estimates, never a literal human ceiling/floor.
    return round(max(5.0, min(95.0, numeric)), 1)


def _is_placeholder(value: int, confidence: float, dimension: Any) -> bool:
    # Low confidence is still drawable evidence. ``placeholder`` is reserved
    # for an axis with no usable evidence/result at all; styling confidence as
    # dashed is handled independently by ``status``.
    return not (
        getattr(dimension, "evidence", None)
        or value != 0
        or getattr(dimension, "score", None) is not None
        or getattr(dimension, "label", None)
        or getattr(dimension, "items", None)
        or getattr(dimension, "distribution", None)
    )


def _is_lit(dimension: Any) -> bool:
    """An axis is lit when there is a *result*, not merely raw evidence.

    "Evidence collected but no score yet" is exactly the state the coverage
    gate and the no-key contract produce.  Drawing it as a value would show a
    number nobody computed, so it stays dark and the collection progress is
    reported through ``coverage``/``scoring_phase`` instead.
    """

    if getattr(dimension, "scoring_phase", "scored") == "collecting" and getattr(dimension, "component_matrix", None):
        return False
    return bool(
        getattr(dimension, "radar_score", None) is not None
        or getattr(dimension, "score", None) is not None
        or getattr(dimension, "label", None)
        or getattr(dimension, "items", None)
    )


def _component_count(dimension: Any) -> tuple[int, int]:
    matrix = getattr(dimension, "component_matrix", None)
    if not isinstance(matrix, dict) or not matrix:
        return 0, 0
    collected = sum(1 for value in matrix.values() if isinstance(value, dict) and value.get("status") in {"collected", "scorable"})
    return collected, len(matrix)


def _status(lit: bool) -> str:
    """Only two states exist now.

    The dashed style used to encode "lit but low confidence".  Confidence is
    already visible as the ``score_interval`` width, and a half-drawn axis read
    as "probably no score" to users, so the style is binary: there is a result
    or there is not.
    """

    return "solid" if lit else "dark"


__all__ = ["LOW_CONFIDENCE_THRESHOLD", "to_radar"]
