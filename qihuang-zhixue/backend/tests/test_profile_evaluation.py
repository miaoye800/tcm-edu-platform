from __future__ import annotations

import json
from pathlib import Path

from app.profile.evaluation import classify_model_error, evaluate_profile_case, evaluate_profile_fixture, inspect_model_response, load_fixture, model_request_record


FIXTURE = Path(__file__).parent / "fixtures" / "profile_eval_cases.json"


def test_fixed_profile_fixture_is_sanitized_and_repeatable() -> None:
    payload = load_fixture(FIXTURE)
    assert payload["version"] == "profile-eval-v1"
    assert len(payload["cases"]) >= 10
    report = evaluate_profile_fixture(payload)
    assert report["status"] == "rule_baseline_only"
    assert report["model_quality_verified"] is False
    assert report["metrics"]["quote_substring_pass_rate"] == 1.0
    assert report["metrics"]["dimension_legality_rate"] == 1.0
    assert report["metrics"]["metadata_legality_rate"] == 1.0
    assert report["metrics"]["provenance_consistent"] is True
    assert report["metrics"]["deterministic_repeat_rate"] == 1.0


def test_profile_evaluation_never_emits_final_profile_fields() -> None:
    case = {"case_id": "safe", "input": "我想学中医", "expected": {"evidence": []}}
    result = evaluate_profile_case(case).as_dict()
    serialized = json.dumps(result, ensure_ascii=False).lower()
    assert "radar_value" not in serialized
    assert "flip_eligibility" not in serialized
    assert result["rule_fallback"] is True
    assert result["provenance"] == "no_key_rule_fallback"


def test_model_mode_is_explicitly_not_evaluated() -> None:
    result = evaluate_profile_case({"case_id": "future", "input": "synthetic"}, mode="model")
    assert result.metrics["status"] == "model_not_evaluated"
    report = evaluate_profile_fixture({"cases": [{"case_id": "future", "input": "synthetic"}]}, mode="model")
    assert report["status"] == "model_not_evaluated"
    assert report["model_quality_verified"] is not True


def test_future_model_audit_metadata_is_hashed_and_redacted() -> None:
    record = model_request_record({"case_id": "x", "input": "脱敏样本"}, model="m", version="v1")
    assert len(record["sample_hash"]) == 64
    assert record["api_key_recorded"] is False
    assert inspect_model_response(raw_response="not json", elapsed_ms=12)["error_class"] == "invalid_json"
    assert inspect_model_response(raw_response=None, elapsed_ms=2, error=402)["error_class"] == "http_402"
    assert classify_model_error(TimeoutError()) == "timeout"
