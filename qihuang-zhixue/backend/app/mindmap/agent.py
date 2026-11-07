from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.agents.resource_context import ResourceDoc
from app.agents.structured_output import structured_create
from app.config import Settings, get_settings
from app.llm.router import ModelRouter

from .archetypes import MAX_LEAVES, build_archetype_tree
from .draft import (
    DEFAULT_DEPTH,
    MAX_DEPTH,
    MIN_DEPTH,
    MindmapDraft,
    clean_topic,
    evidence_of,
    normalize_draft,
    usable_root_title,
)
from .evidence import EVIDENCE_MODEL, EvidencePack, build_evidence_pack
from .intents import MapIntent, classify
from .prompts import EXPAND_SYSTEM_PROMPT, MINDMAP_SYSTEM_PROMPT, build_draft_prompt
from .rules import ALLOWED_TEMPLATE_IDS, choice_reason, content_feature, default_template
from .schemas import MindElixirData, MindmapContent, MindmapMeta, MindmapNode

LLMCall = Callable[[list[dict[str, str]]], Awaitable[str]]


class MindmapAgent:
    """Small LangGraph-style agent boundary: retrieval in, validated content out.

    这一版把「检索」换成了「按问法取素材包」，把「一次生成整棵树」换成了
    「后端定骨架 + 模型填叶子」。两条理由：

    - 骨架是**可以确定**的（分类题=教材目录，辨别题=对比维度），让模型去猜
      只会猜错，而且每次猜得还不一样；
    - 叶子是**真的需要模型**的（把字段写成学生看得懂的短语）。

    没有模型时走 ``build_archetype_tree``：凭真实素材长出可读的图，
    一片「待补充资料」都不会出现。
    """

    def __init__(self, *, settings: Settings | None = None, llm_call: LLMCall | None = None) -> None:
        self.settings = settings or get_settings()
        self.llm_call = llm_call

    async def generate(
        self,
        topic: str,
        docs: list[ResourceDoc],
        profile: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> MindmapContent:
        intent = classify(topic)
        pack = await build_evidence_pack(intent, docs=docs)
        sources = pack.sources()
        skeleton = build_archetype_tree(pack)
        feature = content_feature(topic, docs)
        candidates = tuple(feature["candidates"])
        data = await self._generate_data(topic, pack, skeleton, sources, params=params)
        template_id = default_template(candidates, profile)
        # An injected/available model may choose only after deterministic filtering.
        if self.llm_call and len(candidates) > 1:
            raw = await self.llm_call([{"role": "user", "content": json.dumps({"candidates": candidates, "topic": topic}, ensure_ascii=False)}])
            selected = json.loads(raw).get("template_id")
            if selected in candidates:
                template_id = selected
        if template_id not in ALLOWED_TEMPLATE_IDS:
            raise ValueError("template is outside the approved template enum")
        return MindmapContent(
            template_id=template_id,
            choice_reason=choice_reason(feature, template_id, profile),
            mindmap_data=data,
            sources=sources,
            intent=intent.kind,
            archetype=intent.archetype,
            structure_hint=intent.structure_hint,
            degraded=pack.degraded,
        )

    async def expand(self, topic: str, docs: list[ResourceDoc], parent: MindmapNode, existing_topics: set[str]) -> list[MindmapNode]:
        # Structured fallback is also the keyless behavior and guarantees grounding.
        candidates = self._children_for_doc(docs[0] if docs else None, parent.topic, len(existing_topics))
        return [node for node in candidates if node.topic not in existing_topics]

    # -- 生成 ---------------------------------------------------------------

    async def _generate_data(
        self,
        topic: str,
        pack: EvidencePack,
        skeleton: MindElixirData,
        sources: list[dict[str, str]],
        params: dict[str, Any] | None = None,
    ) -> MindElixirData:
        # 注入式 llm_call 是测试与继承用法，逐行保留原有行为。
        if self.llm_call is not None:
            if self.settings.deepseek_api_key or self.settings.zhipu_api_key:
                raw = await self.llm_call([
                    {"role": "system", "content": MINDMAP_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps({"topic": topic, "docs": pack.herbs, "sources": sources}, ensure_ascii=False)},
                ])
                try:
                    return MindElixirData.model_validate(json.loads(raw))
                except Exception:
                    pass
            return self._fallback_data(topic, pack)
        if not (self.settings.deepseek_api_key or self.settings.zhipu_api_key):
            return self._fallback_data(topic, pack)
        return await self._compose_with_model(topic, pack, skeleton, sources, params)

    async def _compose_with_model(
        self,
        topic: str,
        pack: EvidencePack,
        skeleton: MindElixirData,
        sources: list[dict[str, str]],
        params: dict[str, Any] | None,
    ) -> MindElixirData:
        """敷演：在给定骨架上填叶子。

        模型返回的一级分支按 topic 对上骨架；对不上或该分支没给叶子时，
        保留骨架自己的叶子。骨架结构因此**不可能**被模型改坏，
        最坏情况是退化回确定性版本——这正是我们要的失败模式。
        """

        prompt = build_draft_prompt(
            pack.intent.archetype,
            # 域外主题没有教材目录可依，硬套「概念界定/与中药学的联系」反而更蠢：
            # 「智能体」和中药学没有联系。这类题让模型自己拟一级分支，
            # 它对这个概念的知识才是这里唯一的信息来源。
            [] if pack.degraded else [branch.topic for branch in skeleton.nodeData.children],
            degraded=pack.degraded,
            depth=_depth_param(params),
            brief_lines=brief_lines(params),
        )
        try:
            draft = await structured_create(
                task_type=_mindmap_task_type(self.settings),
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(_prompt_payload(topic, pack, sources, params), ensure_ascii=False)},
                ],
                response_model=MindmapDraft,
                mock_factory=lambda: MindmapDraft.from_elixir(skeleton),
                router=ModelRouter(settings=self.settings),
            )
        except Exception:
            # 模型/网络异常不该让整条炼制任务炸炉：确定性骨架已经是一张能看的图。
            return self._fallback_data(topic, pack)

        if pack.degraded:
            # 域外：没有骨架可合，整棵树都由模型给。仍走 normalize，
            # 这样 id 分配、层数上限、去重与截断与教材内的图完全一致。
            data = normalize_draft(
                draft,
                topic=topic,
                sources=sources,
                params={"depth": _depth_param(params), "branches": _branches_param(params)},
            )
            if not data.nodeData.children:
                return self._fallback_data(topic, pack)
            # 根节点是标题，引用不到任何资料：明确标成推断，
            # 否则前端三档占比会少算一个节点。
            data.nodeData.meta.evidence = EVIDENCE_MODEL
            return data

        merged = _merge_draft(skeleton, draft, source_count=len(sources), depth=_depth_param(params))
        if not merged.nodeData.children:
            return self._fallback_data(topic, pack)
        return merged

    def _fallback_data(self, topic: str, pack: EvidencePack) -> MindElixirData:
        data = build_archetype_tree(pack)
        _stamp_sources(data.nodeData, len(pack.sources()))
        return data

    def _children_for_doc(self, doc: ResourceDoc | None, parent_topic: str, offset: int) -> list[MindmapNode]:
        if not doc:
            return []
        fields: list[tuple[str, list[str]]] = [
            ("性味归经", ["、".join([doc["nature"], *doc["flavor"], *doc["meridians"]])]),
            ("核心功效", list(doc["effects"])[:4]),
            ("主治要点", [item["syndrome"] for item in doc["indications"]][:4]),
            ("用量注意", [str(doc["dosage"].get("text", "常规用量")), *list(doc["cautions"])[:2]]),
        ]
        nodes: list[MindmapNode] = []
        for index, (label, details) in enumerate(fields):
            children = [
                MindmapNode(id=f"n-{offset}-{index}-{child_index}", topic=item[:30], meta=MindmapMeta(source_indexes=[0], evidence="textbook"))
                for child_index, item in enumerate(details)
                if item
            ]
            nodes.append(MindmapNode(id=f"n-{offset}-{index}", topic=label, direction=index % 2, meta=MindmapMeta(source_indexes=[0], evidence="textbook"), children=children))
        return nodes


