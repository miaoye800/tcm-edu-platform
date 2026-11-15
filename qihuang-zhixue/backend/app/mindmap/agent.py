from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.agents.resource_context import ResourceDoc
from app.agents.structured_output import structured_create
from app.config import Settings, get_settings
from app.llm.router import ModelRouter

from . import catalog
from .archetypes import MAX_LEAVES, build_archetype_tree
from .draft import (
    DEFAULT_DEPTH,
    MAX_DEPTH,
    MIN_DEPTH,
    MindmapDraft,
    clean_topic,
    draft_of_tree,
    evidence_of,
    normalize_draft,
    usable_root_title,
)
from .evidence import EVIDENCE_MODEL, EVIDENCE_TEXTBOOK, EvidencePack, build_evidence_pack
from .intents import MapIntent, classify
from .plan_tree import plan_branch_labels, skeleton_from_plan
from .prompts import EXPAND_SYSTEM_PROMPT, MINDMAP_SYSTEM_PROMPT, build_draft_prompt
from .rules import ALLOWED_TEMPLATE_IDS, choice_reason, content_feature, default_template
from .schemas import MindElixirData, MindmapContent, MindmapMeta, MindmapNode
from .spec import measure_tree

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
        # 确认过的方案就是这次的结构约束：分支以方案为准，教材原型只负责
        # 给同名分支配素材。没有方案（旧任务）才退回原型骨架。
        confirmed_plan = (params or {}).get("confirmed_plan") or {}
        planned_units = confirmed_plan.get("units", []) if isinstance(confirmed_plan, dict) else []
        planned = skeleton_from_plan(pack, planned_units) if planned_units else None
        if planned is not None:
            skeleton = planned
        feature = content_feature(topic, docs)
        candidates = tuple(feature["candidates"])
        data = await self._generate_data(topic, pack, skeleton, sources, params=params)
        # 依据最后统一定：不按「素材包空不空」整棵树一刀切，而是逐个节点问
        # 「这个名字库里有没有」。见 ``_ground_by_catalog`` 的注释。
        data = _ground_by_catalog(data, pack)
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
            return self._fallback_data(topic, pack, skeleton)
        if not (self.settings.deepseek_api_key or self.settings.zhipu_api_key):
            return self._fallback_data(topic, pack, skeleton)
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

        模型返回的一级分支按 topic 对上骨架；对不上时按位置兜底对上（模型偶尔
        会把「关键差异」写成「主要区别」，名字换了内容仍是那一段，不能整支丢），
        再对不上或该分支没给叶子时，保留骨架自己的叶子。骨架结构因此**不可能**
        被模型改坏，最坏情况是退化回确定性版本——这正是我们要的失败模式。

        ``depth`` 是用户在方案面板确认过的层数。**说几层就得画几层**：模型
        第一次没画够时补一次定向补深，补完还不够就把差距原样交回去（由
        ``service`` 写进 ``mindmap_verify`` 的 note），不让它静默缩水。
        """

        units = ((params or {}).get("confirmed_plan") or {}).get("units") or []
        plan_branches = plan_branch_labels(units) if isinstance(units, list) else []
        spec = _spec_param(params)
        depth = _depth_param(params)

        prompt = build_draft_prompt(
            pack.intent.archetype,
            # 域外主题没有教材目录可依，硬套「概念界定/与中药学的联系」反而更蠢：
            # 「智能体」和中药学没有联系。这类题让模型自己拟一级分支，
            # 它对这个概念的知识才是这里唯一的信息来源。
            # 但**用户确认过方案**时例外：分支由方案定，模型只负责把内容填进去。
            plan_branches or ([] if pack.degraded else [branch.topic for branch in skeleton.nodeData.children]),
            degraded=pack.degraded and not plan_branches,
            depth=depth,
            brief_lines=brief_lines(params),
            spec=spec,
            branch_covers=_branch_covers(units),
            # 「库里有什么」一并交出去：不给清单，模型只能凭常识编，
            # 编出来的内容既不能标教材，也不该标教材。
            catalog=pack.catalog,
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(_prompt_payload(topic, pack, sources, params), ensure_ascii=False)},
        ]
        try:
            draft = await structured_create(
                task_type=_mindmap_task_type(self.settings),
                messages=messages,
                response_model=MindmapDraft,
                mock_factory=lambda: MindmapDraft.from_elixir(skeleton),
                router=ModelRouter(settings=self.settings),
            )
        except Exception:
            # 模型/网络异常不该让整条炼制任务炸炉：确定性骨架已经是一张能看的图。
            return self._fallback_data(topic, pack)

        if pack.degraded and not plan_branches:
            # 域外且没有方案：没有骨架可合，整棵树都由模型给。仍走 normalize，
            # 这样 id 分配、层数上限、去重与截断与教材内的图完全一致。
            data = normalize_draft(
                draft,
                topic=topic,
                sources=sources,
                params={"depth": depth, "branches": _branches_param(params)},
            )
            if not data.nodeData.children:
                return self._fallback_data(topic, pack, skeleton)
            # 根节点是标题，引用不到任何资料：明确标成推断，
            # 否则前端三档占比会少算一个节点。
            data.nodeData.meta.evidence = EVIDENCE_MODEL
            return data

        merged = _merge_draft(skeleton, draft, source_count=len(sources), depth=depth)
        if not merged.nodeData.children:
            return self._fallback_data(topic, pack, skeleton)

        if depth >= 3 and _actual_depth(merged) < depth:
            repaired = await self._deepen_once(skeleton, merged, sources, messages, depth)
            if repaired is not None:
                merged = repaired
        return merged

    async def _deepen_once(
        self,
        skeleton: MindElixirData,
        merged: MindElixirData,
        sources: list[dict[str, str]],
        messages: list[dict[str, str]],
        depth: int,
    ) -> MindElixirData | None:
        """只补深度，不重排分支。一次为限。

        为什么要这一步：提示词里写了「每一支都要到 N 层」，模型仍常常只给两层。
        用户的判断标准很简单——「我选了 5 层，图上是不是 5 层」。与其解释，
        不如再要一次；要到还不行，就如实说明，不做假。
        """

        if not (self.settings.deepseek_api_key or self.settings.zhipu_api_key):
            return None
        shallow = [label for label, deep in measure_tree(merged.model_dump(mode="json")["nodeData"])["branch_depth"].items() if deep < depth - 1]
        if not shallow:
            return None
        instruction = (
            f"这张图现在只有 {_actual_depth(merged)} 层，用户确认的方案是 {depth} 层。"
            f"深度不够的是：{'、'.join(shallow[:8])}。\n"
            "请在**不改一级分支、不删已有条目**的前提下，把这些分支往下再分一层（或补齐分组下的条目），"
            f"让每一支都到 {depth} 层。资料不够就少写几条，不要编具体剂量与方名。只输出完整 JSON。"
        )
        try:
            draft = await structured_create(
                task_type=_mindmap_task_type(self.settings),
                messages=[
                    *messages,
                    {"role": "assistant", "content": json.dumps(MindmapDraft.from_elixir(merged).model_dump(mode="json"), ensure_ascii=False)},
                    {"role": "user", "content": instruction},
                ],
                response_model=MindmapDraft,
                mock_factory=lambda: MindmapDraft.from_elixir(merged),
                router=ModelRouter(settings=self.settings),
            )
        except Exception:  # noqa: BLE001 - 补深失败就用第一版，交给 verify 如实报差距
            return None
        again = _merge_draft(skeleton, draft, source_count=len(sources), depth=depth)
        if not again.nodeData.children:
            return None
        # 只有真的更深才采用：模型重写一遍却仍是原深度时，不该覆盖掉本来更好的那版。
        return again if _actual_depth(again) > _actual_depth(merged) else None

    def _fallback_data(self, topic: str, pack: EvidencePack, skeleton: MindElixirData | None = None) -> MindElixirData:
        """无模型时的树。

        有确认方案就按方案骨架出图——这正是「不伪造智能规划」的诚实版本：
        结构是用户定的，内容来自素材包，缺的地方留空而不是编。
        """

        data = skeleton or build_archetype_tree(pack)
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

    skeleton_branches = list(skeleton.nodeData.children)
    # 位置兜底：模型把「关键差异」写成「主要区别」时，标签对不上但内容是对的。
    # 只要两边支数一致，就按顺序认领——整支内容丢掉比认错一支严重得多。
    positional = len(draft.branches) == len(skeleton_branches)
    claimed: set[int] = set()

    children: list[MindmapNode] = []
    for index, branch in enumerate(skeleton_branches):
        candidate = _match_branch(branch.topic, draft.branches, by_topic, claimed, index, positional)
        if candidate is not None:
            claimed.add(id(candidate))
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


def _match_branch(topic: str, raws: list[Any], by_topic: dict[str, Any], claimed: set[int], index: int, positional: bool) -> Any | None:
    """把骨架上的这一支对上模型写的哪一段。对不上就认下一段，绝不重复认领。

    四道匹配，从严到宽：同名 → 去后缀再比 → 包含关系 → 按位置。
    「性味归经对比」与「性味归经」是同一支，只是模型多写了两个字；
    只要没被别的支认领过，就归它。
    """

    key = clean_topic(topic)
    exact = by_topic.get(key)
    if exact is not None and id(exact) not in claimed:
        return exact
    norm = _stem(key)
    for raw in raws:
        if id(raw) in claimed:
            continue
        if _stem(clean_topic(raw.topic)) == norm:
            return raw
    for raw in raws:
        if id(raw) in claimed:
            continue
        other = clean_topic(raw.topic)
        if len(other) >= 2 and (other in key or key in other):
            return raw
    if positional and index < len(raws) and id(raws[index]) not in claimed:
        return raws[index]
    return None


#: 分支名后面常见的「体裁词」。「性味归经对比」与「性味归经」指的是同一支。
_LABEL_STEMS = ("对比", "比较", "对照", "要点", "分析", "详解", "概览", "小结", "建议")


def _stem(label: str) -> str:
    text = label
    changed = True
    while changed and len(text) > 2:
        changed = False
        for suffix in _LABEL_STEMS:
            if text.endswith(suffix) and len(text) > len(suffix) + 1:
                text = text[: -len(suffix)].strip("的与及· ")
                changed = True
                break
    return text


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


def _ground_by_catalog(data: MindElixirData, pack: EvidencePack) -> MindElixirData:
    """逐个节点核对：这个名字库里到底有没有。

    以前依据是**整包判定**的——素材包为空 ⇒ ``source_count == 0`` ⇒
    ``evidence_of`` 把每个节点都判成 model。于是「中药学介绍」24 个节点全标
    「推断」，连「四气」「五味」「归经」「中药的毒性」这些教材原样的节名也是。
    那不是判断，是机械结果。

    现在改成按节点问一句：库里有这个名字吗？有就是教材实证并引用目录，
    没有才是推断。判据是 ``catalog.knows``（正名精确匹配），宁可标得保守——
    把「模型自己攒的短语」硬算成教材内容，是另一种骗人。

    只对**以教材目录为依据**的包生效（``catalog_subjects`` 非空）。其它题型的
    依据由素材与骨架决定，不在这里重判：那会把「功效」这类真教材节点误降级。
    """

    if not pack.catalog_subjects:
        return data
    root = data.nodeData
    # 自下而上：先把叶子判完，再判它们的父节点。分支名多半是模型分的组
    # （「总论·基础理论」「各论·解表清热泻下类」），书上没有这个名字；
    # 这一支到底有没有依据，取决于**它装的东西**是不是书上的。
    _mark_evidence(root, _ground_subtree(root))
    # 根节点是标题，不是一条知识：点到某门课就算有据可依。
    if catalog.covers_text(root.topic):
        _mark_evidence(root, True)
    return data


def _ground_subtree(node: MindmapNode) -> bool:
    """递归判依据。

    叶子看名字；分支先看它**自己**是不是书上的名字（「四气」「解表药」是教材的
    节与章，哪怕下面的条目是模型填的），不是的话再看它装的东西里书上的占不占多数
    （「总论·基础理论」这种分组名，书上没有，但装的是七章真内容）。
    """

    if not node.children:
        mine = catalog.covers_node(node.topic)
        _mark_evidence(node, mine)
        return mine
    grounded = sum(1 for child in node.children if _ground_subtree(child))
    mine = catalog.covers_node(node.topic) or grounded * 2 >= len(node.children)
    _mark_evidence(node, mine)
    return mine


def _mark_evidence(node: MindmapNode, grounded: bool) -> None:
    if grounded:
        node.meta.evidence = EVIDENCE_TEXTBOOK
        node.meta.source_indexes = [0]
        return
    node.meta.evidence = EVIDENCE_MODEL
    node.meta.source_indexes = []


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


def _spec_param(params: dict[str, Any] | None) -> dict[str, Any]:
    """方案里那份已经由用户确认过的施工图。没有方案的老任务就是空。"""

    spec = (params or {}).get("spec")
    return spec if isinstance(spec, dict) else {}


def _branch_covers(units: Any) -> dict[str, list[str]]:
    """「这一支必须覆盖什么」。方案单元的 detail 就是它的落点。"""

    covers: dict[str, list[str]] = {}
    for unit in units or []:
        if not isinstance(unit, dict):
            continue
        label = clean_topic(str(unit.get("label") or ""))
        if not label:
            continue
        raw = str(unit.get("must_cover") or unit.get("detail") or unit.get("intent") or "")
        items = [item.strip() for item in re.split(r"[；;、,，]", raw) if item.strip()]
        if items:
            covers[label] = items[:5]
    return covers


def _actual_depth(data: MindElixirData) -> int:
    """这棵树实际画到第几层（根算第 1 层）。"""

    return int(measure_tree(data.model_dump(mode="json")["nodeData"])["depth"])


def _prompt_payload(
    topic: str,
    pack: EvidencePack,
    sources: list[dict[str, str]],
    params: dict[str, Any] | None,
) -> dict[str, Any]:
    """只传结构化字段，不传整段 evidence_text——后者会把 prompt 撑大三倍。"""

    params = params or {}
    spec = _spec_param(params)
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
    if spec:
        # 方案里确认过的受众/组织方式/重点：模型在写叶子时也要看得见。
        payload["spec"] = {
            "audience": spec.get("audience", ""),
            "organization": spec.get("organization", ""),
            "depth": spec.get("depth"),
            "leaves_min": spec.get("leaves_min"),
            "emphasis": spec.get("emphasis") or [],
            "must_answer": spec.get("must_answer") or [],
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
    units = ((params.get("confirmed_plan") or {}) or {}).get("units")
    if isinstance(units, list) and units:
        # 用户确认过的分支要一起交给模型：它填内容，不改结构。
        payload["plan_branches"] = [
            {"label": str(unit.get("label") or ""), "intent": str(unit.get("intent") or ""), "detail": str(unit.get("detail") or "")}
            for unit in units[:8]
            if isinstance(unit, dict)
        ]
    materials = params.get("materials")
    if isinstance(materials, list) and materials:
        payload["user_material"] = [str(item)[:300] for item in materials[:3]]
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
