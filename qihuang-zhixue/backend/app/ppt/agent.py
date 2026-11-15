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

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.agents.resource_context import ResourceDoc, collect_source_refs
from app.agents.structured_output import structured_create
from app.config import Settings, get_settings
from app.llm.router import ModelRouter

from .draft import (
    DeckDraft,
    DraftSlide,
    OutlineDraft,
    align_deck_to_outline,
    normalize_deck,
    normalize_outline,
)
from .params import PptParams
from .prompts import COMPOSE_SYSTEM_PROMPT, OUTLINE_SYSTEM_PROMPT, PAGE_COMPOSE_SYSTEM_PROMPT
from .scene_art import paint_scenes
from .schemas import DeckContent, FramePlan, OutlineSlide
from .templates import deck_title_for, deterministic_deck, deterministic_outline

LLMCall = Callable[[list[dict[str, str]]], Awaitable[str]]

#: 少于这个页数的产出视为「没写出来」，退回确定性模板。
MIN_SLIDES = 3
#: 大纲少于这个页数就等于没谋篇：一份 2 页的大纲撑不起一节课。
MIN_OUTLINE = 3
#: 逐页敷演的并发。三路够快，又不会把网关的限流打满。
PAGE_CONCURRENCY = 3


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
        strict_outline: bool = False,
        design: Any = None,
        resolutions: list[Any] | None = None,
        spec: Any = None,
    ) -> DeckContent:
        """按大纲敷演。

        ``strict_outline`` 用于「用户确认过的方案」：页数与页序是用户定的，
        即使只有两页也照做，不能因为模板要求三页就整份换成药材八股。
        """
        params = params or PptParams()
        sources = collect_source_refs(docs)
        outline = outline or await self.outline(topic, docs, params)
        outline_payload = [
            {"title": item.title, "points": item.points, "intent": item.intent, "source_indexes": item.source_indexes}
            for item in outline
        ]
        fallback = deterministic_deck(topic, docs, params.as_dict(), outline_payload)
        payload = _compose_payload(topic, docs, sources, params, outline_payload, design=design, spec=spec)

        draft: DeckDraft | None = None
        if self.llm_call is not None:
            draft = await self._safe_call(DeckDraft, self.llm_call, COMPOSE_SYSTEM_PROMPT, payload)
        elif len(outline) >= MIN_OUTLINE:
            # **一页一次调用**：整份一次写出来时，模型会把九页写成同一段话切九份；
            # 逐页之后每页只面对自己的 role 与 must_cover，写坏了也只退这一页。
            draft = await self._compose_pages(
                topic, docs, params, outline, design=design, spec=spec, fallback=fallback
            )
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

        source_draft = draft or fallback
        if strict_outline and outline:
            # 方案是用户签过字的：页数、页序、标题都以它为准。模型少写就补齐，
            # 写多了（大纲外）就丢掉——「不在确认方案内」的页不是惊喜，是跑偏。
            source_draft = align_deck_to_outline(source_draft, outline, fallback=fallback)
        deck = normalize_deck(
            source_draft,
            topic=topic,
            sources=sources,
            params=params.as_dict(),
            # 确认方案时页数由方案定，左栏那个「几页」只是生成前的建议值。
            limit_override=len(outline) if strict_outline and outline else None,
            design=design,
            resolutions=resolutions,
        )
        if len(deck.slides) < MIN_SLIDES and not strict_outline:
            # 模型只写出一两页时不能直接交付：封面 + 一页正文不叫讲稿。
            # 注意这里要用「不带大纲」的完整模板：大纲本身也很短时，
            # 用大纲铺页照样只有两页。
            deck = normalize_deck(
                deterministic_deck(topic, docs, params.as_dict()),
                topic=topic,
                sources=sources,
                params=params.as_dict(),
            )
        deck.scene_stats = await self._paint(deck, design=design, deck_title=topic)
        return deck

    async def _compose_pages(
        self,
        topic: str,
        docs: list[ResourceDoc],
        params: PptParams,
        outline: list[Any],
        *,
        design: Any = None,
        spec: Any = None,
        fallback: DeckDraft | None = None,
    ) -> DeckDraft:
        """逐页写。每页一次调用，失败只退这一页的兜底内容。

        输入只给这一页：它的 role、写作契约、必须讲到的点、可用的药材与依据。
        以前整份一起写，模型看不到「这一页是对照页」这种局部约束，只好每页都写
        「标题 + 三条要点」——版式再错落，正文仍是同一段话切开的。
        """

        sources = collect_source_refs(docs)
        fallback_slides = list(getattr(fallback, "slides", []) or [])
        semaphore = asyncio.Semaphore(PAGE_CONCURRENCY)

        async def one(index: int) -> tuple[int, DraftSlide]:
            item = outline[index]
            page_payload = _page_payload(
                topic,
                docs,
                sources,
                params,
                item,
                index=index,
                total=len(outline),
                design=design,
                spec=spec,
            )
            spare = fallback_slides[index] if index < len(fallback_slides) else DraftSlide(title=str(getattr(item, "title", "")))
            async with semaphore:
                try:
                    slide = await structured_create(
                        task_type=_ppt_task_type(self.settings),
                        messages=[
                            {"role": "system", "content": PAGE_COMPOSE_SYSTEM_PROMPT},
                            {"role": "user", "content": json.dumps(page_payload, ensure_ascii=False)},
                        ],
                        response_model=DraftSlide,
                        mock_factory=lambda: spare,
                        router=ModelRouter(settings=self.settings),
                    )
                except Exception:  # noqa: BLE001 - 一页写坏不该拖垮整份
                    slide = spare
            # 注入式模型（测试桩 / 旧网关）可能仍按「整份」回一个 DeckDraft：
            # 从里面取这一页，取不到就用兜底，别让一次形状不对炸掉整份稿子。
            if not isinstance(slide, DraftSlide):
                slides = list(getattr(slide, "slides", []) or [])
                slide = slides[index] if index < len(slides) else spare
            return index, slide

        results = await asyncio.gather(*(one(index) for index in range(len(outline))))
        slides: list[DraftSlide] = [DraftSlide() for _ in outline]
        for index, slide in results:
            slides[index] = slide
        return DeckDraft(deck_title=(fallback.deck_title if fallback is not None else ""), slides=slides)

    async def _paint(self, deck: DeckContent, *, design: Any, deck_title: str) -> dict[str, Any]:
        """逐页排版：母版命中走确定性构造器，否则请模型自由排版。

        母版路径**一次模型调用都没有**，注入式 ``llm_call``（测试路径）也不跳过——
        确定性产出没有「每个用例多造一份场景」的成本，反而让内容测试能顺带
        验到版面。未命中（老方案回放、master_id 写错）原样退 scene_art。
        """

        from .masters import by_id as master_by_id
        from .masters.painter import master_id_of, paint_master_scenes

        master_id = master_id_of(design)
        if master_id and master_by_id(master_id) is not None:
            stats = paint_master_scenes(deck, design=design, deck_title=deck_title)
            if not stats.get("skipped"):
                return stats
            # 理论上到不了这里（已查过目录），兜底交给 scene_art。
        if self.llm_call is not None:
            return {"skipped": "injected_llm"}
        return await paint_scenes(
            deck,
            settings=self.settings,
            design=design,
            deck_title=deck_title,
            task_type=_ppt_task_type(self.settings),
        )

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
    *,
    design: Any = None,
    spec: Any = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "topic": topic,
        "audience": params.audience,
        "theme": params.theme,
        "outline": _outline_with_roles(outline, design=design, spec=spec),
        "sources": _source_payload(sources),
        "herbs": [_compact_doc(doc) for doc in docs[:3]],
    }
    # 设计单是**约束**，不是形容词：模型据此知道这页是两栏对照还是四步流程，
    # 以及每块能写几个字。升级前这里只有一句「视觉呈现要求」，等于没给。
    if design is not None:
        payload["capacity"] = _capacity_payload(design)
    materials = (params.as_dict().get("materials") or []) if isinstance(params.as_dict().get("materials"), list) else []
    if materials:
        payload["user_material"] = [str(item)[:300] for item in materials[:3]]
    return payload


