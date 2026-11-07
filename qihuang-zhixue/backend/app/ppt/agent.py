"""幻灯智能体的编排边界：检索进，校验过的整份幻灯出。

三档执行路径，与 ``app/mindmap/agent.py`` 完全同构：

1. **注入式 llm_call**（测试 / 子类）：直接拿它的字符串返回解析，失败即回落；
2. **真模型**：``structured_create`` + ``ModelRouter(settings=...)``；
3. **无 key**：``router.select`` 返回 ``MockClient`` → 直接返回 ``mock_factory()``，
   也就是 ``templates`` 里的确定性讲稿。

第 2、3 档的产出走同一个 ``normalize_*``，所以「有没有模型」只影响内容丰度，
不影响结构、版式、页数与可保存性——接模型不需要改任何调用方代码。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.agents.resource_context import ResourceDoc, collect_source_refs
from app.agents.structured_output import structured_create
from app.config import Settings, get_settings
from app.llm.router import ModelRouter

from .draft import DeckDraft, OutlineDraft, normalize_deck, normalize_outline
from .params import PptParams
from .prompts import COMPOSE_SYSTEM_PROMPT, OUTLINE_SYSTEM_PROMPT
from .schemas import DeckContent, FramePlan, OutlineSlide
from .templates import deck_title_for, deterministic_deck, deterministic_outline

LLMCall = Callable[[list[dict[str, str]]], Awaitable[str]]

#: 少于这个页数的产出视为「没写出来」，退回确定性模板。
MIN_SLIDES = 3
#: 大纲少于这个页数就等于没谋篇：一份 2 页的大纲撑不起一节课。
MIN_OUTLINE = 3


class PptAgent:
    def __init__(self, *, settings: Settings | None = None, llm_call: LLMCall | None = None) -> None:
        self.settings = settings or get_settings()
        self.llm_call = llm_call

    # ── 立骨 ────────────────────────────────────────────────────────────────
    def frame(self, topic: str, docs: list[ResourceDoc], params: PptParams | None = None) -> FramePlan:
        """定标题、受众与叙事主线。

        这一步刻意不调模型：它只依据「检索到了哪些字段」决定讲几段，
        调用一次模型换不来更好的结构，却会让首屏多等 3 秒。
        """

        params = params or PptParams()
        doc = docs[0] if docs else None
        arc = ["性味归经", "功效主治"]
        if doc and doc["differentiation"]:
            arc.append("配伍与分用")
        if doc and doc["cautions"]:
            arc.append("用量与禁忌")
        arc.append("一页收束")
        return FramePlan(deck_title=deck_title_for(topic, docs), audience=params.audience, arc=arc)

    # ── 谋篇 ────────────────────────────────────────────────────────────────
    async def outline(
        self,
        topic: str,
        docs: list[ResourceDoc],
        params: PptParams | None = None,
        *,
        plan: FramePlan | None = None,
    ) -> list[OutlineSlide]:
        params = params or PptParams()
        sources = collect_source_refs(docs)
        plan = plan or self.frame(topic, docs, params)
        fallback = deterministic_outline(topic, docs, params.as_dict())

        if self.llm_call is not None:
            draft = await self._safe_call(OutlineDraft, self.llm_call, OUTLINE_SYSTEM_PROMPT, _outline_payload(topic, docs, sources, params, plan))
            outline = normalize_outline(draft or fallback, topic=topic, sources=sources, params=params.as_dict())
            return outline

        try:
            draft = await structured_create(
                task_type=_ppt_task_type(self.settings),
                messages=[
                    {"role": "system", "content": OUTLINE_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(_outline_payload(topic, docs, sources, params, plan), ensure_ascii=False)},
                ],
                response_model=OutlineDraft,
                mock_factory=lambda: fallback,
                router=ModelRouter(settings=self.settings),
            )
        except Exception:
            # 谋篇失败不该让整条任务炸炉：确定性大纲本身就是可用的。
            draft = fallback
        outline = normalize_outline(draft, topic=topic, sources=sources, params=params.as_dict())
        if len(outline) < MIN_OUTLINE:
            # 2 页的大纲撑不起一节课，也会毒化后面的敷演与确认面板。
            outline = normalize_outline(fallback, topic=topic, sources=sources, params=params.as_dict())
        return outline

    # ── 敷演 ────────────────────────────────────────────────────────────────
    async def compose(
        self,
        topic: str,
        docs: list[ResourceDoc],
        params: PptParams | None = None,
        *,
        outline: list[OutlineSlide] | None = None,
    ) -> DeckContent:
        params = params or PptParams()
        sources = collect_source_refs(docs)
        outline = outline or await self.outline(topic, docs, params)
        outline_payload = [{"title": item.title, "points": item.points, "source_indexes": item.source_indexes} for item in outline]
        fallback = deterministic_deck(topic, docs, params.as_dict(), outline_payload)
        payload = _compose_payload(topic, docs, sources, params, outline_payload)

        draft: DeckDraft | None = None
        if self.llm_call is not None:
            draft = await self._safe_call(DeckDraft, self.llm_call, COMPOSE_SYSTEM_PROMPT, payload)
        else:
            try:
                draft = await structured_create(
                    task_type=_ppt_task_type(self.settings),
                    messages=[
                        {"role": "system", "content": COMPOSE_SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    response_model=DeckDraft,
                    mock_factory=lambda: fallback,
                    router=ModelRouter(settings=self.settings),
                )
            except Exception:
                draft = fallback

        deck = normalize_deck(draft or fallback, topic=topic, sources=sources, params=params.as_dict())
        if len(deck.slides) < MIN_SLIDES:
            # 模型只写出一两页时不能直接交付：封面 + 一页正文不叫讲稿。
            # 注意这里要用「不带大纲」的完整模板：大纲本身也很短时，
            # 用大纲铺页照样只有两页。
            deck = normalize_deck(
                deterministic_deck(topic, docs, params.as_dict()),
                topic=topic,
                sources=sources,
                params=params.as_dict(),
            )
        return deck

    async def generate(self, topic: str, docs: list[ResourceDoc], params: PptParams | None = None) -> DeckContent:
        """一步出整稿：给「一键模式」与测试用，内部仍是谋篇 → 敷演。"""

        params = params or PptParams()
        outline = await self.outline(topic, docs, params)
        return await self.compose(topic, docs, params, outline=outline)

    # ── 校色 ────────────────────────────────────────────────────────────────
    def polish(self, deck: DeckContent) -> dict[str, int]:
        """统计与自检。不改内容——「生成完偷偷改字」比不改更让人不信任。"""

        return {
            "slides": len(deck.slides),
            "bullets": sum(len(slide.bullets) for slide in deck.slides),
            "grounded": sum(1 for slide in deck.slides if slide.source_indexes),
            "notes": sum(1 for slide in deck.slides if slide.note),
        }

    async def _safe_call(
        self,
        model: type[DeckDraft] | type[OutlineDraft],
        llm_call: LLMCall,
        system_prompt: str,
        payload: dict[str, Any],
    ) -> Any:
        try:
            raw = await llm_call(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ]
            )
            return model.model_validate(json.loads(raw))
        except Exception:
            return None


def _ppt_task_type(settings: Settings) -> str:
    """现网映射表没有 ppt 键，落回 generate（Flash 档），不新增配置项。"""

    return "ppt" if "ppt" in settings.llm_task_model_map else "generate"


def _outline_payload(
    topic: str,
    docs: list[ResourceDoc],
    sources: list[dict[str, str]],
    params: PptParams,
    plan: FramePlan,
) -> dict[str, Any]:
    return {
        "topic": topic,
        "deck_title": plan.deck_title,
        "audience": params.audience,
        "page_count": params.page_count,
        "arc": plan.arc,
        "sources": _source_payload(sources),
        "herbs": [_compact_doc(doc) for doc in docs[:3]],
    }


def _compose_payload(
    topic: str,
    docs: list[ResourceDoc],
    sources: list[dict[str, str]],
    params: PptParams,
    outline: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "topic": topic,
        "audience": params.audience,
        "theme": params.theme,
        "outline": outline,
        "sources": _source_payload(sources),
        "herbs": [_compact_doc(doc) for doc in docs[:3]],
    }


def _source_payload(sources: list[dict[str, str]]) -> list[dict[str, str]]:
    """只给引用需要的三段，不把整段 evidence_text 塞进 prompt。"""

    return [
        {"index": index, "name": item.get("name", ""), "ref": item.get("ref", ""), "quote": str(item.get("quote", ""))[:80]}
        for index, item in enumerate(sources)
    ]


def _compact_doc(doc: ResourceDoc) -> dict[str, Any]:
    return {
        "name": doc["name"],
        "category": doc["category"],
        "nature": doc["nature"],
        "flavor": doc["flavor"],
        "meridians": doc["meridians"],
        "effects": doc["effects"][:6],
        "indications": [item.get("syndrome", "") for item in doc["indications"][:5]],
        "combinations": [
            {"with": item.get("with_names", []), "purpose": item.get("purpose", ""), "formula": item.get("formula_ref", "")}
            for item in doc["combinations"][:4]
        ],
        "differentiation": [
            {"compare_name": item.get("compare_name", ""), "key_points": item.get("key_points", "")}
            for item in doc["differentiation"][:3]
        ],
        "dosage": str(doc["dosage"].get("text", "")),
        "usage_notes": str(doc["dosage"].get("usage_notes", "")),
        "cautions": doc["cautions"][:3],
    }
