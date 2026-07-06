"""Incremental learning-profile updates and conditional jury rescoring."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import Settings
from app.llm.router import ModelRouter
from app.profile.fusion import FusionResult, fuse_dimension, fuse_profile
from app.profile.jury import LLMCall, ScoringResult, score_dimension
from app.profile.radar import to_radar
from app.profile.schema import DIMENSION_LABELS, DimensionKey, EvidenceCard, Profile, WeakPoint
from app.profile.store import DB_PATH, add_evidence, get_profile, init_db, list_evidence, upsert_profile

SHANGHAI_TZ = timezone(timedelta(hours=8))
DEFAULT_JURY_WEIGHT_THRESHOLD = 2.0

ProfileEventType = Literal["quiz", "prescription", "interaction"]
JuryScorer = Callable[..., Awaitable[ScoringResult]]


class ProfileUpdateEvent(BaseModel):
    """Behavior event accepted by the update hook."""

    type: ProfileEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime | None = None


class ProfileUpdateResult(BaseModel):
    """Incremental update result for tests and callers."""

    user_id: str
    evidence_cards: list[EvidenceCard]
    affected_dimensions: list[DimensionKey]
    jury_called_dimensions: list[DimensionKey]
    profile: Profile

    @property
    def jury_call_count(self) -> int:
        """Number of dimensions rescored through the jury."""

        return len(self.jury_called_dimensions)


async def update_profile_from_event(
    user_id: str,
    event: ProfileUpdateEvent | dict[str, Any],
    *,
    db_path: Path = DB_PATH,
    jury_weight_threshold: float = DEFAULT_JURY_WEIGHT_THRESHOLD,
    jury_scorer: JuryScorer = score_dimension,
    llm_call: LLMCall | None = None,
    router: ModelRouter | None = None,
    settings: Settings | None = None,
    force_llm: bool = False,
    now: datetime | None = None,
) -> ProfileUpdateResult:
    """Convert an event to evidence cards and incrementally update dimensions."""

    parsed = event if isinstance(event, ProfileUpdateEvent) else ProfileUpdateEvent.model_validate(event)
    cards = evidence_cards_from_event(parsed, now=now)
    for card in cards:
        add_evidence(user_id, card, db_path=db_path)

    affected = _ordered_dimensions(card.dimension for card in cards)
    all_cards = list_evidence(user_id, db_path=db_path)
    profile = get_profile(user_id, db_path=db_path)
    jury_called: list[DimensionKey] = []
    _init_update_state(db_path)

    for dimension in affected:
        dimension_cards = [card for card in all_cards if card.dimension == dimension]
        fusion = fuse_dimension(dimension, dimension_cards, now=now)
        _apply_fusion_to_profile(profile, dimension, fusion, dimension_cards)
        pending_weight = _increment_pending_weight(user_id, dimension, _new_weight(cards, dimension), db_path)
        if pending_weight >= jury_weight_threshold:
            result = await jury_scorer(
                dimension,
                dimension_cards,
                router=router,
                settings=settings,
                force_llm=force_llm,
                llm_call=llm_call,
            )
            _apply_scoring_to_profile(profile, dimension, result, dimension_cards)
            _reset_pending_weight(user_id, dimension, db_path)
            jury_called.append(dimension)

    upsert_profile(profile, db_path=db_path)
    return ProfileUpdateResult(
        user_id=user_id,
        evidence_cards=cards,
        affected_dimensions=affected,
        jury_called_dimensions=jury_called,
        profile=profile,
    )


async def get_latest_profile(
    user_id: str,
    *,
    db_path: Path = DB_PATH,
    jury_scorer: JuryScorer = score_dimension,
    llm_call: LLMCall | None = None,
    router: ModelRouter | None = None,
    settings: Settings | None = None,
    force_llm: bool = False,
    now: datetime | None = None,
) -> Profile:
    """Return the current profile, applying decay and user-view jury refresh."""

    cards = list_evidence(user_id, db_path=db_path)
    profile = get_profile(user_id, db_path=db_path)
    _init_update_state(db_path)
    if not cards:
        return profile

    fused = fuse_profile(cards, now=now)
    for dimension, fusion in fused.items():
        dimension_cards = [card for card in cards if card.dimension == dimension]
        _apply_fusion_to_profile(profile, dimension, fusion, dimension_cards)

    for dimension in _dimensions_with_evidence(cards):
        dimension_cards = [card for card in cards if card.dimension == dimension]
        result = await jury_scorer(
            dimension,
            dimension_cards,
            router=router,
            settings=settings,
            force_llm=force_llm,
            llm_call=llm_call,
        )
        _apply_scoring_to_profile(profile, dimension, result, dimension_cards)
        _reset_pending_weight(user_id, dimension, db_path)

    upsert_profile(profile, db_path=db_path)
    return profile


def evidence_cards_from_event(event: ProfileUpdateEvent, *, now: datetime | None = None) -> list[EvidenceCard]:
    """Event hook: quiz/prescription/interaction behavior to evidence cards."""

    timestamp = event.timestamp or now or datetime.now(SHANGHAI_TZ)
    if event.type == "quiz":
        return _quiz_cards(event.payload, timestamp)
    if event.type == "prescription":
        return _prescription_cards(event.payload, timestamp)
    return _interaction_cards(event.payload, timestamp)


def profile_to_response(profile: Profile) -> dict[str, Any]:
    """Stable API payload with latest profile and per-dimension confidence."""

    payload = profile.model_dump(mode="json")
    payload["confidence"] = {
        dimension: getattr(profile, dimension).confidence for dimension in DIMENSION_LABELS
    }
    payload["radar"] = to_radar(profile)
    return payload


def _quiz_cards(payload: dict[str, Any], timestamp: datetime) -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    items = payload.get("answers") or payload.get("items") or [payload]
    if not isinstance(items, list):
        items = [items]
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        difficulty = str(item.get("difficulty") or "easy")
        gave_up = bool(item.get("gave_up") or item.get("abandoned"))
        correct = bool(item.get("correct") or item.get("is_correct"))
        weight = 0.6 if gave_up else (1.2 if difficulty in {"hard", "难", "difficult"} else 0.8)
        topic = str(item.get("topic") or item.get("knowledge_point") or "辨药").strip()
        cards.append(
            _card(
                "knowledge_base",
                "做题",
                f"做题第{index}题{'放弃' if gave_up else ('答对' if correct else '答错')}",
                {
                    "question_id": item.get("question_id") or item.get("id") or index,
                    "correct": correct,
                    "difficulty": difficulty,
                    "gave_up": gave_up,
                    "topic": topic,
                },
                weight,
                timestamp,
            )
        )
        if gave_up or not correct:
            cards.append(
                _card(
                    "weak_points",
                    "做题",
                    f"{topic}出现{'放弃' if gave_up else '错误'}",
                    {"label": topic, "correct": correct, "gave_up": gave_up},
                    weight,
                    timestamp,
                )
            )
    return cards


def _prescription_cards(payload: dict[str, Any], timestamp: datetime) -> list[EvidenceCard]:
    signal = {
        "对症": bool(payload.get("对症", payload.get("symptom_match", payload.get("match_symptom", False)))),
        "配伍": bool(payload.get("配伍", payload.get("compatible", False))),
        "剂量": bool(payload.get("剂量", payload.get("dosage_ok", False))),
        "误用毒烈": bool(payload.get("误用毒烈", payload.get("toxic_misuse", False))),
    }
    weak_labels = _weak_labels_from_signal(signal, payload)
    if weak_labels:
        signal["weak_labels"] = weak_labels
    fact = "；".join(
        [
            f"对症{'是' if signal['对症'] else '否'}",
            f"配伍{'是' if signal['配伍'] else '否'}",
            f"剂量{'合适' if signal['剂量'] else '需核查'}",
            f"误用毒烈{'有' if signal['误用毒烈'] else '无'}",
        ]
    )
    cards = [_card("knowledge_base", "开药实战", fact, signal, 1.5, timestamp)]
    if weak_labels:
        cards.append(
            _card(
                "weak_points",
                "开药实战",
                f"开药薄弱标签：{'、'.join(weak_labels)}",
                {"weak_labels": weak_labels, "correct": False},
                1.5,
                timestamp,
            )
        )
    return cards


def _interaction_cards(payload: dict[str, Any], timestamp: datetime) -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    if label := _str_or_none(payload.get("learning_goal")):
        cards.append(_card("learning_goal", "交互事件", f"学习目标选择：{label}", {"label": label}, 1.0, timestamp))
    if label := _str_or_none(payload.get("style") or payload.get("learning_style")):
        cards.append(_card("learning_style", "交互事件", f"学习风格线索：{label}", {"label": label}, 1.0, timestamp))
    if label := _str_or_none(payload.get("resource_type") or payload.get("resource_preference")):
        cards.append(_card("resource_preference", "交互事件", f"资源偏好选择：{label}", {"label": label}, 1.0, timestamp))
    pace_signal = {
        "daily_minutes": payload.get("daily_minutes") or payload.get("minutes_per_day"),
        "target_days": payload.get("target_days") or payload.get("period_days"),
    }
    if pace_signal["daily_minutes"] is not None or pace_signal["target_days"] is not None:
        cards.append(_card("pace", "交互事件", "学习节奏约定", pace_signal, 1.0, timestamp))
    motivation_signal = {
        "label": payload.get("motivation_label") or payload.get("motivation"),
        "motivation_score": payload.get("motivation_score"),
        "accept_challenge": payload.get("accept_challenge") or payload.get("challenge"),
    }
    if any(value is not None for value in motivation_signal.values()):
        cards.append(_card("motivation", "交互事件", "动机偏好线索", motivation_signal, 1.0, timestamp))
    if not cards:
        topic = str(payload.get("topic") or payload.get("knowledge_point") or "未标注内容").strip()
        cards.append(_card("learning_style", "交互事件", f"普通交互：{topic}", {"topic": topic}, 0.3, timestamp))
    return cards


def _apply_fusion_to_profile(profile: Profile, dimension: DimensionKey, fusion: FusionResult, cards: list[EvidenceCard]) -> None:
    dim = getattr(profile, dimension)
    dim.confidence = fusion.confidence
    dim.evidence = cards
    dim.narrative = fusion.narrative
    if hasattr(dim, "score"):
        dim.score = fusion.score
    if hasattr(dim, "label"):
        dim.label = fusion.label
    if hasattr(dim, "distribution"):
        dim.distribution = fusion.distribution
    if dimension == "weak_points":
        dim.items = _weak_points_from_cards(cards)


def _apply_scoring_to_profile(profile: Profile, dimension: DimensionKey, result: ScoringResult, cards: list[EvidenceCard]) -> None:
    dim = getattr(profile, dimension)
    dim.confidence = result.confidence
    dim.evidence = cards
    dim.narrative = result.narrative
    if hasattr(dim, "score") and result.aggregated_score is not None:
        dim.score = result.aggregated_score
    if hasattr(dim, "label") and result.label is not None:
        dim.label = result.label
    if hasattr(dim, "distribution") and result.distribution:
        dim.distribution = result.distribution
    if dimension == "weak_points":
        dim.items = _weak_points_from_cards(cards)


def _weak_points_from_cards(cards: list[EvidenceCard]) -> list[WeakPoint]:
    counter: Counter[str] = Counter()
    for card in cards:
        signal = card.objective_signal
        for key in ("label", "topic", "knowledge_point"):
            value = signal.get(key)
            if isinstance(value, str) and value.strip():
                counter[value.strip()] += 1
        weak_labels = signal.get("weak_labels")
        if isinstance(weak_labels, list):
            for label in weak_labels:
                if isinstance(label, str) and label.strip():
                    counter[label.strip()] += 1
    return [WeakPoint(label=label, strength=strength) for label, strength in counter.most_common()]


def _init_update_state(db_path: Path) -> None:
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_update_state (
                user_id TEXT NOT NULL,
                dimension TEXT NOT NULL,
                pending_weight REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id, dimension)
            )
            """
        )
        conn.commit()


