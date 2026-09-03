"""Offline, repeatable quality evaluation for profile extraction and prescriptions.

This module is intentionally outside the production profile graph.  It compares
sanitized fixtures with observable evidence cards and never writes profile,
radar, session, or scoring state.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.profile.evidence import EvidenceTier
from app.profile.fusion import effective_card_weights, fuse_dimension
from app.profile.guardrail import allowed_score_interval, apply_guardrail
from app.profile.nodes import DialogueExtraction, _dialogue_cards, _fallback_dialogue_extraction
from app.profile.prescription import evaluate_prescription
from app.profile.schema import DIMENSION_LABELS, DimensionKey, EvidenceCard
from app.kb.ingest import load_herbs

LEGAL_DIMENSIONS: tuple[DimensionKey, ...] = tuple(DIMENSION_LABELS)
LEGAL_METHODS = {"llm", "no_key_rule_fallback", "rule_fallback", "legacy"}
LEGAL_BASIS = {"explicit", "inferred"}
KB_REF_RE = re.compile(r"^(?:herb:[A-Za-z0-9_.:-]+|kb:[A-Za-z0-9_.:-]+)$")
FORBIDDEN_OUTPUT_KEYS = {
    "profile_score", "profile_scores", "radar_value", "radar_values",
    "flip_eligibility", "flip_eligible", "card_eligibility", "final_profile",
}


@dataclass(frozen=True)
class CaseEvaluation:
    case_id: str
    input_text: str
    metrics: dict[str, Any]
    actual: dict[str, Any]
    expected: dict[str, Any]
    fallback: bool
    provenance: str
    deterministic: bool

    def as_dict(self) -> dict[str, Any]:
        failed = [key for key, value in self.metrics.items() if key not in {"negation_false_positive"} and (value is False or (isinstance(value, (int, float)) and value < 1.0))]
        return {
            "case_id": self.case_id,
            "input": self.input_text,
            "metrics": self.metrics,
            "actual": self.actual,
            "expected": self.expected,
            "rule_fallback": self.fallback,
            "provenance": self.provenance,
            "deterministic": self.deterministic,
            "failures": [{"case_id": self.case_id, "input": self.input_text, "failed_metrics": failed, "actual_summary": self.actual, "expected_summary": self.expected, "rule_fallback": self.fallback, "provenance": self.provenance, "suggested_fix": "扩充隐晦表达规则或提供标注样本"}] if failed else [],
        }


def load_fixture(path: Path) -> dict[str, Any]:
    """Load a fixture without accepting arbitrary user data."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("fixture must be an object with a cases list")
    return payload


def evaluate_profile_case(case: dict[str, Any], *, mode: str = "no-key", model: str | None = None, version: str | None = None) -> CaseEvaluation:
    """Evaluate one fixed dialogue case using only the deterministic rule path."""

    if mode not in {"no-key", "model"}:
        raise ValueError("mode must be no-key or model")
    text = str(case.get("input") or "")
    expected = dict(case.get("expected") or {})
    if mode == "model":
        # Model execution is deliberately an explicit future integration point.
        # A missing/402 model must never be represented as a rule quality score.
        return CaseEvaluation(
            str(case.get("case_id") or "unknown"), text,
            {"status": "model_not_evaluated", "reason": "model_runner_not_enabled", "request": model_request_record(case, model=model, version=version)},
            {}, expected, False, "not_evaluated", True,
        )

    extraction = _fallback_dialogue_extraction(text)
    cards = _dialogue_cards(extraction)
    structure = DialogueExtraction.model_validate(extraction.model_dump())
    actual_cards = [_card_summary(card) for card in cards]
    checks = _validate_extraction(structure, cards, text)
    matched = 0
    for item in expected.get("evidence", []):
        dimension = str(item.get("dimension") or "")
        quote = str(item.get("quote") or "")
        if any(card.dimension == dimension and (not quote or quote in (card.quote or "")) for card in cards):
            matched += 1
    expected_count = len(expected.get("evidence", []))
    relevant = [item for item in cards if item.dimension in {str(e.get("dimension")) for e in expected.get("evidence", [])}]
    checks.update({
        "evidence_precision": round(matched / len(relevant), 4) if relevant else (1.0 if not expected_count else 0.0),
        "evidence_recall": round(matched / expected_count, 4) if expected_count else 1.0,
        "negation_false_positive": any(
            card.dimension == str(dim) for card in cards for dim in expected.get("must_not_extract_dimensions", [])
        ),
    })
    summary = _stable_summary(cards)
    second = _stable_summary(_dialogue_cards(_fallback_dialogue_extraction(text)))
    return CaseEvaluation(
        str(case.get("case_id") or "unknown"), text, checks, {"cards": actual_cards, "extraction": extraction.model_dump()},
        expected, True, "no_key_rule_fallback", summary == second,
    )


