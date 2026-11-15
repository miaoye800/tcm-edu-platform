"""Objective evidence card schema for profile collection."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EvidenceTier(StrEnum):
    """Reliability tier for profile evidence."""

    MEASURED = "measured"
    BEHAVIORAL = "behavioral"
    SELF_REPORT = "self_report"


EvidenceBasis = Literal["explicit", "inferred"]
ExtractionMethod = Literal["llm", "no_key_rule_fallback", "rule_fallback", "legacy"]


class StructuredEvidence(BaseModel):
    """Stable extraction contract shared by text and structured answer paths."""

    dimension: str
    fact: str
    signal: dict[str, Any] = Field(default_factory=dict)
    quote: str | None = None
    basis: EvidenceBasis = "inferred"
    extraction_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str
    knowledge_base_ref: str | None = None
    extraction_method: ExtractionMethod = "legacy"


# Legacy source names are matched case-insensitively and by substring so stored
# Chinese display labels migrate without a database rewrite.
LEGACY_SOURCE_TIER_MARKERS: tuple[tuple[EvidenceTier, tuple[str, ...]], ...] = (
    (
        EvidenceTier.MEASURED,
        ("quiz", "prescription", "测验", "小测", "答题", "做题", "诊断", "辨药", "抓药", "开药", "速判", "实战"),
    ),
    (EvidenceTier.BEHAVIORAL, ("passive", "interaction", "被动", "交互", "停留", "跳过", "重看")),
    (EvidenceTier.SELF_REPORT, ("dialogue", "对话", "身份卡", "冷启动")),
)


class EvidenceCard(BaseModel):
    """Objective fact captured by rules before any scoring judgment."""

    model_config = ConfigDict(validate_assignment=True)

    evidence_id: str = Field(default_factory=lambda: uuid4().hex)
    dimension: str
    source: str
    tier: EvidenceTier
    fact: str
    objective_signal: dict[str, Any] = Field(default_factory=dict)
    weight: float = Field(ge=0.0)
    claim_status: Literal["pending", "verified", "refuted"] | None = None
    quote: str | None = None
    basis: EvidenceBasis = "inferred"
    extraction_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extraction_method: ExtractionMethod = "legacy"
    knowledge_base_ref: str | None = None
    # Agent-authored component contract. These fields are intentionally kept
    # beside the legacy objective_signal so old stored cards remain readable.
    component: str | None = None
    evidence_group_id: str | None = None
    source_turn_ids: list[int] = Field(default_factory=list)
    normalized_claim: str | None = None
    supports: list[str] = Field(default_factory=list)
    collection_status: Literal["scorable", "pending", "conflict", "invalid"] | None = None
    semantic_relation: Literal["new", "reinforcement", "semantic_duplicate", "conflict"] | None = None
    related_evidence_ids: list[str] = Field(default_factory=list)
    quality_factors: dict[str, Any] = Field(default_factory=dict)
    quality_reason: str = ""
    # 质量分的判定过程（看到什么原话 → 命中哪档 → 档内为何落这里）。
    # 这是模型自己写下的判定说明，不是私有思维链，审计里直接展示。
    quality_thinking: str = ""
    confirmed_aspects: list[str] = Field(default_factory=list)
    pending_aspects: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_structured(
        cls,
        extracted: StructuredEvidence,
        *,
        tier: EvidenceTier,
        weight: float,
        timestamp: datetime | None = None,
    ) -> "EvidenceCard":
        """Convert the shared extraction contract into a persisted evidence card."""

        return cls(
            dimension=extracted.dimension,
            source=extracted.source,
            tier=tier,
            fact=extracted.fact,
            objective_signal=dict(extracted.signal),
            weight=weight,
            quote=extracted.quote,
            basis=extracted.basis,
            extraction_confidence=extracted.extraction_confidence,
            extraction_method=extracted.extraction_method,
            knowledge_base_ref=extracted.knowledge_base_ref,
            timestamp=timestamp or datetime.now(UTC),
        )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_tier(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("tier") is not None:
            return value
        payload = dict(value)
        payload["tier"] = infer_evidence_tier(
            str(payload.get("source") or ""),
            fact=str(payload.get("fact") or ""),
        )
        return payload

    @field_validator("evidence_id", "dimension", "source", "fact")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be non-empty")
        return normalized

    @field_validator("quote", "knowledge_base_ref")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> object:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value


#: 能证明"这一块我不会"的答题形态。行为埋点（跳过/重看）不在其中：
#: 它说明的是注意力投向，不是知识点掌握。
_WEAK_POINT_ANSWER_KINDS = frozenset({"选择", "数字", "自述"})


def weak_point_label_of(card: EvidenceCard) -> str | None:
    """把一条证据读成"薄弱点标签"，读不出来就返回 None。

    以前任何 weak_points 卡都会贡献标签，连"跳过画像引导页"这种页面级埋点
    都会被当成一个知识点：牌面上因此出现"画像引导 29%"——用户看不懂，也不
    是真话。薄弱点必须来自做到题、说过这块不行、或被校准验证过的证据。
    """

    if card.dimension != "weak_points":
        return None
    signal = card.objective_signal if isinstance(card.objective_signal, dict) else {}
    measured = (
        card.claim_status == "verified"
        or signal.get("correct") is False
        or signal.get("is_correct") is False
        or bool(signal.get("question_id"))
        or bool(signal.get("gave_up"))
        or str(signal.get("question_type") or "") in _WEAK_POINT_ANSWER_KINDS
    )
    if not measured:
        return None
    for key in ("label", "topic", "knowledge_point"):
        value = str(signal.get(key) or "").strip()
        if value:
            return value
    labels = signal.get("weak_labels")
    if isinstance(labels, list):
        for label in labels:
            if isinstance(label, str) and label.strip():
                return label.strip()
    return None


def infer_evidence_tier(source: str, *, fact: str = "") -> EvidenceTier:
    """Infer a tier for legacy cards that predate the explicit field."""

    normalized_source = source.strip().casefold()
    normalized_fact = fact.strip().casefold()
    if "身份卡" in normalized_fact:
        return EvidenceTier.SELF_REPORT
    if any(marker in normalized_source for marker in ("dialogue", "对话", "冷启动")):
        return EvidenceTier.SELF_REPORT
    for tier, markers in LEGACY_SOURCE_TIER_MARKERS:
        if any(
            marker.casefold() in normalized_source or marker.casefold() in normalized_fact
            for marker in markers
        ):
            return tier
    # Unknown legacy sources are treated conservatively as self-report.
    return EvidenceTier.SELF_REPORT


__all__ = [
    "EvidenceBasis",
    "EvidenceCard",
    "EvidenceTier",
    "ExtractionMethod",
    "StructuredEvidence",
    "infer_evidence_tier",
    "weak_point_label_of",
]
