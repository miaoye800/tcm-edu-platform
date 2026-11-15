"""会话、轮次与工具轨迹的契约。纯 pydantic / Literal，没有任何 IO。

三种状态刻意分开，因为它们由不同的东西驱动：

- ``conversation`` 的生命周期由用户决定（改名、归档）；
- ``run`` 的生命周期由一次提问决定，**可以停在 ``awaiting_plan``**——工坊的确认
  闸门不是错误，也不是完成，是一种「等人」的状态；
- ``artifact`` 的生命周期跟着生成任务走，与 run 不重合：一个 run 可以产出两张
  卡，一张已 done、一张还在等确认。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RunStatus = Literal["running", "awaiting_plan", "awaiting_task", "done", "failed", "cancelled"]
ArtifactState = Literal["accepted", "awaiting_plan", "done", "failed"]
MessageRole = Literal["user", "assistant", "tool"]

#: 一次提问最多引用几份既有资源。上限来自 prompt 预算，不是数据库能力。
MAX_REFS = 8
#: 单条输入上限。超过这个长度，用户实际是在粘贴材料，应当走 doc.generate 的 materials。
MAX_TEXT = 4000


class ChatTurnRequest(BaseModel):
    """一次提问。

    ``client_turn_id`` 是**轮次幂等键**，与能力层的 ``client_invocation_id`` 同一套
    约定：网络重试原样带回，服务端据此认出「这是同一次提问」，而不是「用户又问了一遍」。
    """

    model_config = ConfigDict(extra="ignore")

    conversation_id: str = Field("", max_length=64, description="留空则新建会话")
    text: str = Field(..., min_length=1, max_length=MAX_TEXT, description="用户这句话")
    client_turn_id: str = Field("", max_length=120, description="轮次幂等键；重试时原样带回")
    refs: list[str] = Field(default_factory=list, max_length=MAX_REFS, description="引用的既有资源 id")
    #: 他此刻站在哪一关（从地图点「问小岐」带过来）。**只进 system，不进 user text**：
    #: 落库存的是原始 text，system 里的提示天然不会被下一轮当历史再喂一次。
    node_id: str = Field("", max_length=120, description="当前所在知识点 id，可为空")


class CreateConversationRequest(BaseModel):
    title: str = Field("", max_length=60)


class RenameConversationRequest(BaseModel):
    title: str = Field("", max_length=60)
    archived: bool = False


class ToolStep(BaseModel):
    """一次工具调用的轨迹。**前端工具行直接渲染它**，所以字段要够用。"""

    model_config = ConfigDict(extra="ignore")

    call_id: str
    tool: str
    capability: str
    title: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    state: Literal["running", "ok", "failed"] = "running"
    summary: str = ""
    error_code: str = ""
    remedy: str = ""
    task_id: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


class AssistantMessage(BaseModel):
    """消息体的结构化形状。落库是 JSON，前端按 ``type`` 分支渲染。

    不把六种消息压成「一段文字 + 附件」：工具行要能展开参数、方案卡要能就地改、
    产物卡要能打开。压平之后这些都得靠正则从文本里抠，是必错的路。
    """

    model_config = ConfigDict(extra="ignore")

    type: Literal["text", "tool", "plan", "artifact", "error"]
    text: str = ""
    steps: list[ToolStep] = Field(default_factory=list)
    plan: dict[str, Any] = Field(default_factory=dict)
    design: dict[str, Any] = Field(default_factory=dict)
    task_id: str = ""
    capability: str = ""
    artifact: dict[str, Any] = Field(default_factory=dict)
    sources: list[dict[str, str]] = Field(default_factory=list)
    ui_actions: list[dict[str, str]] = Field(default_factory=list)
