"""知识库结构化数据模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Category(StrEnum):
    """药物一级分类。

    **这 21 个值直接抄教材目录**（《中药学》第十版各论第 8–28 章），
    不是人为划分的概念。所以「麻黄算解表还是平喘」这类边界问题不存在：
    教材把它编在解表药这一章，它就是解表药。

    一味药只能落一类。教材自己把同一味药收在两章时（罕见），
    主归属仍取一章，另一章记在 ``Herb.secondary_categories``。
    教材没收录的孤儿药，``category`` 留空并标 ``unclassified``——不猜。
    """

    # ---- 第 8 章 解表药 ----
    JIE_BIAO = "解表药"
    # ---- 第 9 章 清热药 ----
    QING_RE = "清热药"
    # ---- 第 10 章 泻下药 ----
    XIE_XIA = "泻下药"
    # ---- 第 11 章 祛风湿药 ----
    QU_FENG_SHI = "祛风湿药"
    # ---- 第 12 章 化湿药 ----
    HUA_SHI = "化湿药"
    # ---- 第 13 章 利水渗湿药 ----
    LI_SHUI_SHEN_SHI = "利水渗湿药"
    # ---- 第 14 章 温里药 ----
    WEN_LI = "温里药"
    # ---- 第 15 章 理气药 ----
    LI_QI = "理气药"
    # ---- 第 16 章 消食药 ----
    XIAO_SHI = "消食药"
    # ---- 第 17 章 驱虫药 ----
    QU_CHONG = "驱虫药"
    # ---- 第 18 章 止血药 ----
    ZHI_XUE = "止血药"
    # ---- 第 19 章 活血化瘀药 ----
    HUO_XUE_HUA_YU = "活血化瘀药"
    # ---- 第 20 章 化痰止咳平喘药 ----
    HUA_TAN_ZHI_KE_PING_CHUAN = "化痰止咳平喘药"
    # ---- 第 21 章 安神药 ----
    AN_SHEN = "安神药"
    # ---- 第 22 章 平肝息风药 ----
    PING_GAN_XI_FENG = "平肝息风药"
    # ---- 第 23 章 开窍药 ----
    KAI_QIAO = "开窍药"
    # ---- 第 24 章 补虚药 ----
    BU_XU = "补虚药"
    # ---- 第 25 章 收涩药 ----
    SHOU_SE = "收涩药"
    # ---- 第 26 章 涌吐药 ----
    YONG_TU = "涌吐药"
    # ---- 第 27 章 攻毒杀虫止痒药 ----
    GONG_DU_SHA_CHONG_ZHI_YANG = "攻毒杀虫止痒药"
    # ---- 第 28 章 拔毒化腐生肌药 ----
    BA_DU_HUA_FU_SHENG_JI = "拔毒化腐生肌药"


class Nature(StrEnum):
    """四气。"""

    DA_HAN = "大寒"
    HAN = "寒"
    WEI_HAN = "微寒"
    LIANG = "凉"
    PING = "平"
    WEI_WEN = "微温"
    WEN = "温"
    RE = "热"
    DA_RE = "大热"


class Flavor(StrEnum):
    """五味及常用扩展味。"""

    SUAN = "酸"
    WEI_KU = "微苦"
    KU = "苦"
    WEI_GAN = "微甘"
    GAN = "甘"
    WEI_XIN = "微辛"
    XIN = "辛"
    WEI_XIAN = "微咸"
    XIAN = "咸"
    DAN = "淡"
    SE = "涩"


class Toxicity(StrEnum):
    """毒性分级。

    **``UNKNOWN`` 是必须存在的第四态，不能省。**

    教材没写毒性 ≠ 无毒。两者混起来很危险：会把「没查到」当成「安全」，
    然后在处方审查里放行。所以：

    - ``NONE`` / ``MINOR`` / ``TOXIC`` / ``SEVERE``：教材明写，按剂量配伍报
    - ``UNKNOWN``：教材没提 → **不报「安全」，也不阻断**，明确标「毒性未知」

    这和 `uncertain` 不能报成 `mismatched`、认不出药名不能判 `block`
    是同一条原则：**「不知道」不能伪装成「没问题」**。
    """

    NONE = "无毒"
    MINOR = "小毒"
    TOXIC = "有毒"
    SEVERE = "大毒"
    UNKNOWN = "毒性未知"


class SourceType(StrEnum):
    """资料来源类型。"""

    TEXTBOOK = "教材"
    PHARMACOPOEIA = "药典"
    FORMULA = "方剂"


class StrictModel(BaseModel):
    """禁止拼错字段悄悄进入知识库。"""

    model_config = ConfigDict(extra="forbid")


class Indication(StrictModel):
    """一条主治。

    同一个药材下会同时存在两套主治，粒度不同、来源不同，**不能混为一谈**：

    - ``textbook``：教材【应用】切出的原文条目，逐字不改。
      例如黄芪的「气虚乏力，食少便溏，水肿尿少，中气下陷，久泻脱肛，便血崩漏」。
    - ``manual``：早先人工归纳的标准证名，例如「脾气虚证」。

    两套都要留。人工那套是**中医通用证名**，学生和医生就是这么说的，删掉之后
    「风寒表实证」这类标准证会变成没有药指向的孤立节点，麻黄汤证那类分析直接断掉；
    教材那套是**原文**，患者按教材措辞描述时只有它能接住。
    ``origin`` 就是为了让下游知道每条边凭的是哪一套，不要混着用。
    """

    syndrome: str
    description: str = ""
    #: ``textbook`` = 教材原文；``manual`` = 人工归纳的通用证名。
    origin: str = "manual"


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
