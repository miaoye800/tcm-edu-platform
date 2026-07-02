"""画像模块命令行演示。"""

from __future__ import annotations

import asyncio
import json

from app.profile.agent import cold_start_profile, export_mermaid
from app.profile.models import DIMENSION_KEYS, DIMENSION_LABELS
from app.profile.radar import to_radar


async def _main() -> None:
    profile = await cold_start_profile(
        user_id="demo-user",
        first_message="我是中医学专业学生，基础一般，目标是期末考试提分，喜欢图表和练习题。",
        diagnostic_answers={
            "q1": "发汗解表",
            "q2": "温通经脉",
            "q3": "行气和胃",
            "q4": "夏月解表",
        },
    )
    export_mermaid()
    print("示例画像 7 维分数：")
    for key in DIMENSION_KEYS:
        dimension = getattr(profile, key)
        print(f"- {DIMENSION_LABELS[key]}: {dimension.score} ({dimension.evidence})")
    print("to_radar 输出：")
    print(json.dumps(to_radar(profile), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
