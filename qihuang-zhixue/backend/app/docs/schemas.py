"""文稿谋篇与落笔的数据契约。

与 ``app/agents/resource_schemas.py`` 的区别：那边是 ``extra="forbid"`` 的严格
证据模型（少一个字段就判失败），这边是**创作**模型——模型多给一个键不该让整篇
文档退回骨架，所以 ``extra="ignore"``，只在长度与条目数上做约束。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DocSectionPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    heading: str = Field(default="", max_length=60)
    goal: str = Field(default="", max_length=200)
    points: list[str] = Field(default_factory=list)


class DocPlan(BaseModel):
    """谋篇结果：写什么体裁、给谁看、分几节。"""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(default="", max_length=60)
    subtitle: str = Field(default="", max_length=80)
    doc_kind: str = Field(default="讲解", max_length=20)
    audience: str = Field(default="", max_length=60)
    thesis: str = Field(default="", max_length=300)
    sections: list[DocSectionPlan] = Field(default_factory=list)
    grounded: bool = False
    keywords: list[str] = Field(default_factory=list)


class DocDraft(BaseModel):
    """落笔结果。``markdown`` 是正文，标题以谋篇为准（模型常自己改标题）。"""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(default="", max_length=60)
    markdown: str = ""
