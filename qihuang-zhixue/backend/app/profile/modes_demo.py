"""Local demo runner for profile collection modes."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from app.profile.modes import (
    APPRENTICE_STEPS,
    ProfileMode,
    ProfileSession,
    answer_profile_session,
    cards_to_json,
    extract_prescription_signal,
    finish_profile_session,
    get_profile_session,
    reset_profile_sessions,
    start_profile_session,
)
from app.profile.schema import EvidenceCard


def main() -> None:
    """Run all four collection modes without touching the real database."""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(_run_demo())


async def _run_demo() -> None:
    reset_profile_sessions()
    with tempfile.TemporaryDirectory(prefix="qihuang-profile-demo-") as temp_dir:
        db_path = Path(temp_dir) / "profile_demo.db"
        demos = [
            await _demo_apprentice(db_path),
            await _demo_quickcard(db_path),
            await _demo_practice(db_path),
            await _demo_passive(db_path),
        ]

    for mode, cards in demos:
        print(f"===== mode: {mode} =====")
        print(json.dumps(cards_to_json(cards), ensure_ascii=False, indent=2))
        print()


async def _demo_apprentice(db_path: Path) -> tuple[str, list[EvidenceCard]]:
    session = start_profile_session("demo-apprentice", ProfileMode.apprentice)
    await answer_profile_session(session.session_id, APPRENTICE_STEPS[0], {"identity_card": "临床学徒", "response_time_ms": 1200})

    quiz_step = get_profile_session(session.session_id).current_step
    questions = quiz_step.payload["questions"] if quiz_step else []
    await answer_profile_session(
        session.session_id,
        APPRENTICE_STEPS[1],
        {
            "answers": [
                {
                    "question_id": questions[0]["id"],
                    "selected": questions[0]["options"][0],
                    "response_time_ms": 2600,
                    "clue_type": "药图",
                },
                {
                    "question_id": questions[1]["id"],
                    "selected": "故意选错",
                    "response_time_ms": 4200,
                },
            ]
        },
    )
    await answer_profile_session(
        session.session_id,
        APPRENTICE_STEPS[2],
        {"case": "风寒表证，恶寒无汗", "herbs": [{"name": "麻黄", "dose": 6}, {"name": "桂枝", "dose": 6}], "response_time_ms": 9000},
    )
    await answer_profile_session(session.session_id, APPRENTICE_STEPS[3], {"resource_type": "医案文选", "response_time_ms": 1500})
    await answer_profile_session(session.session_id, APPRENTICE_STEPS[4], {"daily_minutes": 45, "target_days": 14, "accept_challenge": True, "response_time_ms": 1800})
    finished = finish_profile_session(session.session_id, db_path=db_path)
    return session.mode.value, finished.evidence_cards


async def _demo_quickcard(db_path: Path) -> tuple[str, list[EvidenceCard]]:
    session = start_profile_session("demo-quickcard", ProfileMode.quickcard)
    questions = session.current_step.payload["questions"] if session.current_step else []
    await answer_profile_session(
        session.session_id,
        "quickcard",
        {
            "identity_card": "考前冲刺",
            "elapsed_seconds": 28,
            "answers": [{"question_id": questions[0]["id"], "selected": "故意选错", "response_time_ms": 28000}],
        },
    )
    finished = finish_profile_session(session.session_id, db_path=db_path)
    return session.mode.value, finished.evidence_cards


async def _demo_practice(db_path: Path) -> tuple[str, list[EvidenceCard]]:
    session = start_profile_session("demo-practice", ProfileMode.practice)
    await answer_profile_session(session.session_id, "practice", {"case": "热毒咽痛", "prescription": "金银花15g，连翘12g", "response_time_ms": 7000})
    finished = finish_profile_session(session.session_id, db_path=db_path)
    return session.mode.value, finished.evidence_cards


async def _demo_passive(db_path: Path) -> tuple[str, list[EvidenceCard]]:
    session = start_profile_session("demo-passive", ProfileMode.passive)
    await answer_profile_session(
        session.session_id,
        "passive",
        {
            "events": [
                {"event_type": "click", "resource_type": "知识图谱", "topic": "麻黄", "duration_ms": 2000},
                {"event_type": "skip", "topic": "剂量换算", "response_time_ms": 300},
                {"event_type": "replay", "resource_type": "汤头歌诀", "topic": "麻黄汤", "duration_ms": 5400},
            ]
        },
    )
    finished = finish_profile_session(session.session_id, db_path=db_path)
    return session.mode.value, finished.evidence_cards


__all__ = [
    "APPRENTICE_STEPS",
    "ProfileMode",
    "ProfileSession",
    "answer_profile_session",
    "extract_prescription_signal",
    "finish_profile_session",
    "get_profile_session",
    "main",
    "reset_profile_sessions",
    "start_profile_session",
]


if __name__ == "__main__":
    main()
