"""导图任务的生成参数。

只做「守住可枚举的那几个键」，多余的键一律忽略：``params`` 里还带着
``profile``（画像）和 ``replay_of`` 之类的旁路信息，``extra="forbid"``
会把它们当成错误拒掉。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MIN_DEPTH = 2
MAX_DEPTH = 4
MIN_BRANCHES = 3
MAX_BRANCHES = 8
MAX_TOPIC_INPUT = 60
MAX_FOCUS = 60


class MindmapParams(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topic: str = Field(default="", max_length=MAX_TOPIC_INPUT)
    depth: int = Field(default=3, ge=MIN_DEPTH, le=MAX_DEPTH)
    branches: int = Field(default=6, ge=MIN_BRANCHES, le=MAX_BRANCHES)
    style: Literal["exam", "understand", "compare"] = "understand"
    focus: str = Field(default="", max_length=MAX_FOCUS)


def validate_mindmap_params(params: dict[str, object]) -> MindmapParams:
    """校验失败抛 ``ValueError``，由路由转成 422 并带上具体字段。"""

    try:
        return MindmapParams.model_validate(params)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "params"
        raise ValueError(f"导图参数 {field} 不合法：{first['msg']}") from exc
