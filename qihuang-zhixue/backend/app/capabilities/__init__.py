"""能力契约：一份声明同时派生 tool schema、前端卡描述、开发者文档。

这一层的存在理由，写在 ``spec.py`` 顶上：过去三个地方各写一份「导图能干什么」
——对话路由的分支判断、前端的提示文案、docs 里的一段散文——三份说法互相
打架，改一处忘两处。这里只留一份。

分层刻意做得硬一点：

- ``spec`` / ``registry`` / ``errors`` 是**纯数据**，不摸数据库、不摸网络，
  所以能被文档生成器、判据测试、未来的对话 Agent 同时消费；
- ``invocation`` / ``dispatch`` / ``handlers`` 才碰 IO。

这样「有哪些能力」这件事永远可以在没有运行时的环境里回答出来。
"""

from __future__ import annotations

from .errors import CapabilityError, ErrorCode
from .registry import all_specs, get, register, snapshot, tool_schemas
from .spec import ArtifactSpec, CapabilitySpec, CapabilityResult, ErrorSpec, StepSpec

__all__ = [
    "ArtifactSpec",
    "CapabilityError",
    "CapabilityResult",
    "CapabilitySpec",
    "ErrorCode",
    "ErrorSpec",
    "StepSpec",
    "all_specs",
    "get",
    "register",
    "snapshot",
    "tool_schemas",
]
