"""库情：库里有什么，就让它用什么。

这一组断言针对的是「不管问什么都整棵树标推断」这个病根。它的成因不是模型笨，
而是**没人告诉模型库里有什么**，且系统从不查——「中药学介绍」被判成教材语料
之外，提示词里写着「没有任何检索资料」，24 个节点于是全标成「推断」，
而库里就有这门课 28 章 65 节的目录。

所以这里守四件事：

1. 落在库里的整门课 / 整章不许降级，骨架取教材目录；
2. 真在库外的主题才降级，且降级说明必须交代本库覆盖了什么；
3. 提示词里要有「本库有什么」这张清单——不给清单，模型只能凭常识编；
4. 依据按**节点**判，不按「素材包空不空」整棵树一刀切。
"""

import asyncio
import json

import pytest

from app.mindmap import catalog
from app.mindmap.agent import _ground_by_catalog
from app.mindmap.archetypes import build_archetype_tree
from app.mindmap.evidence import build_evidence_pack
from app.mindmap.intents import classify
from app.mindmap.prompts import build_draft_prompt
from app.mindmap.schemas import MindElixirData, MindmapMeta, MindmapNode
from app.mindmap.spec import _payload, baseline_spec
from app.resources import planning


def _pack(topic: str):
    intent = classify(topic)
    return asyncio.run(build_evidence_pack(intent))


# ---------------------------------------------------------------------------
# 认得出、不降级
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "topic,subject",
    [
        ("中药学介绍", "中药学"),
        ("方剂学怎么学", "方剂学"),
        ("中医基础理论有哪些内容", "中医基础理论"),
        ("针灸学概览", "针灸学"),
    ],
)
def test_subjects_inside_the_library_are_not_degraded(topic, subject):
    """六门课都在库里，问「这门课是什么」不该被判成域外。"""

    intent = classify(topic)
    assert intent.covered is True
    assert intent.degraded is False
    assert intent.archetype == "overview"
    assert intent.covered_subject == subject
    assert subject in intent.describe()


def test_chapter_topic_sinks_one_level():
    """点到某一章时往下沉一层，用这一章自己的节名当骨架。"""

    intent = classify("中药的性能")
    assert intent.covered_chapter == "中药的性能"
    assert intent.label == "整章概览"

    pack = _pack("中药的性能")
    assert pack.degraded is False
    assert pack.branch_slots == ["四气", "五味", "升降浮沉", "归经", "中药的毒性"]
    # 章名本身就是一条可引用的出处，不能只说「本地知识库」。
    assert pack.sources()[0]["type"] == "教材目录"
    assert "中药的性能" in pack.sources()[0]["ref"]


def test_topic_outside_the_library_still_degrades_and_says_what_is_covered():
    """真在库外才降级，而且不许含糊——要写清本库覆盖哪几门课。"""

    intent = classify("智能体是什么")
    assert intent.degraded is True
    assert intent.covered is False

    pack = _pack("智能体是什么")
    assert pack.degraded is True
    assert pack.nodes == []
    assert pack.sources() == []
    assert "中药学" in pack.notes[0] and "方剂学" in pack.notes[0]


def test_two_subjects_both_get_their_catalog():
    """「中药和方剂学的对比」两边都该拿得到目录，而不是「教材里没有可对比的条目」。"""

    pack = _pack("一个关于中药和方剂学的对比介绍")
    assert set(pack.catalog_subjects) == {"herb", "formula"}
    names = {row["name"] for row in pack.nodes}
    assert "解表药" in names and "解表剂" in names
    assert pack.sources()[0]["type"] == "教材目录"


# ---------------------------------------------------------------------------
# 骨架来自目录，不来自模板
# ---------------------------------------------------------------------------


def test_deterministic_skeleton_uses_the_real_table_of_contents():
    """没有模型时也要长成《中药学》的样子，不是「概念界定 / 组成 / 应用」。"""

    pack = _pack("中药学介绍")
    data = build_archetype_tree(pack)
    root = data.nodeData
    assert [branch.topic for branch in root.children] == ["总论", "各论"]

    theory = next(branch for branch in root.children if branch.topic == "总论")
    titles = [child.topic for child in theory.children]
    assert titles[0] == "中药的起源和中药学的发展"
    assert "中药的性能" in titles
    assert all(child.meta.evidence == "textbook" for child in theory.children)
    # 通用骨架里那几个与这本书无关的分支一个都不许出现。
    assert not {"概念界定", "核心内容", "与中药学的联系", "学习建议"} & set(branch.topic for branch in root.children)


def test_oversized_part_says_how_many_are_missing():
    """各论 21 类一张图放不下，就要说出还差多少，不许悄悄只画十个。"""

    pack = _pack("中药学介绍")
    data = build_archetype_tree(pack)
    applied = next(branch for branch in data.nodeData.children if branch.topic == "各论")
    topics = [child.topic for child in applied.children]
    assert len(topics) == 10
    assert topics[-1] == "另 11 类见教材"
    assert topics[-1] and applied.children[-1].meta.evidence == "model"


# ---------------------------------------------------------------------------
# 提示词里要有这张清单
# ---------------------------------------------------------------------------


