from pathlib import Path

import pytest

from app.profile.calibration import next_calibration, settle_calibration
from app.profile.evidence import EvidenceCard, EvidenceTier
from app.profile.fusion import effective_card_weights
from app.profile.store import add_evidence, get_profile, get_trust, list_evidence, list_profile_changes


def _claim(value: float = 80) -> EvidenceCard:
    return EvidenceCard(
        dimension="knowledge_base",
        source="dialogue",
        tier=EvidenceTier.SELF_REPORT,
        fact="learner self-reported knowledge level",
        objective_signal={"value": value, "base_level": "熟练" if value >= 60 else "入门"},
        weight=1.0,
    )


def _answers(task: dict, correct: bool) -> list[dict[str, object]]:
    return [
        {"question_id": question["question_id"], "correct": correct}
        for question in task["questions"]
    ]


def test_pending_claim_generates_matching_calibration_questions(tmp_path: Path) -> None:
    db_path = tmp_path / "calibration.db"
    claim = _claim(82)
    add_evidence("student", claim, db_path)

    task = next_calibration("student", db_path)

    assert claim.claim_status == "pending"
    assert task is not None
    assert task["claim_id"] == claim.evidence_id
    assert 2 <= len(task["questions"]) <= 3
    assert {question["difficulty"] for question in task["questions"]} == {"hard"}
    assert all("correct_answer" not in question for question in task["questions"])


def test_verified_claim_unblocks_confidence_and_increases_trust(tmp_path: Path) -> None:
    db_path = tmp_path / "verified.db"
    claim = _claim(80)
    add_evidence("verified-user", claim, db_path)
    task = next_calibration("verified-user", db_path)
    assert task is not None

    result = settle_calibration(
        "verified-user",
        {"claim_id": claim.evidence_id, "answers": _answers(task, True)},
        db_path,
    )

    profile = get_profile("verified-user", db_path)
    stored_claim = list_evidence("verified-user", db_path)[0]
    assert result["status"] == "verified"
    assert stored_claim.claim_status == "verified"
    assert profile.knowledge_base.score == pytest.approx(80)
    assert profile.knowledge_base.confidence > 0.35
    assert get_trust("verified-user", db_path) == pytest.approx(0.75)


def test_refuted_claim_lowers_score_confidence_and_trust(tmp_path: Path) -> None:
    db_path = tmp_path / "refuted.db"
    claim = _claim(80)
    add_evidence("refuted-user", claim, db_path)
    task = next_calibration("refuted-user", db_path)
    assert task is not None

    result = settle_calibration(
        "refuted-user",
        {"claim_id": claim.evidence_id, "answers": _answers(task, False)},
        db_path,
    )

    profile = get_profile("refuted-user", db_path)
    assert result["status"] == "refuted"
    assert profile.knowledge_base.score is not None
    assert profile.knowledge_base.score < 40
    assert profile.knowledge_base.confidence > 0.35
    assert get_trust("refuted-user", db_path) == pytest.approx(0.55)


def test_inconclusive_calibration_keeps_claim_pending_and_adds_questions(tmp_path: Path) -> None:
    db_path = tmp_path / "inconclusive.db"
    claim = _claim(50)
    add_evidence("pending-user", claim, db_path)
    task = next_calibration("pending-user", db_path)
    assert task is not None

    answers = _answers(task, False)
    answers[0]["correct"] = True
    result = settle_calibration(
        "pending-user",
        {"claim_id": claim.evidence_id, "answers": answers},
        db_path,
    )
    follow_up = next_calibration("pending-user", db_path)

    assert result["status"] == "pending"
    assert result["added_questions"] == 2
    assert follow_up is not None
    assert len(follow_up["questions"]) == 2
    assert list_evidence("pending-user", db_path)[0].claim_status == "pending"
    assert get_trust("pending-user", db_path) == pytest.approx(0.7)


def test_calibration_change_log_is_complete_and_readable(tmp_path: Path) -> None:
    db_path = tmp_path / "change-log.db"
    claim = _claim()
    add_evidence("logged-user", claim, db_path)
    task = next_calibration("logged-user", db_path)
    assert task is not None
    settle_calibration(
        "logged-user",
        {"claim_id": claim.evidence_id, "answers": _answers(task, False)},
        db_path,
    )

    changes = list_profile_changes("logged-user", db_path=db_path)
    assert changes
    assert changes[0]["dim"] == "knowledge_base"
    assert changes[0]["old_value"] != changes[0]["new_value"]
    assert changes[0]["reason"] == "自评偏高，已按实测校准"
    assert changes[0]["source_event"] == "calibration"
    assert claim.evidence_id in changes[0]["evidence_ids"]


def test_lower_trust_reduces_actual_self_report_weight() -> None:
    cards = [_claim(70)]

    assert effective_card_weights(cards, self_report_trust=0.7) == pytest.approx([0.7])
    assert effective_card_weights(cards, self_report_trust=0.55) == pytest.approx([0.55])
