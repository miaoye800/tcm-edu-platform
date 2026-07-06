"""Demo for guarded profile narrative synthesis."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.profile.narrative import synthesize_profile_narrative
from app.profile.update import get_latest_profile, update_profile_from_event

SHANGHAI_TZ = timezone(timedelta(hours=8))


async def _main() -> None:
    user_id = "narrative-demo"
    now = datetime.now(SHANGHAI_TZ)
    db_path = Path(tempfile.gettempdir()) / f"qihuang_narrative_demo_{uuid4().hex}.db"
    no_key_settings = Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None, MOCK_STREAM_DELAY=0.0)
    await update_profile_from_event(
        user_id,
        {
            "type": "quiz",
            "payload": {
                "answers": [
                    {"correct": True, "difficulty": "hard", "topic": "解表药·功效"},
                    {"correct": False, "difficulty": "hard", "topic": "清热药·剂量"},
                ]
            },
        },
        db_path=db_path,
        jury_weight_threshold=99,
        settings=no_key_settings,
        now=now,
    )
    await update_profile_from_event(
        user_id,
        {
            "type": "prescription",
            "payload": {
                "symptom_match": True,
                "compatible": True,
                "dosage_ok": False,
                "toxic_misuse": False,
                "weak_labels": ["清热药·剂量"],
            },
        },
        db_path=db_path,
        jury_weight_threshold=99,
        settings=no_key_settings,
        now=now,
    )
    await update_profile_from_event(
        user_id,
        {
            "type": "interaction",
            "payload": {
                "learning_goal": "应试提分",
                "learning_style": "视觉型",
                "resource_preference": "知识图谱",
                "daily_minutes": 45,
                "target_days": 14,
                "motivation": "内驱",
                "accept_challenge": True,
            },
        },
        db_path=db_path,
        jury_weight_threshold=99,
        settings=no_key_settings,
        now=now,
    )

    profile = await get_latest_profile(user_id, db_path=db_path, settings=no_key_settings, now=now)
    narrative = await synthesize_profile_narrative(profile, settings=no_key_settings)
    print(json.dumps(narrative.model_dump(mode="json"), ensure_ascii=False, indent=2))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(_main())


if __name__ == "__main__":
    main()
