"""SSE 事件、进程内的推送通道。

事件形状与 ``resources/service.py`` 的 ``envelope()`` 完全一致：

```
{"event": "tool_start", "step_index": 0, "total_steps": 0, "step_label": "", "payload": {...}}
```

**为什么照抄而不另起一套**：前端已经有一个 SSE 解析器（``services/resourceApi.ts``），
两种形状意味着两套解析与两套测试。多出来的字段（``step_index`` 等）在对话场景里
只用于工序进度，空着不碍事。

**为什么通道是有界缓冲而不是纯队列**：断线重连要能重放「我错过的那几秒」。纯队列
在被消费后就没了；缓冲保留最近 N 条，重连时先补再续。这与 ``resources/service.py``
的 ``_EVENT_CACHE`` 是同一个取舍，只是那边是全局 dict，这边按 run 隔离。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

#: 缓冲上限。一次生成的事件在百条量级；400 条足够覆盖最长的任务。
BUFFER_LIMIT = 400
#: 心跳间隔。代理常在 30~60 秒无数据时掐连接，15 秒留足余量。
HEARTBEAT_SECONDS = 15.0
#: 终态事件。收到即表示这条连接可以收尾。
TERMINAL = {"done", "error"}


def envelope(
    event: str,
    payload: dict[str, Any] | None = None,
    *,
    step_index: int = 0,
    total_steps: int = 0,
    step_label: str = "",
) -> dict[str, Any]:
    return {
        "event": event,
        "step_index": step_index,
        "total_steps": total_steps,
        "step_label": step_label,
        "payload": payload or {},
    }


def sse(item: dict[str, Any]) -> str:
    return f"event: {item['event']}\ndata: {json.dumps(item, ensure_ascii=False, separators=(',', ':'))}\n\n"


class RunChannel:
    """一次 run 的推送通道。

    写入方有两个：编排循环（token / tool_*）与任务桥（tool_progress / plan_card /
    artifact）。两者都在同一个事件循环里，所以不需要锁；跨线程写是使用错误，
    这里用 ``asyncio.Queue`` 的非线程安全版本正是为了让这种错误早点暴露。
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._buffer: list[dict[str, Any]] = []
        self.closed = False

    def publish(self, event: dict[str, Any]) -> None:
        if self.closed:
            return
        self._buffer.append(event)
        if len(self._buffer) > BUFFER_LIMIT:
            del self._buffer[: len(self._buffer) - BUFFER_LIMIT]
        self._queue.put_nowait(event)

    async def stream(self, *, replay: bool = False) -> AsyncIterator[dict[str, Any]]:
        """产出事件直到终态或通道关闭。

        ``replay=True`` 时先补缓冲。注意补的是**截至此刻**的快照：补完再消费队列，
        两者之间不会漏，因为 ``publish`` 是同步先入缓冲再入队。
        """

        if replay:
            for item in list(self._buffer):
                yield item
                if item["event"] in TERMINAL:
                    return
        while True:
            if self.closed and self._queue.empty():
                return
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                if self.closed:
                    return
                yield envelope("ping", {"ts": time.time()})
                continue
            if item is None:  # close() 放的哨兵：立刻收尾，不再等心跳超时
                return
            yield item
            if item["event"] in TERMINAL:
                return

    def close(self) -> None:
        """关闭并唤醒读者。

        只置 ``closed`` 是不够的：读者正阻塞在 ``queue.get()`` 上，要等一个心跳
        周期（15 秒）才发现流已经结束——HTTP 连接会白白挂 15 秒，前端一直转圈。
        """

        if self.closed:
            return
        self.closed = True
        self._queue.put_nowait(None)


_CHANNELS: dict[str, RunChannel] = {}


def open_channel(run_id: str) -> RunChannel:
    channel = RunChannel(run_id)
    _CHANNELS[run_id] = channel
    return channel


def get_channel(run_id: str) -> RunChannel | None:
    return _CHANNELS.get(run_id)


def drop_channel(run_id: str) -> None:
    """任务结束后保留缓冲一小会儿是没意义的：内存里存着上一次会话的全文。

    但也不能立刻删——用户可能正在断线重连。这里只从活跃表移除，缓冲随通道对象
    一起被 GC；重连的兜底走 ``GET /api/assistant/runs/{id}`` 快照。
    """

    channel = _CHANNELS.get(run_id)
    if channel is not None:
        channel.close()


def forget_all() -> None:
    """只给测试用。"""

    for channel in _CHANNELS.values():
        channel.close()
    _CHANNELS.clear()
