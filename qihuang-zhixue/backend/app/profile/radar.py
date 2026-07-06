"""Profile radar payload conversion for the new Profile schema."""

from __future__ import annotations

from typing import Any

from app.profile.schema import DIMENSION_LABELS, Profile

LOW_CONFIDENCE_THRESHOLD = 0.5

RADAR_LABELS: dict[str, str] = {
    "knowledge_base": "知识基础",
    "learning_goal": "目标清晰度",
    "learning_style": "风格集中度",
    "weak_points": "薄弱热区",
    "pace": "学习节奏",
    "motivation": "动机强度",
    "resource_preference": "资源集中度",
}

RADAR_METRICS: dict[str, str] = {
    "knowledge_base": "数值分",
    "learning_goal": "目标清晰度",
    "learning_style": "偏好集中度",
    "weak_points": "薄弱点热度",
    "pace": "数值分",
    "motivation": "数值分",
    "resource_preference": "偏好集中度",
}


def to_radar(profile: Profile) -> dict[str, Any]:
    """Convert a seven-dimension profile to a frontend-friendly radar payload."""

    axes = [_axis_payload(profile, key, label) for key, label in DIMENSION_LABELS.items()]
    return {
        "dimensions": [axis["label"] for axis in axes],
        "values": [axis["value"] for axis in axes],
        "confidences": [axis["confidence"] for axis in axes],
        "axes": axes,
    }


def _axis_payload(profile: Profile, key: str, source_label: str) -> dict[str, Any]:
    dimension = getattr(profile, key)
    confidence = _confidence(dimension)
    value, kind = _axis_value(key, dimension)
    raw_label = _raw_label(key, dimension)
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
        "confidence_status": "已取证" if confidence >= LOW_CONFIDENCE_THRESHOLD else "待取证",
        "confidenceStatus": "已取证" if confidence >= LOW_CONFIDENCE_THRESHOLD else "待取证",
        "low_confidence": confidence < LOW_CONFIDENCE_THRESHOLD,
        "lowConfidence": confidence < LOW_CONFIDENCE_THRESHOLD,
        "kind": kind,
        "placeholder": _is_placeholder(value, confidence, dimension),
    }


def _axis_value(key: str, dimension: Any) -> tuple[int, str]:
    if key in {"learning_goal", "learning_style", "resource_preference"}:
        return _clamp_score(getattr(dimension, "derived_score", None)), "derived"
    if key == "weak_points":
        return _weak_point_heat(dimension), "hotspot"
    return _clamp_score(getattr(dimension, "score", None)), "numeric"


def _weak_point_heat(dimension: Any) -> int:
    items = getattr(dimension, "items", []) or []
    if not items:
        return _clamp_score(getattr(dimension, "score", None))
    total = sum(float(getattr(item, "strength", 1) or 1) for item in items)
    return _clamp_score(total * 25)


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
