"""Unified seven-dimension component evidence and scoring contract.

Semantic decisions are authored by the extraction/scoring agents.  This module
only validates traceability, stores their relations, deduplicates by the
returned group ids, and applies the published arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import prod
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.profile.schema import DIMENSION_LABELS, DimensionKey, EvidenceCard


COMPONENTS: dict[DimensionKey, tuple[str, ...]] = {
    "knowledge_base": ("concept_foundation", "knowledge_scope", "retrieval_application", "boundary_awareness", "measured_performance"),
    "learning_goal": ("expression_certainty", "goal_specificity", "action_orientation", "priority_signal", "cross_turn_stability"),
    "learning_style": ("preference_clarity", "cross_task_consistency", "comparative_choice", "strategy_execution", "effect_fit"),
    "weak_points": ("recall_correctness", "application_correctness", "cross_context_stability", "error_recovery", "measured_validation"),
    "pace": ("plan_clarity", "time_feasibility", "actual_execution", "sustained_period", "adjustment_recovery"),
    "motivation": ("intrinsic_value", "external_commitment", "actual_investment", "persistence_under_obstacle", "conflict_regulation"),
    "resource_preference": ("preference_specificity", "task_match_reason", "comparative_choice", "availability_constraint", "usage_effect_feedback"),
}

COMPONENT_WEIGHTS: dict[DimensionKey, dict[str, float]] = {
    "knowledge_base": {"concept_foundation": .25, "knowledge_scope": .20, "retrieval_application": .20, "boundary_awareness": .20, "measured_performance": .15},
    "learning_goal": {"expression_certainty": .25, "goal_specificity": .25, "action_orientation": .20, "priority_signal": .15, "cross_turn_stability": .15},
    "learning_style": {"preference_clarity": .25, "cross_task_consistency": .20, "comparative_choice": .20, "strategy_execution": .15, "effect_fit": .20},
    "weak_points": {"recall_correctness": .25, "application_correctness": .25, "cross_context_stability": .20, "error_recovery": .15, "measured_validation": .15},
    "pace": {"plan_clarity": .20, "time_feasibility": .20, "actual_execution": .30, "sustained_period": .15, "adjustment_recovery": .15},
    "motivation": {"intrinsic_value": .25, "external_commitment": .15, "actual_investment": .25, "persistence_under_obstacle": .20, "conflict_regulation": .15},
    "resource_preference": {"preference_specificity": .25, "task_match_reason": .25, "comparative_choice": .20, "availability_constraint": .15, "usage_effect_feedback": .15},
}

DIMENSION_COMPONENT_LABELS: dict[DimensionKey, dict[str, str]] = {
    "knowledge_base": {"concept_foundation": "概念基础", "knowledge_scope": "知识范围", "retrieval_application": "提取应用", "boundary_awareness": "边界误区意识", "measured_performance": "实测表现"},
    "learning_goal": {"expression_certainty": "表达确定性", "goal_specificity": "目标具体度", "action_orientation": "行动指向", "priority_signal": "优先级信号", "cross_turn_stability": "跨轮稳定性"},
    "learning_style": {"preference_clarity": "偏好明确度", "cross_task_consistency": "跨任务一致性", "comparative_choice": "比较性选择", "strategy_execution": "策略执行", "effect_fit": "效果匹配"},
    "weak_points": {"recall_correctness": "回忆正确性", "application_correctness": "应用正确性", "cross_context_stability": "跨场景稳定性", "error_recovery": "纠错恢复", "measured_validation": "实测验证"},
    "pace": {"plan_clarity": "计划清晰度", "time_feasibility": "时间可行性", "actual_execution": "实际执行", "sustained_period": "持续周期", "adjustment_recovery": "调整恢复"},
    "motivation": {"intrinsic_value": "内在价值", "external_commitment": "外在承诺", "actual_investment": "实际投入", "persistence_under_obstacle": "受阻持续性", "conflict_regulation": "冲突调节"},
    "resource_preference": {"preference_specificity": "偏好具体度", "task_match_reason": "任务匹配理由", "comparative_choice": "比较性选择", "availability_constraint": "可获得性约束", "usage_effect_feedback": "使用效果反馈"},
}

# The rubric is deliberately data, not executable semantic rules.  The agent
# reads these descriptions and returns component-level evidence and scores.
_BASE_RUBRIC: dict[str, tuple[str, str, str, str, str, str, str]] = {
    "expression_certainty": ("目标表达的坚定程度", "有明确表达且可追溯", "无有效表达或仅为模糊推测", "有意向但承诺边界未明", "目标表达坚定并在多轮保持一致", "self_report", "确认是否有长期承诺及排他性"),
    "goal_specificity": ("目标的对象、成果、期限具体程度", "至少包含可验证对象或结果", "无具体目标信息", "有目标类别但缺对象、期限或成果", "对象、成果、期限清楚且可验证", "self_report", "追问对象、成果与时间"),
    "action_orientation": ("目标对应的行动、路径和第一步", "出现行动、期限、路径或资源安排", "只能由明确缺乏计划/拖延支持", "有初步路径或近期行动", "目标、期限、路径、第一步和资源安排一致且已有实施", "self_report,behavioral", "追问第一步、路径和已执行行动"),
    "priority_signal": ("目标之间的主次和取舍信号", "出现明确取舍、排序或资源分配", "只能由明确的优先级冲突支持", "有弱顺序信号但未验证取舍", "主次、时间和资源分配一致", "self_report,behavioral", "追问冲突目标如何取舍"),
    "cross_turn_stability": ("目标在不同轮次的稳定性", "至少两次独立且一致表达", "只有明确矛盾或反复放弃", "目前只有一次表达", "多轮一致并有持续行动", "self_report,behavioral", "在后续轮次复述并核对变化"),
}

def _make_rubric() -> dict[DimensionKey, dict[str, dict[str, Any]]]:
    output: dict[DimensionKey, dict[str, dict[str, Any]]] = {}
    generic = {
        "knowledge_base": ("知识掌握的可观察组成", "self_report,behavioral,measured"),
        "learning_style": ("学习方式选择与效果适配", "self_report,behavioral"),
        "weak_points": ("掌握稳定性及纠错表现", "behavioral,measured,self_report"),
        "pace": ("学习节奏的计划、执行和恢复", "self_report,behavioral"),
        "motivation": ("投入动力、承诺和受阻反应", "self_report,behavioral"),
        "resource_preference": ("资源偏好与任务适配", "self_report,behavioral"),
    }
    for dimension, components in COMPONENTS.items():
        output[dimension] = {}
        for key in components:
            label = DIMENSION_COMPONENT_LABELS[dimension][key]
            if key in _BASE_RUBRIC:
                definition, threshold, low, medium, high, types, followup = _BASE_RUBRIC[key]
            else:
                definition = generic[dimension][0] + "：" + label
                threshold = "至少一条与本组件直接相关、可追溯且非空泛的证据"
                low = "只有明确的负向、矛盾、受限、失败或弱表现证据"
                medium = "有一条具体的正向或中性表现，但范围、稳定性或交叉验证不足"
                high = "有多条具体、一致且与本组件直接相关的证据，必要时包含行为或实测验证"
                types = generic[dimension][1]
                followup = f"自然追问{label}的具体例子、限制和后续表现"
            output[dimension][key] = {
                "component": key, "label": label, "definition": definition,
                "weight": COMPONENT_WEIGHTS[dimension][key], "minimum_evidence": threshold,
                "not_scorable_when": "未提及、尚未确认、只有模糊推测，或没有可追溯原话",
                "low": low, "medium": medium, "high": high,
                "accepted_evidence_types": [item.strip() for item in types.split(",")],
                "conflict_principle": "保留全部冲突证据；由评分智能体说明冲突如何降低或改变本组件分，不得静默丢弃",
                "followup_direction": followup,
            }
    return output


COMPONENT_RUBRICS = _make_rubric()

# The quality dimensions have equal published importance. Keeping the weights
# explicit makes this a weighted geometric mean and prevents accidentally
# replacing it with a six-factor product when the contract evolves.
QUALITY_WEIGHTS = {
    "source_reliability": .2,
    "expression_clarity": .2,
    "content_specificity": .2,
    "freshness": .2,
    "consistency": .2,
}

CombinationType = Literal[
    "single_vague_self_report", "single_clear_self_report", "multi_turn_clear_pending_crosscheck",
    "multi_turn_clear_consistent", "multi_turn_self_report_with_priority", "dialogue_behavior_consistent",
    "dialogue_behavior_measurement_long_term",
]

COMBINATION_INTERVALS: dict[CombinationType, tuple[float, float]] = {
    "single_vague_self_report": (.10, .25), "single_clear_self_report": (.20, .38),
    "multi_turn_clear_pending_crosscheck": (.32, .55), "multi_turn_clear_consistent": (.45, .68),
    "multi_turn_self_report_with_priority": (.55, .75), "dialogue_behavior_consistent": (.65, .85),
    "dialogue_behavior_measurement_long_term": (.80, .95),
}


class QualityFactors(BaseModel):
    source_reliability: float = Field(ge=0, le=1)
    expression_clarity: float = Field(ge=0, le=1)
    content_specificity: float = Field(ge=0, le=1)
    freshness: float = Field(ge=0, le=1)
    consistency: float = Field(ge=0, le=1)
    independence_note: str = ""


class AtomicEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    evidence_id: str
    dimension: DimensionKey
    component: str
    evidence_group_id: str
    source_turn_ids: list[int] = Field(default_factory=list)
    quote: str
    normalized_claim: str
    evidence_type: str
    supports: list[str] = Field(default_factory=list)
    collection_status: Literal["scorable", "pending", "conflict", "invalid"] = "pending"
    assessment: str = ""
    confirmed_aspects: list[str] = Field(default_factory=list)
    pending_aspects: list[str] = Field(default_factory=list)
    semantic_relation: Literal["new", "reinforcement", "semantic_duplicate", "conflict"] = "new"
    related_evidence_ids: list[str] = Field(default_factory=list)
    quality_factors: QualityFactors

    @field_validator("quote", "normalized_claim", "evidence_group_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("evidence text and group id must be non-empty")
        return value


class DimensionScan(BaseModel):
    dimension: DimensionKey
    status: Literal["evidence_found", "no_usable_evidence", "pending"]
    reason: str
    components: list[str] = Field(default_factory=list)
    # Structured, user-visible rationale for nearby alternatives considered
    # by the extractor. This is an explanation summary, not private reasoning.
    excluded_candidates: list[dict[str, str]] = Field(default_factory=list)


class ComponentCollection(BaseModel):
    dimension: DimensionKey
    component: str
    status: Literal["scorable", "pending", "conflict"]
    evidence_ids: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class EvidenceCollectionOutput(BaseModel):
    turn_id: int
    combination_type: CombinationType = "single_clear_self_report"
    dimension_scan: list[DimensionScan] = Field(default_factory=list)
    atomic_evidence: list[AtomicEvidence] = Field(default_factory=list)
    component_collection: list[ComponentCollection] = Field(default_factory=list)


class ComponentScore(BaseModel):
    component: str
    score: float = Field(ge=5, le=95)
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str
    conflict_handling: str


class DimensionScoreOutput(BaseModel):
    component_scores: list[ComponentScore] = Field(default_factory=list)
    dimension_summary: str = ""


@dataclass(frozen=True)
class ComponentResult:
    dimension: DimensionKey
    collected: int
    total: int
    component_scores: dict[str, float]
    dimension_score: float | None
    evidence_ids: list[str]
    pending_components: list[str]
    conflicts: list[str]

    @property
    def complete(self) -> bool:
        return not self.pending_components and len(self.component_scores) == self.total


def quote_is_traceable(quote: str, source_text: str) -> bool:
    return bool(quote and source_text and quote.strip() in source_text)


def validate_atomic_evidence(payload: AtomicEvidence, source_text: str) -> AtomicEvidence | None:
    if payload.dimension not in COMPONENTS or payload.component not in COMPONENTS[payload.dimension]:
        return None
    if not quote_is_traceable(payload.quote, source_text):
        return None
    return payload


def deduplicate_evidence(evidence: list[AtomicEvidence]) -> tuple[list[AtomicEvidence], list[AtomicEvidence]]:
    """Return strongest/most recent semantic groups and all conflicts.

    The agent supplies relation and group ids.  No text similarity or keyword
    rule is used here; source groups are the only deterministic identity.
    """
    selected: dict[tuple[str, str, str], AtomicEvidence] = {}
    conflicts: list[AtomicEvidence] = []
    for item in evidence:
        # A quote may support multiple components within a dimension. Keep
        # component attribution distinct while confidence later merges by the
        # shared source group so it cannot count as multiple independent turns.
        key = (item.dimension, item.component, item.evidence_group_id)
        if item.semantic_relation == "conflict":
            conflicts.append(item)
            continue
        current = selected.get(key)
        if current is None or _quality_value(item) > _quality_value(current):
            selected[key] = item
    return list(selected.values()), conflicts


def deduplicate_component_cards(cards: list[EvidenceCard]) -> tuple[list[EvidenceCard], list[EvidenceCard], list[str]]:
    """Apply the agent-authored source grouping to persisted cards.

    Returns kept evidence, conflicts (always retained), and IDs omitted as
    same-group duplicates. This performs no text similarity or semantic test.
    """
    selected: dict[tuple[str, str, str], EvidenceCard] = {}
    conflicts: list[EvidenceCard] = []
    dropped: list[str] = []
    for card in cards:
        if card.semantic_relation == "conflict":
            conflicts.append(card)
            continue
        group = card.evidence_group_id or str(card.objective_signal.get("evidence_group_id") or card.evidence_id)
        key = (card.dimension, card.component or "", group)
        current = selected.get(key)
        if current is None:
            selected[key] = card
            continue
        current_quality = _card_quality(current)
        candidate_quality = _card_quality(card)
        if candidate_quality > current_quality or (candidate_quality == current_quality and card.timestamp > current.timestamp):
            dropped.append(current.evidence_id)
            selected[key] = card
        else:
            dropped.append(card.evidence_id)
    return list(selected.values()), conflicts, dropped


def _card_quality(card: EvidenceCard) -> float:
    raw = card.quality_factors or card.objective_signal.get("quality_factors")
    if not isinstance(raw, dict):
        return 0.0
    try:
        return geometric_quality(QualityFactors.model_validate(raw))
    except Exception:
        return 0.0


def _quality_value(item: AtomicEvidence) -> float:
    q = item.quality_factors
    return geometric_quality(q) * (0.75 if item.semantic_relation == "semantic_duplicate" else 1.0)


def geometric_quality(q: QualityFactors) -> float:
    values = {
        "source_reliability": q.source_reliability,
        "expression_clarity": q.expression_clarity,
        "content_specificity": q.content_specificity,
        "freshness": q.freshness,
        "consistency": q.consistency,
    }
    weighted_product = prod(value ** QUALITY_WEIGHTS[key] for key, value in values.items())
    return round(weighted_product, 4)


def collect_component_results(evidence: list[AtomicEvidence]) -> dict[DimensionKey, ComponentResult]:
    deduped, conflicts = deduplicate_evidence(evidence)
    results: dict[DimensionKey, ComponentResult] = {}
    for dimension, components in COMPONENTS.items():
        by_component: dict[str, list[AtomicEvidence]] = {key: [] for key in components}
        for item in deduped:
            if item.dimension == dimension and item.component in by_component and item.collection_status == "scorable":
                by_component[item.component].append(item)
        pending = [key for key, items in by_component.items() if not items]
        results[dimension] = ComponentResult(
            dimension=dimension, collected=len(components) - len(pending), total=len(components),
            component_scores={}, dimension_score=None,
            evidence_ids=[item.evidence_id for item in deduped if item.dimension == dimension],
            pending_components=pending,
            conflicts=[item.evidence_id for item in conflicts if item.dimension == dimension],
        )
    return results


def apply_dimension_scores(evidence: list[AtomicEvidence], scores: DimensionScoreOutput) -> ComponentResult:
    if not scores.component_scores:
        raise ValueError("component scores are empty")
    dimension = next((item.dimension for item in evidence if item.component == scores.component_scores[0].component), None)
    if dimension is None:
        raise ValueError("scores do not reference evidence")
    collection = collect_component_results([item for item in evidence if item.dimension == dimension])[dimension]
    score_map = {item.component: item.score for item in scores.component_scores if item.component in COMPONENTS[dimension]}
    pending = [key for key in COMPONENTS[dimension] if key not in score_map or key in collection.pending_components]
    if pending:
        return ComponentResult(dimension, collection.collected, collection.total, score_map, None, collection.evidence_ids, pending, collection.conflicts)
    weighted = sum(score_map[key] * COMPONENT_WEIGHTS[dimension][key] for key in COMPONENTS[dimension])
    return ComponentResult(dimension, collection.collected, collection.total, score_map, round(max(5.0, min(95.0, weighted)), 2), collection.evidence_ids, [], collection.conflicts)


def confidence_from_evidence(evidence: list[AtomicEvidence], combination_type: CombinationType) -> dict[str, Any]:
    deduped, conflicts = deduplicate_evidence(evidence)
    groups: dict[str, float] = {}
    for item in deduped:
        contribution = geometric_quality(item.quality_factors)
        groups[item.evidence_group_id] = max(groups.get(item.evidence_group_id, 0.0), contribution)
    progress = 1 - prod(1 - value for value in groups.values()) if groups else 0.0
    lower, upper = COMBINATION_INTERVALS[combination_type]
    final = lower + (upper - lower) * progress
    return {
        "combination_type": combination_type,
        "quality_values": {item.evidence_id: geometric_quality(item.quality_factors) for item in deduped},
        "quality_factors": {item.evidence_id: item.quality_factors.model_dump(mode="json") for item in deduped},
        "independent_source_groups": sorted(groups), "deduplicated_evidence_ids": [item.evidence_id for item in deduped],
        "conflict_evidence_ids": [item.evidence_id for item in conflicts],
        "combination_progress": round(progress, 4), "interval": {"lower": lower, "upper": upper},
        "confidence": round(min(upper, max(lower, final)), 4),
        "formula": "lower + (upper - lower) * (1 - product(1 - effective_contribution))",
    }


def rubric_for(dimension: DimensionKey, component: str) -> dict[str, Any]:
    return COMPONENT_RUBRICS[dimension][component]


__all__ = [
    "COMPONENTS", "COMPONENT_WEIGHTS", "COMPONENT_RUBRICS", "QUALITY_WEIGHTS", "COMBINATION_INTERVALS", "DIMENSION_COMPONENT_LABELS",
    "AtomicEvidence", "ComponentCollection", "ComponentResult", "DimensionScan", "DimensionScoreOutput",
    "EvidenceCollectionOutput", "QualityFactors", "apply_dimension_scores", "collect_component_results",
    "confidence_from_evidence", "deduplicate_evidence", "deduplicate_component_cards", "geometric_quality", "quote_is_traceable", "rubric_for",
]
