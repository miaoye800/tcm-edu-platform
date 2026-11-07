"""八问法判定的评测口径。

``tests/data/mindmap_topics.json`` 是 20 条中药学提问的人工标注集：八类问法各
2~3 条，外加 2 条域外主题。这一组用例把它变成可回归的断言，口径是

- **问法判对**：``intent`` 等于标注的 ``kind``；
- **图够看**：节点数 ≥ 8、一级分支 ≥ 3、没有空壳分支；
- **依据可数**：每个节点都有且只有一个档位，三档之和 = 节点总数
  （底栏占比靠这条，少一个节点数字就对不上）；
- **域外诚实**：整棵树都是 ``model``，且 ``degraded`` 为真。

用无 key 的确定性路径跑，所以断言的是骨架质量，不是模型的运气。
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.agents.resource_context import retrieve_resource_docs
from app.mindmap.agent import MindmapAgent
from app.mindmap.schemas import MindmapNode
from tests.helpers import keyless_settings

_CASES = json.loads((Path(__file__).parent / "data" / "mindmap_topics.json").read_text(encoding="utf-8"))["topics"]
_EVIDENCE_TIERS = ("textbook", "graph", "model")


def _walk(node: MindmapNode, depth: int = 1, out: list[tuple[MindmapNode, int]] | None = None) -> list[tuple[MindmapNode, int]]:
    out = out if out is not None else []
    out.append((node, depth))
    for child in node.children:
        _walk(child, depth + 1, out)
    return out


async def _generate(topic: str):
    docs = await retrieve_resource_docs(topic)
    return await MindmapAgent(settings=keyless_settings()).generate(topic, docs)


#: 每条题只生成一次。20 条就是 20 次检索 + 组装，重复跑会把用例拖到十几秒。
_CACHE: dict[str, object] = {}


def _result(topic: str):
    if topic not in _CACHE:
        _CACHE[topic] = asyncio.run(_generate(topic))
    return _CACHE[topic]


@pytest.mark.parametrize("case", _CASES, ids=[case["topic"] for case in _CASES])
def test_eval_topic_builds_a_usable_map(case):
    result = _result(case["topic"])
    root = result.mindmap_data.nodeData
    nodes = _walk(root)
    top = root.children

    assert result.intent == case["kind"], f"{case['topic']} 判成了 {result.intent}（标注 {case['kind']}）"
    assert len(nodes) >= 8, f"只有 {len(nodes)} 个节点，撑不起一张图"
    assert len(top) >= 3, f"只有 {len(top)} 个一级分支"
    assert all(branch.children for branch in top), "存在空壳分支"

    tiers = [node.meta.evidence for node, _ in nodes]
    assert set(tiers) <= set(_EVIDENCE_TIERS), f"出现了未分档的依据：{set(tiers) - set(_EVIDENCE_TIERS)}"
    assert len(tiers) == len(nodes)


@pytest.mark.parametrize("case", [case for case in _CASES if case["kind"] == "concept"], ids=lambda case: case["topic"])
def test_out_of_corpus_topic_is_honestly_labelled(case):
    result = _result(case["topic"])
    nodes = _walk(result.mindmap_data.nodeData)

    assert result.degraded is True
    assert {node.meta.evidence for node, _ in nodes} == {"model"}
    # 域外不给假装知道的「定义」，给的是怎么学——四个轴必须都是学习动作。
    assert {branch.topic for branch in result.mindmap_data.nodeData.children} == {
        "概念界定",
        "核心内容",
        "与中药学的联系",
        "学习建议",
    }


def test_every_branch_has_a_reason_to_exist():
    """空壳分支是最伤观感的一种坏：一个词挂在根上，底下什么都没有。"""

    for case in _CASES:
        result = _result(case["topic"])
        for branch in result.mindmap_data.nodeData.children:
            assert branch.children, f"{case['topic']} 的「{branch.topic}」是空壳"
            assert len(branch.children) <= 10, f"{case['topic']} 的「{branch.topic}」挂了 {len(branch.children)} 个叶子"


def test_long_question_is_classified_from_the_full_sentence(tmp_path):
    """标题可以截断，判问法不行。

    「…在性味归经上有什么差异」截到 30 字就把「差异」切掉了，会判成分类题，
    于是长出一张分类图——用户问的是对比。这条钉住那个取舍。
    """

    from app.mindmap.intents import classify as classify_topic
    from app.resources import service as resource_service

    full = "请帮我画一张关于解表药和清热药这两类药在性味归经和主要功效上有什么差异的思维导图"
    params = {"topic": full[:30], "query": full}
    task = {"node_id": "general", "meta": {"params": params}}

    short = resource_service._task_topic(task, params, tmp_path)
    assert "差异" not in short, "这个用例的前提是短标题丢掉了关键信号"
    assert classify_topic(short).kind == "taxonomy"

    assert resource_service._intent_topic(task, params, tmp_path) == full
    assert classify_topic(full).kind == "comparison"


def test_intent_topic_falls_back_to_the_node_name(tmp_path):
    """老任务没有 query 字段：退回节点名，不能让判定拿到空串。"""

    from app.path.store import upsert_node
    from app.resources import service as resource_service
    from app.resources import storage as resource_storage

    db_path = tmp_path / "qihuang.db"
    upsert_node({"id": "mahuang", "name": "麻黄", "level": "point"}, db_path)
    resource_storage.init_db(db_path)
    task = {"node_id": "mahuang", "meta": {"params": {}}}
    assert resource_service._intent_topic(task, {}, db_path) == "麻黄"
