"""总控对话 Agent：把一句话派给能力层，把产物卡片放回消息流。

模块边界：

- ``contract`` / ``events`` 是纯数据与推送原语，不碰数据库；
- ``store`` 只碰 sqlite；
- ``tools`` / ``router`` 决定「调什么」；
- ``loop`` / ``bridge`` 决定「怎么跑」；
- ``api`` 只做 HTTP。

这条分界与 ``capabilities/`` 一致：纯数据层可以在没有数据库、没有网络的环境里
被文档生成器与判据测试直接使用。
"""

from __future__ import annotations

from .contract import ChatTurnRequest, ToolStep

__all__ = ["ChatTurnRequest", "ToolStep"]
