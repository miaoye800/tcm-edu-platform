"""经脉学习图的严格契约。

造图结果会落库并决定用户看到的地图长什么样，所以这里的模型全部
``extra="forbid"``：模型多写一个字段就该报错，而不是被默默丢掉。这是
「为什么解表药在肺经」能答上来的前提——落库的东西必须和校验过的东西是同一份。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- 输入


class SectionProposal(StrictModel):
    """一个小节 = 地图上一个穴位。

    ``key`` 形如 ``d01.s1``；``chapter_key`` 是章的 id（``d01`` / ``general``）。
    ``node_ids`` 是挂在它下面的知识点，**只做软引用**，允许出现
    ``custom:`` / ``remedial:`` 前缀。
    """

    key: str = Field(min_length=1)
    chapter_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    point_count: int = Field(default=0, ge=0)
    node_ids: list[str] = Field(default_factory=list)
    #: 该节的代表药 / 代表方。**必须过名称白名单**，未命中进 ``pending_terms``——
    #: 模型编出一味不存在的药，用户看不出来，这是教学内容出错的最短路径。
    sample_terms: list[str] = Field(default_factory=list)
    #: 教材里这一章本来就不分节，只能以章为单位落一个穴位单元。
    #: **必须显式标出来**：前端据此显示「第×章 ××」而不是「第×章第 1 节 ××」，
    #: 否则会出现「化湿药 · 第一节 化湿药」这种自己套自己的层级。
    virtual: bool = Field(default=False)

    @model_validator(mode="after")
    def _key_belongs_to_chapter(self) -> SectionProposal:
        if not self.key.startswith(self.chapter_key + "."):
            raise ValueError(f"section {self.key!r} 不属于章 {self.chapter_key!r}")
        return self


class ChapterProposal(StrictModel):
    key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    #: 教材章号（1 起）。前端要显示「第八章 解表药」，不能靠 ``c08`` 去猜。
    no: int = Field(default=0, ge=0)
    #: 书上印的章号。方剂学的各论从「第一章」重新起算，与 ``no``（内部连续号）
    #: 不同；中药学两者相同。地图显示用这个，内部键用 ``key``。
    book_no: int = Field(default=0, ge=0)
    #: ``总论`` / ``各论``。教材自身的分部，不是我们编的分类。
    part: str = ""
    sections: list[SectionProposal] = Field(default_factory=list)


class StructureProposal(StrictModel):
    """一门学科的结构提案。``source`` 决定它是怎么来的。"""

    subject: Literal["herb", "formula", "diagnosis", "acup", "basic", "internal"] = "herb"
    edition: str = "规划教材"
    source: Literal["graph", "llm", "textbook_toc"] = "graph"
    chapters: list[ChapterProposal] = Field(default_factory=list)
    #: 落库留痕：模型名、提示词哈希、输入哈希。三者缺一，复现性就无法交代。
    model: str = ""
    prompt_hash: str = ""
    input_hash: str = ""

    @property
    def section_count(self) -> int:
        return sum(len(c.sections) for c in self.chapters)

    def flat_sections(self) -> list[SectionProposal]:
        return [s for c in self.chapters for s in c.sections]


# --------------------------------------------------------------------------- 校验


class RejectedItem(StrictModel):
    """被校验层挡下来的东西。**必须回给前端**，不许静默丢。"""

    kind: Literal["chapter", "section", "term", "prereq"]
    key: str
    reason: str
    detail: str = ""


class AutoFix(StrictModel):
    kind: Literal["chapter", "section", "term"]
    key: str
    detail: str


class ValidateResult(StrictModel):
    ok: bool
    proposal: StructureProposal
    rejected: list[RejectedItem] = Field(default_factory=list)
    auto_fixed: list[AutoFix] = Field(default_factory=list)
    pending_terms: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- 分配


class AssignSlot(StrictModel):
    section_id: str
    chapter_key: str
    acupoint_id: str
    channel_id: str
    slot_ord: int = Field(ge=1, le=3)
    t: float = Field(ge=0.0, le=1.0)


class ChannelUsage(StrictModel):
    channel_id: str
    title: str
    capacity: int
    used: int
    ratio: float = Field(ge=0.0)


class BalanceReport(StrictModel):
    channels: list[ChannelUsage] = Field(default_factory=list)
    max_ratio: float = 0.0
    #: 占用率超过阈值的经。空表示分配是均衡的。
    over_threshold: list[str] = Field(default_factory=list)
    #: 挂到第 3 个槽位的穴位数量。超过阈值说明该经已被塞爆，是失衡的早期信号。
    triple_slots: int = 0
    threshold: float = 0.8


class AssignResult(StrictModel):
    layer_id: str
    replayed: bool = False
    assigned: list[AssignSlot] = Field(default_factory=list)
    #: 落不到穴位的小节。**必须回给前端**，展示为「这 N 节暂时落不到穴位，仍在列表里」。
    unmapped: list[str] = Field(default_factory=list)
    balance: BalanceReport = Field(default_factory=BalanceReport)


__all__ = [
    "AssignResult",
    "AssignSlot",
    "AutoFix",
    "BalanceReport",
    "ChapterProposal",
    "ChannelUsage",
    "RejectedItem",
    "SectionProposal",
    "StrictModel",
    "StructureProposal",
    "ValidateResult",
]
