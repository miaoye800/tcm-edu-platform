"""错题归因 API（``/api/mistakes``）。

与 ``/api/kg`` 分开：``/api/kg`` 是图谱能力（邻域、对比、处方审查），
``/api/mistakes`` 是**学习者数据**（我的错题、我的薄弱章节）。两者共用
``app/kg/attribution.py`` 的判定逻辑，但契约不同——混在一个路由里，
权限和缓存策略就没法分开调。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.kg.attribution import (
    CAUSE_LABELS,
    CAUSE_TYPES,
    attribute_mistake,
    attribute_user_mistakes,
    count_mistakes,
    get_mistake,
    list_mistakes,
)
from app.profile.store import DB_PATH

router = APIRouter(prefix="/api/mistakes", tags=["mistakes"])


def get_mistake_db_path() -> Path:
    """测试用依赖覆盖点，与其他路由保持一致。"""

    return DB_PATH


@router.get("")
async def list_my_mistakes(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_mistake_db_path),
) -> dict[str, object]:
    """我的错题原始记录（不含归因）。

    ``count`` 是**本次返回的条数**，``total`` 是**该用户的错题总数**。
    两个字段都要有：侧栏角标要 total，列表要 count，拿 count 当 total 会在
    错题超过 limit 时永远显示 limit。
    """

    items = list_mistakes(current_user.user_id, limit=limit, db_path=db_path)
    return {
        "count": len(items),
        "total": count_mistakes(current_user.user_id, db_path=db_path),
        "items": items,
    }


@router.get("/attribution")
async def my_attribution(
    limit: int = Query(default=20, ge=1, le=100),
    refresh: bool = Query(default=True, description="是否重算并写回 kg_attribution"),
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_mistake_db_path),
) -> dict[str, object]:
    """错题归因 + 汇总。

    每条错题都会带 ``causes``，**至少有一条**：能归因的给具体类型和图上那条边，
    归不了的给 ``unknown`` 并写清卡在哪一步。``summary.unattributed`` 单独计数，
    不摊进任何章节——否则「归因失败」会被读成「这章掌握得还行」。
    """

    result = attribute_user_mistakes(
        current_user.user_id, limit=limit, db_path=db_path, persist=refresh
    )
    result["cause_labels"] = CAUSE_LABELS
    result["cause_types"] = list(CAUSE_TYPES)
    return result


@router.post("/{mistake_id}/attribution")
async def reattribute(
    mistake_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_mistake_db_path),
) -> dict[str, object]:
    """单条重算。错题本详情页打开时调。"""

    record = get_mistake(mistake_id, db_path)
    if record is None:
        raise HTTPException(status_code=404, detail="错题记录不存在")
    # 越权检查：错题记录带 user_id，不能靠「猜不到 id」当权限。
    if record["user_id"] != current_user.user_id:
        raise HTTPException(status_code=404, detail="错题记录不存在")

    return attribute_mistake(mistake_id, db_path=db_path, persist=True)
