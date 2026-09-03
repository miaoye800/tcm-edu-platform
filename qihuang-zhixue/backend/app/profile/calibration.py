"""Calibration plans and settlement for objective self-report claims."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.kb.ingest import load_herbs
from app.profile.evidence import EvidenceCard, EvidenceTier
from app.profile.fusion import fuse_dimension, score_from_evidence_value
from app.profile.store import (
    DB_PATH,
    add_evidence,
    adjust_trust,
    connect,
    get_profile,
    get_trust,
    init_db,
    list_evidence,
    record_profile_change,
    update_evidence_claim_status,
    upsert_profile,
)


class CalibrationAnswer(BaseModel):
    question_id: str = Field(min_length=1)
    answer: str | list[str] | None = None
    correct: bool | None = None


class CalibrationSubmission(BaseModel):
    claim_id: str = Field(min_length=1)
    answers: list[CalibrationAnswer] = Field(min_length=1)


def next_calibration(user_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    """Return the oldest pending calibration plan, creating plans as needed."""

    _ensure_pending_tasks(user_id, db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT claim_id, dimension, questions_json, answers_json, created_at
            FROM calibration_tasks
            WHERE user_id = ? AND status = 'pending'
            ORDER BY created_at, claim_id LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    questions = json.loads(str(row[2]))
    answered_ids = {
        str(item["question_id"])
        for item in json.loads(str(row[3]))
        if item.get("question_id")
    }
    return {
        "claim_id": row[0],
        "dimension": row[1],
        "questions": [
            _public_question(item)
            for item in questions
            if str(item["question_id"]) not in answered_ids
        ],
        "created_at": row[4],
    }


def settle_calibration(
    user_id: str,
    submission: CalibrationSubmission | dict[str, Any],
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Settle a plan, update trust, and recalibrate the affected profile axis."""

    parsed = submission if isinstance(submission, CalibrationSubmission) else CalibrationSubmission.model_validate(submission)
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT dimension, questions_json, answers_json, status
            FROM calibration_tasks WHERE claim_id = ? AND user_id = ?
            """,
            (parsed.claim_id, user_id),
        ).fetchone()
    if row is None:
        raise KeyError(parsed.claim_id)
    if row[3] != "pending":
        raise ValueError("calibration task is already settled")

    questions = json.loads(str(row[1]))
    previous_graded = json.loads(str(row[2]))
    answer_by_id = {item.question_id: item for item in parsed.answers}
    graded_by_id = {str(item["question_id"]): item for item in previous_graded}
    for question in questions:
        answer = answer_by_id.get(str(question["question_id"]))
        if answer is None:
            continue
        correct = answer.correct if answer.correct is not None else _answer_matches(answer.answer, question["correct_answer"])
        graded_by_id[str(question["question_id"])] = {
            "question_id": question["question_id"],
            "correct": bool(correct),
        }
    graded = list(graded_by_id.values())
    if not parsed.answers:
        raise ValueError("submission does not answer any calibration question")
    if any(str(question["question_id"]) not in graded_by_id for question in questions):
        raise ValueError("all outstanding calibration questions must be answered")

    accuracy = sum(1 for item in graded if item["correct"]) / len(graded)
    if accuracy >= 2 / 3:
        status: Literal["verified", "refuted", "pending"] = "verified"
        reason = "自评经实测验证"
        trust = adjust_trust(user_id, 0.05, db_path)
    elif accuracy <= 1 / 3:
        status = "refuted"
        reason = "自评偏高，已按实测校准"
        trust = adjust_trust(user_id, -0.15, db_path)
    else:
        status = "pending"
        reason = "实测结果尚不充分，已补充校准题"
        trust = get_trust(user_id, db_path)

    now = datetime.now(UTC)
    if status == "pending":
        questions.extend(_generate_questions(_claim_for_id(user_id, parsed.claim_id, db_path), count=2, offset=len(questions)))
        _update_task(parsed.claim_id, questions, graded, status, db_path, now)
        return {
            "claim_id": parsed.claim_id,
            "status": status,
            "accuracy": round(accuracy, 4),
            "trust": trust,
            "added_questions": 2,
        }

    claim = update_evidence_claim_status(user_id, parsed.claim_id, status, db_path)
    calibration_card = _calibration_evidence(claim, status, accuracy, reason, now)
    add_evidence(user_id, calibration_card, db_path)
    _update_task(parsed.claim_id, questions, graded, status, db_path, now)
    _recompute_dimension(user_id, claim.dimension, reason, db_path, now)
    return {
        "claim_id": parsed.claim_id,
        "status": status,
        "accuracy": round(accuracy, 4),
        "trust": trust,
        "reason": reason,
    }


def _ensure_pending_tasks(user_id: str, db_path: Path) -> None:
    init_db(db_path)
    claims = [card for card in list_evidence(user_id, db_path) if card.claim_status == "pending"]
    now = datetime.now(UTC).isoformat()
    with closing(connect(db_path)) as conn:
        for claim in claims:
            exists = conn.execute(
                "SELECT 1 FROM calibration_tasks WHERE claim_id = ?",
                (claim.evidence_id,),
            ).fetchone()
            if exists:
                continue
            questions = _generate_questions(claim, count=2)
            conn.execute(
                """
                INSERT INTO calibration_tasks(
                    claim_id, user_id, dimension, status, questions_json,
                    answers_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, '[]', ?, ?)
                """,
                (claim.evidence_id, user_id, claim.dimension, json.dumps(questions, ensure_ascii=False), now, now),
            )
        conn.commit()


def _generate_questions(claim: EvidenceCard, *, count: int, offset: int = 0) -> list[dict[str, Any]]:
    herbs = [herb for herb in load_herbs() if herb.verified] or load_herbs()
    if not herbs:
        return []
    advanced = _claimed_score(claim) >= 60
    questions: list[dict[str, Any]] = []
    for index in range(count):
        herb = herbs[(offset + index) % len(herbs)]
        question_id = f"cal-{claim.evidence_id[:10]}-{offset + index + 1}"
        if advanced and herb.combinations:
            combo = herb.combinations[0]
            correct = combo.purpose
            prompt = f"{herb.name}与该组药配伍时，主要配伍目的是什么？"
            kind = "配伍"
        elif advanced:
            correct = herb.dosage.text
            prompt = f"{herb.name}的常用剂量范围是？"
            kind = "剂量"
        else:
            correct = herb.effects[0]
            prompt = f"{herb.name}的代表性功效是？"
            kind = "基础功效"
        distractors = _distractors(herbs, correct, advanced=advanced)
        questions.append(
            {
                "question_id": question_id,
                "prompt": prompt,
                "options": [correct, *distractors][:4],
                "correct_answer": correct,
                "difficulty": "hard" if advanced else "basic",
                "knowledge_type": kind,
                "herb_id": herb.id,
            }
        )
    return questions


def _distractors(herbs: list[Any], correct: str, *, advanced: bool) -> list[str]:
    values: list[str] = []
    for herb in herbs:
        candidate = herb.dosage.text if advanced else herb.effects[0]
        if candidate != correct and candidate not in values:
            values.append(candidate)
        if len(values) == 3:
            break
    while len(values) < 3:
        values.append(f"以上均不符合{len(values) + 1}")
    return values


def _claimed_score(claim: EvidenceCard) -> float:
    score = score_from_evidence_value(claim)
    return float(score if score is not None else 50.0)


def _claim_for_id(user_id: str, claim_id: str, db_path: Path) -> EvidenceCard:
    for card in list_evidence(user_id, db_path):
        if card.evidence_id == claim_id:
            return card
    raise KeyError(claim_id)


def _calibration_evidence(
    claim: EvidenceCard,
    status: str,
    accuracy: float,
    reason: str,
    timestamp: datetime,
) -> EvidenceCard:
    signal: dict[str, Any] = {
        "accuracy": accuracy,
        "correct_rate": accuracy,
        "claim_id": claim.evidence_id,
    }
    if claim.dimension in {"knowledge_base", "pace"}:
        signal["value"] = _claimed_score(claim) if status == "verified" else accuracy * 100
    return EvidenceCard(
        dimension=claim.dimension,
        source="校准实测",
        tier=EvidenceTier.MEASURED,
        fact=reason,
        objective_signal=signal,
        weight=2.0,
        timestamp=timestamp,
    )


def _recompute_dimension(user_id: str, dimension: str, reason: str, db_path: Path, now: datetime) -> None:
    cards = [card for card in list_evidence(user_id, db_path) if card.dimension == dimension]
    profile = get_profile(user_id, db_path)
    dim = getattr(profile, dimension)
    old_value = _dimension_snapshot(dim)
    result = fuse_dimension(dimension, cards, now=now, self_report_trust=get_trust(user_id, db_path))
    dim.confidence = result.confidence
    dim.evidence = cards
    dim.narrative = reason
    if hasattr(dim, "score"):
        dim.score = result.score
    if hasattr(dim, "label"):
        dim.label = result.label
    if hasattr(dim, "distribution"):
        dim.distribution = result.distribution
    new_value = _dimension_snapshot(dim)
    record_profile_change(
        user_id,
        dimension,
        old_value,
        new_value,
        reason=reason,
        source_event="calibration",
        evidence_ids=[card.evidence_id for card in cards],
        db_path=db_path,
        ts=now,
    )
    upsert_profile(profile, db_path)


def _dimension_snapshot(dim: Any) -> dict[str, Any]:
    return {
        "score": getattr(dim, "score", None),
        "label": getattr(dim, "label", None),
        "distribution": getattr(dim, "distribution", {}),
        "confidence": getattr(dim, "confidence", 0.0),
        "items": [item.model_dump(mode="json") for item in getattr(dim, "items", [])],
    }


def _update_task(
    claim_id: str,
    questions: list[dict[str, Any]],
    answers: list[dict[str, Any]],
    status: str,
    db_path: Path,
    now: datetime,
) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE calibration_tasks
            SET questions_json = ?, answers_json = ?, status = ?, updated_at = ?
            WHERE claim_id = ?
            """,
            (json.dumps(questions, ensure_ascii=False), json.dumps(answers, ensure_ascii=False), status, now.isoformat(), claim_id),
        )
        conn.commit()


def _public_question(question: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in question.items() if key != "correct_answer"}


def _answer_matches(answer: str | list[str] | None, expected: str) -> bool:
    if isinstance(answer, list):
        return len(answer) == 1 and answer[0].strip().casefold() == expected.strip().casefold()
    return isinstance(answer, str) and answer.strip().casefold() == expected.strip().casefold()


__all__ = ["CalibrationSubmission", "next_calibration", "settle_calibration"]