def evaluate_profile_fixture(payload: dict[str, Any], *, mode: str = "no-key", model: str | None = None, version: str | None = None) -> dict[str, Any]:
    cases = [evaluate_profile_case(case, mode=mode, model=model, version=version) for case in payload.get("cases", [])]
    if mode == "model":
        return {"kind": "profile_quality", "status": "model_not_evaluated", "model_quality_verified": False, "reason": "no model call performed", "cases": [c.as_dict() for c in cases]}
    rows = [case.as_dict() for case in cases]
    def avg(key: str) -> float:
        values = [float(row["metrics"].get(key, 0.0)) for row in rows]
        return round(sum(values) / len(values), 4) if values else 0.0
    return {
        "kind": "profile_quality", "status": "rule_baseline_only", "model_quality_verified": False,
        "model_status": {"configured": False, "http_402": 0, "invalid_json": 0, "timeout": 0, "model_not_evaluated": len(rows)},
        "metrics": {
            "evidence_extraction_precision": avg("evidence_precision"),
            "evidence_extraction_recall": avg("evidence_recall"),
            "quote_substring_pass_rate": avg("quote_substring_rate"),
            "dimension_legality_rate": avg("dimension_legality_rate"),
            "metadata_legality_rate": avg("metadata_legality_rate"),
            "negation_false_positive_rate": round(sum(bool(r["metrics"].get("negation_false_positive")) for r in rows) / len(rows), 4) if rows else 0.0,
            "deterministic_repeat_rate": round(sum(bool(r["deterministic"]) for r in rows) / len(rows), 4) if rows else 1.0,
            "repeated_self_report_decay": _decay_check(),
            "self_report_objective_confidence_cap": _self_report_cap_check(),
            "jury_anchor_bound": _jury_anchor_check(),
            "guardrail_final_constraint": _guardrail_check(),
            "provenance_consistent": all(r["provenance"] == "no_key_rule_fallback" for r in rows),
        },
        "cases": rows,
        "notes": ["规则回退通过率不是模型质量；当前报告仅验证 no-key 生产路径。"],
    }


