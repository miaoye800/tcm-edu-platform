"""资源生成组的统一 Pydantic 契约。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ResourceType = Literal["explain", "graph", "mindmap", "quiz", "reading"]


class StrictResourceModel(BaseModel):
    """资源模型基类，禁止模型偷偷塞入未声明字段。"""

    model_config = ConfigDict(extra="forbid")


class TraceItem(StrictResourceModel):
    node: str = Field(description="执行节点名称，用于 LangGraph 全链路观测")
    summary: str = Field(description="节点执行摘要，说明本节点完成了什么")


class SourceRef(StrictResourceModel):
    name: str = Field(description="来源对应的药材、证型或方剂名称")
    type: str = Field(description="来源类型，例如教材、药典、方剂或本地知识库")
    ref: str = Field(description="可追溯的来源条目或文献位置")
    quote: str = Field(default="", description="支持该断言的原文片段，允许为空")


class GroundingReport(StrictResourceModel):
    score: float = Field(default=1.0, ge=0.0, le=1.0, description="有证据支持的断言占比")
    supported_claims: list[str] = Field(default_factory=list, description="已被检索证据支持的断言")
    unsupported_claims: list[str] = Field(default_factory=list, description="检索后仍缺乏支持的断言")
    note: str = Field(default="", description="证据状态提示，低于阈值时提示人工复核")


class ExplainSection(StrictResourceModel):
    title: str = Field(description="讲解分节标题")
    content: str = Field(description="本节 Markdown 正文，只能基于检索命中文档")
    sources: list[SourceRef] = Field(default_factory=list, description="本节引用来源列表")
    claims: list[str] = Field(default_factory=list, description="本节需要逐句对证据的断言")


class ExplainPayload(StrictResourceModel):
    markdown: str = Field(description="结构化讲解 Markdown，按指定七个小节组织")
    sections: list[ExplainSection] = Field(description="讲解分节结构化数据，每节末尾必须挂 sources")


class GraphNodeCategory(StrEnum):
    HERB = "herb"
    SYNDROME = "syndrome"
    FORMULA = "formula"


class GraphRelation(StrEnum):
    XIANG_XU = "相须"
    XIANG_SHI = "相使"
    XIANG_WEI = "相畏"
    XIANG_SHA = "相杀"
    SIMILAR_EFFECT = "功效相似"
    SAME_CLASS = "同类"
    SAME_SYNDROME = "主治同证"


class GraphNode(StrictResourceModel):
    id: str = Field(description="ECharts Graph 节点唯一 ID，必须来自检索白名单")
    name: str = Field(description="节点展示名称，必须来自检索命中的药材、证型或方剂")
    category: GraphNodeCategory = Field(description="节点类别：药材、证型或方剂")
    symbolSize: int = Field(default=46, ge=10, le=90, description="前端节点尺寸")
    draggable: bool = Field(default=True, description="前端是否允许拖动节点")


class GraphLink(StrictResourceModel):
    source: str = Field(description="边起点节点 ID")
    target: str = Field(description="边终点节点 ID")
    relation: GraphRelation = Field(description="边关系枚举，前端用于高亮与断开")
    evidence: str = Field(default="", description="关系来自检索证据的简短说明")


class GraphPayload(StrictResourceModel):
    nodes: list[GraphNode] = Field(description="ECharts Graph nodes 数组")
    links: list[GraphLink] = Field(description="ECharts Graph links 数组")


class MindmapPayload(StrictResourceModel):
    markdown: str = Field(description="markmap 兼容层级 Markdown")
    root: str = Field(description="思维导图根节点名称，应来自检索白名单")
    node_names: list[str] = Field(default_factory=list, description="导图中出现的实体节点名称，用于白名单校验")


class QuizType(StrEnum):
    SINGLE = "单选"
    MULTIPLE = "多选"
    BLANK = "填空"
    SHORT = "简答"
    CASE = "病例分析"


class BloomLevel(StrEnum):
    REMEMBER = "记忆"
    UNDERSTAND = "理解"
    APPLY = "应用"
    ANALYZE = "分析"
    EVALUATE = "评价"
    CREATE = "创造"


class QuizOption(StrictResourceModel):
    key: str = Field(description="选项标识，例如 A、B、C、D")
    text: str = Field(description="选项正文，禁止出现以上皆是、以上皆非")


class QuizQuestion(StrictResourceModel):
    stem: str = Field(description="题干，必须基于检索证据，禁止绝对化措辞")
    type: QuizType = Field(description="题型：单选、多选、填空、简答或病例分析")
    options: list[QuizOption] = Field(default_factory=list, description="选择题选项，非选择题可为空")
    answer: list[str] = Field(description="标准答案；选择题填选项 key，主观题填要点")
    explanation: str = Field(description="解析，说明答案为什么成立并引用知识库证据")
    source: SourceRef = Field(description="本题主要证据来源")
    bloom_level: BloomLevel = Field(description="布鲁姆认知层级")
    difficulty: int = Field(ge=1, le=5, description="题目难度，依据学生 profile 动态调整")
    distractor_rationale: dict[str, str] = Field(
        default_factory=dict,
        description="每个干扰项为何像但错，必须指向常见易混药或证",
    )
    claims: list[str] = Field(default_factory=list, description="题干、答案和解析中需要验证的断言")

    @model_validator(mode="after")
    def validate_quality_rules(self) -> "QuizQuestion":
        validate_quiz_question_quality(self)
        return self


class QuizPayload(StrictResourceModel):
    questions: list[QuizQuestion] = Field(description="题目数组，已通过出题质量黑名单校验")

    @model_validator(mode="after")
    def validate_correct_option_length(self) -> "QuizPayload":
        suspicious = 0
        checked = 0
        for question in self.questions:
            if question.type != QuizType.SINGLE or not question.options:
                continue
            option_by_key = {option.key: option.text for option in question.options}
            correct_text = " ".join(option_by_key.get(key, "") for key in question.answer)
            wrong_texts = [text for key, text in option_by_key.items() if key not in set(question.answer)]
            if wrong_texts:
                checked += 1
                if len(correct_text) > max(len(text) for text in wrong_texts) + 6:
                    suspicious += 1
        if checked > 0 and suspicious == checked:
            raise ValueError("正确项不得系统性最长")
        return self


class ReadingItem(StrictResourceModel):
    title: str = Field(description="延伸阅读标题")
    reason: str = Field(description="推荐该阅读的一句话理由，必须基于知识库覆盖范围")
    sources: list[SourceRef] = Field(description="推荐理由对应的来源")
    claims: list[str] = Field(default_factory=list, description="推荐理由中需要验证的断言")


class ReadingPayload(StrictResourceModel):
    items: list[ReadingItem] = Field(min_length=3, max_length=5, description="3 到 5 条延伸阅读推荐")


ResourcePayload = Annotated[
    ExplainPayload | GraphPayload | MindmapPayload | QuizPayload | ReadingPayload,
    Field(description="资源具体载荷，随 type 选择对应的 Pydantic 模型"),
]


class ResourceEnvelope(StrictResourceModel):
    type: ResourceType = Field(description="资源类型")
    topic: str = Field(description="资源围绕的学习主题")
    payload: ResourcePayload = Field(description="资源载荷，必须由对应 payload 模型校验")
    sources: list[SourceRef] = Field(default_factory=list, description="资源级来源汇总")
    grounding: GroundingReport = Field(default_factory=GroundingReport, description="证据对齐结果")
    trace_node: str = Field(description="生成该资源的 LangGraph 节点")
    generated_by: str = Field(description="生成客户端或模型名称，MOCK 模式也必须标明")
    cache_key: str = Field(description="幂等缓存键，格式 topic|type|profile_hash")

    @model_validator(mode="after")
    def validate_payload_matches_type(self) -> "ResourceEnvelope":
        expected: dict[ResourceType, type[BaseModel]] = {
            "explain": ExplainPayload,
            "graph": GraphPayload,
            "mindmap": MindmapPayload,
            "quiz": QuizPayload,
            "reading": ReadingPayload,
        }
        if not isinstance(self.payload, expected[self.type]):
            raise ValueError(f"type={self.type} 与 payload 类型不匹配")
        return self


BANNED_OPTION_TEXT = ("以上皆是", "以上皆非")
ABSOLUTE_WORDS = ("总是", "从不")


def validate_quiz_question_quality(question: QuizQuestion) -> None:
    """执行题目质量黑名单，供 Pydantic 和测试直接复用。"""

    text_pool = [question.stem, question.explanation, *(option.text for option in question.options)]
    if any(banned in text for text in text_pool for banned in BANNED_OPTION_TEXT):
        raise ValueError("题目禁止使用“以上皆是/以上皆非”")
    if any(word in text for text in text_pool for word in ABSOLUTE_WORDS):
        raise ValueError("题目禁止使用总是/从不等绝对词")
    if question.type in {QuizType.SINGLE, QuizType.MULTIPLE}:
        option_texts = [option.text.strip() for option in question.options]
        if len(option_texts) == 2 and set(option_texts) <= {"正确", "错误", "对", "错", "是", "否"}:
            raise ValueError("选择题不得退化为 True/False 判断")
        answer_keys = set(question.answer)
        missing_rationales = [
            option.key
            for option in question.options
            if option.key not in answer_keys and not question.distractor_rationale.get(option.key, "").strip()
        ]
        if missing_rationales:
            raise ValueError("每个干扰项都必须说明为何像但错")
        weak_rationales = [
            key
            for key, value in question.distractor_rationale.items()
            if key not in answer_keys and "易混" not in value and "常见" not in value
        ]
        if weak_rationales:
            raise ValueError("干扰项理由必须指向常见易混药或证")


def payload_to_summary(payload: ResourcePayload) -> str:
    """生成 CLI 友好的结构化摘要。"""

    if isinstance(payload, ExplainPayload):
        return f"{len(payload.sections)} 个讲解小节"
    if isinstance(payload, GraphPayload):
        return f"{len(payload.nodes)} 个节点 / {len(payload.links)} 条关系"
    if isinstance(payload, MindmapPayload):
        return f"根节点 {payload.root}"
    if isinstance(payload, QuizPayload):
        return f"{len(payload.questions)} 道题"
    if isinstance(payload, ReadingPayload):
        return f"{len(payload.items)} 条阅读"
    return "未知资源"
