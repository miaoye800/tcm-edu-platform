"""导图工坊：从「读不懂那句话」到「按读懂的结果画」。

这一组断言针对的是**病根**，不是某个用例：

- 一句话里同时说了主题、层数、比较角度和「学好的方法」时，四件事都要落到方案上，
  而不是命中「对比」就长出固定的四支模板；
- 面板上改的层数/分支/受众/组织方式必须进生成参数——面板能改但没人读，
  等于在骗用户；
- 结构装不下那么多层要出声，不静默砍层；
- 模型把分支名换了个说法，整支内容不能丢；
- 依据档位由知识库命中重算，不采信模型自报。
"""

import asyncio
import json

import pytest

from app.mindmap import agent as agent_module
from app.mindmap.agent import _merge_draft
from app.mindmap.draft import DraftNode, MindmapDraft
from app.mindmap.schemas import MindElixirData, MindmapMeta, MindmapNode
from app.mindmap.spec import (
    baseline_spec,
    conformance,
    fit_structure,
    generation_params_from_plan,
    measure_tree,
)
from app.resources import planning

#: 用户原始用例。它同时包含四类要求：主题、多层、多角度对比、怎么学。
REQUEST = "一个关于中药和方剂学的对比介绍，多分几个层次，多一些角度对比，并总结学好它们需要怎么做"


def _spec_reply() -> str:
    """一份读懂了这句话的施工图。分支名刻意不用「对比维度/相同点」那套四件套。"""

    return json.dumps(
        {
            "root_topic": "中药与方剂学对比",
            "audience": "中医专业学生",
            "organization": "按学科定位、知识构成、思维方法、临床应用四个角度成对比较，最后单列学法",
            "organization_kind": "compare",
            "depth": 4,
            "leaves_min": 4,
            "structure": "logic-right",
            "branches": [
                {"label": "学科定位", "purpose": "两者各自研究什么", "must_cover": ["中药学", "方剂学"]},
                {"label": "知识构成", "purpose": "各自要背什么", "must_cover": ["性味归经", "君臣佐使"]},
                {"label": "思维方法", "purpose": "分别训练什么思维", "must_cover": ["药性推演", "配伍分析"]},
                {"label": "临床应用", "purpose": "临证怎么分工", "must_cover": ["辨证选药", "依法组方"]},
                {"label": "学习难点", "purpose": "各自卡在哪里", "must_cover": ["易混药", "易混方"]},
                {"label": "学法建议", "purpose": "总结怎么学好两者", "must_cover": ["先药后方", "对比记忆"]},
            ],
            "emphasis": ["多分几个层次", "多一些角度对比", "总结学好它们需要怎么做"],
            "must_answer": ["这两门课怎么配合着学"],
            "interpretation": [
                "你要的不是「相同点/不同点」两栏，而是多角度的成对比较。",
                "我按 4 层、6 支排：前五支逐角度对比，最后单开一支讲怎么学。",
            ],
        },
        ensure_ascii=False,
    )


async def _propose(topic: str = REQUEST, params: dict | None = None, llm_call=None) -> dict:
    return await planning.propose("mindmap", topic, params or {"depth": 3, "branches": 6}, llm_call=llm_call)


# ---------------------------------------------------------------------------
# 读懂整句话
# ---------------------------------------------------------------------------


def test_model_interpretation_replaces_the_fixed_compare_template() -> None:
    async def fake(_messages):
        return _spec_reply()

    plan = asyncio.run(_propose(llm_call=fake))
    labels = [unit["label"] for unit in plan["units"]]

    assert labels == ["学科定位", "知识构成", "思维方法", "临床应用", "学习难点", "学法建议"]
    # 「学法」这一条是用户明确要的，不能被任何模板丢掉。
    assert any("学" in label for label in labels), labels
    assert "相同点" not in labels and "对比维度" not in labels
    assert plan["spec"]["basis"] == "model"
    assert plan["spec"]["depth"] == 4
    # 方案上的层数不是装饰：呈现方式里必须写出来，用户一眼能核对。
    assert "4 层" in plan["presentation"]
    assert "6 支" in plan["presentation"]
    # 解读要能落到面板上，且说人话。
    assert plan["spec"]["interpretation"]
    assert plan["organization"].startswith("按学科定位")