def evaluate_prescription_fixture(payload: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in payload.get("cases", []):
        case_id = str(case.get("case_id") or "unknown")
        evaluation = evaluate_prescription(dict(case.get("input") or {}))
        source = " ".join(str(v) for v in (case.get("input") or {}).values())
        findings = evaluation.findings
        quote_rate = sum(bool(item.get("quote") and str(item["quote"]) in source) for item in findings) / len(findings) if findings else 1.0
        refs = [ref for item in findings for ref in item.get("knowledge_base_refs", [])]
        known_refs = {source.ref for herb in load_herbs() for source in herb.sources if source.ref}
        ref_valid = all(KB_REF_RE.match(str(ref)) or str(ref) in known_refs or str(ref) == "kb:local_herb_index:no_match" for ref in refs)
        expected_findings = set(case.get("expected_findings", []))
        actual_findings = {str(item.get("kind")) for item in findings}
        row = {
            "case_id": case_id, "input": dict(case.get("input") or {}),
            "actual": {"symptom_match_status": evaluation.symptom_match_status, "compatible": evaluation.compatible, "dosage_ok": evaluation.dosage_ok, "toxic_misuse": evaluation.toxic_misuse, "findings": findings, "knowledge_base_refs": evaluation.knowledge_base_refs, "method": evaluation.method},
            "expected": {"symptom_match_status": case.get("expected_status"), "findings": sorted(expected_findings)},
            "metrics": {"finding_quote_substring_rate": round(quote_rate, 4), "kb_ref_format_rate": 1.0 if ref_valid else 0.0, "finding_recall": round(len(expected_findings & actual_findings) / len(expected_findings), 4) if expected_findings else 1.0, "status_match": case.get("expected_status") in (None, evaluation.symptom_match_status), "multiple_solution_allowed": bool(case.get("multiple_solution_allowed", False))},
            "provenance": evaluation.method,
        }
        failed = [key for key, value in row["metrics"].items() if key not in {"multiple_solution_allowed"} and (value is False or (isinstance(value, (int, float)) and value < 1.0))]
        row["failures"] = [{"case_id": case_id, "input": dict(case.get("input") or {}), "failed_metrics": failed, "actual_summary": row["actual"], "expected_summary": row["expected"], "rule_fallback": True, "provenance": evaluation.method, "suggested_fix": "检查处方解析规则、剂量范围或本地知识库引用"}] if failed else []
        rows.append(row)
    return {"kind": "prescription_quality", "status": "rule_baseline_only", "metrics": {"finding_quote_substring_pass_rate": _row_avg(rows, "finding_quote_substring_rate"), "kb_ref_format_rate": _row_avg(rows, "kb_ref_format_rate"), "finding_recall": _row_avg(rows, "finding_recall"), "uncertain_on_unresolved_case": all(r["actual"]["symptom_match_status"] == "uncertain" for r in rows if r["expected"]["symptom_match_status"] == "uncertain"), "provenance_consistent": all(r["provenance"] == "no_key_rule_fallback" for r in rows)}, "cases": rows}


def model_request_record(case: dict[str, Any], *, model: str | None = None, version: str | None = None) -> dict[str, Any]:
    """Return non-sensitive audit metadata for a future model request."""

    sample = json.dumps({"task": "profile_extract", "input": str(case.get("input") or "")}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"sample_hash": hashlib.sha256(sample.encode("utf-8")).hexdigest(), "task": "profile_extract", "model": model, "version": version, "api_key_recorded": False}


def inspect_model_response(*, raw_response: str | None, elapsed_ms: int | float | None, error: Any = None) -> dict[str, Any]:
    """Validate a future model response without retaining its content."""

    if error is not None:
        return {"response_structure_valid": False, "error_class": classify_model_error(error), "elapsed_ms": elapsed_ms}
    try:
        parsed = json.loads(str(raw_response or ""))
        DialogueExtraction.model_validate(parsed)
    except (ValueError, TypeError):
        return {"response_structure_valid": False, "error_class": "invalid_json", "elapsed_ms": elapsed_ms}
    return {"response_structure_valid": True, "error_class": None, "elapsed_ms": elapsed_ms}


def classify_model_error(error: Any) -> str:
    """Map provider failures to a stable, non-sensitive category."""

    status = getattr(error, "status_code", error if isinstance(error, int) else None)
    if status == 402:
        return "http_402"
    if status in {401, 403}:
        return "http_auth"
    if status == 429:
        return "http_429"
    if isinstance(status, int) and 500 <= status < 600:
        return "http_5xx"
    name = error.__class__.__name__.casefold() if error is not None else ""
    if "timeout" in name:
        return "timeout"
    return "provider_error"


def _validate_extraction(extraction: DialogueExtraction, cards: list[EvidenceCard], text: str) -> dict[str, Any]:
    values: list[Any] = []
    for field in ("major", "learning_goal", "base_level", "learning_style", "weak_points", "motivation", "resource_preference", "daily_minutes", "target_days", "motivation_score", "mastery_hint"):
        values.append(getattr(extraction, field))
    quote_checks = [card.quote for card in cards if card.quote]
    metadata_ok = all(item.basis in LEGAL_BASIS and 0 <= item.extraction_confidence <= 1 and item.extraction_method in LEGAL_METHODS and (not item.knowledge_base_ref or KB_REF_RE.match(item.knowledge_base_ref)) for item in cards)
    forbidden = _contains_forbidden(extraction.model_dump())
    return {"structure_valid": True, "dimension_legality_rate": round(sum(card.dimension in LEGAL_DIMENSIONS for card in cards) / len(cards), 4) if cards else 1.0, "quote_substring_rate": round(sum(quote in text for quote in quote_checks) / len(quote_checks), 4) if quote_checks else 1.0, "basis_legal": all(item.basis in LEGAL_BASIS for item in values), "confidence_legal": all(0 <= item.confidence <= 1 for item in values), "extraction_method_legal": all(item.extraction_method in LEGAL_METHODS for item in values), "metadata_legality_rate": 1.0 if metadata_ok else 0.0, "forbidden_final_fields": forbidden}


def _contains_forbidden(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in FORBIDDEN_OUTPUT_KEYS:
                found.append(str(key))
            found.extend(_contains_forbidden(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_contains_forbidden(item))
    return found


def _card_summary(card: EvidenceCard) -> dict[str, Any]:
    return {"dimension": card.dimension, "quote": card.quote, "basis": card.basis, "extraction_confidence": card.extraction_confidence, "extraction_method": card.extraction_method, "knowledge_base_ref": card.knowledge_base_ref}


def _stable_summary(cards: list[EvidenceCard]) -> str:
    return hashlib.sha256(json.dumps([_card_summary(c) for c in cards], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _row_avg(rows: list[dict[str, Any]], key: str) -> float:
    vals = [float(row["metrics"].get(key, 0.0)) for row in rows]
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def _decay_check() -> bool:
    cards = [EvidenceCard(dimension="knowledge_base", source="dialogue", tier=EvidenceTier.SELF_REPORT, fact="same assertion", objective_signal={"base_level": "一般"}, weight=1, timestamp="2026-01-01T00:00:00Z") for _ in range(3)]
    return effective_card_weights(cards) == [1.0, 0.5, 0.25]


def _self_report_cap_check() -> bool:
    card = EvidenceCard(dimension="knowledge_base", source="dialogue", tier=EvidenceTier.SELF_REPORT, fact="self report", objective_signal={"value": 95}, weight=1)
    return fuse_dimension("knowledge_base", [card]).confidence <= 0.35


def _jury_anchor_check() -> bool:
    cards = [EvidenceCard(dimension="knowledge_base", source="quiz", tier=EvidenceTier.MEASURED, fact="measured", objective_signal={"correct": True}, weight=1)]
    low, high, _ = allowed_score_interval("knowledge_base", cards)
    return low <= high and low >= 0 and high <= 100


def _guardrail_check() -> bool:
    card = EvidenceCard(dimension="knowledge_base", source="quiz", tier=EvidenceTier.MEASURED, fact="wrong", objective_signal={"correct": False}, weight=1)
    return apply_guardrail("knowledge_base", evidence_cards=[card], score=100).score <= 35


__all__ = ["classify_model_error", "evaluate_profile_case", "evaluate_profile_fixture", "evaluate_prescription_fixture", "inspect_model_response", "load_fixture", "model_request_record"]