def _page_payload(
    topic: str,
    docs: list[ResourceDoc],
    sources: list[dict[str, str]],
    params: PptParams,
    item: Any,
    *,
    index: int,
    total: int,
    design: Any = None,
    spec: Any = None,
) -> dict[str, Any]:
    """逐页敷演的一页输入。**只给这一页**，别把整份大纲再塞回去。"""

    from .spec import ROLE_CONTRACT

    entry: dict[str, Any] = {
        "title": getattr(item, "title", ""),
        "points": list(getattr(item, "points", []) or []),
        "intent": getattr(item, "intent", ""),
        "source_indexes": list(getattr(item, "source_indexes", []) or []),
    }
    role = ""
    design_pages = design.get("pages") if isinstance(design, dict) else getattr(design, "pages", [])
    pages = list(design_pages or [])
    if index < len(pages):
        data = pages[index] if isinstance(pages[index], dict) else pages[index].model_dump()
        role = str(data.get("archetype") or "")
    spec_pages = list(getattr(spec, "pages", []) or []) if spec is not None else []
    must_cover: list[str] = []
    if index < len(spec_pages):
        role = role or spec_pages[index].role
        must_cover = list(spec_pages[index].must_cover)
    payload: dict[str, Any] = {
        "page_no": f"{index + 1}/{total}",
        "page": {**entry, "role": role, "write": ROLE_CONTRACT.get(role, "")},
        "topic": topic,
        "audience": params.audience,
        "theme": params.theme,
        "sources": _source_payload(sources),
        "herbs": [_compact_doc(doc) for doc in docs[:3]],
    }
    if must_cover:
        payload["page"]["must_cover"] = must_cover[:5]
    if design is not None:
        payload["capacity"] = _capacity_payload(design)
    return payload