def test_vague_more_layers_raises_depth_without_a_numeral() -> None:
    """「多分几个层次」没有数字，也不能当没听见。"""

    spec = baseline_spec(REQUEST, None, {"structure": "logic-right"})
    assert spec.depth >= 4
    assert spec.depth_requested == spec.depth


def test_baseline_is_marked_as_rules_not_as_understood() -> None:
    """没有解读模型时要诚实：这一版按规则拟的，不能标成「按你的原话解读」。"""

    plan = asyncio.run(_propose(llm_call=None))
    assert plan["spec"]["basis"] == "rules"
    assert any("规则" in note for note in plan["spec"]["notes"])
    assert plan["units"], "基线方案也要有分支，不能交白卷"


def test_explicit_counts_are_obeyed_or_explained() -> None:
    """「分四支」「要六层」这类写死的数：能办就办，办不到要说清为什么。"""

    four = baseline_spec("解表药的分类，分四个分支，每个分支至少三条", None, {})
    assert len(four.branches) == 4

    six = baseline_spec("麻黄详解，要六层", None, {})
    assert six.depth == 5, "向右分支最多 5 层"
    assert six.depth_requested == 6
    assert "6 层" in six.depth_gap() and "5 层" in six.depth_gap()


def test_model_branches_are_cleaned_but_not_rewritten() -> None:
    """分支名去兜底词、去重，但语义照模型给的，不由规则改写。"""

    async def fake(_messages):
        return json.dumps(
            {
                "depth": 3,
                "organization": "按维度对比",
                "branches": [
                    {"label": "功效对比"},
                    {"label": "功效对比"},
                    {"label": "其他"},
                    {"label": "主治对比"},
                ],
                "interpretation": ["按两个角度比"],
            },
            ensure_ascii=False,
        )

    plan = asyncio.run(_propose(llm_call=fake))
    labels = [unit["label"] for unit in plan["units"]]
    assert labels == ["功效对比", "主治对比"]


def test_interpretation_never_leaks_field_names() -> None:
    """面板上那几行是给用户看的。模型写「落在 depth=4」要翻成人话，翻不了就丢掉。"""

    async def fake(_messages):
        return json.dumps(
            {
                "depth": 4,
                "organization": "按五维对比",
                "branches": [{"label": "功效"}, {"label": "主治"}, {"label": "学法"}],
                "interpretation": [
                    "你要的多层次落在 depth=4：一级分支下再分两层。",
                    "你提的学法要求在 must_answer 里点名了。",
                    "按 logic-right 排，左右对照。",
                    "这一句是正常的人话。",
                ],
            },
            ensure_ascii=False,
        )

    plan = asyncio.run(_propose(llm_call=fake))
    lines = plan["spec"]["interpretation"]

    assert not any("depth" in line or "must_answer" in line or "logic-right" in line for line in lines), lines
    assert any("4 层" in line for line in lines), lines
    assert "这一句是正常的人话。" in lines


# ---------------------------------------------------------------------------
# 面板改了，生成就得照办
# ---------------------------------------------------------------------------


def _plan_payload(depth_text: str = "5 层 · 6 支 · 可编辑思维导图") -> dict:
    return {
        "kind": "mindmap",
        "goal": "梳理性味功效",
        "audience": "临床医师",
        "organization": "按对比维度展开",
        "presentation": depth_text,
        "units": [
            {"label": "性味对比", "detail": "四气；五味"},
            {"label": "功效对比", "detail": "发汗；解肌"},
            {"label": "主治对比", "detail": "风寒表实；风寒表虚"},
            {"label": "用法对比", "detail": "生用；炙用"},
            {"label": "禁忌对比", "detail": "表虚；阴虚"},
            {"label": "学法建议", "detail": "成对记忆"},
        ],
        "spec": {"depth": 3, "structure": "logic-right", "leaves_min": 3, "branches": []},
        "evidence": {"knowledge_base": [], "user_material": [], "model": []},
        "gaps": [],
    }


