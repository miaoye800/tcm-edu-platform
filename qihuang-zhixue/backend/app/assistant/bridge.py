"""生成任务桥：把「工坊在炼」翻译成「对话在推进」。

这是整个编排层最容易做错的一处，先把三个决定写清楚：

**① 工具调用不等任务。** 生成要十几到几十秒，等在工具循环里会吃掉全部轮次预算，
且模型只能干等。所以工具只拿 202 + ``task_id`` 就返回，进度由这里旁路推进。

**② 等到 ``pause`` 就收工，不挂在连接上等人。** 工坊的闸门语义是「确认后才生成」。
把连接挂着等用户抬手，中间任何一次网络抖动都会让确认这一步丢在半路。状态落
``assistant_artifact``，重进页面就能原样恢复。

**③ 翻译而不是转发。** 资源流的事件是「工序」，对话要的事件是「工具行进度」。
字段对不上不能硬塞，否则前端要为两条流写两套解析。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.resources import service as resource_service

from . import store
from .events import RunChannel, envelope
from .tools import JOB_CAPABILITIES

#: 单份产物的墙钟上限。超时按失败处理——挂着一条永远不结束的流，
#: 比报错更糟：用户会以为还在跑。
MAX_TASK_SECONDS = 240.0


@dataclass
class TaskOutcome:
    state: str = "accepted"
    task_id: str = ""
    resource_id: str = ""
    open_url: str = ""
    kind: str = ""
    plan: dict[str, Any] = field(default_factory=dict)
    design: dict[str, Any] = field(default_factory=dict)
    error: str = ""


async def watch_task(
    *,
    task_id: str,
    user_id: str,
    channel: RunChannel,
    call_id: str,
    capability: str,
    kind: str,
    db_path: Any,
    conversation_id: str = "",
    run_id: str = "",
    chat_persisted: asyncio.Event | None = None,
) -> TaskOutcome:
    """订阅一条资源流，把工序翻成对话事件，直到暂停 / 完成 / 失败。

    ``chat_persisted`` 是「正文那句话已经落库」的闸门。桥和编排循环是并发跑的：
    模型还在生成收尾那句话时，任务可能已经跑到 ``pause``，此时直接落方案卡，刷新
    后历史里方案会排在小岐的解释**前面**。等一个闸门比事后按时间戳排序可靠——
    两条消息的时间戳可能只差几毫秒，排序一抖动就错位。
    """

    outcome = TaskOutcome(task_id=task_id, kind=kind)

    async def _persist(content: dict[str, Any]) -> None:
        if chat_persisted is not None:
            await chat_persisted.wait()
        store.append_message(conversation_id, "assistant", content, run_id=run_id, db_path=db_path)
    latest: dict[str, Any] = {}
    deadline = asyncio.get_event_loop().time() + MAX_TASK_SECONDS

    try:
        async for item in resource_service.stream_task(task_id, user_id, db_path):
            if asyncio.get_event_loop().time() > deadline:
                outcome.state = "failed"
                outcome.error = "生成超时"
                break
            event = str(item.get("event") or "")
            payload = item.get("payload") or {}

            if event in ("step_start", "step_done"):
                channel.publish(
                    envelope(
                        "tool_progress",
                        {"call_id": call_id, "capability": capability, "task_id": task_id},
                        step_index=int(item.get("step_index") or 0),
                        total_steps=int(item.get("total_steps") or 0),
                        step_label=str(item.get("step_label") or ""),
                    )
                )
                continue

            if event == "design":
                # design 事件同时带方案与设计单，是 v3 闸门唯一完整的载荷来源。
                latest = {"plan": payload.get("plan") or {}, "design": payload.get("design") or {}}
                continue

            if event == "plan":
                latest.setdefault("plan", payload.get("plan") or {})
                continue

            if event == "pause":
                outcome.state = "awaiting_plan"
                outcome.plan = latest.get("plan") or {}
                outcome.design = latest.get("design") or {}
                if conversation_id:
                    store.update_artifact_by_task(task_id, state="awaiting_plan", db_path=db_path)
                channel.publish(
                    envelope(
                        "plan_card",
                        {
                            "call_id": call_id,
                            "task_id": task_id,
                            "capability": capability,
                            "kind": kind,
                            "plan": outcome.plan,
                            "design": outcome.design,
                        },
                        step_index=int(item.get("step_index") or 0),
                        total_steps=int(item.get("total_steps") or 0),
                        step_label=str(item.get("step_label") or ""),
                    )
                )
                # 落一条消息：刷新之后方案卡要能原样回来，且还能确认。
                # 只存在内存里的话，用户一刷页面就只剩一句「等待确认」，没有可确认的东西。
                if conversation_id and run_id:
                    await _persist(
                        {
                            "type": "plan",
                            "taskId": task_id,
                            "capability": capability,
                            "plan": outcome.plan,
                            "design": outcome.design,
                        }
                    )
                return outcome

            if event == "error":
                outcome.state = "failed"
                outcome.error = str(payload.get("message") or "生成失败")
                if conversation_id:
                    store.update_artifact_by_task(task_id, state="failed", db_path=db_path)
                channel.publish(
                    envelope("tool_result", {"call_id": call_id, "ok": False, "error_code": "resource.failed", "summary": outcome.error})
                )
                channel.publish(envelope("error", {"code": "resource.failed", "message": outcome.error, "remedy": "改一下主题或稍后重试。"}))
                return outcome

            if event == "done":
                outcome.state = "done"
                outcome.resource_id = str(payload.get("resource_id") or "")
                # 任务自己给出的地址优先。有些产物的落地页**由运行时决定**：
                # 同一条路线既可能出导图也可能出图文，同一个 resource_id 对应
                # `/mindmap?resource=…` 还是 `/doc?resource=…` 只有在跑完才知道。
                # 能力声明里的模板只能表达"模板 + id"这一种关系，是兜底不是事实。
                outcome.open_url = str(payload.get("open_url") or "") or _open_url(capability, outcome.resource_id)
                if conversation_id:
                    store.update_artifact_by_task(
                        task_id,
                        state="done",
                        resource_id=outcome.resource_id,
                        open_url=outcome.open_url,
                        db_path=db_path,
                    )
                channel.publish(
                    envelope(
                        "artifact",
                        {
                            "call_id": call_id,
                            "task_id": task_id,
                            "capability": capability,
                            "kind": kind,
                            "resource_id": outcome.resource_id,
                            "open_url": outcome.open_url,
                            "title": payload.get("title") or "",
                            "verify": payload.get("verify") or {},
                        },
                    )
                )
                if conversation_id and run_id:
                    await _persist(
                        {
                            "type": "artifact",
                            "taskId": task_id,
                            "capability": capability,
                            "kind": kind,
                            "resourceId": outcome.resource_id,
                            "openUrl": outcome.open_url,
                        }
                    )
                return outcome
    except Exception as exc:  # noqa: BLE001 - 流断了不能让整轮对话炸掉
        outcome.state = "failed"
        outcome.error = f"{type(exc).__name__}: {exc}"
        channel.publish(envelope("tool_result", {"call_id": call_id, "ok": False, "error_code": "assistant.stream_lost", "summary": "进度流断了"}))
        return outcome

    if outcome.state == "accepted":
        outcome.state = "failed"
        outcome.error = "任务没有跑到终点"
    return outcome


def _open_url(capability: str, resource_id: str) -> str:
    """兜底地址：从能力声明派生，不在桥里另写一份映射。

    写死一份的后果是可预见的：新增一种产物时桥忘了改，产物卡点开是 404。

    只在任务没自报地址时才用——见 ``done`` 分支里的说明。
    """

    from app.capabilities import registry

    entry = registry.get(capability)
    if entry is None or entry.spec.artifact is None or not resource_id:
        return ""
    return entry.spec.artifact.open_url_template.format(resource_id=resource_id)


class TaskBridge:
    """一次 run 里启动的生成任务集合。

    上限两个：再多，用户要在一屏里判断三份产物的差别，那不叫效率。
    """

    def __init__(self, *, channel: RunChannel, run_id: str, conversation_id: str, user_id: str, db_path: Any) -> None:
        self.channel = channel
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.db_path = db_path
        self._tasks: list[asyncio.Task[TaskOutcome]] = []
        self.outcomes: list[TaskOutcome] = []
        #: 正文落库后置位。编排循环在 ``append_message`` 之后立刻 ``mark_chat_persisted()``；
        #: 没置位之前，桥不写任何消息（见 ``watch_task`` 的说明）。
        self._chat_persisted = asyncio.Event()

    def mark_chat_persisted(self) -> None:
        self._chat_persisted.set()

    @property
    def started(self) -> int:
        return len(self._tasks)

    def can_start(self) -> bool:
        return len(self._tasks) < 2

    def start(self, *, call_id: str, capability: str, task_id: str, kind: str) -> None:
        self._tasks.append(
            asyncio.create_task(
                watch_task(
                    task_id=task_id,
                    user_id=self.user_id,
                    channel=self.channel,
                    call_id=call_id,
                    capability=capability,
                    kind=kind,
                    db_path=self.db_path,
                    conversation_id=self.conversation_id,
                    run_id=self.run_id,
                    chat_persisted=self._chat_persisted,
                )
            )
        )

    async def wait(self) -> list[TaskOutcome]:
        if not self._tasks:
            return []
        self.outcomes = list(await asyncio.gather(*self._tasks, return_exceptions=False))
        return self.outcomes

    @property
    def awaiting_plan(self) -> bool:
        return any(item.state == "awaiting_plan" for item in self.outcomes)

    @property
    def failed(self) -> bool:
        return any(item.state == "failed" for item in self.outcomes)

    def cancel(self) -> None:
        for task in self._tasks:
            task.cancel()


def kind_for(capability: str) -> str:
    """产物类型从能力声明派生，不在这里另写一份映射。

    写死的版本已经错过一次：新增一种产物时忘了加一行，产物卡顶着 ``resource``
    的标签、点开是错的地址，而所有测试都是绿的——因为没人断言过这张表的完整性。
    """

    from app.capabilities import registry

    entry = registry.get(capability)
    if entry is None or entry.spec.artifact is None:
        return "resource"
    return entry.spec.artifact.kind


def is_job(capability: str) -> bool:
    return capability in JOB_CAPABILITIES
