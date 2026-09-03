from __future__ import annotations

from typing import Any, Final

TEMPLATE_RULES: Final[tuple[dict[str, object], ...]] = (
    {"feature": "comparison", "keywords": ("对比", "鉴别", "区别", "异同"), "candidates": ("fishbone-contrast",), "reason": "对比/鉴别类内容"},
    {"feature": "process", "keywords": ("流程", "步骤", "时间", "演变", "顺序"), "candidates": ("timeline-process",), "reason": "流程/时间类内容"},
    {"feature": "divergent", "keywords": ("联想", "发散", "关联", "应用"), "candidates": ("star-chart",), "reason": "发散联想内容"},
    {"feature": "hierarchy", "keywords": (), "candidates": ("ink-scroll", "warm-gold", "neon-night", "herb-garden", "hand-scroll"), "reason": "层级分类内容"},
)
ALLOWED_TEMPLATE_IDS: Final[frozenset[str]] = frozenset(template for rule in TEMPLATE_RULES for template in rule["candidates"])  # type: ignore[arg-type]
DEFAULT_TEMPLATE_ID: Final[str] = "warm-gold"


def content_feature(topic: str, docs: list[dict[str, Any]]) -> dict[str, object]:
    # Only explicit task wording is a layout signal. Incidental KB prose (for
    # example a field named "关联") must not turn an ordinary hierarchy into a star map.
    text = topic
    for rule in TEMPLATE_RULES[:-1]:
        if any(keyword in text for keyword in rule["keywords"]):
            return dict(rule)
    return dict(TEMPLATE_RULES[-1])


def fun_preference(profile: dict[str, Any] | None) -> float | None:
    value = (profile or {}).get("planning_params", {}).get("fun_preference")
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if 0 <= result <= 1 else None


def default_template(candidates: tuple[str, ...], profile: dict[str, Any] | None) -> str:
    fun = fun_preference(profile)
    goal = str((profile or {}).get("learning_goal", {}).get("label", ""))
    if fun is not None and fun >= 0.7 and "herb-garden" in candidates:
        return "herb-garden"
    if "应试" in goal and "warm-gold" in candidates:
        return "warm-gold"
    if "warm-gold" in candidates:
        return "warm-gold"
    return candidates[0] if candidates else DEFAULT_TEMPLATE_ID


def choice_reason(feature: dict[str, object], template_id: str, profile: dict[str, Any] | None) -> str:
    reason = f"{feature['reason']}→{template_id}"
    fun = fun_preference(profile)
    if fun is not None and template_id == "herb-garden":
        reason += f"；趣味偏好{fun:.1f}→清新药园加权"
    return reason