def test_depth_edited_in_the_panel_reaches_generation() -> None:
    """这就是用户报的那一条：改成 5 层，生成必须是 5 层。"""

    confirmed = planning.confirm_payload("mindmap", _plan_payload("5 层 · 6 支"))
    params = generation_params_from_plan(confirmed.model_dump(mode="json"), {"depth": 3, "branches": 6})

    assert confirmed.spec["depth"] == 5
    assert params["depth"] == 5
    assert params["expected"]["depth"] == 5


def test_units_are_the_branch_budget() -> None:
    """加一支删一支都算数：支数以方案里的单元为准，不取提案时的默认值。"""

    payload = _plan_payload("4 层 · 6 支")
    payload["units"] = payload["units"][:4]
    confirmed = planning.confirm_payload("mindmap", payload)
    params = generation_params_from_plan(confirmed.model_dump(mode="json"), {"depth": 3, "branches": 6})

    assert params["branches"] == 4
    assert params["expected"]["labels"] == ["性味对比", "功效对比", "主治对比", "用法对比"]
    assert [branch["label"] for branch in confirmed.spec["branches"]] == params["expected"]["labels"]


def test_audience_and_organization_edits_reach_generation() -> None:
    confirmed = planning.confirm_payload("mindmap", _plan_payload())
    params = generation_params_from_plan(confirmed.model_dump(mode="json"), {})

    assert params["audience"] == "临床医师"
    assert params["organization"] == "按对比维度展开"


def test_depth_written_only_in_prose_still_counts() -> None:
    """老任务/直接调接口没有 spec，只在「呈现方式」里写了 5 层，也要算数。"""

    payload = _plan_payload("5 层 · 可编辑导图")
    payload.pop("spec")
    params = generation_params_from_plan(payload, {"depth": 3})
    assert params["depth"] == 5


def test_structure_that_cannot_hold_the_depth_is_replaced_and_said_out_loud() -> None:
    payload = _plan_payload("5 层 · 6 支")
    payload["spec"]["structure"] = "fishbone"
    confirmed = planning.confirm_payload("mindmap", payload)

    assert confirmed.spec["structure"] == "logic-right", "鱼骨图最多 3 层，不能拿它画 5 层"
    assert any("层" in gap for gap in confirmed.gaps), confirmed.gaps
    assert fit_structure("fishbone", 5) == ("logic-right", True)
    assert fit_structure("fishbone", 3) == ("fishbone", False)


# ---------------------------------------------------------------------------
# 生成与核对
# ---------------------------------------------------------------------------


def test_branch_label_drift_keeps_the_content() -> None:
    """模型把「关键差异」写成「主要区别」时，内容不能整支丢掉。"""

    skeleton = MindElixirData(
        nodeData=MindmapNode(
            id="root",
            topic="麻黄与桂枝",
            children=[
                MindmapNode(id="b0", topic="相同点", meta=MindmapMeta(evidence="model")),
                MindmapNode(id="b1", topic="关键差异", meta=MindmapMeta(evidence="model")),
            ],
        )
    )
    draft = MindmapDraft(
        root_topic="麻黄与桂枝",
        branches=[
            DraftNode(topic="共同点", source_indexes=[0], children=[DraftNode(topic="发汗解表", source_indexes=[0])]),
            DraftNode(topic="主要区别", source_indexes=[0], children=[DraftNode(topic="桂枝兼解肌", source_indexes=[0])]),
        ],
    )
    merged = _merge_draft(skeleton, draft, source_count=2, depth=3)
    leaves = [[child.topic for child in branch.children] for branch in merged.nodeData.children]

    assert leaves == [["发汗解表"], ["桂枝兼解肌"]]


def test_branch_suffix_drift_still_matches() -> None:
    """方案写「性味归经对比」、模型写「性味归经」，是同一支，内容不能丢。"""

    skeleton = MindElixirData(
        nodeData=MindmapNode(
            id="root",
            topic="麻黄与桂枝",
            children=[MindmapNode(id="b0", topic="性味归经对比", meta=MindmapMeta(evidence="model"))],
        )
    )
    draft = MindmapDraft(
        root_topic="麻黄与桂枝",
        branches=[
            DraftNode(topic="性味归经", source_indexes=[0], children=[DraftNode(topic="辛、微苦，温", source_indexes=[0])]),
        ],
    )
    merged = _merge_draft(skeleton, draft, source_count=2, depth=3)
    assert [child.topic for child in merged.nodeData.children[0].children] == ["辛、微苦，温"]


