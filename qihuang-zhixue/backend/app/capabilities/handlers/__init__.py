"""能力 handler 的公共输入模型。

每个能力都有自己的输入模型，但它们都必须带两样东西：

- ``client_invocation_id``：幂等键。调用方重试时原样带回，服务端据此认出
  「这是同一个请求」，而不是「用户又点了一次」。
- ``context``：会话号、轮次之类的旁路信息。存进受理记录，排查时能串回去。

这两样不写进各自的模型里，是因为它们与「这个能力要什么」无关——
它们是**调用协议**的一部分。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CapabilityInput(BaseModel):
    """所有能力输入模型的基类。

    ``extra="ignore"`` 是刻意的：前端会顺手把结构、配色、画像这些旁路字段
    一起带上来，严格模式会把它们当错误拒掉，收益远小于成本。
    """

    model_config = ConfigDict(extra="ignore")

    client_invocation_id: str = Field("", max_length=120, description="幂等键；重试时原样带回，同样的键只受理一次")
    context: dict[str, Any] = Field(default_factory=dict, description="调用上下文（会话号、轮次等），只用于排查")

    #: 前端展示用的那句提示，由 Registry 读 spec 时补，不在请求里传。
    @classmethod
    def field_names(cls) -> set[str]:
        return set(cls.model_fields)
