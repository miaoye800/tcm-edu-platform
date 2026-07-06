"""Seed demo accounts and new-stack profile data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.auth.storage import upsert_seed_user
from app.mastery.bkt import record_mastery_event
from app.mastery.review import record_review_result
from app.profile.schema import WeakPoint, create_blank_profile
from app.profile.store import DB_PATH, upsert_profile

DEMO_PASSWORD = "demo1234"


def seed_demo_accounts(db_path: Path = DB_PATH) -> list[str]:
    """Create demo users with differentiated new-stack profile snapshots."""

    users = [
        upsert_seed_user(username="disciple_a", password=DEMO_PASSWORD, display_name="disciple_a", db_path=db_path),
        upsert_seed_user(username="disciple_b", password=DEMO_PASSWORD, display_name="disciple_b", db_path=db_path),
        upsert_seed_user(username="disciple_c", password=DEMO_PASSWORD, display_name="disciple_c", db_path=db_path),
    ]
    _seed_profile(users[0].user_id, weak_label="清热药-剂量", resource_label="知识图谱", db_path=db_path)
    _seed_profile(users[1].user_id, weak_label="补虚药-功效鉴别", resource_label="医案文选", db_path=db_path)
    _seed_profile(users[2].user_id, weak_label="解表药-配伍", resource_label="汤头歌诀", db_path=db_path)
    _seed_mastery(users[0].user_id, weak_kc="herb_jinyinhua", strong_kc="herb_huangqi", db_path=db_path)
    _seed_mastery(users[1].user_id, weak_kc="herb_huangqi", strong_kc="herb_jinyinhua", db_path=db_path)
    _seed_mastery(users[2].user_id, weak_kc="herb_mahuang", strong_kc="herb_guizhi", db_path=db_path)
    return [user.username for user in users]


def _seed_profile(user_id: str, *, weak_label: str, resource_label: str, db_path: Path) -> None:
    profile = create_blank_profile(user_id)
    profile.knowledge_base.score = 58
    profile.knowledge_base.confidence = 0.82
    profile.knowledge_base.narrative = f"围绕{weak_label}存在连续错题证据。"
    profile.learning_goal.label = "应试提分"
    profile.learning_goal.confidence = 0.72
    profile.learning_style.label = "视觉型"
    profile.learning_style.confidence = 0.68
    profile.weak_points.label = weak_label
    profile.weak_points.confidence = 0.9
    profile.weak_points.items = [WeakPoint(label=weak_label, strength=4)]
    profile.pace.score = 72
    profile.pace.confidence = 0.66
    profile.motivation.score = 76
    profile.motivation.label = "内驱"
    profile.motivation.confidence = 0.7
    profile.resource_preference.label = resource_label  # type: ignore[assignment]
    profile.resource_preference.distribution = {resource_label: 1.0}  # type: ignore[dict-item]
    profile.resource_preference.confidence = 0.78
    upsert_profile(profile, db_path=db_path)


def _seed_mastery(user_id: str, *, weak_kc: str, strong_kc: str, db_path: Path) -> None:
    now = datetime.now(UTC)
    for index in range(3):
        record_mastery_event(
            user_id=user_id,
            kc_id=weak_kc,
            correct=False,
            event_type="seed_quiz",
            created_at=now - timedelta(days=3 - index),
            evidence={"seed": True, "topic": weak_kc},
            db_path=db_path,
        )
    for index in range(4):
        record_mastery_event(
            user_id=user_id,
            kc_id=strong_kc,
            correct=True,
            event_type="seed_quiz",
            created_at=now - timedelta(days=4 - index),
            evidence={"seed": True, "topic": strong_kc},
            db_path=db_path,
        )
    record_review_result(
        user_id=user_id,
        kc_id=weak_kc,
        rating="again",
        reviewed_at=now - timedelta(days=1),
        evidence={"seed": True, "topic": weak_kc},
        db_path=db_path,
    )


def main() -> None:
    usernames = seed_demo_accounts()
    print("Created demo accounts:")
    for username in usernames:
        print(f"- {username} / {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
