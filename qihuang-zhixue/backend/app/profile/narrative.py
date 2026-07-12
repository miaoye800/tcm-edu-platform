"""Structured learning-profile narrative synthesis.

The LLM may choose which existing profile facts to emphasize, but the final
text is rendered from validated seven-dimension data only. This keeps the
narrative useful without allowing invented dimensions, labels, or weak points.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings, get_settings
from app.llm.clients import ChatMessage
from app.llm.router import ModelRouter
from app.profile.schema import DIMENSION_LABELS, DimensionKey, Profile

LLMCall = Callable[[Sequence[ChatMessage]], Awaitable[str]]

DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.5
WEAK_SCORE_THRESHOLD = 40  # 数值维分数低于此值且 status=="ready" 才算真·薄弱
NARRATIVE_TASK_TYPE = "profile_narrative"
NARRATIVE_TIMEOUT_SECONDS = 2.5
NARRATIVE_MAX_TOKENS = 320
NARRATIVE_THINKING = {"type": "disabled"}
NARRATIVE_RESPONSE_FORMAT = {"type": "json_object"}
MOCK_FALLBACK_PREFIX = "[MOCK:"


class WeakPointSummary(BaseModel):
    """Evidence-backed weak point label and objective occurrence strength."""

    model_config = ConfigDict(frozen=True)

    label: str
    strength: int = Field(ge=1)


class DimensionNarrativeFact(BaseModel):
    """One dimension fact allowed to appear in the narrative."""

    model_config = ConfigDict(frozen=True)

    key: DimensionKey
    name: str
    score: float | None = None
    anchor: str | None = None
    label: str | None = None
    distribution: dict[str, float] = Field(default_factory=dict)
    weak_points: list[WeakPointSummary] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    status: Literal["ready", "待补充"]
    evidence_count: int = Field(ge=0)
    evidence_refs: list[str] = Field(default_factory=list)
    narrative: str | None = None


class NarrativeItem(BaseModel):
    """A rendered structured item for advantages or weak points."""

    model_config = ConfigDict(frozen=True)

    dimension_key: DimensionKey
    dimension: str
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    status: Literal["ready", "待补充"]
    score: float | None = None
    label: str | None = None
    weak_points: list[WeakPointSummary] = Field(default_factory=list)
    evidence_count: int = Field(ge=0)
    evidence_refs: list[str] = Field(default_factory=list)


class ProfileNarrative(BaseModel):
    """API payload consumed by the radar side panel and profile scroll."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    method: Literal["llm", "template"]
    description: str
    overview: str
    advantages: list[NarrativeItem]
    weak_points: list[NarrativeItem]
    advice_intro: str
    dimensions: list[DimensionNarrativeFact]
    low_confidence_dimensions: list[DimensionNarrativeFact]
    guardrails: list[str]
    fallback_reason: str | None = None


class _NarrativePlan(BaseModel):
    """LLM-selected references. Free text from the model is intentionally ignored."""

    strength_dimension_keys: list[str] = Field(default_factory=list)
    weakness_dimension_keys: list[str] = Field(default_factory=list)
    weak_point_labels: list[str] = Field(default_factory=list)
    advice_dimension_keys: list[str] = Field(default_factory=list)


async def synthesize_profile_narrative(
    profile: Profile,
    *,
    router: ModelRouter | None = None,
    settings: Settings | None = None,
    force_llm: bool = False,
    llm_call: LLMCall | None = None,
    low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
) -> ProfileNarrative:
    """Summarize the seven-dimension profile into a guarded narrative."""

    active_settings = settings or get_settings()
    facts = build_dimension_facts(profile, low_confidence_threshold=low_confidence_threshold)
    template_plan = _template_plan(facts)
    plan = template_plan
    method: Literal["llm", "template"] = "template"
    fallback_reason: str | None = None

    if _should_call_llm(active_settings, force_llm=force_llm, llm_call=llm_call):
        try:
            raw = await _call_llm_for_plan(
                facts,
                router=router,
                settings=active_settings,
                llm_call=llm_call,
                low_confidence_threshold=low_confidence_threshold,
            )
            if raw.startswith(MOCK_FALLBACK_PREFIX):
                raise ValueError(_mock_fallback_reason(raw))
            plan = _merge_with_template_plan(_sanitize_plan(raw, facts), template_plan)
            method = "llm"
        except Exception as exc:
            fallback_reason = f"{exc.__class__.__name__}: {exc}"
            plan = template_plan

    return _render_narrative(
        user_id=profile.user_id,
        facts=facts,
        plan=plan,
        method=method,
        fallback_reason=fallback_reason,
    )


