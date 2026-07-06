"""Profile radar payload conversion.

Radar axes are display numbers, not scoring judgments. Numeric dimensions use
their real 0-100 scores. Categorical dimensions use their distribution
concentration as a derived display value, while retaining the original label for
the UI. Low-confidence or empty dimensions stay renderable as placeholders.
"""

from __future__ import annotations

from typing import Any

from app.profile.models import (
    DIMENSION_KEYS as LEGACY_DIMENSION_KEYS,
    DIMENSION_LABELS as LEGACY_DIMENSION_LABELS,
    StudentProfile,
)
from app.profile.schema import DIMENSION_LABELS, Profile

LOW_CONFIDENCE_THRESHOLD = 0.5

RADAR_LABELS: dict[str, str] = {
    "knowledge_base": "知识基础",
    "learning_goal": "目标清晰度",
    "goal": "目标清晰度",
    "learning_style": "风格集中度",
    "weak_points": "薄弱热区",
    "pace": "学习节奏",
    "motivation": "动机强度",
    "resource_preference": "资源集中度",
}

RADAR_METRICS: dict[str, str] = {
    "knowledge_base": "数值分",
    "learning_goal": "目标清晰度",
    "goal": "目标清晰度",
    "learning_style": "偏好集中度",
    "weak_points": "薄弱点热度",
    "pace": "数值分",
    "motivation": "数值分",
    "resource_preference": "偏好集中度",
}

LEGACY_TO_SCHEMA_KEY: dict[str, str] = {
    "goal": "learning_goal",
}


def to_radar(profile: Profile | StudentProfile) -> dict[str, Any]:
    """Convert a seven-dimension profile to a frontend-friendly radar payload."""

    axes = [_axis_payload(profile, key, label) for key, label in _dimension_items(profile)]
    return {
        "dimensions": [axis["label"] for axis in axes],
        "values": [axis["value"] for axis in axes],
        "confidences": [axis["confidence"] for axis in axes],
        "axes": axes,
    }


def _dimension_items(profile: Profile | StudentProfile) -> list[tuple[str, str]]:
    if isinstance(profile, Profile):
        return [(key, label) for key, label in DIMENSION_LABELS.items()]
    return [(key, LEGACY_DIMENSION_LABELS[key]) for key in LEGACY_DIMENSION_KEYS]


def _axis_payload(profile: Profile | StudentProfile, key: str, source_label: str) -> dict[str, Any]:
    dimension = getattr(profile, key)
    confidence = _confidence(dimension)
    value, kind = _axis_value(key, dimension)
    raw_label = _raw_label(key, dimension)
    return {
        "key": _schema_key(key),
        "label": RADAR_LABELS.get(key, source_label),
        "source_label": source_label,
        "sourceLabel": source_label,
        "metric": RADAR_METRICS.get(key, "派生值" if kind == "derived" else "数值分"),
        "value": value,
        "raw_label": raw_label,
        "raw": raw_label,
        "confidence": confidence,
        "confidence_status": "已取证" if confidence >= LOW_CONFIDENCE_THRESHOLD else "待取证",
        "confidenceStatus": "已取证" if confidence >= LOW_CONFIDENCE_THRESHOLD else "待取证",
        "low_confidence": confidence < LOW_CONFIDENCE_THRESHOLD,
        "lowConfidence": confidence < LOW_CONFIDENCE_THRESHOLD,
        "kind": kind,
        "placeholder": _is_placeholder(value, confidence, dimension),
    }


def _axis_value(key: str, dimension: Any) -> tuple[int, str]:
    if key in {"learning_goal", "goal", "learning_style", "resource_preference"}:
        return _clamp_score(getattr(dimension, "derived_score", None)), "derived"
    if key == "weak_points":
        return _weak_point_heat(dimension), "hotspot"
    return _clamp_score(getattr(dimension, "score", None)), "numeric"


def _weak_point_heat(dimension: Any) -> int:
    items = getattr(dimension, "items", []) or []
    if not items:
        score = getattr(dimension, "score", None)
        return _clamp_score(score)
    total = 0.0
    strengths = getattr(dimension, "strengths", {}) or {}
    for item in items:
        if isinstance(item, str):
            total += float(strengths.get(item, 1) or 1)
        else:
            total += float(getattr(item, "strength", 1) or 1)
    return _clamp_score(total * 25)


def _raw_label(key: str, dimension: Any) -> str:
    if key == "weak_points":
        items = getattr(dimension, "items", []) or []
        labels = [item if isinstance(item, str) else getattr(item, "label", "") for item in items]
        labels = [str(label).strip() for label in labels if str(label).strip()]
        return "、".join(labels) if labels else "待补充"
    label = getattr(dimension, "label", None)
    if label:
        return str(label)
    score = getattr(dimension, "score", None)
    if score is not None:
        return f"{_clamp_score(score)} 分"
    return "待补充"


def _confidence(dimension: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(getattr(dimension, "confidence", 0.0) or 0.0))), 4)
    except (TypeError, ValueError):
        return 0.0


def _clamp_score(value: Any) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0
    return int(max(0, min(100, round(numeric))))


def _schema_key(key: str) -> str:
    return LEGACY_TO_SCHEMA_KEY.get(key, key)


def _is_placeholder(value: int, confidence: float, dimension: Any) -> bool:
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        return True
    if value != 0:
        return False
    return not (
        getattr(dimension, "score", None) is not None
        or getattr(dimension, "label", None)
        or getattr(dimension, "items", None)
        or getattr(dimension, "distribution", None)
    )


__all__ = ["LOW_CONFIDENCE_THRESHOLD", "to_radar"]