def _increment_pending_weight(user_id: str, dimension: DimensionKey, weight: float, db_path: Path) -> float:
    now = datetime.now(UTC).isoformat()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO profile_update_state(user_id, dimension, pending_weight, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, dimension) DO UPDATE SET
                pending_weight = pending_weight + excluded.pending_weight,
                updated_at = excluded.updated_at
            """,
            (user_id, dimension, weight, now),
        )
        row = conn.execute(
            "SELECT pending_weight FROM profile_update_state WHERE user_id = ? AND dimension = ?",
            (user_id, dimension),
        ).fetchone()
        conn.commit()
    return float(row[0]) if row else 0.0


def _reset_pending_weight(user_id: str, dimension: DimensionKey, db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO profile_update_state(user_id, dimension, pending_weight, updated_at)
            VALUES (?, ?, 0, ?)
            ON CONFLICT(user_id, dimension) DO UPDATE SET
                pending_weight = 0,
                updated_at = excluded.updated_at
            """,
            (user_id, dimension, datetime.now(UTC).isoformat()),
        )
        conn.commit()


def _new_weight(cards: list[EvidenceCard], dimension: DimensionKey) -> float:
    return sum(card.weight for card in cards if card.dimension == dimension)


def _dimensions_with_evidence(cards: list[EvidenceCard]) -> list[DimensionKey]:
    return _ordered_dimensions(card.dimension for card in cards)


