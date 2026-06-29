"""知识库结构化数据模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Category(StrEnum):
    """药物一级分类。"""

    JIE_BIAO = "解表药"
    QING_RE = "清热药"
    BU_XU = "补虚药"


class Nature(StrEnum):
    """四气。"""

    HAN = "寒"
    WEI_HAN = "微寒"
    LIANG = "凉"
    PING = "平"
    WEI_WEN = "微温"
    WEN = "温"
    RE = "热"


class Flavor(StrEnum):
    """五味及常用扩展味。"""

    SUAN = "酸"
    KU = "苦"
    GAN = "甘"
    XIN = "辛"
    XIAN = "咸"
    DAN = "淡"
    SE = "涩"


class Toxicity(StrEnum):
    """毒性分级。"""

    NONE = "无毒"
    MINOR = "小毒"
    TOXIC = "有毒"
    SEVERE = "大毒"


class SourceType(StrEnum):
    """资料来源类型。"""

    TEXTBOOK = "教材"
    PHARMACOPOEIA = "药典"
    FORMULA = "方剂"


class StrictModel(BaseModel):
    """禁止拼错字段悄悄进入知识库。"""

    model_config = ConfigDict(extra="forbid")


class Indication(StrictModel):
    syndrome: str
    description: str = ""


class Dosage(StrictModel):
    text: str
    min_g: float | None = None
    max_g: float | None = None
    usage_notes: str = ""

    @model_validator(mode="after")
    def validate_range(self) -> "Dosage":
        if self.min_g is not None and self.max_g is not None and self.min_g > self.max_g:
            raise ValueError("min_g 不能大于 max_g")
        return self


class Incompatibility(StrictModel):
    shibafan: list[str] = Field(default_factory=list)
    shijiuwei: list[str] = Field(default_factory=list)


class Combination(StrictModel):
    with_: list[str] = Field(default_factory=list, alias="with")
    purpose: str
    formula_ref: str = ""


class Differentiation(StrictModel):
    compare_with: str
    key_points: str


class Tags(StrictModel):
    learning_difficulty: int = Field(ge=1, le=5)
    high_frequency_exam: bool = False
    common_confusions: list[str] = Field(default_factory=list)


class Media(StrictModel):
    portrait_url: str = ""
    specimen_url: str = ""


class Source(StrictModel):
    type: SourceType
    ref: str
    quote: str = ""


class Herb(StrictModel):
    id: str
    name: str
    pinyin: str
    aliases: list[str] = Field(default_factory=list)
    category: Category
    subcategory: str
    nature: Nature
    flavor: list[Flavor] = Field(min_length=1)
    toxicity: Toxicity = Toxicity.NONE
    meridians: list[str] = Field(min_length=1)
    effects: list[str] = Field(min_length=1)
    indications: list[Indication] = Field(min_length=1)
    dosage: Dosage
    cautions: list[str] = Field(default_factory=list)
    incompatibility: Incompatibility | None = None
    combinations: list[Combination] = Field(default_factory=list)
    differentiation: list[Differentiation] = Field(default_factory=list)
    tags: Tags | None = None
    media: Media | None = None
    sources: list[Source] = Field(min_length=1)
    verified: bool = False
    confidence: Literal["high", "low"] = "high"
    embedding_text: str = ""


class Formula(StrictModel):
    """方剂模型，供后续方剂知识库复用。"""

    id: str
    name: str
    pinyin: str = ""
    category: str
    composition: list[str] = Field(default_factory=list)
    effects: list[str] = Field(default_factory=list)
    indications: list[Indication] = Field(default_factory=list)
    sources: list[Source] = Field(min_length=1)
    embedding_text: str = ""


class Syndrome(StrictModel):
    """证候模型，供辨证与推荐链路复用。"""

    id: str
    name: str
    description: str = ""
    symptoms: list[str] = Field(default_factory=list)
    tongue: str = ""
    pulse: str = ""
    related_herbs: list[str] = Field(default_factory=list)
    related_formulas: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(min_length=1)
    embedding_text: str = ""