def _merge_draft(skeleton: MindElixirData, draft: MindmapDraft, *, source_count: int, depth: int = 3) -> MindElixirData:
    """骨架 + 模型内容 = 最终树。

    ``MAX_LEAVES`` 在这里再截一次：模型给 12 个叶子也照收，但只留 10 个，
    与 ``MindmapNode.children`` 的上限留出余量（用户还要能手动加）。

    ``depth`` 是用户要的层数，口径与提示词一致：**根算第一层**。
    骨架的一级分支因此是第 2 层，它的 children 从第 3 层起算——
    这里曾经按第 2 层传下去，于是「要 3 层」画出 4 层、「要 4 层」画出 5 层，
    两边差一层，用户只会觉得「说了不算」。
    """

    by_topic: dict[str, Any] = {}
    for branch in draft.branches:
        key = clean_topic(branch.topic)
        if key and key not in by_topic:
            by_topic[key] = branch

    children: list[MindmapNode] = []
    for branch in skeleton.nodeData.children:
        candidate = by_topic.get(branch.topic) or by_topic.get(clean_topic(branch.topic))
        leaves = branch.children
        if candidate is not None and candidate.children:
            built = _model_children(
                candidate.children,
                prefix=branch.id,
                level=3,  # 根=1、分支=2，所以分支的孩子是第 3 层
                depth=depth,
                source_count=source_count,
            )
            if built:
                leaves = built
        children.append(branch.model_copy(update={"children": leaves}))

    root = skeleton.nodeData
    # 模型给的标题通常比「抄提问」像标题。只在它像标题时才采信：
    # 过长（等于又把整句抄一遍）或过短（一个「图」字）都退回骨架自己的判断。
    title = usable_root_title(draft.root_topic)
    if title:
        root = root.model_copy(update={"topic": title})
    return MindElixirData(nodeData=root.model_copy(update={"children": children}))


