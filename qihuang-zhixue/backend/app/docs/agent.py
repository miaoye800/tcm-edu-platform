"""文稿写作 Agent：先读整句话（施工图），再谋篇，再落笔，最后把 Markdown 还原成块。

与旧的 ``explain`` 资源（按药材条目复述七个字段）不同，这里先判断**用户想要
什么交付物**——讲解、对比、指南、计划、综述、案例、答疑、纪要、速查表——再按
施工图谋篇。知识库既是事实来源，也是结构来源：主题落在教材某门课里时，章节名
照用（``docs/evidence.py``），只有库外内容才由模型补充。

排版在这里被当成内容的一部分：每节按 ``form`` 出「一句结论 + 结构化主体」，
写完再量一次结构（``spec.measure_doc``），整节都是大段落时定向补一次排版。
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.resource_context import ResourceDoc
from app.config import Settings, get_settings
from app.llm.clients import MockClient
from app.llm.router import ModelRouter

from .evidence import DocEvidence, build_doc_evidence
from .markdown_blocks import markdown_to_blocks
from .prompts import EXPAND_SYSTEM_PROMPT, RESHAPE_SYSTEM_PROMPT, WRITE_SYSTEM_PROMPT, WRITE_SYSTEM_PROMPT_GENERAL, task_type_for
from .spec import (
    GENRE_LABEL,
    DocSectionSpec,
    DocSpec,
    DocSpecDraft,
    _clean_heading,
    align_spec_with_units,
    compose_doc_spec,
    doc_spec_of,
    genre_of,
    measure_doc,
    words_from_text,
)

LLMCall = Callable[[list[dict[str, str]]], Awaitable[str]]

#: 引用池上限。与 ``evidence.MAX_SOURCES`` 同源，旧调用点从这儿取。
MAX_SOURCES = 12
#: 送进提示词的单条原文上限。资源里存完整摘录，只在这里截断。
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
#:
#: **它只用于兜底与旧接口兼容**：真正的体裁由 ``spec.compose_doc_spec`` 读整句
#: 决定。不要再往这张表里加「XX 关键词就给 XX 章节」——那正是「输入什么都长成
#: 同一份规划」的病根。
_KIND_RULES: tuple[tuple[tuple[str, ...], str, tuple[str, ...]], ...] = (
    (
        ("计划", "规划", "路线", "安排", "怎么学", "学习路径", "入门", "备考", "复习", "刷题"),
        "学习计划",
        ("目标与现状", "前置准备", "阶段路线", "练习与自测", "调整与复盘"),
    ),
    (
        ("对比", "区别", "鉴别", "异同", "哪个好", "怎么选"),
        "对比分析",
        ("为什么容易混", "核心差异", "逐项对比", "各自适用场景", "选择建议"),
    ),
    (
        ("怎么做", "如何", "步骤", "教程", "操作", "流程", "上手"),
        "操作指南",
        ("要解决的问题", "准备清单", "操作步骤", "关键细节", "验收标准"),
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

#: 旧接口保留：体裁初判。**不要用它决定章节**，它只认关键词。
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


def build_source_pool(docs: list[ResourceDoc], evidence: DocEvidence | None = None) -> list[dict[str, str]]:
    """按「教材目录 / 药材 → 原文片段」展开引用池。

    旧实现每节只取 ``doc.sources[0]``，所以整篇文档的引文都是同一句「甘、微苦…」。
    这里把目录条目与每个片段都收进来，让正文用 ``[n]`` 精确指向它依据的那一条。
    """

    if evidence is not None:
        return list(evidence.quotes)
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

    # ── 入口 ────────────────────────────────────────────────────────────────
    async def generate(
        self,
        request: str,
        docs: list[ResourceDoc] | None = None,
        plan: dict[str, Any] | None = None,
        *,
        materials: list[str] | None = None,
        design: dict[str, Any] | None = None,
        planned: dict[str, Any] | None = None,
    ) -> DocResult:
        request = (request or "").strip() or "中药学基础"
        docs = docs or []
        materials = [str(item) for item in (materials or []) if str(item).strip()][:5]
        evidence = build_doc_evidence(request, docs)

        spec = _spec_from_plan(plan, request, docs, evidence)
        if spec is None:
            spec = await self.propose_spec(request, docs, evidence, materials=materials, params=planned or {})

        # 篇幅：方案翻译出来的最算数（方案是唯一事实源），其次设计单，最后施工图自己。
        target = (
            _as_int((planned or {}).get("target_words"))
            or _target_words(design)
            or words_from_text((plan or {}).get("presentation") if isinstance(plan, dict) else "")
            or spec.target_words
        )
        spec = spec.model_copy(update={"target_words": min(max(int(target), 400), 12000)})

        markdown = await self._write(request, spec, evidence, materials, docs)
        # 先补篇幅再补排版：扩写可能带进大段落，排版补在后面收口。
        markdown = await self._expand_once(markdown, spec, request)
        # 定向补排版：只重写「整节都是大段落」的那几节，事实不动。
        markdown = await self._reshape_once(markdown, spec, request)
        blocks = markdown_to_blocks(markdown)
        if not blocks:
            blocks = markdown_to_blocks(_fallback_markdown(spec, docs))
        blocks, removed = _sanitize_citations(blocks, len(evidence.quotes))

        return DocResult(
            title=(spec.title or "").strip() or derive_title(request, spec.genre_label, docs),
            blocks=blocks,
            sources=list(evidence.quotes),
            doc_kind=spec.genre_label,
            grounded=evidence.grounded,
            summary=spec.thesis,
            meta={
                "doc_kind": spec.genre_label,
                "source_mode": "knowledge_base" if evidence.grounded else "general",
                "audience": spec.audience,
                "sections": spec.headings,
                "has_user_material": bool(materials),
                # 清洗前先记账：清洗把越界标记删掉之后，verify 再去统计就永远是 0。
                "bad_citations": len(removed),
                "removed_citations": removed[:10],
                # 施工图透传下去：核对步要按它算「方案要 5 节，实际落了几节」。
                "doc_spec": spec.model_dump(mode="json"),
                "coverage": evidence.digest(),
            },
        )

    # ── 谋篇 ────────────────────────────────────────────────────────────────
    async def propose_spec(
        self,
        request: str,
        docs: list[ResourceDoc] | None = None,
        evidence: DocEvidence | None = None,
        *,
        materials: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> DocSpec:
        """读整句话，出施工图。模型优先，无模型退回标了 rules 的基线。"""

        evidence = evidence or build_doc_evidence(request, docs or [])
        return await compose_doc_spec(
            request,
            coverage=evidence.coverage,
            docs=docs or [],
            params=params or {},
            materials=materials,
            llm_call=self.llm_call,
            router=self.router,
            task_type=self.task_type,
        )

    # ── 落笔 ────────────────────────────────────────────────────────────────
    async def _write(
        self,
        request: str,
        spec: DocSpec,
        evidence: DocEvidence,
        materials: list[str] | None = None,
        docs: list[ResourceDoc] | None = None,
    ) -> str:
        messages = _write_messages(request, spec, evidence, materials)
        if self.llm_call is not None:
            text = await self._safe_text(self.llm_call, messages)
            return _clean_markdown(text) or _fallback_markdown(spec, list(docs or []))

        client = self.router.select(self.task_type)
        if isinstance(client, MockClient):
            return _fallback_markdown(spec, list(docs or []))
        try:
            text = await self.router.chat(self.task_type, messages, temperature=0.6)
        except Exception:  # noqa: BLE001 - 落笔失败不能让整篇文档空着
            return _fallback_markdown(spec, list(docs or []))
        return _clean_markdown(text) or _fallback_markdown(spec, list(docs or []))

    # ── 补排版 ──────────────────────────────────────────────────────────────
    async def _reshape_once(self, markdown: str, spec: DocSpec, request: str) -> str:
        """整节都是大段落的，定向改成「结论 + 结构化主体」。

        只做一次，且**只接受真的变结构化了的版本**：模型如果只是把话换个说法，
        换回去等于白干，还会丢掉原来的事实。注入式 ``llm_call`` 的调用方自己
        管整段文本，这里不插手（否则调用次数会变）。
        """

        if self.llm_call is not None or not markdown.strip():
            return markdown
        preamble, sections = _split_sections(markdown)
        if len(sections) < 2:
            return markdown
        forms = {section.clean_heading: section.form for section in spec.sections}
        targets = [
            (heading, body)
            for heading, body in sections
            if _needs_reshape(body, forms.get(_clean_heading(heading), "prose"))
        ]
        if not targets:
            return markdown
        client = self.router.select(self.task_type)
        if isinstance(client, MockClient):
            return markdown
        payload = {
            "request": request[:400],
            "sections": [
                {
                    "heading": heading,
                    "form": forms.get(_clean_heading(heading), "prose"),
                    "current": body[:2000],
                }
                for heading, body in targets
            ],
        }
        try:
            raw = await self.router.chat(
                self.task_type,
                [
                    {"role": "system", "content": RESHAPE_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.35,
            )
        except Exception:  # noqa: BLE001 - 补排版是加分项，失败就交原稿
            return markdown
        revised = _split_sections(_clean_markdown(raw))[1]
        if not revised:
            return markdown
        mapping = {_clean_heading(heading): body for heading, body in revised}
        # 要求每一节都真的被改到了，否则不动——半成品拼进去只会更乱。
        if any(_clean_heading(heading) not in mapping for heading, _body in targets):
            return markdown
        rebuilt: list[tuple[str, str]] = []
        for heading, body in sections:
            key = _clean_heading(heading)
            if any(key == _clean_heading(target) for target, _body in targets):
                body = mapping.get(key, body)
            rebuilt.append((heading, body))
        return _join_sections(preamble, rebuilt)

    # ── 补篇幅 ──────────────────────────────────────────────────────────────
    async def _expand_once(self, markdown: str, spec: DocSpec, request: str) -> str:
        """字数距目标差太多时，把最薄的几节定向扩写。

        判据用核对步同一把尺（``measure_doc``）：不到目标的 70% 且缺口 ≥ 400 字
        才动手，写够了绝不打扰模型。只做一次，且**只有真的变长了才采用**——
        模型把话换个说法不叫扩写，换回去等于白干，还会丢排版。注入式
        ``llm_call`` 的调用方自己管整段文本，这里不插手（否则调用次数会变）。
        """

        if self.llm_call is not None or not markdown.strip():
            return markdown
        total = int(measure_doc(markdown_to_blocks(markdown), None).get("words") or 0)
        shortfall = spec.target_words - total
        if shortfall < 400 or total > spec.target_words * 0.7:
            return markdown
        preamble, sections = _split_sections(markdown)
        if len(sections) < 2:
            return markdown
        # 每节的「份」= 目标字数 / 节数；低于七成的节才送扩写，够厚的原样保留。
        share = max(int(spec.target_words / max(len(sections), 1)), 200)
        forms = {section.clean_heading: section for section in spec.sections}
        targets: list[tuple[str, str]] = []
        for heading, body in sections:
            length = len(re.sub(r"\s+", "", body))
            if length < share * 0.7:
                targets.append((heading, body))
        if not targets:
            return markdown
        client = self.router.select(self.task_type)
        if isinstance(client, MockClient):
            return markdown
        payload = {
            "request": request[:400],
            "target_words": spec.target_words,
            "sections": [
                {
                    "heading": heading,
                    "form": forms.get(_clean_heading(heading)).form
                    if _clean_heading(heading) in forms
                    else "prose",
                    "goal_words": max(share, 400),
                    "must_cover": forms[_clean_heading(heading)].must_cover[:4]
                    if _clean_heading(heading) in forms
                    else [],
                    "current": body[:3000],
                }
                for heading, body in targets
            ],
        }
        try:
            raw = await self.router.chat(
                self.task_type,
                [
                    {"role": "system", "content": EXPAND_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.4,
            )
        except Exception:  # noqa: BLE001 - 补篇幅是加分项，失败就交原稿
            return markdown
        revised = _split_sections(_clean_markdown(raw))[1]
        if not revised:
            return markdown
        mapping = {_clean_heading(heading): body for heading, body in revised}
        # 每一节都得交回来，且都不能比原来短——缩写回来的不叫扩写。
        if any(
            _clean_heading(heading) not in mapping
            or len(re.sub(r"\s+", "", mapping[_clean_heading(heading)])) < len(re.sub(r"\s+", "", body))
            for heading, body in targets
        ):
            return markdown
        rebuilt: list[tuple[str, str]] = []
        for heading, body in sections:
            key = _clean_heading(heading)
            if any(key == _clean_heading(target) for target, _body in targets):
                body = mapping.get(key, body)
            rebuilt.append((heading, body))
        candidate = _join_sections(preamble, rebuilt)
        # 总账也要真的长：至少补上缺口的三分之一（且 ≥200 字），否则不值得换稿。
        grown = int(measure_doc(markdown_to_blocks(candidate), None).get("words") or 0) - total
        if grown < max(200, shortfall // 3):
            return markdown
        return candidate

    # ── 容错 ────────────────────────────────────────────────────────────────
    async def _safe_text(self, llm_call: LLMCall, messages: list[dict[str, str]]) -> str:
        try:
            return await llm_call(messages)
        except Exception:  # noqa: BLE001
            return ""


# ── 内部工具 ────────────────────────────────────────────────────────────────


def _as_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _spec_from_plan(
    plan: dict[str, Any] | None,
    request: str,
    docs: list[ResourceDoc],
    evidence: DocEvidence,
) -> DocSpec | None:
    """确认过的方案 → 施工图。老方案（没有 spec）用 units 与那几行字拼一份。

    老任务重放不能重新谋篇：用户确认的是他看到的那些章节，重跑一次模型会换一套，
    那就等于「确认了也不算」。
    """

    if not isinstance(plan, dict):
        return None
    spec = doc_spec_of(plan)
    units = [unit for unit in plan.get("units") or [] if isinstance(unit, dict) and str(unit.get("label") or "").strip()]
    if spec is not None and spec.sections:
        # 方案里的 units 才是用户改过的那一版：spec 可能是确认时留下的旧结构
        # （直接写存储的入口不走 normalize_plan），这里按 units 对齐一次，
        # 否则「改了章节名却按旧名生成」——正是上一版导图踩过的坑。
        if units:
            spec = align_spec_with_units(spec, units)
        return _adopt_plan_fields(spec, plan)
    if not units:
        return None

    text = " ".join([str(plan.get("organization") or ""), str(plan.get("goal") or ""), request]).strip()
    genre = genre_of(text) or "explain"
    sections: list[DocSectionSpec] = []
    for unit in units:
        detail = [item.strip() for item in re.split(r"[；;、,，]", str(unit.get("detail") or "")) if item.strip()]
        sections.append(
            DocSectionSpec(
                heading=_clean_heading(str(unit.get("label") or "")),
                goal=str(unit.get("intent") or "")[:160],
                must_cover=detail[:5],
                form="prose",
            )
        )
    words = words_from_text(plan.get("presentation")) or 0
    return _adopt_plan_fields(
        DocSpec(
            title=str(plan.get("title") or "")[:40] or request[:20],
            subtitle=GENRE_LABEL.get(genre, "讲解"),
            genre=genre,  # type: ignore[arg-type]
            audience=str(plan.get("audience") or "")[:60],
            organization=str(plan.get("organization") or "")[:300],
            thesis=str(plan.get("goal") or "")[:300],
            target_words=min(max(words or 2200, 400), 12000),
            sections=sections,
            basis="rules",
        ),
        plan,
    )


def _adopt_plan_fields(spec: DocSpec, plan: dict[str, Any]) -> DocSpec:
    """方案卡上看得见、改得动的四行字，比 spec 里的旧值更算数。"""

    patch: dict[str, Any] = {}
    for key, attr in (("title", "title"), ("audience", "audience"), ("organization", "organization"), ("goal", "thesis")):
        value = str(plan.get(key) or "").strip()
        if value:
            patch[attr] = value[:300]
    words = words_from_text(plan.get("presentation"))
    if words:
        patch["target_words"] = min(max(words, 400), 12000)
    return spec.model_copy(update=patch) if patch else spec


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
    spec: DocSpec,
    evidence: DocEvidence,
    materials: list[str] | None = None,
) -> list[dict[str, str]]:
    low, high = spec.word_range()
    payload: dict[str, Any] = {
        "request": request[:600],
        "title": spec.title,
        "genre": spec.genre_label,
        "audience": spec.audience,
        "thesis": spec.thesis,
        # 篇幅是**约束**，不是愿望：不给具体区间，模型只会写出 1200 字的安全答案。
        "target_words": {"low": low, "high": high},
        "sections": [
            {
                "heading": section.clean_heading,
                "goal": section.goal,
                "must_cover": section.must_cover[:4],
                "form": section.form,
            }
            for section in spec.sections
        ],
        "devices": list(spec.devices),
    }
    if spec.emphasis:
        payload["emphasis"] = spec.emphasis[:6]
    if evidence.library:
        # 「库里有什么」必须让写手看见：只给引用片段，它会不知道结构该怎么照。
        payload["library"] = evidence.library
    if evidence.coverage is not None:
        payload["matched"] = {
            "subjects": list(evidence.subjects),
            "chapter": evidence.chapter,
        }
    if evidence.quotes:
        payload["knowledge_base"] = [
            {"index": index, "ref": item["定位"], "quote": item["原文摘录"][:PROMPT_QUOTE_LIMIT]}
            for index, item in enumerate(evidence.quotes)
        ]
    if materials:
        # 用户材料是独立来源：既不能当成教材引用，也不能被忽略。
        payload["user_material"] = [item[:PROMPT_QUOTE_LIMIT] for item in materials[:3]]
    return [
        {"role": "system", "content": WRITE_SYSTEM_PROMPT if evidence.grounded else WRITE_SYSTEM_PROMPT_GENERAL},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


#: 设计单的体裁 → 章节标题的写法。与 ``resources/designs.py`` 的 DocGenre 一一对应。
_GENRE_LABELS: dict[str, str] = dict(GENRE_LABEL)


def _target_words(design: dict[str, Any] | None) -> int | None:
    try:
        value = int((design or {}).get("target_words"))
    except (TypeError, ValueError):
        return None
    return min(max(value, 400), 12000)


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


#: 一行里出现这些开头，就算「结构化了」。整节只有别的，就是一大段话。
_STRUCTURE_LINE = re.compile(r"^\s*(?:\||[-*+]\s|\d+[.)]\s|>\s|###\s|- \[[ xX]\]\s)")


def _needs_reshape(body: str, form: str) -> bool:
    """这一节要不要补排版。判据是事实可数的，不是感觉。"""

    lines = [line for line in (body or "").split("\n") if line.strip()]
    paragraphs = [line for line in lines if not _STRUCTURE_LINE.match(line)]
    structured = len(lines) - len(paragraphs)
    longest = max((len(re.sub(r"\s+", "", line)) for line in paragraphs), default=0)
    if structured == 0 and len(paragraphs) >= 2:
        return True
    if form != "prose" and structured == 0:
        return True
    return longest >= 220


def _split_sections(markdown: str) -> tuple[str, list[tuple[str, str]]]:
    """按 ``## `` 切。返回（导语, [(标题行, 正文)]）。"""

    lines = (markdown or "").split("\n")
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in lines:
        if re.match(r"^##\s+", line):
            sections.append((line.rstrip(), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    return "\n".join(preamble).strip(), [(heading, "\n".join(body).strip()) for heading, body in sections]


def _join_sections(preamble: str, sections: list[tuple[str, str]]) -> str:
    parts = [preamble] if preamble else []
    for heading, body in sections:
        parts.append(f"{heading}\n\n{body}".strip())
    return "\n\n".join(parts).strip()


def _sanitize_citations(
    blocks: list[dict[str, Any]],
    pool_size: int,
    removed: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """越界或非数字的引文标记必须去掉，**并把去掉的记下来**。

    两件事都会在页面上露馅：
      - ``[7]`` 超过来源池长度 → 点角标打开空面板；
      - 模型偶尔写 ``[herbs]``、``[来源]`` 这类占位——它们不是引文，会被原样显示。
    宁可不显示角标，也不给读者一个点不开的引用。
    """

    removed = removed if removed is not None else []

    def clean(text: str) -> str:
        raw = text or ""
        removed.extend(_BAD_CITATION.findall(raw))
        text = _BAD_CITATION.sub("", raw)

        def repl(match: re.Match[str]) -> str:
            index = int(match.group(1))
            if index < pool_size:
                return match.group(0)
            removed.append(match.group(0))
            return ""

        return re.sub(r"\[(\d+)\]", repl, text)

    for block in blocks:
        block["text"] = clean(block.get("text", ""))
        if block.get("children"):
            _sanitize_citations(block["children"], pool_size, removed)
    return blocks, removed


def _fallback_markdown(spec: DocSpec, docs: list[ResourceDoc]) -> str:
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

    for section in spec.sections:
        heading = section.clean_heading
        lines.append(f"## {heading}")
        lines.append("")
        paragraph = _fact_paragraph(heading, doc) if doc is not None else ""
        if paragraph:
            lines.append(paragraph)
            lines.append("")
        points = section.must_cover or []
        if points:
            lines.extend(f"- {point}" for point in points)
            lines.append("")
        elif not paragraph:
            lines.append(section.goal or f"本节围绕「{heading}」展开，待补写正文。")
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


__all__ = [
    "DocAgent",
    "DocResult",
    "build_source_pool",
    "classify_kind",
    "clean_topic",
    "derive_title",
    "sections_for",
    "DocSpecDraft",
]