def test_draft_prompt_tells_the_model_what_the_library_has():
    """不给清单，模型只能凭常识编；给了清单，它才会照抄真实章名。"""

    pack = _pack("中药学介绍")
    prompt = build_draft_prompt("overview", list(pack.branch_slots), depth=3, catalog=pack.catalog)
    assert "# 本库有什么" in prompt
    assert "《中药学》" in prompt
    assert "中药的性能" in prompt
    assert "逻辑" not in prompt.split("# 本库有什么")[0][-200:]


def test_spec_payload_carries_the_catalog():
    """拟方案那一步也要看得见目录，否则分支还是凭空拟。"""

    pack = _pack("中药学介绍")
    spec = baseline_spec("中药学介绍", pack, {})
    payload = _payload("中药学介绍", pack, {}, spec)
    assert "解表药" in payload["evidence"]["catalog"]
    assert payload["evidence"]["degraded"] is False


# ---------------------------------------------------------------------------
# 依据逐节点判
# ---------------------------------------------------------------------------


def _tree() -> MindElixirData:
    return MindElixirData(
        nodeData=MindmapNode(
            id="root",
            topic="中药学入门框架",
            children=[
                MindmapNode(
                    id="b0",
                    topic="中药基本性能",
                    children=[
                        MindmapNode(id="b0-1", topic="四气"),
                        MindmapNode(id="b0-2", topic="五味"),
                        MindmapNode(id="b0-3", topic="模型自己攒的说法"),
                    ],
                ),
                MindmapNode(
                    id="b1",
                    topic="模型自造的分支",
                    children=[MindmapNode(id="b1-1", topic="另一个自造条目")],
                ),
            ],
        )
    )


def test_node_evidence_is_decided_one_by_one():
    """「素材包空 ⇒ 整棵树推断」换成「这个名字库里有没有」。"""

    pack = _pack("中药学介绍")
    data = _ground_by_catalog(_tree(), pack)
    by_id = {node.id: node for node in _walk(data.nodeData)}
    assert by_id["b0-1"].meta.evidence == "textbook"
    assert by_id["b0-1"].meta.source_indexes == [0]
    assert by_id["b0-3"].meta.evidence == "model"
    assert by_id["b0-3"].meta.source_indexes == []
    # 根节点是标题：点到某门课就算有据可依。
    assert by_id["root"].meta.evidence == "textbook"


def test_branch_evidence_follows_its_children():
    """分支名是模型分的组，书上没这个名字；它有没有依据看它装了什么。"""

    pack = _pack("中药学介绍")
    data = _ground_by_catalog(_tree(), pack)
    by_id = {node.id: node for node in _walk(data.nodeData)}
    # 2/3 来自教材 → 这一支算有依据
    assert by_id["b0"].meta.evidence == "textbook"
    # 一条都不在书上 → 整支照实标推断
    assert by_id["b1"].meta.evidence == "model"


def test_parallel_listing_counts_as_textbook_but_mixed_does_not():
    """「四气、五味」是把书上的几条并列写在一起；混进一条自造的就不是。"""

    assert catalog.covers_node("四气、五味、升降浮沉、归经") is True
    assert catalog.covers_node("四气、五味、模型自造") is False
    assert catalog.covers_node("炮制的目的与方法") is False


def test_grounding_does_not_touch_other_archetypes():
    """别的题型不在这里重判依据——那会把「功效」这种真教材节点误降级。"""

    pack = _pack("麻黄的功效")
    assert pack.catalog_subjects == ()
    data = _tree()
    # 先按其它题型的规则给出依据，再确认原样返回、一个都不改。
    for node in _walk(data.nodeData):
        node.meta = MindmapMeta(source_indexes=[0], evidence="textbook")
    assert _ground_by_catalog(data, pack) is data
    assert all(node.meta.evidence == "textbook" for node in _walk(data.nodeData))


# ---------------------------------------------------------------------------
# 方案侧：分支名来自目录，就该按「教材覆盖」计
# ---------------------------------------------------------------------------


def _spec_reply() -> str:
    return json.dumps(
        {
            "root_topic": "中药学概览",
            "organization": "按教材目录展开：总论与各论两大篇",
            "organization_kind": "taxonomy",
            "depth": 3,
            "branches": [
                {"label": "总论·基础理论", "purpose": "共同理论", "must_cover": ["中药的性能"]},
                {"label": "各论·药物分类", "purpose": "按功效分类", "must_cover": ["解表药", "清热药"]},
            ],
            "interpretation": ["按《中药学》的目录排。"],
        },
        ensure_ascii=False,
    )


async def _fake_llm(messages):
    return _spec_reply()


def test_plan_units_are_sourced_from_the_table_of_contents():
    """没有药材条目时，方案里的分支仍应按「教材覆盖」计，而不是全挂模型补充。"""

    tiers = planning.evidence_tiers([], [])

    async def run():
        plan = await planning._propose_mindmap(
            "中药学介绍",
            [],
            {},
            "",
            [],
            tiers,
            llm_call=_fake_llm,
        )
        return plan

    plan = asyncio.run(run())
    assert [unit.label for unit in plan.units] == ["总论·基础理论", "各论·药物分类"]
    assert all(unit.source == "knowledge_base" for unit in plan.units)
    assert plan.evidence["knowledge_base"], "教材目录必须登记为一条依据"
    assert "目录" in plan.evidence["knowledge_base"][0]
    # 收口时的逐项核对不能把它降回 model。
    for unit in plan.units:
        checked = planning._verify_unit(unit, tiers, [])
        assert checked.source == "knowledge_base"
        assert checked.evidence


def _walk(node: MindmapNode):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)