def build_dimension_facts(
    profile: Profile,
    *,
    low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
) -> list[DimensionNarrativeFact]:
    """Extract the exact seven dimension facts that may be cited."""

    facts: list[DimensionNarrativeFact] = []
    for key, name in DIMENSION_LABELS.items():
        dimension = getattr(profile, key)
        score = getattr(dimension, "score", None)
        label = getattr(dimension, "label", None)
        distribution = {
            str(item_key): float(item_value)
            for item_key, item_value in getattr(dimension, "distribution", {}).items()
        }
        weak_points = [
            WeakPointSummary(label=item.label, strength=item.strength)
            for item in getattr(dimension, "items", [])
        ]
        confidence = float(getattr(dimension, "confidence", 0.0) or 0.0)
        facts.append(
            DimensionNarrativeFact(
                key=key,
                name=name,
                score=float(score) if score is not None else None,
                anchor=_anchor_text(key, float(score)) if score is not None else None,
                label=str(label) if label is not None else None,
                distribution=distribution,
                weak_points=weak_points,
                confidence=round(confidence, 4),
                status="ready" if confidence >= low_confidence_threshold else "待补充",
                evidence_count=len(getattr(dimension, "evidence", []) or []),
                evidence_refs=_evidence_refs(getattr(dimension, "evidence", []) or []),
                narrative=(str(dimension.narrative) if getattr(dimension, "narrative", "") else None),
            )
        )
    return facts


def build_narrative_messages(
    facts: list[DimensionNarrativeFact],
    *,
    low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
) -> list[ChatMessage]:
    """Build a strict prompt that asks the model for references, not facts."""

    facts_json = json.dumps([fact.model_dump(mode="json") for fact in facts], ensure_ascii=False, indent=2)
    available_weak_points = sorted({item.label for fact in facts for item in fact.weak_points})
    schema = {
        "strength_dimension_keys": ["knowledge_base", "learning_goal"],
        "weakness_dimension_keys": ["weak_points"],
        "weak_point_labels": available_weak_points[:2],
        "advice_dimension_keys": ["weak_points", "learning_style"],
    }
    return [
        {
            "role": "system",
            "content": (
                "你是「岐黄智学」学习画像叙述编排器。只允许引用输入 JSON 中已经存在的七维画像事实，"
                "不得创造新维度、新标签、新薄弱点、新分数或新证据。低置信维度必须视为“待补充”。"
                "只输出 JSON，不要 Markdown，不要额外解释。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"低置信阈值：confidence < {low_confidence_threshold:.2f}。\n"
                f"七维画像事实：{facts_json}\n"
                f"可引用薄弱标签：{json.dumps(available_weak_points, ensure_ascii=False)}\n"
                "请只选择要强调的 dimension key 和 weak_point label，生成优势、薄弱点、学习建议引子的结构化引用。"
                "不要写自然语言长文，最终文字会由系统根据真实数据渲染。\n"
                f"输出 JSON 结构示例：{json.dumps(schema, ensure_ascii=False)}"
            ),
        },
    ]


def _template_plan(facts: list[DimensionNarrativeFact]) -> _NarrativePlan:
    strengths = [_fact_key(fact) for fact in facts if _is_template_strength(fact)]
    real_weaknesses = [_fact_key(fact) for fact in facts if _is_real_weakness(fact)]
    if not strengths:
        strengths = [
            _fact_key(fact)
            for fact in facts
            if fact.key != "weak_points" and fact.status == "ready" and (fact.label or fact.score is not None)
        ][:2]
    weak_labels = [item.label for fact in facts if fact.key == "weak_points" for item in fact.weak_points]
    weaknesses = _unique_strings(real_weaknesses)
    advice = real_weaknesses
    if not advice:
        advice = strengths[:2]
    return _NarrativePlan(
        strength_dimension_keys=strengths[:3],
        weakness_dimension_keys=weaknesses[:3],
        weak_point_labels=weak_labels[:5],
        advice_dimension_keys=advice[:3],
    )


