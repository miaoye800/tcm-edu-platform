"""Weighted evidence fusion for learning-profile scoring.

The rule layer never invents a subjective judgment. It only converts objective
signals on EvidenceCard records into numeric evidence values or category votes,
then applies the scoring formula from ``画像评分细则.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from app.profile.anchors import base_level_midpoints, midpoint_from_signal
from app.profile.schema import CATEGORY_DIMENSIONS, DIMENSION_LABELS, DimensionKey, EvidenceCard, EvidenceTier
from app.profile.component_model import AtomicEvidence, COMPONENTS, COMPONENT_WEIGHTS, confidence_from_evidence

HALF_LIFE_DAYS: dict[DimensionKey, float | None] = {
    "knowledge_base": 30.0,
    "learning_goal": None,
    "learning_style": None,
    "weak_points": 30.0,
    "pace": None,
    "motivation": None,
    "resource_preference": None,
}

ALLOWED_LABELS: dict[DimensionKey, tuple[str, ...]] = {
    # Assumption for dialogue cards: "其他" is only a traceable fallback bucket for
    # goals outside the four canonical classes; fusion still uses the same vote
    # and confidence formula and does not infer any new semantic score.
    "learning_goal": ("应试提分", "临床实用", "兴趣科普", "学术深造", "基础巩固", "能力提升", "其他"),
    "learning_style": ("视觉型", "文字型", "记忆(口诀)型"),
    "motivation": ("内驱", "外驱", "内外混合", "被动"),
    "resource_preference": ("知识图谱", "拟人化短剧", "汤头歌诀", "医案文选"),
}

DEFAULT_CONFIDENCE_WEIGHT_K = 3.0
NUMERIC_VARIANCE_NORMALIZER = 2500.0
# Compatibility view for callers that display the nearest base-level label.
BASE_LEVEL_SCORES: dict[str, float] = base_level_midpoints()

# Objective dimensions require stronger-than-self-report evidence to become solid.
OBJECTIVE_CONFIDENCE_DIMENSIONS: frozenset[DimensionKey] = frozenset(
    {"knowledge_base", "weak_points", "pace"}
)
EVIDENCE_TIER_CONFIDENCE_CAPS: dict[EvidenceTier, float] = {
    EvidenceTier.SELF_REPORT: 0.35,
    EvidenceTier.BEHAVIORAL: 0.6,
    EvidenceTier.MEASURED: 1.0,
}
EVIDENCE_TIER_RANK: dict[EvidenceTier, int] = {
    EvidenceTier.SELF_REPORT: 0,
    EvidenceTier.BEHAVIORAL: 1,
    EvidenceTier.MEASURED: 2,
}

# Only assertion-bearing fields participate in self-report deduplication;
# telemetry such as turn number and response latency must not make a repeat new.
SELF_REPORT_ASSERTION_FIELDS: frozenset[str] = frozenset(
    {
        "value",
        "score",
        "evidence_value",
        "numeric_value",
        "accuracy",
        "correct_rate",
        "gave_up",
        "abandoned",
        "correct",
        "is_correct",
        "对症",
        "配伍",
        "剂量",
        "误用毒烈",
        "prior_score",
        "base_score",
        "label",
        "base_level",
        "daily_minutes",
        "minutes_per_day",
        "target_days",
        "period_days",
        "motivation",
        "motivation_score",
        "strength",
        "accept_challenge",
        "challenge",
        "weak_labels",
        "knowledge_point",
        "topic",
        "votes",
        "distribution",
        "resource_type",
        "identity_card",
    }
)
SELF_REPORT_REPEAT_DECAY_BASE = 0.5


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
    # ``distribution`` is the observed composition of cards.  These fields are
    # intentionally separate: a single observed card can occupy 100% of the
    # observed distribution without claiming the whole person is known.
    radar_score: float | None = None
    coverage: float = 0.0
    unknown_reasons: list[str] = field(default_factory=list)
    confidence_breakdown: dict[str, float] = field(default_factory=dict)
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
            "radar_score": self.radar_score,
            "coverage": self.coverage,
            "unknown_reasons": self.unknown_reasons,
            "confidence_breakdown": self.confidence_breakdown,
            "metadata": self.metadata,
        }


def fuse_profile(
    evidence_cards: list[EvidenceCard],
    *,
    now: datetime | None = None,
    confidence_weight_k: float = DEFAULT_CONFIDENCE_WEIGHT_K,
    confidence_weight_dimensions: tuple[DimensionKey, ...] | None = None,
    self_report_trust: float = 1.0,
) -> dict[DimensionKey, FusionResult]:
    """Fuse all seven dimensions from evidence cards."""

    return {
        dimension: fuse_dimension(
            dimension,
            evidence_cards,
            now=now,
            confidence_weight_k=(
                confidence_weight_k
                if confidence_weight_dimensions is None or dimension in confidence_weight_dimensions
                else DEFAULT_CONFIDENCE_WEIGHT_K
            ),
            self_report_trust=self_report_trust,
        )
        for dimension in DIMENSION_LABELS
    }


def fuse_dimension(
    dimension: DimensionKey,
    evidence_cards: list[EvidenceCard],
    *,
    now: datetime | None = None,
    confidence_weight_k: float = DEFAULT_CONFIDENCE_WEIGHT_K,
    self_report_trust: float = 1.0,
) -> FusionResult:
    """Fuse one dimension using the weighted + confidence + decay formula."""

    cards = [card for card in evidence_cards if card.dimension == dimension]
    if dimension in CATEGORY_DIMENSIONS or dimension == "weak_points":
        result = _fuse_label_dimension(
            dimension,
            cards,
            now=now,
            confidence_weight_k=confidence_weight_k,
            self_report_trust=self_report_trust,
        )
    elif dimension == "motivation":
        result = _fuse_motivation(
            cards,
            now=now,
            confidence_weight_k=confidence_weight_k,
            self_report_trust=self_report_trust,
        )
    else:
        result = _fuse_numeric_dimension(
            dimension,
            cards,
            now=now,
            confidence_weight_k=confidence_weight_k,
            self_report_trust=self_report_trust,
        )
    return _with_agent_assessment(result, cards, now=now)


def _with_agent_assessment(
    result: FusionResult,
    cards: list[EvidenceCard],
    *,
    now: datetime | None,
) -> FusionResult:
    """Aggregate semantic values supplied by the extraction agent.

    This function deliberately does *not* interpret wording or labels. It
    validates the already structured agent judgment, keeps one strongest
    contribution per source utterance, and combines independent confirmations
    probabilistically. Therefore a second independently expressed statement
    raises confirmation, while several cards from a single answer do not.
    """

    unit_candidates: dict[str, tuple[EvidenceCard, dict[str, float | str | list[str]]]] = {}
    for index, card in enumerate(cards):
        raw = card.objective_signal.get("agent_assessment")
        if not isinstance(raw, dict):
            continue
        assessment = _valid_agent_assessment(raw)
        if assessment is None:
            continue
        unit_id = str(card.objective_signal.get("evidence_unit_id") or f"card:{index}")
        existing = unit_candidates.get(unit_id)
        if existing is None or float(assessment["confidence_contribution"]) > float(existing[1]["confidence_contribution"]):
            unit_candidates[unit_id] = (card, assessment)

    component_cards = [card for card in cards if card.component]
    component_gate: dict[str, Any] | None = None
    component_weighted_score: float | None = None
    if component_cards:
        required = set(COMPONENTS.get(result.dimension, ()))
        collected = {
            card.component for card in component_cards
            if card.component in required and card.collection_status == "scorable"
        }
        component_scores: dict[str, float] = {}
        for card in component_cards:
            raw_score = card.objective_signal.get("component_score")
            try:
                parsed_score = float(raw_score)
            except (TypeError, ValueError):
                continue
            if card.component in required and 5 <= parsed_score <= 95:
                component_scores[card.component] = parsed_score
        missing = sorted(required - collected)
        unscored = sorted(required - set(component_scores))
        component_gate = {
            "collected": len(collected), "total": len(required), "missing_components": missing,
            "unscored_components": unscored, "component_scores": component_scores,
            "scoring_phase": "scored" if not missing and not unscored else "collecting",
        }
        if missing or unscored:
            result = replace(
                result, score=None, radar_score=None,
                narrative=f"{result.dimension_label}已收集 {len(collected)}/{len(required)} 个组件，评分暂缓。",
                metadata={**result.metadata, "component_collection": component_gate},
            )
    if not unit_candidates:
        if not component_cards:
            return result
        # Component-contract evidence has its own confidence formula and does
        # not require the legacy one-card assessment fields. Keep the score
        # pending until all components have a score, but still expose the
        # verified collection/confidence ledger.
        atoms: list[AtomicEvidence] = []
        for card in component_cards:
            try:
                atoms.append(AtomicEvidence(
                    evidence_id=card.evidence_id,
                    dimension=card.dimension,
                    component=card.component or "",
                    evidence_group_id=card.evidence_group_id or str(card.objective_signal.get("evidence_group_id") or card.evidence_id),
                    source_turn_ids=card.source_turn_ids,
                    quote=card.quote or "",
                    normalized_claim=card.normalized_claim or card.fact,
                    evidence_type=card.objective_signal.get("evidence_type", "self_report"),
                    collection_status=card.collection_status or "pending",
                    semantic_relation=card.semantic_relation or "new",
                    quality_factors=card.quality_factors or card.objective_signal.get("quality_factors", {}),
                ))
            except Exception:
                continue
        if not atoms:
            return result
        raw_type = str(component_cards[0].objective_signal.get("combination_type") or "single_clear_self_report")
        allowed_types = {"single_vague_self_report", "single_clear_self_report", "multi_turn_clear_pending_crosscheck", "multi_turn_clear_consistent", "multi_turn_self_report_with_priority", "dialogue_behavior_consistent", "dialogue_behavior_measurement_long_term"}
        component_confidence = confidence_from_evidence(atoms, raw_type if raw_type in allowed_types else "single_clear_self_report")
        metadata = {
            **result.metadata,
            "semantic_assessment": True,
            "component_collection": component_gate or {},
            "component_confidence": component_confidence,
            "confidence_formula": component_confidence["formula"],
            "confidence_units": [],
            "observed_composition_is_not_coverage": True,
        }
        complete_scores = component_gate is not None and component_gate.get("scoring_phase") == "scored"
        weighted = None
        if complete_scores:
            scores = component_gate.get("component_scores", {})
            weighted = round(max(5.0, min(95.0, sum(scores[key] * COMPONENT_WEIGHTS[result.dimension][key] for key in COMPONENTS[result.dimension]))), 2)
        return replace(
            result,
            score=weighted,
            radar_score=weighted,
            confidence=component_confidence["confidence"],
            confidence_breakdown={"semantic_evidence": component_confidence["confidence"], "independent_units": float(len(component_confidence["independent_source_groups"]))},
            metadata=metadata,
        )

    contributions: list[float] = []
    coverage_terms: list[tuple[float, float]] = []
    radar_terms: list[tuple[float, float]] = []
    unknown_reasons: list[str] = []
    confidence_units: list[dict[str, float | str | None]] = []
    for card, assessment in unit_candidates.values():
        decay = decay_factor(card, now=now)
        contribution = float(assessment["confidence_contribution"]) * float(assessment["independence"]) * decay
        contribution = _clamp01(contribution)
        contributions.append(contribution)
        confidence_units.append(
            {
                "source_unit": str(card.objective_signal.get("evidence_unit_id") or card.evidence_id),
                "fact": card.fact,
                "agent_contribution": round(float(assessment["confidence_contribution"]), 4),
                "independence": round(float(assessment["independence"]), 4),
                "decay": round(decay, 4),
                "effective_contribution": round(contribution, 4),
                "agent_radar_score": assessment.get("radar_score"),
            }
        )
        evidence_weight = contribution
        coverage_terms.append((float(assessment["coverage"]), evidence_weight))
        radar_score = assessment.get("radar_score")
        if isinstance(radar_score, (int, float)):
            # Contribution, coverage and priority are all agent authored;
            # their mean is only an aggregation weight, not a lexical rule.
            radar_weight = (
                float(assessment["confidence_contribution"])
                + float(assessment["coverage"])
                + float(assessment["priority_strength"])
            ) / 3
            if radar_weight > 0:
                radar_terms.append((float(radar_score), radar_weight * decay))
        for reason in assessment["unknown_reasons"]:
            if reason not in unknown_reasons:
                unknown_reasons.append(reason)

    semantic_confidence = 1.0
    for contribution in contributions:
        semantic_confidence *= 1.0 - contribution
    semantic_confidence = round(_clamp01(1.0 - semantic_confidence), 4)
    component_confidence: dict[str, Any] | None = None
    if component_cards and all(card.quality_factors for card in component_cards):
        atoms: list[AtomicEvidence] = []
        for card in component_cards:
            try:
                atoms.append(AtomicEvidence(
                    evidence_id=card.evidence_id,
                    dimension=card.dimension,
                    component=card.component or "",
                    evidence_group_id=card.evidence_group_id or str(card.objective_signal.get("evidence_group_id") or card.evidence_id),
                    source_turn_ids=card.source_turn_ids,
                    quote=card.quote or "",
                    normalized_claim=card.normalized_claim or card.fact,
                    evidence_type=card.objective_signal.get("evidence_type", "self_report"),
                    collection_status=card.collection_status or "pending",
                    semantic_relation=card.semantic_relation or "new",
                    quality_factors=card.quality_factors,
                ))
            except Exception:
                continue
        if atoms:
            raw_type = str(component_cards[0].objective_signal.get("combination_type") or "single_clear_self_report")
            allowed_types = {"single_vague_self_report", "single_clear_self_report", "multi_turn_clear_pending_crosscheck", "multi_turn_clear_consistent", "multi_turn_self_report_with_priority", "dialogue_behavior_consistent", "dialogue_behavior_measurement_long_term"}
            component_confidence = confidence_from_evidence(atoms, raw_type if raw_type in allowed_types else "single_clear_self_report")
            semantic_confidence = component_confidence["confidence"]
    coverage = _weighted_mean(coverage_terms)
    radar_score = _weighted_mean(radar_terms)
    if radar_score is not None:
        radar_score = round(max(5.0, min(95.0, radar_score)), 2)
    metadata = {
        **result.metadata,
        "semantic_assessment": True,
        "assessment_units": len(unit_candidates),
        "confidence_formula": "1-product(1-agent_contribution*independence*decay)",
        "radar_formula": "weighted_mean(agent_radar_score, (agent_contribution+coverage+priority_strength)/3*decay)",
        "confidence_units": confidence_units,
        "observed_composition_is_not_coverage": True,
    }
    if component_confidence is not None:
        metadata["component_confidence"] = component_confidence
    if component_gate is not None:
        metadata["component_collection"] = component_gate
        if component_gate["scoring_phase"] == "scored":
            component_scores = {
                card.component: float(card.objective_signal["component_score"])
                for card in component_cards
                if card.component and isinstance(card.objective_signal.get("component_score"), (int, float))
            }
            weighted = sum(component_scores[key] * COMPONENT_WEIGHTS[result.dimension][key] for key in COMPONENTS[result.dimension])
            component_weighted_score = round(max(5.0, min(95.0, weighted)), 2)
            result = replace(result, score=component_weighted_score, radar_score=component_weighted_score)
    return replace(
        result,
        confidence=semantic_confidence,
        radar_score=component_weighted_score if component_weighted_score is not None else radar_score,
        coverage=round(coverage or 0.0, 4),
        unknown_reasons=unknown_reasons[:5],
        confidence_breakdown={
            "semantic_evidence": semantic_confidence,
            "independent_units": float(len(unit_candidates)),
            "coverage": round(coverage or 0.0, 4),
        },
        metadata=metadata,
    )


def _valid_agent_assessment(raw: dict[str, Any]) -> dict[str, float | str | list[str]] | None:
    """Validate an LLM assessment without adding semantic interpretation."""

    try:
        contribution = float(raw.get("confidence_contribution", 0.0))
        coverage = float(raw.get("coverage", 0.0))
        priority = float(raw.get("priority_strength", 0.0))
        independence = float(raw.get("independence", 1.0))
    except (TypeError, ValueError):
        return None
    if not all(isfinite(value) for value in (contribution, coverage, priority, independence)):
        return None
    radar_score: float | None = None
    raw_score = raw.get("radar_score")
    if raw_score is not None:
        try:
            parsed_score = float(raw_score)
        except (TypeError, ValueError):
            return None
        if not isfinite(parsed_score) or not 5.0 <= parsed_score <= 95.0:
            return None
        radar_score = parsed_score
    reasons = raw.get("unknown_reasons")
    if not isinstance(reasons, list):
        reasons = []
    return {
        "radar_score": radar_score,
        "confidence_contribution": _clamp01(min(contribution, 0.95)),
        "coverage": _clamp01(coverage),
        "priority_strength": _clamp01(priority),
        "independence": _clamp01(independence),
        "unknown_reasons": list(dict.fromkeys(str(reason).strip() for reason in reasons if str(reason).strip()))[:5],
    }


def _weighted_mean(terms: list[tuple[float, float]]) -> float | None:
    denominator = sum(weight for _, weight in terms if weight > 0)
    if denominator <= 0:
        return None
    return sum(value * weight for value, weight in terms if weight > 0) / denominator


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
    self_report_trust: float,
) -> FusionResult:
    cards = _latest_elo_snapshot_cards(dimension, cards)
    weighted_values: list[tuple[float, float, float]] = []
    contributing_cards: list[EvidenceCard] = []
    effective_weights = effective_card_weights(cards, self_report_trust=self_report_trust)
    for card, effective_weight in zip(cards, effective_weights, strict=True):
        value = score_from_evidence_value(card)
        if value is None:
            continue
        decay = decay_factor(card, now=now)
        weighted_values.append((value, effective_weight, decay))
        contributing_cards.append(card)

    numerator = sum(value * weight * decay for value, weight, decay in weighted_values)
    denominator = sum(weight * decay for _, weight, decay in weighted_values)
    score = round(numerator / denominator, 2) if denominator > 0 else None
    variance = _weighted_variance(weighted_values, score) if score is not None else 0.0
    total_weight = sum(weight for card, weight in zip(cards, effective_weights, strict=True) if score_from_evidence_value(card) is not None)
    confidence = confidence_from_weight_and_variance(total_weight, variance, k=confidence_weight_k)
    confidence = _cap_confidence_by_tier(dimension, confidence, contributing_cards)
    coverage_count, coverage_target = _coverage_from_cards(dimension, contributing_cards)
    if coverage_count is not None:
        coverage_factor = max(0.5, min(1.0, coverage_count / coverage_target))
        confidence = round(confidence * coverage_factor, 4)
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
        metadata={
            "weighted_denominator": round(denominator, 4),
            "effective_total_weight": round(total_weight, 4),
            "formula": "score_D=sum(value*weight*decay)/sum(weight*decay)",
            **(
                {
                    "coverage_count": coverage_count,
                    "coverage_target": coverage_target,
                    "coverage": round(min(1.0, coverage_count / coverage_target), 4),
                    "coverage_factor": coverage_factor,
                    "coverage_label": f"已考察主题 {coverage_count}/{coverage_target}",
                }
                if coverage_count is not None
                else {}
            ),
        },
    )


def _latest_elo_snapshot_cards(
    dimension: DimensionKey,
    cards: list[EvidenceCard],
) -> list[EvidenceCard]:
    """Keep only the newest cumulative Elo snapshot in numeric fusion.

    Elo cards describe one evolving global rating, not independent observations.
    Retaining historical snapshots would turn a current rating into a historical
    average. Other measured knowledge evidence remains untouched.
    """

    if dimension != "knowledge_base":
        return cards
    snapshots = [card for card in cards if card.source == "Elo 实测能力"]
    if len(snapshots) <= 1:
        return cards
    latest = max(snapshots, key=lambda card: _aware_datetime(card.timestamp))
    return [card for card in cards if card.source != "Elo 实测能力" or card is latest]


def _fuse_motivation(
    cards: list[EvidenceCard],
    *,
    now: datetime | None,
    confidence_weight_k: float,
    self_report_trust: float,
) -> FusionResult:
    numeric = _fuse_numeric_dimension(
        "motivation",
        cards,
        now=now,
        confidence_weight_k=confidence_weight_k,
        self_report_trust=self_report_trust,
    )
    distribution, label, label_weight, label_variance = _label_distribution(
        "motivation", cards, now=now, self_report_trust=self_report_trust
    )
    total_weight = max(
        sum(effective_card_weights(cards, self_report_trust=self_report_trust)),
        label_weight,
    )
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
    self_report_trust: float,
) -> FusionResult:
    distribution, label, total_weight, variance = _label_distribution(
        dimension, cards, now=now, self_report_trust=self_report_trust
    )
    confidence = confidence_from_weight_and_variance(
        total_weight,
        variance,
        k=confidence_weight_k,
        variance_normalizer=1.0,
    )
    confidence = _cap_confidence_by_tier(dimension, confidence, cards)
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
    self_report_trust: float,
) -> tuple[dict[str, float], str | None, float, float]:
    weights: dict[str, float] = {}
    for card, effective_weight in zip(
        cards,
        effective_card_weights(cards, self_report_trust=self_report_trust),
        strict=True,
    ):
        decay = decay_factor(card, now=now)
        for label, vote_weight in _label_votes_from_signal(dimension, card.objective_signal).items():
            weights[label] = weights.get(label, 0.0) + effective_weight * decay * vote_weight

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

    prior = _first_numeric(signal, ("prior_score", "base_score"))
    if prior is not None:
        return _clamp_score(prior)

    return midpoint_from_signal("knowledge_base", signal)


def _pace_value(signal: dict[str, Any]) -> float | None:
    return midpoint_from_signal("pace", signal)


def _motivation_value(signal: dict[str, Any]) -> float | None:
    explicit = _first_numeric(signal, ("motivation_score", "strength"))
    if explicit is not None:
        return _clamp_score(explicit)
    return midpoint_from_signal("motivation", signal)


def effective_card_weights(
    cards: list[EvidenceCard],
    *,
    self_report_trust: float = 1.0,
) -> list[float]:
    """Apply geometric decay to repeated homogeneous self-report assertions."""

    occurrences: dict[str, int] = {}
    weights: list[float] = []
    for card in cards:
        if _effective_tier(card) != EvidenceTier.SELF_REPORT:
            weights.append(card.weight)
            continue
        assertion_hash = _self_report_assertion_hash(card)
        occurrence = occurrences.get(assertion_hash, 0) + 1
        occurrences[assertion_hash] = occurrence
        trust = max(0.3, min(1.0, self_report_trust))
        weights.append(card.weight * trust * (SELF_REPORT_REPEAT_DECAY_BASE ** (occurrence - 1)))
    return weights


def _self_report_assertion_hash(card: EvidenceCard) -> str:
    assertion = {
        key: card.objective_signal[key]
        for key in sorted(SELF_REPORT_ASSERTION_FIELDS)
        if key in card.objective_signal
    }
    if assertion:
        normalized = json.dumps(assertion, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    else:
        normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", card.fact.casefold())
    payload = f"{card.dimension}:{normalized}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _cap_confidence_by_tier(
    dimension: DimensionKey,
    confidence: float,
    cards: list[EvidenceCard],
) -> float:
    if dimension not in OBJECTIVE_CONFIDENCE_DIMENSIONS or not cards:
        return confidence
    if any(card.claim_status == "pending" for card in cards):
        return round(min(confidence, 0.35), 4)
    highest_tier = max((_effective_tier(card) for card in cards), key=EVIDENCE_TIER_RANK.__getitem__)
    return round(min(confidence, EVIDENCE_TIER_CONFIDENCE_CAPS[highest_tier]), 4)


def _effective_tier(card: EvidenceCard) -> EvidenceTier:
    if card.tier == EvidenceTier.SELF_REPORT and card.claim_status == "verified":
        return EvidenceTier.MEASURED
    return card.tier


def _weighted_variance(weighted_values: list[tuple[float, float, float]], mean: float | None) -> float:
    if mean is None:
        return 0.0
    denominator = sum(weight * decay for _, weight, decay in weighted_values)
    if denominator <= 0:
        return 0.0
    return sum(((value - mean) ** 2) * weight * decay for value, weight, decay in weighted_values) / denominator


def _coverage_from_cards(
    dimension: DimensionKey,
    cards: list[EvidenceCard],
) -> tuple[int | None, int]:
    """Read the latest cumulative Elo coverage carried by measured evidence."""

    if dimension != "knowledge_base":
        return None, 8
    coverage_values: list[tuple[int, int]] = []
    for card in cards:
        signal = card.objective_signal
        count = _as_float(signal.get("coverage_count"))
        target = _as_float(signal.get("coverage_target"))
        if count is None:
            continue
        coverage_values.append((max(0, int(count)), max(1, int(target or 8))))
    return max(coverage_values, key=lambda item: item[0]) if coverage_values else (None, 8)


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
    "EVIDENCE_TIER_CONFIDENCE_CAPS",
    "FusionResult",
    "HALF_LIFE_DAYS",
    "confidence_from_weight_and_variance",
    "decay_factor",
    "effective_card_weights",
    "evidence_ref",
    "fuse_dimension",
    "fuse_profile",
    "score_from_evidence_value",
]
