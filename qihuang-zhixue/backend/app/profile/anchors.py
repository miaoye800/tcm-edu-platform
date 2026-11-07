"""Central score-anchor intervals shared by fusion, jury, and guardrails."""

from __future__ import annotations

from typing import Any

from app.profile.schema import DimensionKey

ScoreInterval = tuple[float, float]

# Every qualitative scoring anchor is an interval. Fusion uses its midpoint,
# while the jury may choose a concrete value and the guardrail enforces bounds.
SCORE_ANCHOR_INTERVALS: dict[DimensionKey, dict[str, dict[str, ScoreInterval]]] = {
    "knowledge_base": {
        "base_level": {
            "零基础": (5.0, 18.0),
            "入门": (22.0, 38.0),
            "一般": (42.0, 58.0),
            "较好": (62.0, 78.0),
            "熟练": (75.0, 92.0),
        },
    },
    "pace": {
        "daily_minutes": {
            "lt_15": (10.0, 30.0),
            "15_to_40": (40.0, 65.0),
            "gt_40": (75.0, 95.0),
        },
        "target_days": {
            "le_14": (75.0, 95.0),
            "15_to_45": (45.0, 65.0),
            "gt_45": (15.0, 35.0),
        },
    },
    "motivation": {
        "label": {
            "被动": (25.0, 45.0),
            "佛系": (25.0, 45.0),
            "外部推动": (25.0, 45.0),
            "外驱": (42.0, 58.0),
            "内外混合": (57.0, 73.0),
            "内驱": (68.0, 85.0),
        },
        "behavior": {
            "accept_challenge": (72.0, 88.0),
        },
    },
}


def interval_midpoint(interval: ScoreInterval) -> float:
    """Return the compatibility fallback used by deterministic fusion."""

    return round((interval[0] + interval[1]) / 2.0, 2)


def base_level_midpoints() -> dict[str, float]:
    """Expose legacy base-level point mappings as derived interval midpoints."""

    return {
        label: interval_midpoint(interval)
        for label, interval in SCORE_ANCHOR_INTERVALS["knowledge_base"]["base_level"].items()
    }


def signal_anchor_intervals(
    dimension: DimensionKey,
    signal: dict[str, Any],
) -> list[tuple[ScoreInterval, str]]:
    """Resolve all qualitative anchors supported by one objective signal."""

    if dimension == "knowledge_base":
        label = str(signal.get("label") or signal.get("base_level") or "").strip()
        interval = SCORE_ANCHOR_INTERVALS["knowledge_base"]["base_level"].get(label)
        return [(interval, f"基础自评“{label}”应落在区间 [{interval[0]:g}, {interval[1]:g}]")] if interval else []

    if dimension == "pace":
        anchors: list[tuple[ScoreInterval, str]] = []
        minutes = _as_float(signal.get("daily_minutes") or signal.get("minutes_per_day"))
        if minutes is not None:
            bucket = "lt_15" if minutes < 15 else "15_to_40" if minutes <= 40 else "gt_40"
            interval = SCORE_ANCHOR_INTERVALS["pace"]["daily_minutes"][bucket]
            anchors.append((interval, f"每日时长 {minutes:g} 分钟应落在区间 [{interval[0]:g}, {interval[1]:g}]"))
        target_days = _as_float(signal.get("target_days") or signal.get("period_days"))
        if target_days is not None:
            bucket = "le_14" if target_days <= 14 else "15_to_45" if target_days <= 45 else "gt_45"
            interval = SCORE_ANCHOR_INTERVALS["pace"]["target_days"][bucket]
            anchors.append((interval, f"目标周期 {target_days:g} 天应落在区间 [{interval[0]:g}, {interval[1]:g}]"))
        return anchors

    if dimension == "motivation":
        label = str(signal.get("label") or signal.get("motivation") or "").strip()
        interval = SCORE_ANCHOR_INTERVALS["motivation"]["label"].get(label)
        if interval:
            return [(interval, f"动机“{label}”应落在区间 [{interval[0]:g}, {interval[1]:g}]")]
        if bool(signal.get("accept_challenge") or signal.get("challenge")):
            interval = SCORE_ANCHOR_INTERVALS["motivation"]["behavior"]["accept_challenge"]
            return [(interval, f"主动接受挑战应落在区间 [{interval[0]:g}, {interval[1]:g}]")]
    return []


def midpoint_from_signal(dimension: DimensionKey, signal: dict[str, Any]) -> float | None:
    """Return the mean of applicable anchor midpoints for rule fallback."""

    anchors = signal_anchor_intervals(dimension, signal)
    if not anchors:
        return None
    midpoints = [interval_midpoint(interval) for interval, _ in anchors]
    return round(sum(midpoints) / len(midpoints), 2)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_band(
    tier: str,
    dimension: DimensionKey,
    cards: Any,
) -> tuple[ScoreInterval, list[str]]:
    """Intersect the model-judged tier band with the objective anchor intervals.

    The tier is judged from *all* the evidence, while an anchor only sees one
    signal (say, minutes per day).  So when the two disagree the tier wins and
    the conflict is recorded for audit instead of silently overriding either
    side -- a student who studies 30 minutes on weekdays but binges at weekends
    would otherwise be pinned to the low band forever.
    """

    from app.profile.component_model import TIER_BANDS

    band = TIER_BANDS.get(str(tier), (5.0, 95.0))
    warnings: list[str] = []
    intervals: list[ScoreInterval] = []
    for card in cards or ():
        signal = getattr(card, "objective_signal", None) or {}
        if not isinstance(signal, dict):
            continue
        intervals.extend(interval for interval, _ in signal_anchor_intervals(dimension, signal))
    if not intervals:
        return band, warnings
    low = max([band[0], *(interval[0] for interval in intervals)])
    high = min([band[1], *(interval[1] for interval in intervals)])
    if low > high:
        warnings.append("anchor_tier_conflict")
        return band, warnings
    return (round(low, 2), round(high, 2)), warnings


__all__ = [
    "SCORE_ANCHOR_INTERVALS",
    "ScoreInterval",
    "base_level_midpoints",
    "interval_midpoint",
    "midpoint_from_signal",
    "resolve_band",
    "signal_anchor_intervals",
]
