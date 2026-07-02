"""画像雷达图数据转换。"""

from __future__ import annotations

from app.profile.models import DIMENSION_KEYS, DIMENSION_LABELS, StudentProfile


def to_radar(profile: StudentProfile) -> dict[str, list[int] | list[str]]:
    """转换为 ECharts Radar 可直接消费的数据。"""

    return {
        "dimensions": [DIMENSION_LABELS[key] for key in DIMENSION_KEYS],
        "values": [int(getattr(profile, key).score) for key in DIMENSION_KEYS],
    }