def _outline_with_roles(outline: list[dict[str, Any]], *, design: Any = None, spec: Any = None) -> list[dict[str, Any]]:
    """把「这一页是什么版式、按什么写法写」并进大纲条目。

    升级前 ``design`` 是单独一列 ``[{archetype, density}]``，与 outline 平行——
    模型要自己数第几个才配得上第几页，实际等于没给。现在每页自带 ``role``
    与 ``write``（该版式的写作契约），模型照着写，版式才可能真的错落。
    """

    from .spec import ROLE_CONTRACT

    pages = []
    design_pages = design.get("pages") if isinstance(design, dict) else getattr(design, "pages", [])
    design_pages = list(design_pages or [])
    spec_pages = list(getattr(spec, "pages", []) or []) if spec is not None else []
    for index, item in enumerate(outline):
        entry = dict(item)
        role = ""
        density = ""
        must_cover: list[str] = []
        if index < len(design_pages):
            page = design_pages[index]
            data = page if isinstance(page, dict) else page.model_dump()
            role = str(data.get("archetype") or "")
            density = str(data.get("density") or "")
        if index < len(spec_pages):
            page_spec = spec_pages[index]
            role = role or page_spec.role
            must_cover = list(page_spec.must_cover)
        if role:
            entry["role"] = role
            entry["write"] = ROLE_CONTRACT.get(role, "")
        if density:
            entry["density"] = density
        if must_cover:
            entry["must_cover"] = must_cover[:5]
        pages.append(entry)
    return pages


def _design_payload(design: Any, outline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages = design.get("pages") if isinstance(design, dict) else getattr(design, "pages", [])
    result: list[dict[str, Any]] = []
    for page in pages or []:
        data = page if isinstance(page, dict) else page.model_dump()
        result.append({"archetype": data.get("archetype", "points"), "density": data.get("density", "normal")})
    return result[: len(outline)]


def _capacity_payload(design: Any) -> dict[str, int]:
    from .design import DENSITY_CAPS

    pages = design.get("pages") if isinstance(design, dict) else getattr(design, "pages", [])
    densest = max(
        (str((page if isinstance(page, dict) else page.model_dump()).get("density") or "normal") for page in pages or []),
        key=lambda value: DENSITY_CAPS.get(value, 5),
        default="normal",
    )
    return {"blocks": DENSITY_CAPS.get(densest, 5), "bullet_chars": 40, "column_chars": 30}


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
