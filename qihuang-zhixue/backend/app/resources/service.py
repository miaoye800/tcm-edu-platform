"""Task runner. Progress is committed before each outward SSE event batch."""

from __future__ import annotations

import json
import random
import re
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from app.agents.resources_graph import run_resource_generation
from app.agents.resource_context import retrieve_resource_docs
from app.agents.quiz_agent import generate_quiz_resource
from app.agents.story_agent import generate_story_resource
from app.agents.graph_intelligence import GraphAgent
from app.config import get_settings
from app.docs import DocAgent
from app.docs.spec import doc_conformance, doc_params_from_plan, doc_spec_of
from app.mindmap import MindmapAgent
from app.ppt import PptAgent
from app.ppt.params import PptParams
from app.ppt.schemas import OutlineSlide
from app.ppt.spec import DeckSpec, deck_conformance, deck_params_from_plan, deck_spec_of
from app.mindmap.intents import classify
from app.mindmap.evidence import build_evidence_pack
from app.mindmap.archetypes import build_archetype_tree
from app.mindmap.plan_tree import skeleton_from_plan
from app.resources.constants import ALCHEMY_STEPS, CURRENT_PIPELINE_VERSION, gate_step, steps_for
from app.resources.designs import design_for
from app.resources.planning import plan_units, propose as propose_resource_plan, story_subject, user_materials
from app.mindmap.spec import conformance, generation_params_from_plan
from app.ppt.select import summary as select_summary
from app.resources import storage

_EVENT_CACHE: dict[str, list[dict[str, Any]]] = {}
_PROGRESS_CACHE: dict[str, dict[str, Any]] = {}


def envelope(event: str, step_index: int, total_steps: int, step_label: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"event": event, "step_index": step_index, "total_steps": total_steps, "step_label": step_label, "payload": payload or {}}


