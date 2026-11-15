"""经脉学习图：几何资产、穴位槽位、学科图层与造图管线。

与既有模块的关系是**软引用、零外键**：``map_section`` 只存 ``node_id`` 字符串，
不建到 ``knowledge_node`` 的外键。原因是 ``custom:xxxx``（``service.add_custom_node``
造）与 ``remedial:d01.s1.p1``（``planner.adjust_after_node`` 造）都不在
``knowledge_node`` 里，硬外键会直接拦掉这两个已上线功能。

章节结构的**唯一事实源是教材目录**（``syllabus``），不是知识树。知识树把
《中药学》总论压成了 1 章 1 节，用它造图会丢掉教材前 7 章——所以目录单独
成文件，由 ``scripts/build_syllabus.py`` 从教材条目与印刷目录生成。
"""

from __future__ import annotations

__all__ = [
    "assign",
    "authoring",
    "hints",
    "schema",
    "seed",
    "store",
    "syllabus",
    "whitelist",
]