def _sanitize_plan(raw: str, facts: list[DimensionNarrativeFact]) -> _NarrativePlan:
    payload = _parse_json_object(raw)
    plan = _NarrativePlan.model_validate(payload)
    fact_by_key = {fact.key: fact for fact in facts}
    valid_keys = set(fact_by_key)
    ready_keys = {fact.key for fact in facts if fact.status == "ready"}
    valid_weak_labels = {item.label for fact in facts for item in fact.weak_points}

    strengths = [
        key for key in _unique_strings(plan.strength_dimension_keys)
        if key in ready_keys and key != "weak_points"
    ]
    weaknesses = [
        key for key in _unique_strings(plan.weakness_dimension_keys)
        if key in valid_keys and _is_real_weakness(fact_by_key[key])
    ]
    weak_labels = [label for label in _unique_strings(plan.weak_point_labels) if label in valid_weak_labels]
    advice = [key for key in _unique_strings(plan.advice_dimension_keys) if key in valid_keys]
    return _NarrativePlan(
        strength_dimension_keys=strengths,
        weakness_dimension_keys=weaknesses,
        weak_point_labels=weak_labels,
        advice_dimension_keys=advice,
    )


def _merge_with_template_plan(plan: _NarrativePlan, template: _NarrativePlan) -> _NarrativePlan:
    return _NarrativePlan(
        strength_dimension_keys=_unique_strings(plan.strength_dimension_keys + template.strength_dimension_keys)[:3],
        weakness_dimension_keys=_unique_strings(plan.weakness_dimension_keys + template.weakness_dimension_keys)[:3],
        weak_point_labels=_unique_strings(plan.weak_point_labels + template.weak_point_labels)[:5],
        advice_dimension_keys=_unique_strings(plan.advice_dimension_keys + template.advice_dimension_keys)[:3],
    )


async def _call_llm_for_plan(
    facts: list[DimensionNarrativeFact],
    *,
    router: ModelRouter | None,
    settings: Settings,
    llm_call: LLMCall | None,
    low_confidence_threshold: float,
) -> str:
    messages = build_narrative_messages(facts, low_confidence_threshold=low_confidence_threshold)
    if llm_call is not None:
        return await llm_call(messages)
    active_router = router or ModelRouter(settings=settings)
    client = _select_narrative_client(active_router)
    return await client.chat(
        messages,
        temperature=0,
        max_tokens=NARRATIVE_MAX_TOKENS,
        thinking=NARRATIVE_THINKING,
        response_format=NARRATIVE_RESPONSE_FORMAT,
        timeout_seconds=NARRATIVE_TIMEOUT_SECONDS,
        prefer_reasoning_content=True,
    )


def _select_narrative_client(router: ModelRouter):
    try:
        return router.select(NARRATIVE_TASK_TYPE)
    except ValueError as exc:
        if NARRATIVE_TASK_TYPE not in str(exc):
            raise
        return router.select("generate")


def _render_narrative(
    *,
    user_id: str,
    facts: list[DimensionNarrativeFact],
    plan: _NarrativePlan,
    method: Literal["llm", "template"],
    fallback_reason: str | None,
) -> ProfileNarrative:
    fact_by_key = {fact.key: fact for fact in facts}
    pending = [fact for fact in facts if fact.status == "待补充"]
    ready = [fact for fact in facts if fact.status == "ready"]
    advantages = [
        _advantage_item(fact_by_key[key])
        for key in plan.strength_dimension_keys
        if key in fact_by_key and fact_by_key[key].status == "ready"
    ]
    weak_items = [
        _weakness_item(fact_by_key[key], selected_labels=plan.weak_point_labels)
        for key in plan.weakness_dimension_keys
        if key in fact_by_key
    ]
    weak_items = [item for item in weak_items if item is not None]
    advice_intro = _advice_intro(facts, plan, weak_items)
    overview = _overview(ready)

    advantage_text = (
        "优势：" + _join_summaries(item.summary for item in advantages)
        if advantages
        else "优势：暂无足够高置信证据，需要继续收集。"
    )
    weakness_text = (
        "薄弱点：" + _join_summaries(item.summary for item in weak_items)
        if weak_items
        else "薄弱点：暂无高置信薄弱点证据。"
    )
    pending_text = (
        "待补充：" + "、".join(fact.name for fact in pending) + "。"
        if pending
        else ""
    )
    description = " ".join(part for part in [overview, advantage_text, weakness_text, pending_text, advice_intro] if part)

    return ProfileNarrative(
        user_id=user_id,
        method=method,
        description=description,
        overview=overview,
        advantages=advantages,
        weak_points=weak_items,
        advice_intro=advice_intro,
        dimensions=facts,
        low_confidence_dimensions=pending,
        guardrails=[
            "只引用七维画像快照中的 score、label、weak_points、confidence 与 evidence_count。",
            "confidence 低于阈值的维度在叙述中标注“待补充”。",
            "LLM 输出的新维度、新标签、新薄弱点会被过滤，最终文本由验证后的结构化事实渲染。",
        ],
        fallback_reason=fallback_reason,
    )


