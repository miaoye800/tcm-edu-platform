"""Persistent Elo-style overall ability for learning profiles.

This is deliberately separate from BKT: BKT estimates mastery for one
knowledge component, while this module estimates a student's cross-topic
ability from calibrated question difficulty.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.profile.store import DB_PATH

INITIAL_RATING = 1000.0
INITIAL_K = 32.0
LATER_K = 16.0
LATER_K_AFTER_ATTEMPTS = 20
REPEAT_CORRECT_MULTIPLIER = 0.3
COVERAGE_TARGET = 8

DIFFICULTY_RATINGS: dict[str, float] = {
    "easy": 800.0,
    "medium": 1000.0,
    "hard": 1200.0,
}


@dataclass(frozen=True)
class AbilitySnapshot:
    rating: float = INITIAL_RATING
    attempt_count: int = 0
    coverage_count: int = 0

    @property
    def score(self) -> float:
        return rating_to_score(self.rating)

    @property
    def coverage(self) -> float:
        return coverage_from_count(self.coverage_count)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rating": round(self.rating, 2),
            "score": self.score,
            "attempt_count": self.attempt_count,
            "coverage_count": self.coverage_count,
            "coverage_target": COVERAGE_TARGET,
            "coverage": self.coverage,
        }


def normalize_difficulty(value: str | int | None) -> str:
    """Normalize legacy 1-5 difficulty and named difficulty to three bands."""

    if isinstance(value, int) and not isinstance(value, bool):
        if value <= 2:
            return "easy"
        if value >= 4:
            return "hard"
        return "medium"
    normalized = str(value or "medium").strip().casefold()
    if normalized in {"easy", "simple", "简单", "易", "1", "2"}:
        return "easy"
    if normalized in {"hard", "difficult", "困难", "难", "4", "5"}:
        return "hard"
    return "medium"


def expected_score(user_rating: float, question_rating: float) -> float:
    """Return the Elo probability of a correct answer."""

    return 1.0 / (1.0 + 10.0 ** ((question_rating - user_rating) / 400.0))


def rating_to_score(rating: float) -> float:
    """Map 800..1200 linearly to 20..80, clipping both ends."""

    if rating <= 800.0:
        return 20.0
    if rating >= 1200.0:
        return 80.0
    return round(20.0 + (rating - 800.0) * 0.15, 2)


def coverage_from_count(coverage_count: int) -> float:
    return round(min(1.0, max(0, coverage_count) / COVERAGE_TARGET), 4)


def coverage_confidence_factor(coverage_count: int) -> float:
    return max(0.5, coverage_from_count(coverage_count))


def init_ability_db(db_path: Path = DB_PATH) -> None:
    """Create the ability state table and migrate it idempotently."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path, timeout=5.0)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_ability (
                user_id TEXT PRIMARY KEY,
                rating REAL NOT NULL DEFAULT 1000,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                coverage_json TEXT NOT NULL DEFAULT '[]',
                correct_repetitions_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'
            )
            """
        )
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(user_ability)").fetchall()}
        for name, definition in {
            "rating": "REAL NOT NULL DEFAULT 1000",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "coverage_json": "TEXT NOT NULL DEFAULT '[]'",
            "correct_repetitions_json": "TEXT NOT NULL DEFAULT '{}'",
            "updated_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
        }.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE user_ability ADD COLUMN {name} {definition}")
        conn.commit()


def get_ability(user_id: str, *, db_path: Path = DB_PATH) -> AbilitySnapshot:
    """Read the current ability without manufacturing a user row."""

    init_ability_db(db_path)
    with closing(sqlite3.connect(db_path, timeout=5.0)) as conn:
        row = conn.execute(
            "SELECT rating, attempt_count, coverage_json FROM user_ability WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return AbilitySnapshot()
    return AbilitySnapshot(
        rating=float(row[0]),
        attempt_count=int(row[1]),
        coverage_count=len(_json_list(row[2])),
    )


def record_ability_attempt(
    *,
    user_id: str,
    kc_id: str,
    correct: bool,
    difficulty: str | int | None = "medium",
    topic: str | None = None,
    db_path: Path = DB_PATH,
    updated_at: datetime | None = None,
) -> AbilitySnapshot:
    """Apply one answer to overall ability and persist the resulting snapshot."""

    init_ability_db(db_path)
    difficulty_band = normalize_difficulty(difficulty)
    question_rating = DIFFICULTY_RATINGS[difficulty_band]
    knowledge_key = kc_id.strip() or (topic or "unknown").strip() or "unknown"
    coverage_key = knowledge_key
    now_value = updated_at or datetime.now(UTC)
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=UTC)
    now = now_value.astimezone(UTC).isoformat()

    with closing(sqlite3.connect(db_path, timeout=5.0)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT rating, attempt_count, coverage_json, correct_repetitions_json
            FROM user_ability WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        rating = float(row[0]) if row else INITIAL_RATING
        attempts = int(row[1]) if row else 0
        coverage = set(_json_list(row[2])) if row else set()
        repetitions = _json_dict(row[3]) if row else {}
        repetition_key = f"{knowledge_key}\u001f{difficulty_band}"
        prior_correct = int(repetitions.get(repetition_key, 0))
        k = LATER_K if attempts >= LATER_K_AFTER_ATTEMPTS else INITIAL_K
        multiplier = REPEAT_CORRECT_MULTIPLIER if correct and prior_correct >= 2 else 1.0
        delta = k * ((1.0 if correct else 0.0) - expected_score(rating, question_rating)) * multiplier
        rating += delta
        attempts += 1
        coverage.add(coverage_key)
        if correct:
            repetitions[repetition_key] = prior_correct + 1
        conn.execute(
            """
            INSERT INTO user_ability(
                user_id, rating, attempt_count, coverage_json, correct_repetitions_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                rating = excluded.rating,
                attempt_count = excluded.attempt_count,
                coverage_json = excluded.coverage_json,
                correct_repetitions_json = excluded.correct_repetitions_json,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                rating,
                attempts,
                json.dumps(sorted(coverage), ensure_ascii=False),
                json.dumps(repetitions, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        conn.commit()
    return AbilitySnapshot(rating=rating, attempt_count=attempts, coverage_count=len(coverage))


def _json_list(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _json_dict(value: object) -> dict[str, int]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): int(count) for key, count in parsed.items() if isinstance(count, int)}


__all__ = [
    "AbilitySnapshot",
    "COVERAGE_TARGET",
    "DIFFICULTY_RATINGS",
    "coverage_confidence_factor",
    "coverage_from_count",
    "expected_score",
    "get_ability",
    "init_ability_db",
    "normalize_difficulty",
    "rating_to_score",
    "record_ability_attempt",
]
