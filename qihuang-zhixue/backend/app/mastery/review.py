"""FSRS 风格间隔复习调度。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from app.mastery.storage import DB_PATH, get_review_state, list_review_states, upsert_review_state

Rating = Literal["again", "hard", "good", "easy"]


@dataclass(frozen=True)
class ReviewUpdate:
    """一次复习状态更新结果。"""

    kc_id: str
    rating: Rating
    difficulty: float
    stability: float
    retrievability: float
    due_at: datetime
    attempts: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "kc_id": self.kc_id,
            "rating": self.rating,
            "difficulty": round(self.difficulty, 4),
            "stability": round(self.stability, 4),
            "retrievability": round(self.retrievability, 4),
            "due_at": self.due_at.isoformat(),
            "attempts": self.attempts,
        }


def rating_from_answer(correct: bool, elapsed_seconds: float | None = None) -> Rating:
    """由对错和耗时映射 FSRS 评级。"""

    if not correct:
        return "again"
    if elapsed_seconds is not None and elapsed_seconds > 90:
        return "hard"
    if elapsed_seconds is not None and elapsed_seconds <= 20:
        return "easy"
    return "good"


def record_review_result(
    *,
    user_id: str,
    kc_id: str,
    rating: Rating | None = None,
    correct: bool | None = None,
    elapsed_seconds: float | None = None,
    reviewed_at: datetime | None = None,
    evidence: dict[str, Any] | None = None,
    db_path: Path = DB_PATH,
) -> ReviewUpdate:
    """按 FSRS 风格更新难度、稳定性、可提取性与到期日。"""

    if rating is None:
        if correct is None:
            raise ValueError("rating 与 correct 至少提供一个")
        rating = rating_from_answer(correct, elapsed_seconds)

    now = reviewed_at or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    previous = get_review_state(user_id, kc_id, db_path=db_path)
    old_difficulty = float(previous["difficulty"]) if previous else 5.0
    old_stability = float(previous["stability"]) if previous else 1.0
    attempts = int(previous["attempts"]) + 1 if previous else 1

    difficulty = _next_difficulty(old_difficulty, rating)
    stability = _next_stability(old_stability, difficulty, rating)
    retrievability = _retrievability(stability, now, previous)
    due_at = now + _interval_for_rating(stability, rating)

    upsert_review_state(
        user_id=user_id,
        kc_id=kc_id,
        difficulty=difficulty,
        stability=stability,
        retrievability=retrievability,
        due_at=due_at,
        last_rating=rating,
        attempts=attempts,
        evidence=evidence,
        updated_at=now,
        db_path=db_path,
    )
    return ReviewUpdate(
        kc_id=kc_id,
        rating=rating,
        difficulty=difficulty,
        stability=stability,
        retrievability=retrievability,
        due_at=due_at,
        attempts=attempts,
    )


def due_reviews(
    user_id: str,
    *,
    today: datetime | None = None,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """返回今日到期复习条目与建议题量。"""

    now = today or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    due: list[dict[str, Any]] = []
    for state in list_review_states(user_id, db_path=db_path):
        due_at = state["due_at"]
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=UTC)
        if due_at <= now:
            urgency_hours = max((now - due_at).total_seconds() / 3600.0, 0.0)
            due.append(
                {
                    "kc_id": state["kc_id"],
                    "due_at": due_at.isoformat(),
                    "difficulty": round(float(state["difficulty"]), 4),
                    "stability": round(float(state["stability"]), 4),
                    "retrievability": round(float(state["retrievability"]), 4),
                    "last_rating": state["last_rating"],
                    "attempts": state["attempts"],
                    "urgency": round(min(1.0, 0.4 + urgency_hours / 48.0), 4),
                    "evidence": state["evidence"],
                }
            )
    due.sort(key=lambda item: (item["due_at"], -float(item["difficulty"])))
    suggested_count = min(max(len(due), 0), 12)
    return {"items": due, "suggested_count": suggested_count}


def _next_difficulty(old: float, rating: Rating) -> float:
    delta_by_rating: dict[Rating, float] = {
        "again": 0.8,
        "hard": 0.25,
        "good": -0.25,
        "easy": -0.55,
    }
    return min(max(old + delta_by_rating[rating], 1.0), 10.0)


def _next_stability(old: float, difficulty: float, rating: Rating) -> float:
    if rating == "again":
        return max(0.25, old * 0.45)
    growth_by_rating: dict[Rating, float] = {
        "hard": 1.25,
        "good": 2.35,
        "easy": 3.4,
        "again": 1.0,
    }
    difficulty_penalty = 1.0 + (10.0 - difficulty) / 30.0
    return min(max(old * growth_by_rating[rating] * difficulty_penalty, 0.25), 365.0)


def _interval_for_rating(stability: float, rating: Rating) -> timedelta:
    if rating == "again":
        return timedelta(minutes=10)
    if rating == "hard":
        return timedelta(days=max(1.0, stability * 0.6))
    if rating == "good":
        return timedelta(days=max(1.0, stability))
    return timedelta(days=max(2.0, stability * 1.45))


def _retrievability(stability: float, now: datetime, previous: dict[str, Any] | None) -> float:
    if previous is None:
        return 1.0
    updated_at = previous["updated_at"]
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    elapsed_days = max((now - updated_at).total_seconds() / 86400.0, 0.0)
    return round((1.0 + elapsed_days / max(stability, 0.25)) ** -1.0, 4)

