"""画像动态更新规则。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.profile.models import ProfileEvent, StudentProfile


def update_from_event(profile: StudentProfile, event: ProfileEvent | dict[str, object]) -> StudentProfile:
    """根据做题、答疑、游戏事件更新画像。"""

    parsed = event if isinstance(event, ProfileEvent) else ProfileEvent.model_validate(event)
    updated = profile.model_copy(deep=True)

    if parsed.event_type == "quiz_answer":
        _apply_quiz_event(updated, parsed)
    elif parsed.event_type == "qa":
        _apply_qa_event(updated, parsed)
    elif parsed.event_type == "game":
        _apply_game_event(updated, parsed)

    updated.updated_at = datetime.now(UTC)
    return updated


def _apply_quiz_event(profile: StudentProfile, event: ProfileEvent) -> None:
    topic = event.topic or event.skill or "未命名知识点"
    if event.is_correct is False:
        _add_weak_point(profile, topic)
        profile.knowledge_base.score = _clamp(profile.knowledge_base.score - 8)
        profile.weak_points.score = _clamp(profile.weak_points.score - 12)
        profile.knowledge_base.evidence = f"做错{topic}相关题目，需回补概念辨析。"
        profile.weak_points.evidence = f"新增薄弱点：{topic}。{event.detail}".strip()
        return

    if event.is_correct is True:
        profile.knowledge_base.score = _clamp(profile.knowledge_base.score + 4)
        profile.weak_points.score = _clamp(profile.weak_points.score + 3)
        profile.knowledge_base.evidence = f"{topic}相关题目作答正确，基础掌握度小幅提升。"


def _apply_qa_event(profile: StudentProfile, event: ProfileEvent) -> None:
    detail = event.detail or event.topic or "主动答疑"
    profile.motivation.score = _clamp(profile.motivation.score + 3)
    profile.learning_style.evidence = f"答疑中表现出偏好：{detail}"
    if event.topic:
        profile.resource_preference.evidence = f"近期关注资源主题：{event.topic}"


def _apply_game_event(profile: StudentProfile, event: ProfileEvent) -> None:
    delta = event.score_delta if event.score_delta is not None else (5 if event.is_correct else -5)
    profile.pace.score = _clamp(profile.pace.score + delta)
    profile.motivation.score = _clamp(profile.motivation.score + max(-3, min(5, delta)))
    profile.pace.evidence = event.detail or "开药模拟表现已纳入进度节奏判断。"


def _add_weak_point(profile: StudentProfile, topic: str) -> None:
    if topic not in profile.weak_points.items:
        profile.weak_points.items.append(topic)


def _clamp(value: int) -> int:
    return max(0, min(100, value))
