import asyncio
import json

import pytest

from app.agents.resource_context import retrieve_resource_docs
from app.config import Settings
from app.mindmap.agent import MindmapAgent
from app.mindmap.rules import ALLOWED_TEMPLATE_IDS
from app.mindmap.schemas import MindElixirData, MindmapNode, merge_branch


def test_template_is_from_rule_candidate_and_reason_cites_feature_or_profile():
    async def choose(_messages):
        return json.dumps({"template_id": "herb-garden"})
    docs = asyncio.run(retrieve_resource_docs("麻黄"))
    result = asyncio.run(MindmapAgent(settings=Settings(DEEPSEEK_API_KEY="x"), llm_call=choose).generate("麻黄", docs, {"planning_params": {"fun_preference": 0.9}}))
    assert result.template_id in ALLOWED_TEMPLATE_IDS
    assert "层级分类内容" in result.choice_reason or "趣味偏好" in result.choice_reason


def test_outside_template_enum_is_not_accepted():
    async def choose(_messages):
        return json.dumps({"template_id": "not-approved"})
    docs = asyncio.run(retrieve_resource_docs("麻黄"))
    result = asyncio.run(MindmapAgent(settings=Settings(DEEPSEEK_API_KEY="x"), llm_call=choose).generate("麻黄", docs, {}))
    assert result.template_id in ALLOWED_TEMPLATE_IDS
    assert result.template_id != "not-approved"


def test_expand_merge_is_idempotent():
    data = MindElixirData.model_validate({"nodeData": {"id": "root", "topic": "麻黄", "children": [{"id": "effect", "topic": "功效"}]}})
    added = [MindmapNode(id="effect-1", topic="发汗解表")]
    merged, idempotent = merge_branch(data, ["root", "effect"], added)
    assert not idempotent and len(merged.nodeData.children[0].children) == 1
    _, idempotent = merge_branch(merged, ["root", "effect"], added)
    assert idempotent


def test_no_key_falls_back_to_two_level_map_and_default_template():
    docs = asyncio.run(retrieve_resource_docs("麻黄"))
    result = asyncio.run(MindmapAgent(settings=Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None)).generate("麻黄", docs, {}))
    assert result.template_id == "warm-gold"
    assert result.mindmap_data.nodeData.children
    assert any(node.children for node in result.mindmap_data.nodeData.children)
