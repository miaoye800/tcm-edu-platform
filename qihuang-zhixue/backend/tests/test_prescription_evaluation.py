from __future__ import annotations

from pathlib import Path

from app.profile.evaluation import evaluate_prescription_fixture, load_fixture


FIXTURE = Path(__file__).parent / "fixtures" / "prescription_eval_cases.json"


def test_prescription_matrix_accepts_multiple_solutions_and_traces_findings() -> None:
    report = evaluate_prescription_fixture(load_fixture(FIXTURE))
    assert report["status"] == "rule_baseline_only"
    assert report["metrics"]["finding_quote_substring_pass_rate"] == 1.0
    assert report["metrics"]["finding_recall"] == 1.0
    assert report["metrics"]["uncertain_on_unresolved_case"] is True
    assert report["metrics"]["provenance_consistent"] is True
    rows = {row["case_id"]: row for row in report["cases"]}
    assert rows["reasonable_a"]["metrics"]["multiple_solution_allowed"] is True
    assert "dose_abnormal" in {item["kind"] for item in rows["dose"]["actual"]["findings"]}
    assert "toxic_misuse" in {item["kind"] for item in rows["toxic"]["actual"]["findings"]}
    assert rows["unresolved_case"]["actual"]["symptom_match_status"] == "uncertain"


def test_prescription_report_does_not_contain_profile_scores() -> None:
    report = evaluate_prescription_fixture({"cases": [{"case_id": "x", "input": {"case": "未知", "prescription": "未知药10g"}}]})
    rendered = str(report).lower()
    assert "profile_score" not in rendered
    assert "radar_value" not in rendered
