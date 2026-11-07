"""导图工坊：把「一句人话里的额外要求」变成可执行约束。

这一层的价值不在算法，而在**能解释**：用户说「必须四层」，图就得是四层，
并且能说清「四层」是从哪个词读出来的。所以这里测的全是「读得准不准」，
以及读不准时的兜底（钳位、否定优先、读不出就空）。
"""

from app.mindmap import agent as agent_module
from app.mindmap.agent import _merge_draft
from app.mindmap.brief import parse_brief
from app.mindmap.draft import (
    MindmapDraft,
    MindmapNode,
    DraftNode,
    MindElixirData,
    question_title,
    usable_root_title,
)


# --------------------------------------------------------------------------
# 层数：报告里的第一号问题（「说四层只出三层」）
# --------------------------------------------------------------------------


def test_depth_is_read_from_chinese_and_arabic_numerals():
    assert parse_brief("淫羊藿的详细介绍，思维导图必须包含四层").depth == 4
    assert parse_brief("生成四层的导图").depth == 4
    assert parse_brief("要 5 层").depth == 5
    assert parse_brief("分两层就行").depth == 2


def test_depth_does_not_swallow_branch_counts():
    """「分四个分支」说的是分支数，不是层数——两个正则必须分得开。"""

    brief = parse_brief("解表药，分四个分支，每个分支至少三条")
    assert brief.depth is None
    assert brief.branches == 4
    assert brief.leaves_min == 3


def test_depth_is_clamped_into_the_supported_range():
    """模型/接口扛不住 9 层；用户写了也得钳到 5，而不是照单全收。"""

    assert parse_brief("要九层").depth == 5
    assert parse_brief("两层").depth == 2


def test_vague_requests_for_more_detail_raise_depth():
    assert parse_brief("麻黄，再深一层").depth == 4
    assert parse_brief("细一点，详细一点").depth == 4


# --------------------------------------------------------------------------
# 分支 / 叶子 / 取向 / 结构
# --------------------------------------------------------------------------


def test_branches_and_leaves_are_read_separately():
    brief = parse_brief("拆成五个板块，每支至少 4 条")
    assert brief.branches == 5
    assert brief.leaves_min == 4


def test_leaves_min_accepts_at_least_phrasing():
    assert parse_brief("每个分支至少三条").leaves_min == 3
    assert parse_brief("不少于 5 个").leaves_min == 5


def test_style_picks_exam_first():
    assert parse_brief("考研用的，重点是考点").style == "exam"
    assert parse_brief("帮我记，要能背诵").style == "memory"
    assert parse_brief("临床怎么开方").style == "clinic"
    assert parse_brief("零基础也能看懂的科普").style == "intro"


def test_structure_terms_map_to_layouts():
    assert parse_brief("用鱼骨图拆原因").structure == "fishbone"
    assert parse_brief("从中心往四面八方发散").structure == "mind-both"
    assert parse_brief("做成向右的大纲").structure == "logic-right"


# --------------------------------------------------------------------------
# 要含 / 不要含
# --------------------------------------------------------------------------


def test_include_terms_become_labels():
    brief = parse_brief("要有记忆口诀和用量，顺便带上禁忌")
    assert set(brief.include) == {"mnemonic", "dosage", "caution"}
    assert "记忆口诀" in brief.labels()


def test_exclude_wins_over_include():
    """「不要写剂量」里也含「剂量」两个字。以否定为准，否则会自相矛盾。"""

    brief = parse_brief("要口诀，不要剂量")
    assert "dosage" in brief.exclude
    assert "dosage" not in brief.include


# --------------------------------------------------------------------------
# to_params / describe / prompt_lines：三个消费端
# --------------------------------------------------------------------------


def test_to_params_only_carries_what_the_user_said():
    """没说的一律不出现——``None`` 不能变成覆盖默认值的 0 或 False。"""

    brief = parse_brief("麻黄四层")
    params = brief.to_params()
    assert params["depth"] == 4
    assert "branches" not in params
    assert "style" not in params


def test_describe_is_human_readable_and_short():
    text = parse_brief("四层，每个分支至少三条，帮我记，要口诀，不要剂量").describe()
    assert "4 层" in text
    assert "每支至少 3 条" in text
    assert text.count(" · ") <= 3


def test_prompt_lines_number_the_hard_constraints():
    lines = parse_brief("四层，每支至少三条").prompt_lines()
    assert any("4 层" in line for line in lines)
    assert any("至少 3 个叶子" in line for line in lines)


def test_empty_brief_is_reported_as_empty():
    brief = parse_brief("麻黄")
    assert brief.empty
    assert brief.to_params() == {}
    assert brief.describe() == ""


def test_blank_input_does_not_raise():
    assert parse_brief("").empty
    assert parse_brief("   \n ").empty


# --------------------------------------------------------------------------
# 根节点标题：报告里的第二号问题（「直接抄用户原文」）
# --------------------------------------------------------------------------


