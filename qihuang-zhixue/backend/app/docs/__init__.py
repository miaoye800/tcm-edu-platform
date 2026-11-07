"""文档（讲解书卷）写作 Agent。

与 ``app/ppt``、``app/mindmap`` 同构的三档执行路径：
注入式 ``llm_call``（测试）→ 真模型（``structured_create`` / ``router.chat``）→
无 key 时的确定性骨架。三档共用同一套块校验，因此「有没有模型」只影响内容丰度，
不影响结构、可编辑性与导出。

设计上刻意与旧的 ``explain`` 资源分开：``explain`` 是「按药材条目复述七个字段」，
本模块是「按用户诉求写一篇文档」——谋篇先判断体裁（讲解 / 学习计划 / 对比 /
操作指南…），再落笔。药名命中知识库时以教材原文为准，未命中时按通用知识写作，
不伪造引文。
"""

from __future__ import annotations

from .agent import DocAgent, DocResult

__all__ = ["DocAgent", "DocResult"]