def _overview(ready: list[DimensionNarrativeFact]) -> str:
    if not ready:
        return "当前画像暂无高置信维度。"
    if len(ready) < len(DIMENSION_LABELS):
        return f"当前画像已有 {len(ready)} 个高置信维度。"
    return "当前画像七个维度均已有高置信数据。"


def _advantage_item(fact: DimensionNarrativeFact) -> NarrativeItem:
    return _item_from_fact(fact, _advantage_summary(fact))


def _weakness_item(
    fact: DimensionNarrativeFact,
    *,
    selected_labels: list[str],
) -> NarrativeItem | None:
    weak_points = fact.weak_points
    if fact.key == "weak_points" and selected_labels:
        selected = set(selected_labels)
        weak_points = [item for item in fact.weak_points if item.label in selected]
    summary = _weakness_summary(fact, weak_points)
    if not summary:
        return None
    return _item_from_fact(fact, summary, weak_points=weak_points)


def _item_from_fact(
    fact: DimensionNarrativeFact,
    summary: str,
    *,
    weak_points: list[WeakPointSummary] | None = None,
) -> NarrativeItem:
    return NarrativeItem(
        dimension_key=fact.key,
        dimension=fact.name,
        summary=summary,
        confidence=fact.confidence,
        status=fact.status,
        score=fact.score,
        label=fact.label,
        weak_points=fact.weak_points if weak_points is None else weak_points,
        evidence_count=fact.evidence_count,
        evidence_refs=fact.evidence_refs,
    )


def _advantage_summary(fact: DimensionNarrativeFact) -> str:
    if fact.score is not None:
        if fact.key == "knowledge_base":
            anchor = f"（{fact.anchor}）" if fact.anchor else ""
            return f"知识基础为 {fact.score:.1f} 分{anchor}，已形成可继续推进的基础。"
        if fact.key == "motivation":
            label = f"，动机标签为{fact.label}" if fact.label else ""
            anchor = f"（{fact.anchor}）" if fact.anchor else ""
            return f"动机强度为 {fact.score:.1f} 分{anchor}{label}。"
        if fact.key == "pace":
            anchor = f"（{fact.anchor}）" if fact.anchor else ""
            return f"学习节奏分为 {fact.score:.1f} 分{anchor}，当前节奏线索明确。"
    if fact.label:
        return f"{fact.name}已明确为“{fact.label}”。"
    return f"{fact.name}已有高置信画像线索。"


def _weakness_summary(fact: DimensionNarrativeFact, weak_points: list[WeakPointSummary]) -> str:
    if fact.status == "待补充":
        return f"{fact.name}待补充，当前置信度 {fact.confidence:.2f}。"
    if fact.key == "weak_points" and weak_points:
        labels = "、".join(f"{item.label}({item.strength}次)" for item in weak_points)
        return f"薄弱标签集中在 {labels}。"
    if fact.key == "knowledge_base" and fact.score is not None:
        anchor = f"（{fact.anchor}）" if fact.anchor else ""
        return f"知识基础为 {fact.score:.1f} 分{anchor}，仍需回到证据对应知识点巩固。"
    if fact.key == "motivation" and fact.score is not None:
        label = f"，动机标签为{fact.label}" if fact.label else ""
        anchor = f"（{fact.anchor}）" if fact.anchor else ""
        return f"动机强度为 {fact.score:.1f} 分{anchor}{label}，需要更清晰的目标牵引。"
    if fact.key == "pace" and fact.score is not None:
        anchor = f"（{fact.anchor}）" if fact.anchor else ""
        return f"学习节奏分为 {fact.score:.1f} 分{anchor}，节奏安排需要继续校准。"
    if fact.label:
        return f"{fact.name}偏向“{fact.label}”，可继续用后续证据校验。"
    return ""