def test_question_title_strips_request_wrapping():
    assert question_title("做一个智能体的简介的思维导图") == "智能体简介"
    assert question_title("请帮我画一张麻黄的知识图谱") == "麻黄"
    assert question_title("淫羊藿的详细介绍，思维导图必须包含四层") == "淫羊藿详解"


def test_question_title_takes_the_topic_segment_of_a_long_sentence():
    """后面全是「对图的要求」，不是主题。取第一段就够。"""

    assert question_title("解表药，分四个分支，每个分支至少三条") == "解表药"
    assert len(question_title("桂枝的功效与主治以及常见配伍和禁忌还有用量")) <= 16


def test_question_title_returns_blank_for_noise():
    assert question_title("") == ""
    assert question_title("思维导图") == ""


def test_usable_root_title_rejects_copied_questions():
    """模型把整句提问抄成根节点时不能采信——根节点是锚，越长越不像标题。"""

    assert usable_root_title("做一个智能体的简介的思维导图") == ""
    assert usable_root_title("图") == ""
    assert usable_root_title("智能体") == "智能体"


# --------------------------------------------------------------------------
# 四层合并：报告里的「说四层只出三层」的第二个原因（只收一层 children）
# --------------------------------------------------------------------------


def _skeleton(branch_topic: str = "性味归经") -> MindElixirData:
    return MindElixirData(
        nodeData=MindmapNode(
            id="root",
            topic="淫羊藿",
            children=[MindmapNode(id="b0", topic=branch_topic, children=[]), MindmapNode(id="b1", topic="功效", children=[])],
        )
    )


def _deep_draft() -> MindmapDraft:
    """根(1) → 性味归经(2) → 分组A(3) → 细目(4) → 要点(5)。

    模型在 depth=4 时确实会给到这么深，正好用来验证「多出来那一层要削掉」。
    """

    return MindmapDraft(
        root_topic="淫羊藿",
        branches=[
            DraftNode(
                topic="性味归经",
                children=[
                    DraftNode(
                        topic="分组A",
                        children=[DraftNode(topic="细目", children=[DraftNode(topic="要点")])],
                    )
                ],
            )
        ],
    )


def _measure(node: MindmapNode) -> int:
    return 1 + max((_measure(child) for child in node.children), default=0)


def test_merge_keeps_every_level_up_to_the_requested_depth():
    """depth=4：第 4 层的「细目」要在，第 5 层的「要点」要削。"""

    merged = _merge_draft(_skeleton(), _deep_draft(), source_count=2, depth=4)
    assert _measure(merged.nodeData) == 4
    branch = merged.nodeData.children[0]
    assert branch.children[0].topic == "分组A"
    assert branch.children[0].children[0].topic == "细目"
    assert branch.children[0].children[0].children == []


def test_merge_stops_at_the_requested_depth():
    """depth=3 时第 4 层要被截掉——以前这里是 4 层，比用户要的多一层。"""

    merged = _merge_draft(_skeleton(), _deep_draft(), source_count=2, depth=3)
    assert _measure(merged.nodeData) == 3
    assert merged.nodeData.children[0].children[0].topic == "分组A"


def test_merge_recurses_deeper_than_one_level():
    """回归「说四层只出三层」：只要 depth 允许，模型给的层级必须逐层收下来。"""

    merged = _merge_draft(_skeleton(), _deep_draft(), source_count=2, depth=5)
    assert _measure(merged.nodeData) == 5


def test_merge_prefers_a_title_like_root_topic():
    draft = MindmapDraft(root_topic="淫羊藿", branches=[DraftNode(topic="性味归经", children=[DraftNode(topic="辛、甘，温")])])
    merged = _merge_draft(_skeleton(), draft, source_count=2, depth=3)
    assert merged.nodeData.topic == "淫羊藿"


def test_merge_ignores_root_topic_that_is_a_copied_question():
    """模型抄提问时不采信，骨架自己的标题留下。"""

    draft = MindmapDraft(
        root_topic="做一个淫羊藿的详细介绍的思维导图",
        branches=[DraftNode(topic="性味归经", children=[DraftNode(topic="辛、甘，温")])],
    )
    merged = _merge_draft(_skeleton(), draft, source_count=2, depth=3)
    assert merged.nodeData.topic == "淫羊藿"


def test_depth_param_shares_one_clamp_with_the_prompt():
    """提示词与合并必须用同一个数，否则「prompt 说四层、合并按三层削」。"""

    assert agent_module._depth_param({"depth": 4}) == 4
    assert agent_module._depth_param({"depth": 99}) == 5
    assert agent_module._depth_param({"depth": 1}) == 2
    assert agent_module._depth_param({}) == agent_module.DEFAULT_DEPTH
    assert agent_module._depth_param(None) == agent_module.DEFAULT_DEPTH