async def generate_content(resource_type: str, topic: str, *, node_id: str | None = None, db_path: Any = None, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Adapter for the existing validated LangGraph resource agents.

    It is deliberately a narrow seam: unit tests replace it with a deterministic
    generator, and a future node-dialogue agent can replace it without changing
    the persistence or SSE contract.
    """
    if resource_type == "graph":
        content = await GraphAgent().generate(center_id=node_id or topic, center_name=topic, db_path=db_path)
        payload = content.model_dump(mode="json")
        return payload, payload["sources"]
    if resource_type == "mindmap":
        docs = await retrieve_resource_docs(topic)
        # 设计单里的结构要真的影响生成，否则「四面发散」只是换了个画法。
        # 结构 → （分支预算, 深度上限）的换算在 ``designs.STRUCTURE_BUDGET``，
        # 这里只把结果塞进 agent 真正读的那两个键。
        design = (params or {}).get("confirmed_design")
        if isinstance(design, dict):
            params = {
                **params,
                "depth": design.get("max_depth") or (params or {}).get("depth"),
                "branches": design.get("branch_budget") or (params or {}).get("branches"),
                "structure": design.get("structure") or (params or {}).get("structure"),
                "theme": design.get("theme") or (params or {}).get("theme"),
            }
        # 方案是唯一事实源，且必须**排在设计单之后**：设计单是提案时按原始入参
        # 算的，用户在方案面板上把层数改成 5 之后它就是旧的。这一步把确认过的
        # 那一版翻成生成参数（层数/支数/受众/组织方式/必答项），并带上
        # ``expected`` 供生成后核对。
        params = generation_params_from_plan((params or {}).get("confirmed_plan"), params)
        # Resource tasks currently do not mutate the learner profile; the profile
        # is injected by callers that have it. Keyless execution still succeeds.
        content = await MindmapAgent().generate(topic, docs, profile=(params or {}).get("profile"), params=params)
        payload = content.model_dump(mode="json")
        return payload, payload["sources"]
    if resource_type == "ppt":
        docs = await retrieve_resource_docs(topic)
        deck = await _generate_deck(topic, docs, params or {})
        payload = deck.model_dump(mode="json")
        return payload, payload["sources"]
    if resource_type == "doc":
        # 文稿是「按诉求写一篇」，不是「按药材条目复述字段」：先读整句话定体裁与
        # 章节（施工图），再落笔。主题落在教材某门课里时，章节名照用库里的目录；
        # 库外题材按通用知识写——跨领域主题也必须出稿，不能回「无相关中药信息」。
        docs = await retrieve_resource_docs(topic)
        confirmed_plan = (params or {}).get("confirmed_plan")
        planned = doc_params_from_plan(confirmed_plan if isinstance(confirmed_plan, dict) else None, params or {})
        result = await DocAgent().generate(
            topic,
            docs,
            plan=confirmed_plan if isinstance(confirmed_plan, dict) else None,
            materials=user_materials(params),
            # 篇幅只读方案（设计单是提案时算的，用户在方案卡上改过之后它就是旧的）。
            design=(params or {}).get("confirmed_design") if isinstance((params or {}).get("confirmed_design"), dict) else None,
            planned=planned,
        )
        payload = {
            "blocks": result.blocks,
            "title": result.title,
            "doc_kind": result.doc_kind,
            "source_mode": result.meta.get("source_mode", "general"),
            "summary": result.summary,
            # 清洗前的真数。传下去是为了让 verify 能报「模型编了几条引用」。
            "bad_citations": int(result.meta.get("bad_citations") or 0),
            "sections": result.meta.get("sections", []),
            # 施工图随成品落库：核对步按它算「方案要的章节与排版落成了没有」。
            "doc_spec": result.meta.get("doc_spec") or {},
            "expected": planned.get("expected") or {},
        }
        return payload, result.sources
    if resource_type == "quiz":
        # 试卷有自己的 agent，且带 mock 载荷：无 key 也能出题。走旧图
        # （run_resource_generation）要先过一道「必须配了密钥」的检查，
        # 那条检查对带 mock 的能力是错的——它会把能跑的东西挡在门外。
        docs = await retrieve_resource_docs(topic)
        profile = (params or {}).get("profile")
        # 题量是用户最容易有主见的一个数，而 agent 的入参只有 topic：把它写进
        # 题目里比在出题后裁剪可靠——先出八道再砍成六道，会把配比也砍歪。
        count = int((params or {}).get("question_count") or 0)
        ask = f"{topic}（共 {count} 道题）" if count else topic
        quiz = await generate_quiz_resource(topic=ask, retrieved_docs=docs, profile=profile)
        payload = quiz.payload.model_dump(mode="json")
        if not payload.get("questions"):
            # 模型没给题（长尾主题上见过 400 与空数组两种）。交白卷比给一份
            # 按教材复述的题更糟：用户点开是一张空卷子，还得再来一次。
            # 换成确定性题目并在 verify 里注明，比沉默地封存诚实。
            payload = _fallback_quiz_payload(docs, profile)
        return payload, [item.model_dump(mode="json") for item in quiz.sources]
    if resource_type == "story":
        # 漫剧不能拿「延伸阅读」顶替：那是书单，不是剧本。story 有自己的 agent，
        # 出的是结构化场次 + 由场次派生的可读文稿块。
        docs = await retrieve_resource_docs(topic)
        scenes = int((params or {}).get("scene_count") or 0)
        ask = f"{topic}（分 {scenes} 场）" if scenes else topic
        content, sources = await generate_story_resource(topic=ask, retrieved_docs=docs, profile=(params or {}).get("profile"))
        if not content.get("scenes"):
            # 一场都没有等于没交付：退回确定性三场，并在 verify 里注明。
            from app.agents.story_agent import fallback_story

            content = fallback_story(topic, docs)
        return content, sources
    settings = get_settings()
    if not (settings.deepseek_api_key or settings.zhipu_api_key):
        raise RuntimeError("未配置可用的 LLM 密钥")
    old_type = {"doc": "explain", "story": "reading"}.get(resource_type, resource_type)
    state = await run_resource_generation(topic, resource_types=[old_type])
    resource = state["resources"].get(old_type)
    if resource is None:
        raise RuntimeError("丹方未能产出资源")
    raw = resource.model_dump(mode="json")
    sources = [{"出处": item.get("name", "知识库"), "原文摘录": item.get("quote", ""), "定位": item.get("ref", "")} for item in raw.get("sources", [])]
    payload = raw["payload"]
    if resource_type == "doc":
        return {"blocks": _doc_blocks(payload)}, sources
    return payload, sources


def _fallback_quiz_payload(docs: list[Any], profile: Any) -> dict[str, Any]:
    """模型没出题时的确定性兜底：按教材条目复述成题。"""

    from app.agents.quiz_agent import _mock_payload

    payload = _mock_payload(docs, profile).model_dump(mode="json")
    if payload.get("questions"):
        payload["fallback"] = "教材复述（模型未返回可用题目）"
    return payload


async def _generate_deck(topic: str, docs: list[Any], params: dict[str, Any]) -> Any:
    """铺一份幻灯：优先按「确认过的方案」逐页敷演。

    ``confirmed_plan`` 是用户在方案面板里确认（可能改过）的最终版本；它取代了
    旧版「先出大纲再确认」那一步——方案里的每一页就是一份大纲页，但多了
    每页目的与依据档位。``confirmed_outline`` 仍然保留，供 v1 在途任务使用。
    """

    ppt_params = PptParams.parse(params)
    agent = PptAgent()
    planned = _outline_from_plan(params.get("confirmed_plan"))
    design = params.get("confirmed_design")
    resolutions = params.get("slot_resolution")
    # 施工图里存着「这一页必须讲到什么」与每页的版式：不带给模型，用户写进
    # must_cover 的硬要求（「激励他们适应大学生活」）就永远到不了正文。
    spec = _deck_spec_param(params.get("confirmed_plan"))
    if planned:
        # 用户确认过的页数与页序就是交付契约：不再套用「至少三页」的模板兜底。
        deck = await agent.compose(
            topic,
            docs,
            ppt_params,
            outline=planned,
            strict_outline=True,
            design=design,
            resolutions=resolutions if isinstance(resolutions, list) else None,
            spec=spec,
        )
        return _settle_plan_deck(deck, params.get("confirmed_plan"))
    confirmed = params.get("confirmed_outline")
    if confirmed:
        try:
            outline = [OutlineSlide.model_validate(item) for item in confirmed]
        except Exception:
            outline = []
        if outline:
            return await agent.compose(topic, docs, ppt_params, outline=outline, design=design, spec=spec)
    return await agent.generate(topic, docs, ppt_params)


def _deck_spec_param(plan: Any) -> DeckSpec | None:
    """确认方案 → 施工图（已按卡片上改过的页面重排过）。

    走 ``deck_params_from_plan`` 而不是直接读 ``spec``：用户在卡片上加过页、
    改过页名，施工图得先对齐再交给生成。老方案没有 spec，返回 None。
    """

    planned = deck_params_from_plan(plan if isinstance(plan, dict) else None)
    data = planned.get("deck_spec")
    if not isinstance(data, dict):
        return None
    try:
        return DeckSpec.model_validate(data)
    except Exception:  # noqa: BLE001 - 坏施工图不该拦住出稿
        return None


def _settle_plan_deck(deck: Any, plan: Any) -> Any:
    """封面不冒充教材依据。

    排版层会给封面盖上「全部来源」的下标，这是「整份讲稿参考了这些资料」的
    意思；但如果方案里没有任何一页有知识库依据，封面就不该显示成教材实证。
    只调整来源元数据，不改标题与正文。
    """

    units = plan_units(plan)
    if units and not any(unit.source == "knowledge_base" for unit in units) and deck.slides:
        cover = deck.slides[0]
        if cover.kind == "cover" and cover.source_indexes:
            cover.source_indexes = []
    return deck


def _outline_from_plan(plan: Any) -> list[OutlineSlide]:
    """方案单元 → 大纲页。只读用户确认过的那一份，不在这里重新构思。"""

    slides: list[OutlineSlide] = []
    for index, unit in enumerate(plan_units(plan)[:16]):
        label = unit.label.strip()
        if not label:
            continue
        points = [part.strip()[:60] for part in re.split(r"[；;\n]", unit.detail) if part.strip()][:5]
        if not points and unit.intent:
            points = [unit.intent[:60]]
        slides.append(
            OutlineSlide(
                id=f"s{index}",
                title=label[:40],
                points=points,
                intent=unit.intent[:60],
            )
        )
    return slides


def _block_id(prefix: str) -> str:
    return f"blk_{prefix}_{uuid4().hex[:8]}"

def _table_markdown(columns: list[str], rows: list[list[str]]) -> str:
    """表格同时保留结构化 props 与 Markdown 文本。

    文本不是冗余：旧前端、导出、以及「没有 props 时退化渲染」都依赖它。
    """
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return "\n".join(lines)


def _doc_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """把 ExplainPayload 的七节转成 Notion 式块。

    两点约束（都是硬约束，改动会使既有测试与旧前端失效）：
      1. `type` 仍用旧六值（heading/paragraph/bullet/table/callout/quote）
      2. 每块必须带 `text` —— 旧前端靠它渲染，且列表/表格此前只给了 items/rows，
         拿不到 text 会直接抛异常
    新增的 `id` / `props` 供新渲染器使用；`props` 里的结构信息与 `text` 同时存在，
    因此无论前端新旧都能读。
    """
    blocks: list[dict[str, Any]] = []
    for section in payload.get("sections", []):
        title = str(section.get("title") or "讲解")
        content = str(section.get("content") or "")
        blocks.append({"id": _block_id("h"), "type": "heading", "text": title, "props": {"level": 2}})
        blocks.append({"id": _block_id("p"), "type": "paragraph", "text": content, "props": {}})

        claims = [str(item) for item in section.get("claims", []) if str(item).strip()]
        if claims:
            blocks.append({
                "id": _block_id("l"),
                "type": "bullet",
                "text": "\n".join(claims[:5]),
                "props": {"items": claims[:5]},
            })
        if title == "功效" and claims:
            columns = ["要点", "依据"]
            rows = [[claim, "知识库已检索"] for claim in claims[:4]]
            blocks.append({
                "id": _block_id("t"),
                "type": "table",
                "text": _table_markdown(columns, rows),
                "props": {"columns": columns, "rows": rows, "header": True},
            })
        if title == "用量与使用注意":
            blocks.append({
                "id": _block_id("c"),
                "type": "callout",
                "text": content,
                "props": {"tone": "warn", "icon": "⚠"},
            })
        for source in section.get("sources", [])[:1]:
            quote = str(source.get("quote", "")).strip() or str(source.get("ref", ""))
            if quote:
                blocks.append({
                    "id": _block_id("q"),
                    "type": "quote",
                    "text": quote,
                    "props": {"source": {"出处": source.get("ref", ""), "原文摘录": quote}},
                })
    if not blocks:
        return [{
            "id": _block_id("c"),
            "type": "callout",
            "text": "知识库暂未覆盖此节点。",
            "props": {"tone": "info", "icon": "注"},
        }]
    return blocks


def _mindmap_root(content: dict[str, Any]) -> dict[str, Any] | None:
    data = content.get("mindmap_data")
    if not isinstance(data, dict):
        return None
    root = data.get("nodeData")
    return root if isinstance(root, dict) else None


def _mindmap_node_count(root: dict[str, Any]) -> int:
    count = 0
    stack = [root]
    while stack:
        node = stack.pop()
        count += 1
        stack.extend(node.get("children") or [])
    return count


def _mindmap_node_events(content: dict[str, Any], step_index: int, total_steps: int, step_label: str) -> list[dict[str, Any]]:
    """把整棵树拆成逐节点事件，按层序（BFS）推。

    三个刻意的约定：

    - **按层序推**：前端用 ``addChild(findEle(parent_id))`` 增量插节点，
      父节点必须先到，否则只能整树重建，生长动画就废了。
    - **id 由后端确定性分配**（root / b0 / b0-1），断线重连会从 branch 重推
      同一批 id → 前端靠 id 去重，不需要事件回放。
    - ``node_total`` 带在每条事件里，前端不必等 done 就知道要长多少节点。
    """

    root = _mindmap_root(content)
    if root is None:
        return []
    total_nodes = _mindmap_node_count(root)
    events: list[dict[str, Any]] = []
    queue: list[tuple[dict[str, Any], str | None, int]] = [(root, None, 0)]
    order = 0
    while queue:
        node, parent_id, depth = queue.pop(0)
        node_id = str(node.get("id") or "")
        events.append(
            envelope(
                "node",
                step_index,
                total_steps,
                step_label,
                {
                    "node": {
                        "id": node_id,
                        "parent_id": parent_id,
                        "topic": str(node.get("topic") or ""),
                        "depth": depth,
                        "order": order,
                        "direction": int(node.get("direction") or 0),
                        "source_indexes": list((node.get("meta") or {}).get("source_indexes") or []),
                        # 依据等级与角色随节点一起推：前端要靠它打「推断」角标、
                        # 底栏要靠它算三档占比。放在事件里，就不必再拉一次整树。
                        "evidence": (node.get("meta") or {}).get("evidence") or ("textbook" if (node.get("meta") or {}).get("source_indexes") else "model"),
                        "role": "root" if parent_id is None else ("leaf" if not (node.get("children") or []) else "branch"),
                        "node_total": total_nodes,
                    }
                },
            )
        )
        order += 1
        for child in node.get("children") or []:
            queue.append((child, node_id, depth + 1))
    return events


def _mindmap_verify(content: dict[str, Any], source_count: int) -> dict[str, int]:
    """只裁剪越界依据、统计覆盖率，**不删节点**。

    长出来又被抹掉的节点会让用户以为生成失败；宁可留一个「暂无依据」的节点。
    """

    stats = {"node_count": 0, "grounded": 0, "trimmed": 0}
    root = _mindmap_root(content)
    if root is None:
        return stats
    stack = [root]
    while stack:
        node = stack.pop()
        stats["node_count"] += 1
        meta = node.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            node["meta"] = meta
        indexes = meta.get("source_indexes") or []
        kept = sorted({value for value in (_safe_index(item) for item in indexes) if 0 <= value < source_count})
        if len(kept) != len(indexes):
            stats["trimmed"] += len(indexes) - len(kept)
            meta["source_indexes"] = kept
        if kept:
            stats["grounded"] += 1
        stack.extend(node.get("children") or [])
    return stats


def _ppt_slide_events(content: dict[str, Any], step_index: int, total_steps: int, step_label: str) -> list[dict[str, Any]]:
    """把整份幻灯拆成逐页事件。

    与导图不同，这里**不做延迟节拍**：页的节奏交给前端（260ms/页），
    后端一次推完。原因是一页幻灯的渲染成本远低于一个导图节点，
    在后端 sleep 只会让断线重连的时间窗变长、更容易中途丢事件。
    """

    slides = content.get("slides") or []
    total = len(slides)
    events: list[dict[str, Any]] = []
    for position, slide in enumerate(slides):
        events.append(
            envelope(
                "slide",
                step_index,
                total_steps,
                step_label,
                {"slide": slide, "index": position, "total": total},
            )
        )
    return events


def _deck_stats(content: dict[str, Any]) -> dict[str, int]:
    """校色的产物：只统计，不改内容。"""

    slides = content.get("slides") or []
    return {
        "slides": len(slides),
        "bullets": sum(len(slide.get("bullets") or []) for slide in slides),
        "columns": sum(len(slide.get("columns") or []) for slide in slides),
        "grounded": sum(1 for slide in slides if slide.get("source_indexes")),
        "notes": sum(1 for slide in slides if slide.get("note")),
    }


def _norm(text: Any) -> str:
    return re.sub(r"[\s·：:，,。.、（）()【】\[\]“”\"'—-]+", "", str(text or ""))


def _same(a: Any, b: Any) -> bool:
    left, right = _norm(a), _norm(b)
    if not left or not right:
        return False
    return left == right or (min(len(left), len(right)) >= 3 and (left in right or right in left))


def _plan_pages(plan: Any) -> list[str]:
    return [unit.label.strip() for unit in plan_units(plan) if unit.label.strip()]


def _deck_verify(
    content: dict[str, Any],
    plan: Any,
    source_count: int,
    design: Any = None,
    resolutions: list[Any] | None = None,
) -> dict[str, Any]:
    """逐页核对：是否回应方案、是否重复、是否空页、引用是否越界、设计是否越界。

    只报问题，不改内容——「校验完偷偷重写」会让用户对成品失去信任。

    五类问题，前四类升级前就有（结构 / 页级依据），后两类是本版新增：

    - **容量**：块数、字数、版式不接受的块类型。超限只能由逐页回路定向重写。
    - **设计**：字号越阶、装饰预算、正文对比度。校验只报，不自动改设计。
    - **资产**：许可是否在白名单、CC-BY 是否有署名、生成图是否进了证据位。
    """

    from app.ppt.design import MIN_CONTRAST, THEME_HEX, check_page_capacity
    from app.ppt.select import LICENSE_ALLOWLIST, LICENSES_NEEDING_ATTRIBUTION

    slides = content.get("slides") or []
    planned = _plan_pages(plan)
    pages: list[dict[str, Any]] = []
    matched_plan: set[int] = set()
    duplicates: list[str] = []
    seen: dict[str, int] = {}
    capacity_issues: list[str] = []
    design_issues: list[str] = []
    asset_issues: list[str] = []
    page_designs = _design_pages_of(design)

    theme_hex = THEME_HEX.get(str((design or {}).get("theme_id") or content.get("theme_id") or "paper"), THEME_HEX["paper"])
    from app.ppt.design import contrast_ratio

    if contrast_ratio(theme_hex["fg"], theme_hex["bg"]) < MIN_CONTRAST:
        design_issues.append("正文对比度低于 4.5:1")

    for index, slide in enumerate(slides):
        title = str(slide.get("title") or "")
        key = _norm(title)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            duplicates.append(title[:40])
        plan_index = next((i for i, label in enumerate(planned) if _same(label, title)), None)
        if plan_index is not None:
            matched_plan.add(plan_index)
        indexes = [value for value in (_safe_index(item) for item in (slide.get("source_indexes") or [])) if 0 <= value < source_count]
        issues: list[str] = []
        if plan_index is None and planned and slide.get("kind") != "cover":
            issues.append("不在确认方案内")
        if not (slide.get("bullets") or slide.get("columns") or slide.get("blocks")) and slide.get("kind") not in ("cover", "section"):
            issues.append("页面没有内容")
        if len(indexes) != len(slide.get("source_indexes") or []):
            issues.append("引用越界")

        page_design = page_designs[index] if index < len(page_designs) else None
        if page_design is not None:
            blocks = [block for block in (slide.get("blocks") or []) if isinstance(block, dict)]
            problems = check_page_capacity(page_design, blocks)
            if problems:
                capacity_issues.append(f"第 {index + 1} 页：{'；'.join(problems)}")
                issues.extend(problems)
            if len(page_design.slots) > 3:
                design_issues.append(f"第 {index + 1} 页装饰超预算")

        for ref in slide.get("asset_refs") or []:
            if not isinstance(ref, dict):
                continue
            license_id = str(ref.get("license") or "")
            result = str(ref.get("result") or "")
            if license_id and license_id not in LICENSE_ALLOWLIST:
                asset_issues.append(f"第 {index + 1} 页：许可不在白名单（{license_id}）")
                issues.append("资产许可不合规")
            if license_id in LICENSES_NEEDING_ATTRIBUTION and not str(ref.get("attribution") or "").strip():
                asset_issues.append(f"第 {index + 1} 页：CC-BY 缺署名")
                issues.append("资产缺署名")
            if result == "generated" and str(ref.get("slot") or "") == "evidence":
                asset_issues.append(f"第 {index + 1} 页：生成图进证据位")
                issues.append("生成图不得进证据位")
            if result == "generated" and "AI 生成" not in str(ref.get("note") or ""):
                asset_issues.append(f"第 {index + 1} 页：生成图未标注")
                issues.append("生成图未标注")

        pages.append(
            {
                "index": index,
                "title": title[:40],
                "planned": plan_index is not None,
                "source": "knowledge_base" if indexes else "model",
                "issues": issues,
            }
        )
    missing = [label for index, label in enumerate(planned) if index not in matched_plan]
    unfilled = [
        f"{item.get('unit_id')}:{item.get('slot')}"
        for item in (resolutions or [])
        if str(item.get("result") or "") == "none" and str((item.get("requested") or {}).get("priority") or "") == "required"
    ]
    # 「排版太单一」是可以量出来的：用了几种版式、最长连续同类几页、方案要的版式缺了哪些。
    # 只报事实，不自动重排——用户签过的是方案，改版式得回到方案卡上改。
    variety = deck_conformance(content, deck_spec_of(plan))
    return {
        "slides": len(slides),
        "planned": len(planned),
        "matched": len(matched_plan),
        "missing": missing[:8],
        "duplicates": duplicates[:5],
        "empty": sum(1 for page in pages if "页面没有内容" in page["issues"]),
        "bad_sources": sum(1 for page in pages if "引用越界" in page["issues"]),
        "grounded": sum(1 for page in pages if page["source"] == "knowledge_base"),
        "pages": pages[:20],
        "design": {"checked": len(page_designs), "issues": design_issues[:8]},
        "capacity": {"issues": capacity_issues[:8]},
        "assets": {"issues": asset_issues[:8], "slot_unfilled": unfilled[:8]},
        "variety": variety,
    }


async def _revise_deck_pages(
    draft: dict[str, Any],
    stats: dict[str, Any],
    design: Any,
) -> dict[str, Any]:
    """把「这一页没写对」修掉。只改有问题的页，改完只重排这几页。

    素材类问题（许可、署名、生成图进证据位）不在这里修：那是 ``select`` 步定下的，
    改正文改不了它，硬改只会把「为什么用这张图」的理由抹掉。
    """

    pages = stats.get("pages") or []
    if not pages:
        return {"revised": [], "skipped": []}
    from app.ppt.revise import fixable, revise_pages

    if not any(fixable(list(page.get("issues") or [])) for page in pages):
        return {"revised": [], "skipped": []}
    settings = get_settings()
    try:
        return await revise_pages(
            draft.get("content") or {},
            pages,
            settings=settings,
            design=design,
            task_type="generate",
        )
    except Exception as error:  # noqa: BLE001 - 修不好就照原样交付，不能让收卷失败
        return {"revised": [], "skipped": [{"reason": f"error: {str(error).splitlines()[0][:120]}"}]}


def _design_pages_of(design: Any) -> list[Any]:
    """读设计单的 pages。坏数据返回空表，让校验退化成「只查旧四类」。"""

    from app.ppt.design import PageDesign

    if not isinstance(design, dict):
        return []
    result: list[Any] = []
    for item in design.get("pages") or []:
        try:
            result.append(PageDesign.model_validate(item))
        except Exception:  # noqa: BLE001 - 一条坏页不该让整份校验失效，
            # 但要占位：丢掉一条会让后面所有页与设计单错位。
            result.append(None)
    return result


_CITATION = re.compile(r"\[(\d+)\]")


def _items_verify(resource_type: str, content: dict[str, Any], source_count: int) -> dict[str, Any]:
    """试卷/漫剧的校验：只报能数清的事实，不假装做过深度审查。

    这两类没有逐页/逐枝的结构可对齐，能回答的是「出了几道题/几场戏、
    几条依据、有没有一条依据都没有」。把这三件事说清楚，比给一个
    看起来很专业的分数有用。
    """

    key = {"quiz": "questions", "story": "scenes"}.get(resource_type, "items")
    items = content.get(key)
    count = len(items) if isinstance(items, list) else 0
    fallback = str(content.get("fallback") or "")
    if fallback:
        note = f"{fallback}，共 {count} 条"
    elif not count:
        note = "这一版没产出条目，建议重来一次"
    elif source_count:
        note = f"共 {count} 条，均来自教材条目"
    else:
        note = f"共 {count} 条，但未取到教材依据，按通用知识生成"
    return {
        "items": count,
        "sources": source_count,
        "grounded": source_count > 0 and not fallback and count > 0,
        "note": note,
    }


def _doc_verify(blocks: list[dict[str, Any]], plan: Any, source_count: int, bad_citations: int = 0) -> dict[str, Any]:
    """逐节核对：章节是否回应方案、引文编号是否有效、依据档位是否说得清，
    以及**排版有没有落成**——整节都是大段落、方案要的表格一张没有，都要如实说。

    顺手把依据档位写回标题块的 ``props.evidence``——用户点开任一章节就能看到
    「这节是教材依据还是模型补充」，而不是整篇只标一个「有依据」。
    """

    planned = _plan_pages(plan)
    spec = doc_spec_of(plan) if isinstance(plan, dict) else None
    headings: list[str] = []
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    invalid = 0
    for block in blocks:
        block_type = str(block.get("type") or "")
        if block_type in ("heading", "heading1", "heading2", "heading3"):
            title = str(block.get("text") or "").strip()
            headings.append(title)
            current = {"heading": title[:60], "citations": 0, "source": "model"}
            sections.append(current)
            continue
        text = str(block.get("text") or "")
        for value in _CITATION.findall(text):
            if int(value) >= source_count:
                invalid += 1
            elif current is not None:
                current["citations"] += 1
        # 一个 quote 块只证明「这里贴了引文」，**不证明正文被它支持**。
        # 升级前它直接让本节变成 knowledge_base，等于贴一段原文就能冒充实证。

    for section in sections:
        section["source"] = "knowledge_base" if section["citations"] else "model"
    for block in blocks:
        if str(block.get("type") or "") in ("heading", "heading1", "heading2", "heading3"):
            title = str(block.get("text") or "").strip()
            hit = next((section for section in sections if section["heading"] == title[:60]), None)
            if hit is not None:
                props = block.setdefault("props", {})
                if isinstance(props, dict):
                    props["evidence"] = hit["source"]

    matched = sum(1 for label in planned if any(_same(label, heading) for heading in headings))
    missing = [label for label in planned if not any(_same(label, heading) for heading in headings)]
    # 排版核对按施工图算：方案说「第 3 节要一张对照表」，成品里没有就得说出来。
    conformance = doc_conformance(blocks, spec)
    return {
        "sections": len(sections),
        "planned": len(planned),
        "matched": matched,
        "missing": missing[:8],
        "bad_citations": bad_citations + invalid,
        "grounded": sum(1 for section in sections if section["source"] == "knowledge_base"),
        # 下面这几项是「排版落成了没有」。前端只说事实，不打分。
        "words": conformance["words"],
        "expected_words": conformance["expected_words"],
        "dense": conformance["dense"],
        "long_paragraphs": conformance["long_paragraphs"],
        "devices": conformance["devices"],
        "devices_missing": conformance["devices_missing"],
        "source_map": sections[:16],
    }


def _plan_coverage(content: dict[str, Any], plan: Any) -> dict[str, Any]:
    """方案分支在树里落地了几支。用于回答「我说的那几支画出来了吗」。"""

    planned = _plan_pages(plan)
    root = _mindmap_root(content)
    if root is None or not planned:
        return {"planned": len(planned), "matched": 0, "missing": planned[:8]}
    branches = [str(node.get("topic") or "") for node in (root.get("children") or [])]
    matched = [label for label in planned if any(_same(label, branch) for branch in branches)]
    return {
        "planned": len(planned),
        "matched": len(matched),
        "missing": [label for label in planned if label not in matched][:8],
    }


def _safe_index(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _pipeline_version(task: dict[str, Any]) -> int:
    """管线版本。读不到就是 1——在途的老任务必须继续按四步表跑完。"""

    try:
        return int((task.get("meta") or {}).get("pipeline_version") or 1)
    except (TypeError, ValueError):
        return 1


def _plan_gate(task: dict[str, Any]) -> bool:
    """方案确认只对**新**任务生效。

    在途的 v1 任务（含幻灯片的大纲确认流程）在本次升级前就已落库，
    它们的步骤语义不能改：给它们加一道「先确认方案」的停点，
    等于让用户手里跑到一半的任务永远跑不完。
    """

    return task["resource_type"] in ("ppt", "doc", "mindmap", "quiz", "story") and _pipeline_version(task) >= 2


def _gate_step(task: dict[str, Any]) -> str:
    """确认闸门落在哪一步：v3 起是 design（方案与设计同批确认），v2 是 plan。"""

    return gate_step(task["resource_type"], _pipeline_version(task))


def _design_for(resource_type: str, topic: str, params: dict[str, Any], plan: Any) -> dict[str, Any]:
    """给这一次请求出设计单。失败要能给「能用的默认」，不能拦住任务。"""

    try:
        return design_for(resource_type, topic, params, plan)
    except Exception:  # noqa: BLE001 - 设计失败退回最保守的版式，而不是让任务炸炉
        if resource_type == "ppt":
            return {
                "theme_id": str(params.get("theme") or "paper"),
                "paper": "xuan-plain",
                "type_pair": "song-kai",
                "ornament_set": "none",
                "pages": [],
                "rationale": ["设计步未能完成，整份按统一要点版式给出，可逐页改。"],
                "assumptions": ["受众与篇幅按默认值处理。"],
            }
        return {"rationale": ["设计步未能完成，按默认体裁与结构给出。"], "assumptions": []}


def _resolve_deck_slots(design: Any) -> list[Any]:
    """逐槽解析（确定性选型器）。**现在是兜底，不再是主路径。**"""

    if not isinstance(design, dict) or not design.get("pages"):
        return []
    from app.ppt.design import DeckDesign
    from app.ppt.select import resolve_slots

    try:
        return resolve_slots(DeckDesign.model_validate(design))
    except Exception:  # noqa: BLE001 - 设计单坏掉时宁可无图，也不要整份失败
        return []


async def _resolve_deck_slots_live(design: Any, settings: Any) -> tuple[list[Any], dict[str, Any]]:
    """先让模型自己找图，找不到的槽位再交给确定性选型器。

    为什么保留两套：**模型的不确定性只该影响「选哪张图」，不该影响「这份稿子出不出得来」**。
    循环拿到结果就用；没拿到（断网、没 key、模型不调工具、一轮就收尾）就整体回落到
    `select`，产出的仍是一份完整、可渲染、可导出的稿子——只是图位可能空着。
    """

    if not isinstance(design, dict) or not design.get("pages"):
        return [], {}
    from app.llm.router import ModelRouter
    from app.ppt.agent_loop import resolve_with_tools
    from app.ppt.design import DeckDesign, SlotResolution

    try:
        deck_design = DeckDesign.model_validate(design)
    except Exception:  # noqa: BLE001
        return _resolve_deck_slots(design), {}

    task_type = "ppt_assets" if "ppt_assets" in settings.llm_task_model_map else "generate"
    try:
        loop = await resolve_with_tools(deck_design, router=ModelRouter(settings=settings), task_type=task_type)
    except Exception:  # noqa: BLE001 - 循环炸了也不能拦住院子
        return _resolve_deck_slots(design), {}

    base = _resolve_deck_slots(design)
    loop_meta = {
        "rounds": loop.rounds,
        "calls": loop.calls,
        "generated": loop.generated,
        "assigned": len(loop.assignments),
        "notes": loop.notes[:4],
    }
    if not loop.assignments:
        return base, loop_meta

    covered = {(item.unit_id, item.slot) for item in loop.assignments}
    merged: list[Any] = []
    for raw in loop.resolutions():
        try:
            merged.append(SlotResolution.model_validate(raw))
        except Exception:  # noqa: BLE001 - 一条坏记录丢掉，不影响其余
            continue
    for item in base:
        data = item if isinstance(item, dict) else item.model_dump()
        if (str(data.get("unit_id") or ""), str(data.get("slot") or "")) in covered:
            continue
        merged.append(item)
    return merged, loop_meta


def _plan_topic(task: dict[str, Any], params: dict[str, Any], db_path: Any) -> str:
    """拟方案时看「用户到底要什么」，用完整那句而不是截断后的卡片标题。"""

    full = " ".join(str(params.get("query") or "").split())
    return (full[:200] or str(params.get("topic") or "").strip()[:200] or storage.node_name(task["node_id"], db_path))


#: v2 前三步之间的素材包缓存。
#:
#: 采撷一次要读 16 个 JSON + 若干条 SQL（约 30~300ms），三步各算一遍纯属浪费。
#: 只活在进程内：断线重连后重新算一次也没关系——它没有副作用。
_PACK_CACHE: dict[str, Any] = {}


async def _mindmap_prepare(task: dict[str, Any], step_id: str, done: dict[str, Any], db_path: Any, task_id: str, index: int, user_id: str) -> None:
    """v2 的前三步：辨明主题 → 采撷素材 → 谋篇立骨。

    三步的产出都写进 ``done``（会落库），所以断线重连时不会重算，
    也不会因为重算得到不同的骨架而出现「同一张图前后不一样」。
    """

    from app.mindmap.intents import classify as _classify

    params = task.get("meta", {}).get("params", {}) or {}
    topic = _intent_topic(task, params, db_path)

    if step_id == "resolve":
        if "intent" in done:
            return
        intent = _classify(topic)
        done["intent"] = {
            "kind": intent.kind,
            "archetype": intent.archetype,
            "structure_hint": intent.structure_hint,
            "topic": intent.topic,
            # label / note 是给对话栏直接用的一句话：前端不该自己拼中文。
            "label": intent.label,
            "note": _brief_note(intent.describe(), params),
            "brief": str(params.get("brief_note") or ""),
            "anchors": {
                "herbs": list(intent.anchors.herbs),
                "categories": list(intent.anchors.categories),
                "syndromes": list(intent.anchors.syndromes),
                "effects": list(intent.anchors.effects),
            },
            "degraded": intent.degraded,
        }
    elif step_id == "gather":
        if "evidence" in done:
            return
        pack = await _mindmap_pack(task_id, topic)
        done["evidence"] = pack.summary()
        if pack.notes:
            done["notes"] = list(pack.notes)
    elif step_id == "shape":
        # 骨架 = 用户确认过的分支。这里只把「将要画哪几支」落库并回报，
        # 真正的树在 compose 步生成；分支本身不再由规则原型决定。
        if "shape" in done:
            return
        pack = await _mindmap_pack(task_id, topic)
        units = ((done.get("plan") or {}).get("units")) or []
        skeleton = skeleton_from_plan(pack, units) if units else None
        base = skeleton or build_archetype_tree(pack)
        done["shape"] = {
            "branches": [branch.topic for branch in base.nodeData.children][:8],
            "from_plan": skeleton is not None,
        }
    else:
        return

    storage.update_task(task_id, user_id, current_step=index, steps_done=done, db_path=db_path)
    _cache_progress(task_id, index, done)


async def _mindmap_pack(task_id: str, topic: str):
    cached = _PACK_CACHE.get(task_id)
    if cached is not None:
        return cached
    from app.mindmap.intents import classify as _classify

    pack = await build_evidence_pack(_classify(topic))
    _PACK_CACHE[task_id] = pack
    return pack


def _mindmap_evidence_stats(content: dict[str, Any]) -> dict[str, int]:
    """三档依据的节点数。三数之和恒等于节点总数——这条是评测集的断言之一。"""

    stats = {"textbook": 0, "graph": 0, "model": 0}
    root = _mindmap_root(content)
    if root is None:
        return stats
    stack = [root]
    while stack:
        node = stack.pop()
        meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
        # 老数据没有 evidence 字段：有引用就算教材实证，没有就是推断。
        evidence = meta.get("evidence") or ("textbook" if meta.get("source_indexes") else "model")
        if evidence not in stats:
            evidence = "model"
        stats[evidence] += 1
        stack.extend(node.get("children") or [])
    return stats



async def stream_task(task_id: str, user_id: str, db_path: Any) -> AsyncIterator[dict[str, Any]]:
    task = storage.get_task(task_id, user_id, db_path)
    if task is None:
        yield envelope("error", 0, 0, "任务不存在", {"message": "未找到此炼制任务"})
        return
    # SQLite is authoritative across process restarts; this cache fills the
    # small window between an emitted event and a concurrent reader seeing it.
    cached = _PROGRESS_CACHE.get(task_id)
    if cached and int(cached["current_step"]) >= int(task["current_step"]):
        task["current_step"] = cached["current_step"]
        task["steps_done"] = dict(cached["steps_done"])
    steps = steps_for(task["resource_type"], _pipeline_version(task))
    total = len(steps)
    done = task["steps_done"]
    if task["status"] == "failed":
        yield envelope("error", task["current_step"], total, "炸炉", {"message": done.get("error", "炼制失败，请炸炉重炼")})
        return
    if task["status"] == "done":
        # 断线重连落到这里：任务早已完成，只能回头把资源的标题和依据补出来，
        # 否则前端刷新后产物卡上没有标题、也没有依据。
        sealed = storage.get_resource(str(task.get("resource_id") or ""), user_id, db_path=db_path) or {}
        yield envelope(
            "done",
            total,
            total,
            "丹成",
            {
                "resource_id": task["resource_id"],
                "title": str(sealed.get("title") or ""),
                "verify": (sealed.get("meta") or {}).get("verify") or {},
            },
        )
        return
    storage.update_task(task_id, user_id, status="running", db_path=db_path)
    start = int(task["current_step"])
    try:
        for index in range(start, total):
            spec = steps[index]
            label = random.choice(spec["variants"])
            # 取消只在工序边界生效：正在跑的那一步不打断（模型调用没法半路喊停，
            # 硬杀会留下半截写入），跑完这一步再停。已完成的工序不重跑。
            if done.get("cancel_requested"):
                done["error"] = "已取消（用户中止）"
                storage.update_task(task_id, user_id, status="failed", current_step=index, steps_done=done, db_path=db_path)
                cancel_event = envelope("error", index, total, "收炉", {"message": "已取消（用户中止）"})
                _remember(task_id, cancel_event)
                yield cancel_event
                return
            # A disconnect can happen after step_start reached the browser. Keep
            # that fact so a reconnection finishes this stage rather than replaying it.
            resuming_active_step = done.get("active_step") == index
            if not resuming_active_step:
                done["active_step"] = index
                storage.update_task(task_id, user_id, current_step=index, steps_done=done, db_path=db_path)
                _cache_progress(task_id, index, done)
                start_event = envelope("step_start", index, total, label, {"step_id": spec["id"]})
                _remember(task_id, start_event)
                yield start_event
            if spec["id"] == "plan" and _plan_gate(task):
                if not done.get("plan"):
                    task_params = (task.get("meta") or {}).get("params") or {}
                    topic = _plan_topic(task, task_params, db_path)
                    plan = await propose_resource_plan(task["resource_type"], topic, task_params)
                    done["plan"] = plan
                    storage.update_task(task_id, user_id, current_step=index, steps_done=done, db_path=db_path)
                    _cache_progress(task_id, index, done)
                plan_event = envelope("plan", index, total, label, {
                    "plan": done["plan"],
                    "resource_type": task["resource_type"],
                    "confirmed": bool(done.get("plan_confirmed")),
                    "steps": [{"id": step["id"], "label": step["label"]} for step in steps],
                })
                _remember(task_id, plan_event)
                yield plan_event
                # v3 起闸门后移到 design：方案与设计必须**同批**确认，否则用户
                # 会先确认一份看不见版式的方案，再看系统按自己的意思排出来。
                if _gate_step(task) == "plan" and not done.get("plan_confirmed"):
                    # 没有确认就不进生成步。status 回 pending、current_step 停在 plan，
                    # 重连时读同一份方案，不会重新拟一遍。
                    done["awaiting_plan"] = True
                    storage.update_task(task_id, user_id, status="pending", current_step=index, steps_done=done, db_path=db_path)
                    _cache_progress(task_id, index, done)
                    pause_event = envelope("pause", index, total, label, {"reason": "await_plan", "task_id": task_id})
                    _remember(task_id, pause_event)
                    yield pause_event
                    return
            if spec["id"] == "design" and _plan_gate(task):
                if not done.get("design"):
                    task_params = (task.get("meta") or {}).get("params") or {}
                    topic = _plan_topic(task, task_params, db_path)
                    done["design"] = _design_for(task["resource_type"], topic, task_params, done.get("plan"))
                    storage.update_task(task_id, user_id, current_step=index, steps_done=done, db_path=db_path)
                    _cache_progress(task_id, index, done)
                design_event = envelope("design", index, total, label, {
                    "plan": done.get("plan"),
                    "design": done.get("design"),
                    "resource_type": task["resource_type"],
                    "confirmed": bool(done.get("plan_confirmed")),
                    "steps": [{"id": step["id"], "label": step["label"]} for step in steps],
                })
                _remember(task_id, design_event)
                yield design_event
                if not done.get("plan_confirmed"):
                    # 方案与设计一起等确认。落库的键仍是 awaiting_plan，
                    # 因为确认接口读的就是它——换名字只会让在途页面失联。
                    done["awaiting_plan"] = True
                    storage.update_task(task_id, user_id, status="pending", current_step=index, steps_done=done, db_path=db_path)
                    _cache_progress(task_id, index, done)
                    pause_event = envelope("pause", index, total, label, {"reason": "await_plan", "task_id": task_id})
                    _remember(task_id, pause_event)
                    yield pause_event
                    return
            if spec["id"] == "select" and task["resource_type"] == "ppt":
                if not isinstance(done.get("slot_resolution"), list):
                    resolutions, loop_meta = await _resolve_deck_slots_live(done.get("design"), get_settings())
                    done["slot_resolution"] = [item.model_dump() for item in resolutions]
                    done["slot_summary"] = select_summary(resolutions)
                    # 循环的账（几轮、几次调用、生成几张、模型自己说了什么）也留一份。
                    # 出了「这页为什么配了这张图」的疑问，答案在这里，不在日志里。
                    if loop_meta.get("calls"):
                        done["art_loop"] = loop_meta
                    storage.update_task(task_id, user_id, current_step=index, steps_done=done, db_path=db_path)
                    _cache_progress(task_id, index, done)
                select_event = envelope("slot_resolution", index, total, label, {
                    "slots": done.get("slot_resolution") or [],
                    "summary": done.get("slot_summary") or {},
                })
                _remember(task_id, select_event)
                yield select_event
            if task["resource_type"] == "mindmap" and spec["id"] in ("resolve", "gather", "shape"):
                # v2 的前三步都是「真事」：辨问法、取素材、定骨架。
                # 三步的结果都落在 done 里，重连时读得到，不会重算。
                await _mindmap_prepare(task, spec["id"], done, db_path, task_id, index, user_id)
            if task["resource_type"] == "mindmap" and spec["id"] == "check":
                draft = done.get("draft")
                if draft is not None:
                    stats = _mindmap_verify(draft["content"], len(draft["sources"]))
                    evidence = _mindmap_evidence_stats(draft["content"])
                    coverage = _plan_coverage(draft["content"], done.get("plan"))
                    # 「方案说几层、画出来几层」必须能回答。层数不足时把差距原样
                    # 交出去，而不是让用户自己数——这是「说了五层只有三层」的
                    # 第二半修复：不做假，也不沉默。
                    expected = generation_params_from_plan(
                        done.get("plan"),
                        (task.get("meta") or {}).get("params") or {},
                    ).get("expected") or {}
                    fit = conformance(draft["content"], expected)
                    done["mindmap_verify"] = {**stats, "plan_coverage": coverage, "conformance": fit}
                    verify_event = envelope("mindmap_verify", index, total, label, {
                        "stats": dict(stats),
                        "evidence": evidence,
                        "plan_coverage": coverage,
                        "conformance": fit,
                        "intent": (done.get("intent") or {}).get("kind", ""),
                        "intent_label": (done.get("intent") or {}).get("label", ""),
                        "intent_note": (done.get("intent") or {}).get("note", ""),
                        "degraded": bool((done.get("intent") or {}).get("degraded")),
                        "template_id": str(draft["content"].get("template_id") or ""),
                    })
                    _remember(task_id, verify_event)
                    yield verify_event
            elif task["resource_type"] == "mindmap" and spec["id"] == "render":
                draft = done.get("draft")
                if draft is None:
                    content, sources = await _generate_task_content(task, db_path)
                    done["draft"] = {"content": content, "sources": sources}
                    storage.update_task(task_id, user_id, current_step=index, steps_done=done, db_path=db_path)
                    _cache_progress(task_id, index, done)
                for node_event in _mindmap_node_events(done["draft"]["content"], index, total, label):
                    _remember(task_id, node_event)
                    yield node_event
            elif task["resource_type"] == "mindmap" and spec["id"] == "compose":
                # 敷演才是真正调模型、真正花时间的那一步，前四步都不该碰它。
                if "draft" not in done:
                    content, sources = await _generate_task_content(task, db_path)
                    done["draft"] = {"content": content, "sources": sources}
                    storage.update_task(task_id, user_id, current_step=index, steps_done=done, db_path=db_path)
                    _cache_progress(task_id, index, done)
            if task["resource_type"] == "mindmap" and spec["id"] == "branch":
                # 导图在第二步就出整棵树：前 3 步原本是空转，用户要等到第 4 步
                # 才见到东西。前置到 branch 后，第一屏内容早到一步。
                if "draft" not in done:
                    content, sources = await _generate_task_content(task, db_path)
                    done["draft"] = {"content": content, "sources": sources}
                    # 先落库再推事件：重连时读到 draft 就不再重复调模型。
                    storage.update_task(task_id, user_id, current_step=index, steps_done=done, db_path=db_path)
                    _cache_progress(task_id, index, done)
                for node_event in _mindmap_node_events(done["draft"]["content"], index, total, label):
                    _remember(task_id, node_event)
                    yield node_event
            elif task["resource_type"] == "ppt" and spec["id"] == "outline":
                # 谋篇：先出大纲。一键模式继续往下走，大纲模式在这里停下等确认。
                task_params = task.get("meta", {}).get("params", {}) or {}
                ppt_params = PptParams.parse(task_params)
                if "outline" not in done:
                    topic = _task_topic(task, task_params, db_path)
                    docs = await retrieve_resource_docs(topic)
                    agent = PptAgent()
                    outline = await agent.outline(topic, docs, ppt_params)
                    done["outline"] = [item.model_dump(mode="json") for item in outline]
                    done["frame"] = agent.frame(topic, docs, ppt_params).model_dump(mode="json")
                    # 先落库再推事件：重连时读到 outline 就不再重复调模型。
                    storage.update_task(task_id, user_id, current_step=index, steps_done=done, db_path=db_path)
                    _cache_progress(task_id, index, done)
                outline_event = envelope("outline", index, total, label, {"outline": done["outline"], "total": len(done["outline"]), "deck_title": (done.get("frame") or {}).get("deck_title", "")})
                _remember(task_id, outline_event)
                yield outline_event
                if ppt_params.mode == "outline" and not task_params.get("confirmed_outline"):
                    # 暂停：status 回 pending、current_step 停在谋篇，active_step 保留，
                    # 重连时既不重跑前面的工序，也不会误判为已完成。
                    done["awaiting_outline"] = True
                    storage.update_task(task_id, user_id, status="pending", current_step=index, steps_done=done, db_path=db_path)
                    _cache_progress(task_id, index, done)
                    pause_event = envelope("pause", index, total, label, {"reason": "await_outline", "task_id": task_id})
                    _remember(task_id, pause_event)
                    yield pause_event
                    return
                done.pop("awaiting_outline", None)
            elif task["resource_type"] == "ppt" and spec["id"] == "compose":
                if "draft" not in done:
                    content, sources = await _generate_task_content(task, db_path)
                    done["draft"] = {"content": content, "sources": sources}
                    storage.update_task(task_id, user_id, current_step=index, steps_done=done, db_path=db_path)
                    _cache_progress(task_id, index, done)
                for slide_event in _ppt_slide_events(done["draft"]["content"], index, total, label):
                    _remember(task_id, slide_event)
                    yield slide_event
            elif task["resource_type"] == "doc" and spec["id"] == "compose":
                if "draft" not in done:
                    task_params = dict(task.get("meta", {}).get("params", {}) or {})
                    topic = _task_topic(task, task_params, db_path)
                    content, sources = await generate_content(
                        "doc",
                        topic,
                        params={**task_params, "confirmed_plan": done.get("plan")},
                    )
                    done["draft"] = {"content": content, "sources": sources}
                    storage.update_task(task_id, user_id, current_step=index, steps_done=done, db_path=db_path)
                    _cache_progress(task_id, index, done)
                for block_index, block in enumerate(done["draft"]["content"].get("blocks", [])):
                    block_event = envelope("block", index, total, label, {"block": block, "index": block_index})
                    _remember(task_id, block_event)
                    yield block_event
            elif task["resource_type"] in ("quiz", "story") and spec["id"] == "compose":
                if "draft" not in done:
                    content, sources = await _generate_task_content(task, db_path)
                    done["draft"] = {"content": content, "sources": sources}
                    storage.update_task(task_id, user_id, current_step=index, steps_done=done, db_path=db_path)
                    _cache_progress(task_id, index, done)
            elif task["resource_type"] in ("quiz", "story") and spec["id"] == "verify":
                draft = done.get("draft")
                if draft is not None:
                    stats = _items_verify(task["resource_type"], draft["content"], len(draft["sources"]))
                    done["items_verify"] = stats
                    verify_event = envelope("items_verify", index, total, label, {"stats": stats})
                    _remember(task_id, verify_event)
                    yield verify_event
            elif task["resource_type"] == "doc" and spec["id"] == "verify":
                draft = done.get("draft")
                if draft is not None:
                    result = _doc_verify(
                        draft["content"].get("blocks") or [],
                        done.get("plan"),
                        len(draft["sources"]),
                        int(draft["content"].get("bad_citations") or 0),
                    )
                    done["doc_verify"] = result
                    verify_event = envelope("doc_verify", index, total, label, {
                        "stats": {key: value for key, value in result.items() if key != "source_map"},
                        "source_map": result["source_map"],
                    })
                    _remember(task_id, verify_event)
                    yield verify_event
            elif task["resource_type"] == "ppt" and spec["id"] == "verify":
                draft = done.get("draft")
                if draft is not None:
                    stats = _deck_verify(
                        draft["content"],
                        done.get("plan"),
                        len(draft["sources"]),
                        done.get("design"),
                        done.get("slot_resolution"),
                    )
                    # 校验只报不改；但「容量超了 / 这页空了 / 引用越界」是这一页
                    # 没写对，不是整份稿子错了。只重写这几页，然后重验一次。
                    revised = await _revise_deck_pages(draft, stats, done.get("design"))
                    if revised.get("revised"):
                        done["deck_revise"] = revised
                        stats = _deck_verify(
                            draft["content"],
                            done.get("plan"),
                            len(draft["sources"]),
                            done.get("design"),
                            done.get("slot_resolution"),
                        )
                    done["deck_verify"] = stats
                    verify_event = envelope("deck_verify", index, total, label, {
                        "stats": {key: value for key, value in stats.items() if key != "pages"},
                        "pages": stats["pages"],
                        "revised": revised.get("revised") or [],
                        "theme_id": str(draft["content"].get("theme_id") or "paper"),
                    })
                    _remember(task_id, verify_event)
                    yield verify_event
            elif task["resource_type"] == "ppt" and spec["id"] == "polish":
                draft = done.get("draft")
                if draft is not None:
                    stats = _deck_stats(draft["content"])
                    done["deck_verify"] = stats
                    verify_event = envelope("deck_verify", index, total, label, {
                        "stats": dict(stats),
                        # 前端据此在收卷前切到生成时选定的皮肤，避免完成后二次闪跳。
                        "theme_id": str(draft["content"].get("theme_id") or "paper"),
                    })
                    _remember(task_id, verify_event)
                    yield verify_event
            elif spec["id"] == "distill" or (
                # v1 的老启发式：「倒数第二步就该出内容了」。9 步之后 total-2 落在
                # persist 上，会重复生成一次——v2 显式跳过（它有自己的 compose）。
                _pipeline_version(task) < 2
                and task["resource_type"] != "doc"
                and index == total - 2
            ):
                if "draft" not in done:
                    content, sources = await _generate_task_content(task, db_path)
                    done["draft"] = {"content": content, "sources": sources}
            if task["resource_type"] == "doc" and spec["id"] == "layout":
                draft = done.get("draft")
                if draft is None:
                    # 与 592 行的标题取词同源：params.topic 自由主题优先，缺省回落节点名。
                    doc_topic = _task_topic(task, task.get("meta", {}).get("params", {}) or {}, db_path)
                    content, sources = await generate_content("doc", doc_topic); draft={"content":content,"sources":sources}; done["draft"] = draft
                for block_index, block in enumerate(draft["content"].get("blocks", [])):
                    block_event = envelope("block", index, total, label, {"index": block_index, "block": block})
                    _remember(task_id, block_event)
                    yield block_event
            if task["resource_type"] == "mindmap" and spec["id"] == "verify":
                draft = done.get("draft")
                if draft is not None:
                    stats = _mindmap_verify(draft["content"], len(draft["sources"]))
                    done["mindmap_verify"] = stats
                    verify_event = envelope("mindmap_verify", index, total, label, {
                        "stats": dict(stats),
                        # 前端据此在收卷前切到生成时选定的图纸，避免完成后二次闪跳。
                        "template_id": str(draft["content"].get("template_id") or ""),
                    })
                    _remember(task_id, verify_event)
                    yield verify_event
            done_event = envelope("step_done", index, total, label, {"step_id": spec["id"]})
            _remember(task_id, done_event)
            done.pop("active_step", None)
            storage.update_task(task_id, user_id, current_step=index + 1, steps_done=done, db_path=db_path)
            _cache_progress(task_id, index + 1, done)
            yield done_event
        draft = done.get("draft")
        if draft is None:
            content, sources = await _generate_task_content(task, db_path); draft={"content":content,"sources":sources}; done["draft"] = draft
        resource_id = str(uuid4())
        task_params = task.get("meta", {}).get("params", {}) or {}
        title_topic = _task_topic(task, task_params, db_path)
        if task["resource_type"] == "mindmap":
            # 标题用根节点的字：那是「被问的那个东西」，比抄提问像标题。
            root = ((draft["content"] or {}).get("mindmap_data") or {}).get("nodeData") or {}
            title_topic = str(root.get("topic") or "").strip() or title_topic
        if task["resource_type"] == "story":
            # 「把麻黄汤证讲成一出短剧 · 漫剧」当标题，主题被动词框架裹住了。
            # 与方案卡共用同一份取词，确保「确认的是这个名字，成品也是这个名字」。
            title_topic = story_subject(title_topic)
        # 必须在 mindmap 改写 title_topic 之后再拼：拼早了导图标题会退回节点名。
        resource_title = f"{title_topic} · {_type_label(task['resource_type'])}"
        if task["resource_type"] == "doc":
            # 标题按文稿内容定，不照抄提问：把「请生成一份…的文档」当标题既不像
            # 标题，也看不出这篇到底在讲什么。模型没给标题时才退回旧规则。
            generated_title = str((draft["content"] or {}).get("title") or "").strip()
            resource_title = generated_title or f"{title_topic} · 讲解文档"
        resource = {"id": resource_id, "user_id": user_id, "node_id": task["node_id"], "type": task["resource_type"], "title": resource_title, "content": draft["content"], "sources": draft["sources"], "notes": "炼丹手记：依次完成本草检索、结构编排与溯源校验后封存此资源。", "status": "ready", "created_at": storage.now(), "meta": {"task_id": task_id, "generator": "langgraph-resource-adapter"}}
        resource["meta"]["params"] = task_params
        # 方案与校验结果跟着成品一起存：刷新、重开、导出时仍能回答
        # 「这一页/这一节/这一支的依据是什么」。
        if isinstance(done.get("plan"), dict):
            resource["meta"]["plan"] = done["plan"]
            resource["meta"]["plan_revision"] = int(done["plan"].get("revision") or 0)
        if task["resource_type"] == "ppt":
            # **冻结**：设计单、逐槽解析结果与工具循环的账一起落库。
            # 不落这三样，重开旧稿就只剩画面——「这一页为什么长这样」「这张图哪来的」
            # 全部要重新跑一遍模型才能回答，而重跑的结果和上次不一定一样。
            if isinstance(done.get("design"), dict):
                resource["meta"]["design"] = done["design"]
            if isinstance(done.get("slot_resolution"), list):
                resource["meta"]["slot_resolution"] = done["slot_resolution"]
            if isinstance(done.get("art_loop"), dict):
                resource["meta"]["art_loop"] = done["art_loop"]
            # 逐页排版的账也存：哪些页是模型自己排的、哪些退回了兜底、为什么。
            # 它和 slot_resolution 一起回答「这页为什么长这样」。
            scene_stats = (done.get("draft") or {}).get("content", {}).get("scene_stats")
            if isinstance(scene_stats, dict) and scene_stats.get("painted"):
                resource["meta"]["scene_art"] = scene_stats
            # 校验后重写过哪几页、为什么。与 verify 分开存：verify 说的是「稿子
            # 现在什么样」，revise 说的是「改过什么」，合并就回答不了后者。
            revise = done.get("deck_revise")
            if isinstance(revise, dict) and revise.get("revised"):
                resource["meta"]["revise"] = revise
            resource["meta"]["art_frozen"] = True
        if isinstance(done.get("deck_verify"), dict):
            resource["meta"]["verify"] = done["deck_verify"]
            resource["meta"]["source_map"] = [
                {"index": page["index"], "title": page["title"], "source": page["source"]}
                for page in done["deck_verify"].get("pages") or []
            ]
        if isinstance(done.get("doc_verify"), dict):
            resource["meta"]["verify"] = done["doc_verify"]
            resource["meta"]["source_map"] = done["doc_verify"].get("source_map") or []
        if isinstance(done.get("mindmap_verify"), dict):
            resource["meta"]["verify"] = done["mindmap_verify"]
        if isinstance(done.get("items_verify"), dict):
            # 试卷/漫剧只有这一份校验结果；产物卡要把它显示出来，
            # 所以 done 事件里也带一份（见下面 envelope）。
            resource["meta"]["verify"] = done["items_verify"]
        if task["resource_type"] == "graph":
            resource["meta"]["generator"] = "graph-agent"
        if task["resource_type"] == "mindmap":
            resource["meta"]["generator"] = "mindmap-agent"
            # 整树保存接口的乐观并发基线。与 doc 的 doc_version 同一套思路。
            resource["meta"]["mindmap_version"] = 1
            resource["meta"]["pipeline_version"] = _pipeline_version(task)
            # 问法与结构建议：刷新回来时能复原「这张图当初按什么组织」，
            # 也让「为什么长这样」有据可查，不必翻日志。
            intent = done.get("intent") or {}
            if intent:
                resource["meta"]["intent"] = intent.get("kind", "")
                resource["meta"]["intent_label"] = intent.get("label", "")
                resource["meta"]["intent_note"] = intent.get("note", "")
                # 用户那句额外要求（「必须四层」「要口诀」）也留一份：
                # 从历史里翻回来时，得看得出当时是按什么要求画的。
                resource["meta"]["brief_note"] = intent.get("brief", "")
                resource["meta"]["archetype"] = intent.get("archetype", "")
                resource["meta"]["degraded"] = bool(intent.get("degraded"))
                # 结构以确认过的方案为准：用户在面板里换了画法，画布打开时就该是那种
                # 画法，而不是又回到问法建议的那一种。
                plan_spec = (done.get("plan") or {}).get("spec") or {}
                resource["meta"]["mindmap_structure"] = str(
                    plan_spec.get("structure") or task_params.get("structure") or intent.get("structure_hint") or "logic-right"
                )
                if plan_spec.get("depth"):
                    resource["meta"]["mindmap_depth"] = int(plan_spec["depth"])
            theme = str(task_params.get("theme") or "")
            if theme:
                resource["meta"]["mindmap_theme"] = theme
        if task["resource_type"] == "ppt":
            resource["meta"]["generator"] = "ppt-agent"
            # 整份存盘接口的乐观并发基线，与 doc_version / mindmap_version 一致。
            resource["meta"]["ppt_version"] = 1
            # 记住当初的讲题：刷新回来时「再起一稿」不必让用户重新打一遍。
            resource["meta"]["topic"] = title_topic
            # 资源标题取封面那一页：方案卡上确认的是「从一味药开始，认识中药学」，
            # 成品就不能回到「用户原句 · 讲稿」——同一份稿两个名字最伤信任。
            slides = (draft["content"] or {}).get("slides") or []
            cover = next((slide for slide in slides if str(slide.get("kind") or "") == "cover"), None)
            deck_title = str((cover or {}).get("title") or (draft["content"] or {}).get("deck_title") or "").strip()
            # 资源卡上写「· ppt」太像字段名，中文交付物该有中文名。
            resource["title"] = f"{deck_title or title_topic} · 讲稿"[:120]
        resource["notes"] = f"炼丹手记：已完成本草检索、结构编排与溯源校验；收录 {len(draft['sources'])} 条依据。"
        storage.create_resource(resource, db_path)
        storage.update_task(task_id, user_id, status="done", current_step=total, steps_done=done, resource_id=resource_id, db_path=db_path)
        _cache_progress(task_id, total, done)
        _PACK_CACHE.pop(task_id, None)
        # 标题取落库后的那份，不取 ``resource_title`` 变量：导图/讲稿分支后面改过它。
        event = envelope("done", total, total, "丹成", {"resource_id": resource_id, "title": str(resource.get("title") or ""), "verify": resource["meta"].get("verify") or {}})
        _remember(task_id, event)
        yield event
    except Exception as exc:
        done["error"] = "炉火不稳，资源未能炼成，请炸炉重炼。"
        storage.update_task(task_id, user_id, status="failed", steps_done=done, db_path=db_path)
        _cache_progress(task_id, int(task["current_step"]), done)
        event = envelope("error", int(task["current_step"]), total, "炸炉", {"message": done["error"], "detail": str(exc)})
        _remember(task_id, event)
        yield event


def _remember(task_id: str, event: dict[str, Any]) -> None:
    _EVENT_CACHE.setdefault(task_id, []).append(event)


def _cache_progress(task_id: str, current_step: int, steps_done: dict[str, Any]) -> None:
    _PROGRESS_CACHE[task_id] = {"current_step": current_step, "steps_done": dict(steps_done)}


def reset_progress(task_id: str) -> None:
    """外部改写了任务进度后清掉缓存，否则下一次 stream 会拿旧值覆盖新进度。"""

    _PROGRESS_CACHE.pop(task_id, None)


async def _generate_task_content(task: dict[str, Any], db_path: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    params = dict(task.get("meta", {}).get("params", {}) or {})
    done = task.get("steps_done") or {}
    params["confirmed_plan"] = task.get("meta", {}).get("confirmed_plan") or done.get("plan")
    # 设计单与选型结果必须一起透传：生成期「只读已确认的东西」，
    # 不允许它自己再猜一遍版式，更不允许它编 asset_id。
    if isinstance(done.get("design"), dict):
        params["confirmed_design"] = done["design"]
    if isinstance(done.get("slot_resolution"), list):
        params["slot_resolution"] = done["slot_resolution"]
    if task["resource_type"] == "graph":
        return await generate_content("graph", storage.node_name(task["node_id"], db_path), node_id=task["node_id"], db_path=db_path, params=params)
    # 导图的主题就是「要画什么」，判问法要看完整那句；ppt 仍用短标题。
    topic = _intent_topic(task, params, db_path) if task["resource_type"] == "mindmap" else _task_topic(task, params, db_path)
    if task["resource_type"] in ("mindmap", "ppt", "doc", "quiz", "story"):
        # 这几种都要读「确认过的方案」与用户材料，因此必须透传 params。
        return await generate_content(task["resource_type"], topic, params=params)
    return await generate_content(task["resource_type"], topic)


def _type_label(resource_type: str) -> str:
    """资源卡上的类型后缀。

    「· mindmap」是字段名不是人话，导图统一写成「· 思维导图」；
    其余类型保持原样，免得动到别处的标题断言。
    """

    return {"mindmap": "思维导图", "quiz": "试卷", "story": "漫剧"}.get(resource_type, resource_type)


def _task_topic(task: dict[str, Any], params: dict[str, Any], db_path: Any) -> str:
    """自由主题优先，没有就退回节点名。

    自由主题的 node_id 固定落在 ``general``，若标题也取节点名，资源卡会全部
    显示「中药学总论」。
    """

    custom = str(params.get("topic") or "").strip()
    return custom or storage.node_name(task["node_id"], db_path)


def _brief_note(base: str, params: dict[str, Any]) -> str:
    """把「按「单味药」组织」与「四层 · 含口诀」拼成一句。

    用户在句子里提了额外要求时，图上必须看得出来是被听懂了——
    否则他只会觉得「说了四层，结果还是三层」。
    """

    brief = str(params.get("brief_note") or "").strip()
    return f"{base} · {brief}" if brief else base


def _intent_topic(task: dict[str, Any], params: dict[str, Any], db_path: Any) -> str:
    """辨问法与取素材用的那一句。

    标题用截断后的 ``topic``（卡片放不下长句），但**判问法要看全句**：
    「帮我画一张解表药和清热药在性味归经上有什么差异的图」截到 30 字就把
    「差异」切掉了，判成分类题，图直接长错。所以有 ``query`` 就优先用它。
    """

    full = " ".join(str(params.get("query") or "").split())
    return full[:120] or _task_topic(task, params, db_path)
