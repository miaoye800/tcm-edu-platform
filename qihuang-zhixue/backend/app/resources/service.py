"""Task runner. Progress is committed before each outward SSE event batch."""

from __future__ import annotations

import json
import random
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from app.agents.resources_graph import run_resource_generation
from app.agents.resource_context import retrieve_resource_docs
from app.agents.graph_intelligence import GraphAgent
from app.config import get_settings
from app.docs import DocAgent
from app.mindmap import MindmapAgent
from app.ppt import PptAgent
from app.ppt.params import PptParams
from app.ppt.schemas import OutlineSlide
from app.mindmap.intents import classify
from app.mindmap.evidence import build_evidence_pack
from app.resources.constants import ALCHEMY_STEPS, steps_for
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
        # 文稿是「按诉求写一篇」，不是「按药材条目复述字段」：先判断体裁（讲解 /
        # 学习计划 / 对比…）再落笔。药名命中知识库时以教材原文为事实来源，
        # 未命中就按通用知识写作——跨领域主题也必须出稿，不能回「无相关中药信息」。
        docs = await retrieve_resource_docs(topic)
        result = await DocAgent().generate(topic, docs)
        payload = {
            "blocks": result.blocks,
            "title": result.title,
            "doc_kind": result.doc_kind,
            "source_mode": result.meta.get("source_mode", "general"),
            "summary": result.summary,
            "sections": result.meta.get("sections", []),
        }
        return payload, result.sources
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


async def _generate_deck(topic: str, docs: list[Any], params: dict[str, Any]) -> Any:
    """铺一份幻灯：有「确认过的大纲」就按大纲敷演，否则谋篇+敷演一步到位。

    ``confirmed_outline`` 走的是任务 meta 里的 params，而不是另加一张表：
    确认动作本身就是「改写这次任务的输入」，放进 params 与「重新炼制」的语义一致。
    """

    ppt_params = PptParams.parse(params)
    agent = PptAgent()
    confirmed = params.get("confirmed_outline")
    if confirmed:
        try:
            outline = [OutlineSlide.model_validate(item) for item in confirmed]
        except Exception:
            outline = []
        if outline:
            return await agent.compose(topic, docs, ppt_params, outline=outline)
    return await agent.generate(topic, docs, ppt_params)


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
    elif step_id == "plan":
        if "plan" in done:
            return
        pack = await _mindmap_pack(task_id, topic)
        done["plan"] = {"slots": list(pack.branch_slots)[:8]}
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
        yield envelope("done", total, total, "丹成", {"resource_id": task["resource_id"]})
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
            if task["resource_type"] == "mindmap" and spec["id"] in ("resolve", "gather", "plan"):
                # v2 的前三步都是「真事」：辨问法、取素材、定骨架。
                # 三步的结果都落在 done 里，重连时读得到，不会重算。
                await _mindmap_prepare(task, spec["id"], done, db_path, task_id, index, user_id)
            if task["resource_type"] == "mindmap" and spec["id"] == "check":
                draft = done.get("draft")
                if draft is not None:
                    stats = _mindmap_verify(draft["content"], len(draft["sources"]))
                    evidence = _mindmap_evidence_stats(draft["content"])
                    done["mindmap_verify"] = stats
                    verify_event = envelope("mindmap_verify", index, total, label, {
                        "stats": dict(stats),
                        "evidence": evidence,
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
        # 必须在 mindmap 改写 title_topic 之后再拼：拼早了导图标题会退回节点名。
        resource_title = f"{title_topic} · {_type_label(task['resource_type'])}"
        if task["resource_type"] == "doc":
            # 标题按文稿内容定，不照抄提问：把「请生成一份…的文档」当标题既不像
            # 标题，也看不出这篇到底在讲什么。模型没给标题时才退回旧规则。
            generated_title = str((draft["content"] or {}).get("title") or "").strip()
            resource_title = generated_title or f"{title_topic} · 讲解文档"
        resource = {"id": resource_id, "user_id": user_id, "node_id": task["node_id"], "type": task["resource_type"], "title": resource_title, "content": draft["content"], "sources": draft["sources"], "notes": "炼丹手记：依次完成本草检索、结构编排与溯源校验后封存此资源。", "status": "ready", "created_at": storage.now(), "meta": {"task_id": task_id, "generator": "langgraph-resource-adapter"}}
        resource["meta"]["params"] = task_params
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
                resource["meta"]["mindmap_structure"] = str(task_params.get("structure") or intent.get("structure_hint") or "logic-right")
            theme = str(task_params.get("theme") or "")
            if theme:
                resource["meta"]["mindmap_theme"] = theme
        if task["resource_type"] == "ppt":
            resource["meta"]["generator"] = "ppt-agent"
            # 整份存盘接口的乐观并发基线，与 doc_version / mindmap_version 一致。
            resource["meta"]["ppt_version"] = 1
            # 记住当初的讲题：刷新回来时「再起一稿」不必让用户重新打一遍。
            resource["meta"]["topic"] = title_topic
            # 资源卡上写「· ppt」太像字段名，中文交付物该有中文名。
            resource["title"] = f"{title_topic} · 讲稿"
        resource["notes"] = f"炼丹手记：已完成本草检索、结构编排与溯源校验；收录 {len(draft['sources'])} 条依据。"
        storage.create_resource(resource, db_path)
        storage.update_task(task_id, user_id, status="done", current_step=total, steps_done=done, resource_id=resource_id, db_path=db_path)
        _cache_progress(task_id, total, done)
        _PACK_CACHE.pop(task_id, None)
        event = envelope("done", total, total, "丹成", {"resource_id": resource_id})
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
    params = task.get("meta", {}).get("params", {}) or {}
    if task["resource_type"] == "graph":
        return await generate_content("graph", storage.node_name(task["node_id"], db_path), node_id=task["node_id"], db_path=db_path, params=params)
    # 导图的主题就是「要画什么」，判问法要看完整那句；ppt 仍用短标题。
    topic = _intent_topic(task, params, db_path) if task["resource_type"] == "mindmap" else _task_topic(task, params, db_path)
    if task["resource_type"] in ("mindmap", "ppt"):
        # 只有 mindmap / ppt 透传 params：doc/quiz/story 的 generate_content 仍是两参，
        # test_resources.py 里替换它的 stub 也是两参签名。
        return await generate_content(task["resource_type"], topic, params=params)
    return await generate_content(task["resource_type"], topic)


def _type_label(resource_type: str) -> str:
    """资源卡上的类型后缀。

    「· mindmap」是字段名不是人话，导图统一写成「· 思维导图」；
    其余类型保持原样，免得动到别处的标题断言。
    """

    return "思维导图" if resource_type == "mindmap" else resource_type


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
