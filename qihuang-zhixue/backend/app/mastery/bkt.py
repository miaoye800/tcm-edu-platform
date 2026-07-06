"""贝叶斯知识追踪（BKT）掌握度模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.mastery.storage import DB_PATH, add_mastery_event, list_mastery_events


@dataclass(frozen=True)
class BKTParams:
    """单个知识点的 BKT 四参数。"""

    p_known_initial: float = 0.35
    p_learn: float = 0.18
    p_slip: float = 0.1
    p_guess: float = 0.2

    def validate(self) -> None:
        """校验参数概率范围。"""

        for name, value in (
            ("p_known_initial", self.p_known_initial),
            ("p_learn", self.p_learn),
            ("p_slip", self.p_slip),
            ("p_guess", self.p_guess),
        ):
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{name} 必须在 0 到 1 之间")


@dataclass(frozen=True)
class MasterySnapshot:
    """单个知识点的掌握度快照。"""

    kc_id: str
    p_mastery: float
    attempts: int
    last_seen: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "p_mastery": self.p_mastery,
            "attempts": self.attempts,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }


DEFAULT_BKT_PARAMS = BKTParams()
MASTERY_THRESHOLD = 0.8


def update_p_mastery(p_mastery: float, correct: bool, params: BKTParams = DEFAULT_BKT_PARAMS) -> float:
    """根据一次答题结果更新 P(mastery)。"""

    params.validate()
    clipped = _clip_probability(p_mastery)
    if correct:
        denominator = clipped * (1.0 - params.p_slip) + (1.0 - clipped) * params.p_guess
        posterior = _safe_divide(clipped * (1.0 - params.p_slip), denominator, clipped)
    else:
        denominator = clipped * params.p_slip + (1.0 - clipped) * (1.0 - params.p_guess)
        posterior = _safe_divide(clipped * params.p_slip, denominator, clipped)
    learned = posterior + (1.0 - posterior) * params.p_learn
    return _clip_probability(learned)


def record_mastery_event(
    *,
    user_id: str,
    kc_id: str,
    correct: bool,
    event_type: str = "quiz",
    elapsed_seconds: float | None = None,
    evidence: dict[str, Any] | None = None,
    created_at: datetime | None = None,
    db_path: Path = DB_PATH,
) -> None:
    """记录来自测验或开药游戏的一次知识点事件。"""

    add_mastery_event(
        user_id=user_id,
        kc_id=kc_id,
        correct=correct,
        event_type=event_type,
        elapsed_seconds=elapsed_seconds,
        evidence=evidence,
        created_at=created_at,
        db_path=db_path,
    )


def get_mastery(
    user_id: str,
    db_path: Path = DB_PATH,
    params_by_kc: dict[str, BKTParams] | None = None,
) -> dict[str, dict[str, Any]]:
    """返回用户每个知识点的掌握度、尝试次数与最近出现时间。"""

    params_map = params_by_kc or {}
    states: dict[str, MasterySnapshot] = {}
    p_values: dict[str, float] = {}
    attempts: dict[str, int] = {}
    last_seen: dict[str, datetime | None] = {}

    for event in list_mastery_events(user_id, db_path=db_path):
        kc_id = str(event["kc_id"])
        params = params_map.get(kc_id, DEFAULT_BKT_PARAMS)
        current = p_values.get(kc_id, params.p_known_initial)
        p_values[kc_id] = update_p_mastery(current, bool(event["correct"]), params)
        attempts[kc_id] = attempts.get(kc_id, 0) + 1
        last_seen[kc_id] = event["created_at"]

    for kc_id, p_mastery in p_values.items():
        states[kc_id] = MasterySnapshot(
            kc_id=kc_id,
            p_mastery=round(p_mastery, 4),
            attempts=attempts.get(kc_id, 0),
            last_seen=last_seen.get(kc_id),
        )
    return {kc_id: snapshot.as_dict() for kc_id, snapshot in states.items()}


def _clip_probability(value: float) -> float:
    return min(max(value, 0.0001), 0.9999)


def _safe_divide(numerator: float, denominator: float, default: float) -> float:
    if abs(denominator) < 1e-12:
        return default
    return numerator / denominator

