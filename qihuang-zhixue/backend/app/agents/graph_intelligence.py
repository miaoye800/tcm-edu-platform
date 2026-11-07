"""Evidence-first GraphAgent used by persisted `type=graph` resources."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.agents.graph_contract import GraphContent, GraphEdgeContent, GraphNodeContent, GraphRelationDraft, GraphSource, RelationType
from app.agents.prompts import GRAPH_AGENT_SYSTEM_PROMPT
from app.agents.resource_context import ResourceDoc, _herb_to_doc, herb_name_by_id, retrieve_resource_docs
from app.graph_storage import get_graph, node_exists, put_entity, put_graph, put_relations
from app.kb.ingest import load_formulas, load_herbs
from app.llm.clients import MockClient
from app.llm.router import ModelRouter
from app.profile.store import DB_PATH

logger = logging.getLogger(__name__)
_relation_list = TypeAdapter(list[GraphRelationDraft])

def formula_display_name(formula_ref: str) -> str:
    """方剂 id → 中文名。**查主数据，不再维护第二份映射表。**

    这里原有一张 40 条的硬编码 ``FORMULA_NAMES``，和
    ``data/formulas/_index.json`` 构成两份真相：新增方剂忘了同步，图上就会直接
    显示 ``formula_xxx`` 这种原始 id。现在只留一条查表路径，查不到才退回剥前缀。
    """

    ref = (formula_ref or "").strip()
    if not ref:
        return ""
    entry = load_formulas().get(ref)
    if entry:
        name = str(entry.get("name") or "").strip()
        if name:
            return name
    return ref.removeprefix("formula_")


def entity_id(kind: str, name: str) -> str:
    return f"{kind}_{hashlib.sha1(name.encode('utf-8')).hexdigest()[:14]}"


def source_for(doc: ResourceDoc, excerpt: str) -> GraphSource:
    raw = doc['sources'][0] if doc['sources'] else {}
    return GraphSource(
        出处=str(raw.get('ref') or '本地中药学知识库'),
        原文摘录=excerpt[:180],
        定位=str(raw.get('ref') or doc['name']),
    )


class GraphAgent:
    """Build a compact, cacheable relation subgraph around one herb node."""

    def __init__(self, *, llm_call: Callable[[str], Awaitable[str]] | None = None) -> None:
        self._llm_call = llm_call
        self._herbs_by_name: dict[str, Any] | None = None

    async def generate(self, *, center_id: str, center_name: str, db_path: Path = DB_PATH) -> GraphContent:
        cached = get_graph(center_id, db_path)
        if cached is not None:
            return GraphContent.model_validate(cached)

        center = self._doc_by_name(center_name)
        if self._llm_enabled():
            docs = await retrieve_resource_docs(center_name, top_k=8)
            center = next((doc for doc in docs if doc['name'] == center_name), center)
        else:
            docs = self._local_related_docs(center)
        if center is None:
            content = GraphContent(center_id=center_id, nodes=[GraphNodeContent(id=center_id, name=center_name, type='herb', brief='知识库暂未覆盖')], edges=[], sources=[])
            put_graph(center_id, content.model_dump(mode='json'), _now(), db_path)
            return content

        rules, nodes = self._rule_relations(center_id, center, docs)
        enhanced = await self._llm_relations(center_id, center, docs)
        relations = self._dedupe(rules + enhanced)
        content = self._content(center_id, center, nodes, relations)
        created_at = _now()
        put_relations([item.model_dump(mode='json') for item in relations], created_at, db_path)
        for node in content.nodes:
            knowledge_node_id = center_id if node.id == center_id and node_exists(center_id, db_path) else None
            put_entity(node.id, node.name, node.type, created_at, knowledge_node_id=knowledge_node_id, db_path=db_path)
        put_graph(center_id, content.model_dump(mode='json'), created_at, db_path)
        return content

    def build_rules_only(self, *, center_id: str, center_name: str) -> tuple[GraphContent, list[GraphRelationDraft]] | None:
        """规则层构图：不调用 LLM、不读写数据库。

        离线预计算脚本与缓存缺失时的降级路径共用这一入口，保证两者结构一致。
        返回 ``None`` 表示知识库里没有这味药。
        """
        center = self._doc_by_name(center_name)
        if center is None:
            return None
        docs = self._local_related_docs(center)
        rules, nodes = self._rule_relations(center_id, center, docs)
        relations = self._dedupe(rules)
        return self._content(center_id, center, nodes, relations), relations

    def _doc_by_name(self, name: str) -> ResourceDoc | None:
        # 构建全图时会被调用上千次，知识库 JSON 只读一次即可。
        if self._herbs_by_name is None:
            self._herbs_by_name = {herb.name: herb for herb in load_herbs()}
        herb = self._herbs_by_name.get(name)
        if herb is None:
            return None
        return _herb_to_doc(herb, [source.model_dump(mode='json') for source in herb.sources])

    def _local_related_docs(self, center: ResourceDoc | None) -> list[ResourceDoc]:
        if center is None:
            return []
        related = [self._doc_by_name(item.name) for item in load_herbs() if item.name != center['name'] and item.subcategory == center['subcategory']]
        return [center, *(item for item in related if item is not None)][:8]

    def _llm_enabled(self) -> bool:
        if self._llm_call is not None:
            return True
        return not isinstance(ModelRouter().select('generate'), MockClient)

    def _rule_relations(self, center_id: str, center: ResourceDoc, docs: list[ResourceDoc]) -> tuple[list[GraphRelationDraft], dict[str, GraphNodeContent]]:
        nodes = {center_id: GraphNodeContent(id=center_id, name=center['name'], type='herb', brief='中心药材')}
        relations: list[GraphRelationDraft] = []
        center_source = lambda text: [source_for(center, text)]
        herb_ids = {doc['id']: entity_id('herb', doc['name']) for doc in docs}
        herb_ids[center['id']] = center_id
        for doc in docs:
            target_id = herb_ids[doc['id']]
            if target_id == center_id:
                continue
            nodes[target_id] = GraphNodeContent(id=target_id, name=doc['name'], type='herb', brief='关联药材')
            for effect in sorted(set(center['effects']) & set(doc['effects']))[:2]:
                relations.append(GraphRelationDraft(source_id=center_id, target_id=target_id, relation_type=RelationType.EFFECT_SIMILAR, label=f'功效相通·{effect}', sources=center_source(f"{center['name']}功效：{effect}"), confidence=.92))
            for meridian in sorted(set(center['meridians']) & set(doc['meridians']))[:2]:
                relations.append(GraphRelationDraft(source_id=center_id, target_id=target_id, relation_type=RelationType.MERIDIAN_CLUSTER, label=f'归经聚类·{meridian}经', sources=center_source(f"{center['name']}归{meridian}经"), confidence=.88))
            shared_flavor = sorted(set(center['flavor']) & set(doc['flavor']))
            if center['nature'] == doc['nature'] and shared_flavor:
                relations.append(GraphRelationDraft(source_id=center_id, target_id=target_id, relation_type=RelationType.NATURE_FLAVOR_SIMILAR, label=f"性味相通·{center['nature']}{shared_flavor[0]}", sources=center_source(f"{center['name']}性{center['nature']}，味{shared_flavor[0]}"), confidence=.84))
        for indication in center['indications'][:3]:
            syndrome = str(indication.get('syndrome', '')).strip()
            if not syndrome:
                continue
            syndrome_id = entity_id('syndrome', syndrome)
            nodes[syndrome_id] = GraphNodeContent(id=syndrome_id, name=syndrome, type='syndrome', brief=str(indication.get('description', ''))[:80])
            excerpt = f"{syndrome}：{indication.get('description', '')}".strip('：')
            relations.append(GraphRelationDraft(source_id=center_id, target_id=syndrome_id, relation_type=RelationType.HERB_SYNDROME, label=f'药证链·{syndrome}', sources=center_source(excerpt), confidence=.94))
        for combo in center['combinations'][:3]:
            purpose = str(combo.get('purpose', '')).strip()
            formula_ref = str(combo.get('formula_ref', '')).strip()

            # 配伍：数据里只有 with + purpose，教材统一归到「相须/相使」协同组。
            for partner_id in combo.get('with', [])[:2]:
                partner_name = herb_name_by_id(partner_id)
                if not partner_name or partner_name == center['name'] or self._doc_by_name(partner_name) is None:
                    continue
                partner_node_id = entity_id('herb', partner_name)
                nodes[partner_node_id] = GraphNodeContent(id=partner_node_id, name=partner_name, type='herb', brief='配伍药材')
                pairing = f"{center['name']}配{partner_name}：{purpose}" if purpose else f"{center['name']}与{partner_name}配伍"
                relations.append(GraphRelationDraft(source_id=center_id, target_id=partner_node_id, relation_type=RelationType.XIANG_XU, label=f'相须·{partner_name}', sources=center_source(pairing), confidence=.9))

            if not formula_ref:
                continue
            formula_name = formula_display_name(formula_ref)
            formula_id = entity_id('formula', formula_ref)
            nodes[formula_id] = GraphNodeContent(id=formula_id, name=formula_name, type='formula', brief=purpose[:80])
            excerpt = f"{formula_name}：{purpose}" if purpose else f"{formula_name}含{center['name']}"
            # 方药组成：本药是该方的组成药味。
            relations.append(GraphRelationDraft(source_id=center_id, target_id=formula_id, relation_type=RelationType.COMPOSITION, label=f'方中组成·{formula_name}', sources=center_source(excerpt), confidence=.88))
            # 证方链：优先取 purpose 点名的证候，取不到再回退到首个药证链。
            syndrome_id = next((item.target_id for item in relations if item.relation_type == RelationType.HERB_SYNDROME and item.target_id in nodes and nodes[item.target_id].name in purpose), None) if purpose else None
            if syndrome_id is None:
                syndrome_id = next((item.target_id for item in relations if item.relation_type == RelationType.HERB_SYNDROME), None)
            if syndrome_id:
                relations.append(GraphRelationDraft(source_id=syndrome_id, target_id=formula_id, relation_type=RelationType.SYNDROME_FORMULA, label=f'证方链·{formula_name}', sources=center_source(excerpt), confidence=.82))
        for item in center['differentiation'][:2]:
            other = str(item.get('compare_name') or item.get('compare_with') or '').strip()
            if not other:
                continue
            other_id = entity_id('herb', other)
            nodes[other_id] = GraphNodeContent(id=other_id, name=other, type='herb', brief='同类鉴别')
            relations.append(GraphRelationDraft(source_id=center_id, target_id=other_id, relation_type=RelationType.DIFFERENTIATION, label=f'同类鉴别·{other}', sources=center_source(str(item.get('key_points', ''))), confidence=.93))
        for name in (center.get('incompatibility') or {}).get('shibafan', []):
            other_id = entity_id('herb', str(name))
            nodes[other_id] = GraphNodeContent(id=other_id, name=str(name), type='herb', brief='十八反禁忌')
            relations.append(GraphRelationDraft(source_id=center_id, target_id=other_id, relation_type=RelationType.XIANG_FAN, label=f'相反·{name}', sources=center_source(f"{center['name']}反{name}"), confidence=.95))
        return relations, nodes

    async def _llm_relations(self, center_id: str, center: ResourceDoc, docs: list[ResourceDoc]) -> list[GraphRelationDraft]:
        if not self._llm_enabled():
            return []
        prompt = f"center_id={center_id}\ncenter={center}\nretrieved_docs={docs}"
        async def call() -> str:
            return await self._llm_call(prompt) if self._llm_call else await ModelRouter().chat('generate', [{'role': 'system', 'content': GRAPH_AGENT_SYSTEM_PROMPT}, {'role': 'user', 'content': prompt}], response_format={'type': 'json_object'}, temperature=.1)
        # Exactly one repair is allowed; invalid individual relations are dropped.
        invalid_seen = False
        for attempt in range(2):
            try:
                payload = json.loads(await call())
                raw = payload.get('relations', []) if isinstance(payload, dict) else []
                valid: list[GraphRelationDraft] = []
                for item in raw:
                    try:
                        valid.append(GraphRelationDraft.model_validate(item))
                    except ValidationError:
                        invalid_seen = True
                        logger.warning('Dropping invalid GraphAgent relation', exc_info=True)
                if invalid_seen and attempt == 0:
                    # A bad enum/source is a whole-response contract failure: retry once.
                    invalid_seen = False
                    continue
                return valid
            except (json.JSONDecodeError, TypeError, ValidationError):
                logger.warning('GraphAgent relation response invalid; retry=%s', attempt, exc_info=True)
        return []

    @staticmethod
    def _dedupe(relations: list[GraphRelationDraft]) -> list[GraphRelationDraft]:
        seen: set[tuple[str, str, RelationType, str]] = set(); result: list[GraphRelationDraft] = []
        for relation in relations:
            key = (relation.source_id, relation.target_id, relation.relation_type, relation.label)
            if key not in seen and relation.sources:
                seen.add(key); result.append(relation)
        return result

    @staticmethod
    def _content(center_id: str, center: ResourceDoc, nodes: dict[str, GraphNodeContent], relations: list[GraphRelationDraft]) -> GraphContent:
        sources: list[GraphSource] = []; source_index: dict[tuple[str, str, str], int] = {}; edges: list[GraphEdgeContent] = []
        for relation in relations:
            source = relation.sources[0]; key = (source.出处, source.原文摘录, source.定位)
            index = source_index.setdefault(key, len(sources))
            if index == len(sources): sources.append(source)
            edges.append(GraphEdgeContent(source=relation.source_id, target=relation.target_id, relation_type=relation.relation_type, label=relation.label, source_ref=index))
        edges = [edge for edge in edges if edge.source in nodes and edge.target in nodes]
        used = {center_id, *(edge.source for edge in edges), *(edge.target for edge in edges)}
        return GraphContent(center_id=center_id, nodes=[node for key, node in nodes.items() if key in used], edges=edges, sources=sources)


def _now() -> str:
    return datetime.now(UTC).isoformat()
