"""文稿写作 Agent：先谋篇，再落笔，最后把 Markdown 还原成块。

与旧的 ``explain`` 资源（按药材条目复述七个字段）不同，这里先判断**用户想要
什么交付物**——讲解、学习计划、对比分析、操作指南——再按体裁谋篇。知识库只
在药名命中时充当事实来源，未命中就按通用知识写作，绝不写「无相关中药信息」。
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.resource_context import ResourceDoc
from app.agents.structured_output import structured_create
from app.config import Settings, get_settings
from app.llm.clients import MockClient
from app.llm.router import ModelRouter

from .markdown_blocks import markdown_to_blocks
from .prompts import PLAN_SYSTEM_PROMPT, WRITE_SYSTEM_PROMPT, WRITE_SYSTEM_PROMPT_GENERAL, task_type_for
from .schemas import DocPlan, DocSectionPlan

LLMCall = Callable[[list[dict[str, str]]], Awaitable[str]]

#: 引用池上限。再多只会把 prompt 撑满，正文里也用不到第 13 条。
MAX_SOURCES = 12
#: 送进 prompt 的单条原文上限。资源里存完整摘录，只在这里截断。
PROMPT_QUOTE_LIMIT = 300


@dataclass
class DocResult:
    title: str
    blocks: list[dict[str, Any]]
    sources: list[dict[str, str]]
    doc_kind: str
    grounded: bool
    summary: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


#: 体裁判定：顺序有意义——「学习计划」优先于「操作指南」，否则「怎么学」会被
#: 认成步骤教程。命中即用，不命中才算讲解。
_KIND_RULES: tuple[tuple[tuple[str, ...], str, tuple[str, ...]], ...] = (
    (
        ("计划", "规划", "路线", "安排", "怎么学", "学习路径", "入门", "备考", "复习", "刷题"),
        "学习计划",
        ("学之前先对表", "阶段路线", "每周做什么", "重点与难点", "怎么检验学会了", "常见误区", "下一步"),
    ),
    (
        ("对比", "区别", "鉴别", "异同", "哪个好", "怎么选"),
        "对比分析",
        ("为什么容易混", "核心差异", "逐项对比", "各自适用场景", "选择建议", "记忆抓手"),
    ),
    (
        ("怎么做", "如何", "步骤", "教程", "操作", "流程", "上手"),
        "操作指南",
        ("要解决的问题", "准备清单", "操作步骤", "关键细节", "常见错误", "验收标准"),
    ),
    (
        ("方案", "设计", "建议", "怎么安排"),
        "方案",
        ("目标与约束", "整体思路", "具体安排", "风险与备选", "如何验收"),
    ),
)

_HERB_SECTIONS = ("概览", "性味归经", "功效与主治", "配伍与方剂", "用量与禁忌", "相似药鉴别", "小结")
_GENERAL_SECTIONS = ("先说清楚它是什么", "核心内容", "关键细节", "常见误区", "小结与实践")

_TITLE_NOISE = re.compile(
    r"(请|帮我|帮忙|我想|我要|给我|麻烦|生成|制作|写|做|整理|出)(一份|一篇|一个|个)?"
    r"(关于|有关)?|的?(文档|文档页|内容|资料|文章|介绍|材料)$"
)

#: 模型用来「假装引用」的非数字占位符。它们不该出现在正文里。
_BAD_CITATION = re.compile(
    r"\[(?:herbs?|source[s]?|citation|ref|n|来源|出处|教材|引文|资料|知识库)\]",
    re.IGNORECASE,
)


def classify_kind(request: str) -> str:
    for keywords, kind, _sections in _KIND_RULES:
        if any(word in request for word in keywords):
            return kind
    return "讲解"


def sections_for(kind: str, *, grounded: bool) -> tuple[str, ...]:
    for _keywords, rule_kind, sections in _KIND_RULES:
        if rule_kind == kind:
            return sections
    return _HERB_SECTIONS if grounded else _GENERAL_SECTIONS


def clean_topic(request: str) -> str:
    """把「请帮我生成一份人参的文档」压成「人参」。只用于兜底标题。"""

    text = _TITLE_NOISE.sub("", (request or "").strip())
    text = re.sub(r"[，。！？、,.!?：:；;]+$", "", text.strip())
    text = re.sub(r"\s+", " ", text)
    return text[:24] or "未命名文稿"


def derive_title(request: str, kind: str, docs: list[ResourceDoc]) -> str:
    topic = clean_topic(request)
    if docs:
        name = docs[0]["name"]
        if kind == "学习计划":
            return f"{name}学习计划"
        if kind == "对比分析":
            return f"{name}对比与鉴别"
        if kind == "操作指南":
            return f"{name}实用指南"
        return f"{name}：从性味归经到临床应用"
    return topic if kind == "讲解" else f"{topic}·{kind}"


def build_source_pool(docs: list[ResourceDoc]) -> list[dict[str, str]]:
    """按「药材 → 原文片段」展开引用池。

    旧实现每节只取 ``doc.sources[0]``，所以整篇文档的引文都是同一句「甘、微苦…」。
    这里把每个片段都收进来，让正文用 ``[n]`` 精确指向它依据的那一条。
    """

    pool: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for doc in docs:
        for source in doc["sources"]:
            quote = str(source.get("quote", "")).strip()
            if not quote:
                continue
            ref = str(source.get("ref", ""))
            key = (ref, quote)
            if key in seen:
                continue
            seen.add(key)
            pool.append({"出处": doc["name"], "原文摘录": quote, "定位": ref})
            if len(pool) >= MAX_SOURCES:
                return pool
    return pool


class DocAgent:
    def __init__(self, *, settings: Settings | None = None, llm_call: LLMCall | None = None) -> None:
        self.settings = settings or get_settings()
        self.llm_call = llm_call
        self.router = ModelRouter(settings=self.settings)
        self.task_type = task_type_for(self.settings)

    async def generate(self, request: str, docs: list[ResourceDoc] | None = None) -> DocResult:
        request = (request or "").strip() or "中药学基础"
        docs = docs or []
        grounded = bool(docs)
        plan = await self._plan(request, docs)
        pool = build_source_pool(docs) if grounded else []

        markdown = await self._write(request, plan, docs, pool, grounded)
        blocks = markdown_to_blocks(markdown)
        if not blocks:
            blocks = markdown_to_blocks(_fallback_markdown(plan, docs, grounded))
        blocks = _sanitize_citations(blocks, len(pool))

        title = (plan.title or "").strip() or derive_title(request, plan.doc_kind, docs)
        return DocResult(
            title=title,
            blocks=blocks,
            sources=pool,
            doc_kind=plan.doc_kind,
            grounded=grounded,
            summary=plan.thesis,
            meta={
                "doc_kind": plan.doc_kind,
                "source_mode": "knowledge_base" if grounded else "general",
                "audience": plan.audience,
                "sections": [section.heading for section in plan.sections],
            },
        )

    # ── 谋篇 ────────────────────────────────────────────────────────────────
    async def _plan(self, request: str, docs: list[ResourceDoc]) -> DocPlan:
        fallback = _fallback_plan(request, docs)
        payload = {
            "request": request[:600],
            "knowledge_base": [_kb_digest(doc) for doc in docs[:3]],
        }
        messages = [
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        if self.llm_call is not None:
            plan = await self._safe_json(self.llm_call, messages, DocPlan)
        else:
            try:
                plan = await structured_create(
                    task_type=self.task_type,
                    messages=messages,
                    response_model=DocPlan,
                    mock_factory=lambda: fallback,
                    router=self.router,
                )
            except Exception:  # noqa: BLE001 - 谋篇失败退回确定性方案
                plan = fallback
        return _normalize_plan(plan, fallback, request, docs)

    # ── 落笔 ────────────────────────────────────────────────────────────────
    async def _write(
        self,
        request: str,
        plan: DocPlan,
        docs: list[ResourceDoc],
        pool: list[dict[str, str]],
        grounded: bool,
    ) -> str:
        if self.llm_call is not None:
            text = await self._safe_text(
                self.llm_call, _write_messages(request, plan, docs, pool, grounded)
            )
            return _clean_markdown(text) or _fallback_markdown(plan, docs, grounded)

        client = self.router.select(self.task_type)
        if isinstance(client, MockClient):
            return _fallback_markdown(plan, docs, grounded)

        try:
            text = await self.router.chat(
                self.task_type,
                _write_messages(request, plan, docs, pool, grounded),
                temperature=0.65,
            )
        except Exception:  # noqa: BLE001 - 落笔失败不能让整篇文档空着
            return _fallback_markdown(plan, docs, grounded)
        return _clean_markdown(text) or _fallback_markdown(plan, docs, grounded)

    # ── 容错 ────────────────────────────────────────────────────────────────
    async def _safe_json(self, llm_call: LLMCall, messages: list[dict[str, str]], model: type[DocPlan]) -> DocPlan | None:
        try:
            raw = await llm_call(messages)
            return model.model_validate_json(_extract_json(raw))
        except Exception:  # noqa: BLE001
            return None

    async def _safe_text(self, llm_call: LLMCall, messages: list[dict[str, str]]) -> str:
        try:
            return await llm_call(messages)
        except Exception:  # noqa: BLE001
            return ""


# ── 内部工具 ────────────────────────────────────────────────────────────────


def _extract_json(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    raise ValueError("谋篇输出中没有 JSON")


def _kb_digest(doc: ResourceDoc) -> dict[str, Any]:
    return {
        "name": doc["name"],
        "category": f"{doc['category']}·{doc['subcategory']}",
        "nature": doc["nature"],
        "flavor": doc["flavor"],
        "meridians": doc["meridians"],
        "effects": doc["effects"][:8],
        "indications": [item.get("syndrome", "") for item in doc["indications"][:5]],
        "dosage": str(doc["dosage"].get("text", "")),
        "cautions": doc["cautions"][:4],
        "combinations": [
            {"with": item.get("with_names", [])[:3], "purpose": item.get("purpose", "")}
            for item in doc["combinations"][:4]
        ],
        "differentiation": [
            {"against": item.get("compare_name", ""), "points": item.get("key_points", "")}
            for item in doc["differentiation"][:3]
        ],
    }


def _write_messages(
    request: str,
    plan: DocPlan,
    docs: list[ResourceDoc],
    pool: list[dict[str, str]],
    grounded: bool,
) -> list[dict[str, str]]:
    payload: dict[str, Any] = {
        "request": request[:600],
        "plan": {
            "title": plan.title,
            "doc_kind": plan.doc_kind,
            "audience": plan.audience,
            "thesis": plan.thesis,
            "sections": [
                {"heading": section.heading, "goal": section.goal, "points": section.points[:4]}
                for section in plan.sections
            ],
        },
    }
    if grounded:
        payload["knowledge_base"] = [
            {"index": index, "ref": item["定位"], "quote": item["原文摘录"][:PROMPT_QUOTE_LIMIT]}
            for index, item in enumerate(pool)
        ]
        payload["herbs"] = [_kb_digest(doc) for doc in docs[:3]]
    return [
        {"role": "system", "content": WRITE_SYSTEM_PROMPT if grounded else WRITE_SYSTEM_PROMPT_GENERAL},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _clean_markdown(text: str) -> str:
    """去掉整篇围栏与一级标题：页面标题由资源渲染，正文里再来一个就重了。"""

    body = (text or "").strip()
    fence = re.match(r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", body, re.DOTALL)
    if fence:
        body = fence.group(1).strip()
    lines = body.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^#\s+", lines[0]):
        lines.pop(0)
    return "\n".join(lines).strip()


def _normalize_plan(plan: DocPlan | None, fallback: DocPlan, request: str, docs: list[ResourceDoc]) -> DocPlan:
    plan = plan or fallback
    kind = (plan.doc_kind or fallback.doc_kind or "讲解").strip()[:12]
    sections = [section for section in plan.sections if (section.heading or "").strip()][:8]
    if len(sections) < 3:
        sections = fallback.sections
    title = (plan.title or "").strip()
    if not title or title == request.strip():
        title = derive_title(request, kind, docs)
    return DocPlan(
        title=title[:40],
        subtitle=(plan.subtitle or fallback.subtitle).strip()[:60],
        doc_kind=kind,
        audience=(plan.audience or fallback.audience).strip()[:40],
        thesis=(plan.thesis or fallback.thesis).strip()[:200],
        sections=[
            DocSectionPlan(
                heading=section.heading.strip()[:30],
                goal=(section.goal or "").strip()[:120],
                points=[str(point).strip()[:120] for point in section.points[:4] if str(point).strip()],
            )
            for section in sections
        ],
        grounded=plan.grounded,
        keywords=[str(word)[:20] for word in plan.keywords[:8]],
    )


def _sanitize_citations(blocks: list[dict[str, Any]], pool_size: int) -> list[dict[str, Any]]:
    """越界或非数字的引文标记必须去掉。

    两件事都会在页面上露馅：
      - ``[7]`` 超过来源池长度 → 点角标打开空面板；
      - 模型偶尔写 ``[herbs]``、``[来源]`` 这类占位——它们不是引文，会被原样显示。
    宁可不显示角标，也不给读者一个点不开的引用。
    """

    def clean(text: str) -> str:
        text = _BAD_CITATION.sub("", text or "")

        def repl(match: re.Match[str]) -> str:
            index = int(match.group(1))
            return match.group(0) if index < pool_size else ""

        return re.sub(r"\[(\d+)\]", repl, text)

    for block in blocks:
        block["text"] = clean(block.get("text", ""))
        if block.get("children"):
            _sanitize_citations(block["children"], pool_size)
    return blocks


def _fallback_plan(request: str, docs: list[ResourceDoc]) -> DocPlan:
    grounded = bool(docs)
    kind = classify_kind(request)
    headings = sections_for(kind, grounded=grounded)
    title = derive_title(request, kind, docs)
    thesis = f"围绕「{clean_topic(request)}」的{kind}"
    return DocPlan(
        title=title,
        subtitle=kind,
        doc_kind=kind,
        audience="中医专业学生" if grounded else "通用读者",
        thesis=thesis,
        sections=[DocSectionPlan(heading=heading, goal="", points=[]) for heading in headings],
        grounded=grounded,
        keywords=[doc["name"] for doc in docs[:3]],
    )


def _fallback_markdown(plan: DocPlan, docs: list[ResourceDoc], grounded: bool) -> str:
    """无模型时的确定性骨架。

    有教材资料：按小节标题挑对应字段写成事实段，仍是可读的简稿。
    没有资料：给结构 + 要点，并明确标注这是骨架，不假装写完了。
    """

    doc = docs[0] if docs else None
    lines: list[str] = [
        "> [!NOTE] 当前未接入可用的写作模型，以下为结构骨架：章节与要点已就位，"
        "正文尚未展开。配置模型密钥后重新炼制即可得到完整文稿。",
        "",
    ]

    for section in plan.sections:
        lines.append(f"## {section.heading}")
        lines.append("")
        paragraph = _fact_paragraph(section.heading, doc) if doc is not None else ""
        if paragraph:
            lines.append(paragraph)
            lines.append("")
        points = section.points or []
        if points:
            lines.extend(f"- {point}" for point in points)
            lines.append("")
        elif not paragraph:
            lines.append(section.goal or f"本节围绕「{section.heading}」展开，待补写正文。")
            lines.append("")

    return "\n".join(lines).strip()


def _fact_paragraph(heading: str, doc: ResourceDoc) -> str:
    """把药材条目里与标题最相关的那部分写成一段。"""

    if "性味" in heading or "归经" in heading:
        flavor = "、".join(doc["flavor"])
        meridians = "、".join(doc["meridians"])
        return f"{doc['name']}性{doc['nature']}，味{flavor}，归{meridians}经。"
    if "功效" in heading or "主治" in heading:
        effects = "、".join(doc["effects"])
        syndromes = "、".join(item.get("syndrome", "") for item in doc["indications"][:4])
        return f"{doc['name']}的功效是{effects}；常用于{syndromes}等证。"
    if "配伍" in heading or "方剂" in heading:
        parts = [
            f"与{'、'.join(item.get('with_names', [])[:3])}配伍，{item.get('purpose', '')}"
            for item in doc["combinations"][:3]
        ]
        return "；".join(parts) + "。" if parts else ""
    if "用量" in heading or "禁忌" in heading or "注意" in heading:
        dosage = str(doc["dosage"].get("text", ""))
        cautions = "、".join(doc["cautions"][:3])
        return f"用量：{dosage or '见教材'}。使用注意：{cautions or '遵医嘱'}。"
    if "鉴别" in heading or "相似" in heading:
        parts = [
            f"与{item.get('compare_name', '')}鉴别：{item.get('key_points', '')}"
            for item in doc["differentiation"][:3]
        ]
        return "；".join(parts) + "。" if parts else ""
    if "概览" in heading or "是什么" in heading or "先说清楚" in heading:
        effects = "、".join(doc["effects"][:5])
        return f"{doc['name']}属{doc['category']}·{doc['subcategory']}，主要功效为{effects}。"
    return ""