def test_conformance_reports_depth_shortfall_instead_of_hiding_it() -> None:
    content = {
        "mindmap_data": {
            "nodeData": {
                "id": "root",
                "topic": "图",
                "children": [
                    {"id": "b0", "topic": "对比维度", "children": [{"id": "b0-0", "topic": "性味"}]},
                    {"id": "b1", "topic": "学法建议", "children": []},
                ],
            }
        }
    }
    fit = conformance(content, {"depth": 5, "labels": ["对比维度", "学法建议", "临床应用"]})

    assert fit["depth"] == 3
    assert "5 层" in fit["note"] and "3 层" in fit["note"]
    assert fit["missing"] == ["临床应用"]
    assert measure_tree(content["mindmap_data"]["nodeData"])["depth"] == 3


def test_shallow_result_gets_one_deepening_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """方案定 4 层、模型只画到 3 层时，要再要一次，而不是就这么交出去。"""

    from app.config import Settings

    skeleton = MindElixirData(
        nodeData=MindmapNode(
            id="root",
            topic="麻黄与桂枝",
            children=[
                MindmapNode(id="b0", topic="性味归经对比", meta=MindmapMeta(evidence="model")),
                MindmapNode(id="b1", topic="功效对比", meta=MindmapMeta(evidence="model")),
            ],
        )
    )
    shallow = MindmapDraft(
        root_topic="麻黄与桂枝",
        branches=[
            DraftNode(topic="性味归经对比", children=[DraftNode(topic="辛温")]),
            DraftNode(topic="功效对比", children=[DraftNode(topic="发汗")]),
        ],
    )
    merged = _merge_draft(skeleton, shallow, source_count=0, depth=4)
    assert measure_tree(merged.model_dump(mode="json")["nodeData"])["depth"] == 3

    deeper = MindmapDraft(
        root_topic="麻黄与桂枝",
        branches=[
            DraftNode(topic="性味归经对比", children=[DraftNode(topic="四气", children=[DraftNode(topic="辛温")])]),
            DraftNode(topic="功效对比", children=[DraftNode(topic="功效类", children=[DraftNode(topic="发汗")])]),
        ],
    )

    async def fake_create(**_kwargs):
        return deeper

    monkeypatch.setattr(agent_module, "structured_create", fake_create)
    agent = agent_module.MindmapAgent(settings=Settings(DEEPSEEK_API_KEY="x"))
    fixed = asyncio.run(agent._deepen_once(skeleton, merged, [], [], 4))

    assert fixed is not None
    assert measure_tree(fixed.model_dump(mode="json")["nodeData"])["depth"] == 4


def test_spec_is_kept_when_revision_model_forgets_it() -> None:
    """修订一句话不该把层数/画法/必答项全丢了。"""

    base = planning.normalize_plan("mindmap", _plan_payload("5 层 · 6 支"))
    assert base.spec["depth"] == 5

    async def revise(_messages):
        plan = base.model_dump(mode="json")
        plan.pop("spec", None)
        plan["units"][0]["label"] = "性味与归经对比"
        return json.dumps({"plan": plan, "clarification": ""}, ensure_ascii=False)

    revised = asyncio.run(
        planning.revise(
            "mindmap",
            "麻黄与桂枝的区别",
            base.model_dump(mode="json"),
            "第一支标题改一下",
            llm_call=revise,
        )
    )
    assert revised["applied"] is True
    assert revised["spec"]["depth"] == 5
    assert revised["units"][0]["label"] == "性味与归经对比"


@pytest.fixture(autouse=True)
def _keyless_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """这组测试不碰真实模型：解读一律走注入的 llm_call 或确定性基线。"""

    from tests.helpers import keyless_settings

    monkeypatch.setattr(agent_module, "get_settings", keyless_settings)