def _advice_intro(
    facts: list[DimensionNarrativeFact],
    plan: _NarrativePlan,
    weak_items: list[NarrativeItem],
) -> str:
    fact_by_key = {fact.key: fact for fact in facts}
    weak_labels = [
        item.label
        for label in plan.weak_point_labels
        for fact in facts
        for item in fact.weak_points
        if item.label == label
    ]
    if not weak_labels:
        weak_labels = [item.label for fact in facts if fact.key == "weak_points" for item in fact.weak_points]

    if weak_labels:
        focus = "、".join(_unique_strings(weak_labels)[:3])
        intro = f"学习建议引子：先围绕 {focus} 安排复盘与练习"
    elif weak_items:
        focus = "、".join(item.dimension for item in weak_items[:2])
        intro = f"学习建议引子：先围绕 {focus} 做针对性巩固"
    else:
        focus_keys = [key for key in plan.advice_dimension_keys if key in fact_by_key]
        focus = "、".join(fact_by_key[key].name for key in focus_keys[:2]) or "当前优势维度"
        intro = f"学习建议引子：围绕 {focus} 继续加深应用"

    style = _ready_label(fact_by_key.get("learning_style"))
    resource = _ready_label(fact_by_key.get("resource_preference"))
    additions: list[str] = []
    if style:
        additions.append(f"学习风格可按“{style}”组织")
    if resource:
        additions.append(f"资源呈现可优先采用“{resource}”")
    if additions:
        intro = f"{intro}，{'，'.join(additions)}"
    return f"{intro}。"


def _is_template_strength(fact: DimensionNarrativeFact) -> bool:
    if fact.status != "ready":
        return False
    if fact.key == "knowledge_base":
        return fact.score is not None and fact.score >= 60
    if fact.key == "motivation":
        return fact.score is not None and fact.score >= 60
    if fact.key == "pace":
        return fact.score is not None and fact.score >= 31
    return bool(fact.label) and fact.key != "weak_points"


def _is_template_weakness(fact: DimensionNarrativeFact) -> bool:
    return _is_real_weakness(fact)


def _is_real_weakness(fact: DimensionNarrativeFact) -> bool:
    if fact.status != "ready":
        return False
    if fact.key == "weak_points":
        return bool(fact.weak_points)
    if fact.key in {"knowledge_base", "pace", "motivation"}:
        return fact.score is not None and fact.score < WEAK_SCORE_THRESHOLD
    return False


def _join_summaries(summaries: Sequence[str]) -> str:
    cleaned = [summary.rstrip("。；; ") for summary in summaries if summary.strip()]
    return "；".join(cleaned) + "。"


def _anchor_text(key: DimensionKey, score: float) -> str | None:
    if key == "knowledge_base":
        if score <= 20:
            return "药名认不全，性味归经空白"
        if score <= 40:
            return "认得常见药名，功效模糊"
        if score <= 60:
            return "掌握常用药功效，配伍薄弱"
        if score <= 80:
            return "功效+配伍较熟，能应对多数病例"
        return "配伍加减熟练、剂量精准"
    if key == "motivation":
        if score <= 20:
            return "被动应付，几乎无自驱"
        if score <= 40:
            return "需外部持续推动"
        if score <= 60:
            return "有意愿，需目标牵引"
        if score <= 80:
            return "主动性强，能自我安排"
        return "高度自驱，主动找挑战"
    if key == "pace":
        if score <= 30:
            return "慢：每天 <15min / 目标周期宽松"
        if score <= 70:
            return "中：每天 15-40min"
        return "快：每天 >40min / 目标周期紧"
    return None


def _ready_label(fact: DimensionNarrativeFact | None) -> str | None:
    if fact is None or fact.status != "ready":
        return None
    return fact.label


def _fact_key(fact: DimensionNarrativeFact) -> str:
    return str(fact.key)


def _evidence_refs(cards: Sequence[Any]) -> list[str]:
    return [
        f"{getattr(card, 'source', '证据')}#{index}"
        for index, card in enumerate(cards, start=1)
    ]


def _unique_strings(items: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        result.append(stripped)
    return result


def _parse_json_object(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    return json.loads(match.group(0) if match else stripped)


def _should_call_llm(settings: Settings, *, force_llm: bool, llm_call: LLMCall | None) -> bool:
    if llm_call is not None or force_llm:
        return True
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
    return "router returned mock response"


__all__ = [
    "DEFAULT_LOW_CONFIDENCE_THRESHOLD",
    "WEAK_SCORE_THRESHOLD",
    "DimensionNarrativeFact",
    "NarrativeItem",
    "ProfileNarrative",
    "WeakPointSummary",
    "build_dimension_facts",
    "build_narrative_messages",
    "synthesize_profile_narrative",
]