def _model_children(raws: list[Any], *, prefix: str, level: int, depth: int, source_count: int) -> list[MindmapNode]:
    """把模型的一层内容收成节点，深度够就继续往下收。"""

    nodes: list[MindmapNode] = []
    for index, raw in enumerate(raws):
        node = _model_leaf(raw, prefix, index, source_count, level=level, depth=depth)
        if node is None:
            continue
        nodes.append(node)
        if len(nodes) >= MAX_LEAVES:
            break
    return nodes


def _model_leaf(raw: Any, branch_id: str, index: int, source_count: int, *, level: int = 3, depth: int = 3) -> MindmapNode | None:
    topic = clean_topic(raw.topic)
    if not topic:
        return None
    indexes = sorted({value for value in (int(item) for item in raw.source_indexes if isinstance(item, int)) if 0 <= value < source_count})[:3]
    node_id = f"{branch_id}-{index}"
    # level 是这一节点在第几层（根=1、分支=2，所以传进来最小是 3）。
    children: list[MindmapNode] = []
    if level < depth and getattr(raw, "children", None):
        children = _model_children(raw.children, prefix=node_id, level=level + 1, depth=depth, source_count=source_count)
    return MindmapNode(
        id=node_id,
        topic=topic,
        meta=MindmapMeta(source_indexes=indexes, evidence=evidence_of(raw.evidence, indexes)),
        children=children,
    )


def _stamp_sources(node: MindmapNode, source_count: int) -> None:
    """给确定性节点补上依据下标。

    素材包里的每一条都来自本地知识库，所以「非推断」的节点统一引第 0 条——
    第 0 条一定是主素材（那味药 / 那一类 / 那条关系）的出处。
    推断节点保持空，前端据此打角标、底栏据此算占比。
    """

    stack = [node]
    while stack:
        current = stack.pop()
        if current.meta.evidence == "model" or source_count <= 0:
            current.meta.source_indexes = []
        elif not current.meta.source_indexes:
            current.meta.source_indexes = [0]
        stack.extend(current.children)


def _mindmap_task_type(settings: Settings) -> str:
    """现网映射表没有 mindmap 键，落回 generate（Flash 档），不新增配置项。"""

    return "mindmap" if "mindmap" in settings.llm_task_model_map else "generate"


def _depth_param(params: dict[str, Any] | None) -> int:
    """用户要的层数。越界钳住，解析失败回默认——提示词与合并两边必须用同一个数。"""

    try:
        value = int((params or {}).get("depth"))
    except (TypeError, ValueError):
        return DEFAULT_DEPTH
    return min(max(value, MIN_DEPTH), MAX_DEPTH)


def _branches_param(params: dict[str, Any] | None) -> int:
    """一级分支上限。域外流程整棵树由模型给，也受同一个上限约束。"""

    try:
        value = int((params or {}).get("branches"))
    except (TypeError, ValueError):
        return 6
    return min(max(value, 3), 8)


def brief_lines(params: dict[str, Any] | None) -> list[str]:
    """``brief.parse_brief`` 已经翻译好的「本次额外要求」，逐条喂给模型。"""

    raw = (params or {}).get("brief")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item).strip()][:8]


def _prompt_payload(
    topic: str,
    pack: EvidencePack,
    sources: list[dict[str, str]],
    params: dict[str, Any] | None,
) -> dict[str, Any]:
    """只传结构化字段，不传整段 evidence_text——后者会把 prompt 撑大三倍。"""

    params = params or {}
    payload: dict[str, Any] = {
        "topic": topic,
        "intent": pack.intent.kind,
        "intent_label": pack.intent.label,
        "depth": params.get("depth", 3),
        "style": params.get("style", "understand"),
        "focus": params.get("focus", ""),
        "slots": pack.branch_slots,
        "sources": [
            {"index": index, "name": item.get("name", ""), "ref": item.get("ref", ""), "quote": str(item.get("quote", ""))[:80]}
            for index, item in enumerate(sources)
        ],
        "herbs": [_compact_doc(doc) for doc in pack.herbs[:6]],
    }
    if pack.effects:
        payload["effects"] = pack.effects
    if pack.edges:
        payload["edges"] = pack.edges[:24]
    if pack.syndromes:
        payload["syndromes"] = [
            {key: value for key, value in item.items() if key != "sources"}
            for item in pack.syndromes[:2]
        ]
    if pack.nodes:
        payload["tree"] = pack.nodes[:24]
    if pack.notes:
        payload["notes"] = pack.notes
    return payload


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
            {"purpose": item.get("purpose", ""), "formula": item.get("formula_ref", "")}
            for item in doc["combinations"][:4]
        ],
        "differentiation": [
            {"compare_name": item.get("compare_name", ""), "key_points": item.get("key_points", "")}
            for item in doc["differentiation"][:3]
        ],
        "dosage": str(doc["dosage"].get("text", "")),
        "cautions": doc["cautions"][:3],
    }
