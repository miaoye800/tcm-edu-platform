"""Deterministic, traceable evaluation of free-form TCM prescriptions.

The evaluator deliberately reports observable checks rather than a model score.
It is shared by profile modes and incremental profile events; fusion, jury and
guardrails remain the only profile-scoring layers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.kb.ingest import load_herbs
from app.kb.models import Herb


@dataclass(frozen=True)
class PrescriptionEvaluation:
    """Traceable checks derived solely from the submitted case and prescription."""

    symptom_match: bool
    symptom_match_status: str
    compatible: bool
    dosage_ok: bool
    toxic_misuse: bool
    findings: list[dict[str, Any]]
    normalized_herbs: list[dict[str, Any]]
    knowledge_base_refs: list[str]
    quote: str
    method: str = "no_key_rule_fallback"

    def signal(self) -> dict[str, Any]:
        return {
            "\u5bf9\u75c7": self.symptom_match,
            "symptom_match_status": self.symptom_match_status,
            "\u914d\u4f0d": self.compatible,
            "\u5242\u91cf": self.dosage_ok,
            "\u8bef\u7528\u6bd2\u70c8": self.toxic_misuse,
            "prescription_findings": self.findings,
            "normalized_herbs": self.normalized_herbs,
            "knowledge_base_refs": self.knowledge_base_refs,
            "quote": self.quote or None,
            "basis": "explicit",
            "extraction_method": self.method,
        }


def evaluate_prescription(payload: dict[str, Any]) -> PrescriptionEvaluation:
    """Evaluate a free prescription without assuming a single canonical formula."""

    case_text = _case_text(payload)
    prescription_text = _prescription_text(payload)
    herbs = _known_herbs(payload, prescription_text)
    findings: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    refs: list[str] = []
    selected_names = {herb.name for herb, _, _, _ in herbs}
    selected_ids = {herb.id for herb, _, _, _ in herbs}

    target_terms = _target_terms(case_text)
    target_requires_all = _target_requires_all(case_text)
    matching_herbs: set[str] = set()
    dosage_ok = True
    toxic_misuse = False
    for herb, raw_name, dose, quote in herbs:
        herb_refs = _herb_refs(herb)
        refs.extend(ref for ref in herb_refs if ref not in refs)
        normalized.append(
            {
                "name": herb.name,
                "raw_name": raw_name,
                "dose_g": dose,
                "preparation": _preparation(raw_name),
                "knowledge_base_refs": herb_refs,
            }
        )
        findings.append(
            _finding("herb_normalized", quote, herb_refs, f"normalized to {herb.name}"))
        if target_terms and _herb_matches_target(herb, target_terms, require_all=target_requires_all):
            matching_herbs.add(herb.name)
        if dose is not None and not _dose_in_range(herb, dose):
            dosage_ok = False
            findings.append(
                _finding("dose_abnormal", quote, herb_refs, f"{herb.name} dose {dose:g}g is outside its KB range"))
        if _is_toxic(herb) and (dose is None or not _dose_in_range(herb, dose)):
            toxic_misuse = True
            findings.append(_finding("toxic_misuse", quote, herb_refs, f"toxic herb {herb.name} lacks a safe documented dose"))

    unknown_mentions = _unknown_mentions(prescription_text, herbs)
    for mention in unknown_mentions:
        findings.append(_finding("unrelated_herb", mention, [], "material is not present in the local herb knowledge base"))

    incompatible_pairs = _incompatible_pairs(herbs, selected_names, selected_ids)
    for left, right, quote, pair_refs in incompatible_pairs:
        findings.append(_finding("incompatible_pair", quote, pair_refs, f"{left} conflicts with {right} in the KB"))

    if target_terms:
        symptom_match = bool(matching_herbs)
        symptom_match_status = "matched" if symptom_match else "mismatched"
    else:
        symptom_match = False
        symptom_match_status = "uncertain"
        findings.append(_finding("case_unresolved", case_text, refs, "case symptoms could not be mapped to a supported KB indication/effect target"))
    if target_terms and not symptom_match:
        findings.append(_finding("case_mismatch", case_text, refs, "no selected herb has a KB indication matching the case target"))
    elif target_terms:
        for herb, _, _, quote in herbs:
            if herb.name not in matching_herbs:
                findings.append(_finding("unrelated_herb", quote, _herb_refs(herb), f"{herb.name} does not match the identified case target"))

    compatible = bool(herbs) and not incompatible_pairs and not toxic_misuse
    if not herbs:
        dosage_ok = False
        findings.append(_finding("no_recognized_herb", prescription_text, [], "no herb could be normalized from the submission"))
    quote = prescription_text or case_text
    return PrescriptionEvaluation(
        symptom_match=symptom_match,
        symptom_match_status=symptom_match_status,
        compatible=compatible,
        dosage_ok=dosage_ok,
        toxic_misuse=toxic_misuse,
        findings=findings,
        normalized_herbs=normalized,
        knowledge_base_refs=refs,
        quote=quote,
    )


def _case_text(payload: dict[str, Any]) -> str:
    return " ".join(
        str(payload.get(key) or "").strip()
        for key in ("case", "symptoms", "scenario")
        if str(payload.get(key) or "").strip()
    )


def _prescription_text(payload: dict[str, Any]) -> str:
    return " ".join(
        str(payload.get(key) or "").strip()
        for key in ("prescription", "answer", "selected_herbs", "herbs")
        if str(payload.get(key) or "").strip()
    )


def _known_herbs(payload: dict[str, Any], text: str) -> list[tuple[Herb, str, float | None, str]]:
    by_name = {name: herb for herb in load_herbs() for name in (herb.name, *herb.aliases) if name}
    matches: list[tuple[Herb, str, float | None, str]] = []
    seen: set[str] = set()
    structured = payload.get("herbs") or payload.get("selected_herbs")
    if isinstance(structured, list):
        for item in structured:
            raw_name = str(item.get("name") or item.get("herb") or "").strip() if isinstance(item, dict) else str(item).strip()
            herb = by_name.get(raw_name)
            if herb is None or herb.id in seen:
                continue
            dose = _as_float(item.get("dose") or item.get("dosage") or item.get("grams")) if isinstance(item, dict) else None
            quote = raw_name if raw_name in text else text
            matches.append((herb, raw_name, dose, quote))
            seen.add(herb.id)
    for raw_name, herb in sorted(by_name.items(), key=lambda item: len(item[0]), reverse=True):
        if herb.id in seen or raw_name not in text:
            continue
        occurrence = raw_name
        quote = _quote_for_name(occurrence, text)
        raw_name = _raw_name_with_preparation(occurrence, quote)
        dose = _dose_after_name(occurrence, text)
        matches.append((herb, raw_name, dose, quote))
        seen.add(herb.id)
    return matches


def _target_terms(case_text: str) -> tuple[str, ...]:
    targets = (
        (("\u98ce\u5bd2", "\u6076\u5bd2", "\u65e0\u6c57", "\u8868\u5bd2"), ("\u89e3\u8868", "\u6563\u5bd2", "\u53d1\u6c57", "\u98ce\u5bd2")),
        (("\u70ed\u6bd2", "\u54bd\u75db", "\u53e3\u6e34", "\u9ad8\u70ed"), ("\u6e05\u70ed", "\u6cfb\u706b", "\u89e3\u6bd2", "\u751f\u6d25")),
        (("\u6c14\u865a", "\u4e4f\u529b", "\u81ea\u6c57", "\u810f\u6c14\u4e0d\u8db3"), ("\u8865\u6c14", "\u5065\u810f", "\u76ca\u6c14", "\u56fa\u8868")),
        (("\u6c34\u80bf", "\u5c0f\u4fbf\u4e0d\u5229", "\u6d6e\u80bf"), ("\u5229\u6c34", "\u6d88\u80bf", "\u6e17\u6e7f")),
    )
    for case_keywords, effect_keywords in targets:
        if any(keyword in case_text for keyword in case_keywords):
            return effect_keywords
    if any(keyword in case_text for keyword in ("\u54b3\u55fd", "\u75f0\u591a", "\u75f0")):
        return ("\u54b3\u55fd", "\u75f0")
    return ()


def _target_requires_all(case_text: str) -> bool:
    return any(keyword in case_text for keyword in ("\u54b3\u55fd", "\u75f0\u591a"))


def _herb_matches_target(herb: Herb, target_terms: tuple[str, ...], *, require_all: bool = False) -> bool:
    text = " ".join([*herb.effects, *(f"{item.syndrome} {item.description}" for item in herb.indications)])
    if require_all:
        return all(term in text for term in target_terms)
    return any(term in text for term in target_terms)


def _dose_in_range(herb: Herb, dose: float) -> bool:
    lower = herb.dosage.min_g
    upper = herb.dosage.max_g
    return (lower is None or dose >= lower) and (upper is None or dose <= upper)


def _is_toxic(herb: Herb) -> bool:
    return str(herb.toxicity) not in {"\u65e0\u6bd2", "Toxicity.NONE"}


def _incompatible_pairs(
    herbs: list[tuple[Herb, str, float | None, str]], selected_names: set[str], selected_ids: set[str]
) -> list[tuple[str, str, str, list[str]]]:
    pairs: list[tuple[str, str, str, list[str]]] = []
    for herb, _, _, quote in herbs:
        incompatibility = herb.incompatibility
        if incompatibility is None:
            continue
        forbidden = {*incompatibility.shibafan, *incompatibility.shijiuwei}
        for item in forbidden:
            if item in selected_names or item in selected_ids:
                pairs.append((herb.name, item, quote, _herb_refs(herb)))
    return pairs


def _unknown_mentions(text: str, herbs: list[tuple[Herb, str, float | None, str]]) -> list[str]:
    known = {raw_name for _, raw_name, _, _ in herbs}
    mentions = []
    for match in re.finditer(r"([\u4e00-\u9fff]{2,6})(?=\s*\d+(?:\.\d+)?\s*(?:g|\u514b))", text, flags=re.I):
        name = match.group(1)
        if name not in known and name not in mentions:
            mentions.append(name)
    return mentions


def _dose_after_name(name: str, text: str) -> float | None:
    match = re.search(re.escape(name) + r"(?:[\uff08(][^\uff09)]*[\uff09)])?\s*(\d{1,3}(?:\.\d+)?)\s*(?:g|\u514b)", text, flags=re.I)
    return _as_float(match.group(1)) if match else None


def _quote_for_name(name: str, text: str) -> str:
    match = re.search(re.escape(name) + r"[^,;\uff0c\uff1b\u3002\n]*", text)
    return match.group(0).strip() if match else name


def _preparation(raw_name: str) -> str | None:
    match = re.search(r"[\uff08(]([^\uff09)]+)[\uff09)]", raw_name)
    if match:
        return match.group(1).strip()
    prefix = re.match(r"(\u70d9|\u7092|\u9152\u5236|\u59dc\u5236|\u918b\u5236|\u9eb8\u7092)", raw_name)
    return prefix.group(1) if prefix else None


def _raw_name_with_preparation(name: str, quote: str) -> str:
    match = re.search(r"(?:\u70d9|\u7092|\u9152\u5236|\u59dc\u5236|\u918b\u5236|\u9eb8\u7092)?" + re.escape(name), quote)
    return match.group(0) if match else name


def _herb_refs(herb: Herb) -> list[str]:
    return [f"herb:{herb.id}", *(source.ref for source in herb.sources if source.ref)]


def _finding(kind: str, quote: str, refs: list[str], reason: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "quote": quote,
        "knowledge_base_refs": refs or ["kb:local_herb_index:no_match"],
        "reason": reason,
        "rule": "prescription_rule_v1",
    }


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = ["PrescriptionEvaluation", "evaluate_prescription"]