def _ordered_dimensions(dimensions: Any) -> list[DimensionKey]:
    seen = set(dimensions)
    return [dimension for dimension in DIMENSION_LABELS if dimension in seen]


def _weak_labels_from_signal(signal: dict[str, bool], payload: dict[str, Any]) -> list[str]:
    provided = payload.get("weak_labels")
    if isinstance(provided, list):
        return [str(item).strip() for item in provided if str(item).strip()]
    labels: list[str] = []
    if not signal["对症"]:
        labels.append("辨证·对症")
    if not signal["配伍"]:
        labels.append("方药·配伍")
    if not signal["剂量"]:
        labels.append("中药·剂量")
    if signal["误用毒烈"]:
        labels.append("用药安全·毒烈药")
    return labels


def _card(
    dimension: DimensionKey,
    source: str,
    fact: str,
    objective_signal: dict[str, Any],
    weight: float,
    timestamp: datetime,
) -> EvidenceCard:
    return EvidenceCard(
        dimension=dimension,
        source=source,
        fact=fact,
        objective_signal={key: value for key, value in objective_signal.items() if value is not None},
        weight=weight,
        timestamp=timestamp,
    )


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = [
    "DEFAULT_JURY_WEIGHT_THRESHOLD",
    "ProfileUpdateEvent",
    "ProfileUpdateResult",
    "evidence_cards_from_event",
    "get_latest_profile",
    "profile_to_response",
    "update_profile_from_event",
]
