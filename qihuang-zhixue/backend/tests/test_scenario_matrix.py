"""场景矩阵：把"真实用户会怎么答"逐类跑一遍，看证据收集与评分是否符合预期。

为什么单独建一个文件
--------------------
`test_modes.py` 是**按代码路径**组织的（这个分支有没有走对），
这里按**按用户行为**组织（这样一个人这样答，结果应该是什么）。
两者不能互相替代：前者保证实现没退化，后者保证产品判断没走偏。

矩阵覆盖的七类对话回答（对应真实用户里最常见的七种人）：

===============  =========================================  ============================
场景              用户做了什么                                期望
===============  =========================================  ============================
一句话极简          "想学中医""就是想学"                       只收到说出口的那一条，其余六维留白
详细一次说清        一段话里同时讲了目标、动机、喜欢的资源        一段话能落成多条，且各归各维
重复同一句         把同一句话再发两遍                          只算一条认定，第二遍不再计入
复述历史           把上一轮 AI 的问题贴回来                     不算证据（不是用户的表达）
前后矛盾           "每天只有十分钟" → "其实能学两小时"          两条都留，不静默覆盖成一条
跑题              "今天天气不错"                             不产证据，也不中断会话
留白              直接发空                                   不产证据，不伪造

剧情侧另有六类（全对 / 全错 / 混合 / 全留白 / 自定义身份 / 中途重载），
断言的是覆盖度、门槛与分数区间，而不是选择题本身。

**这里不打真实模型**：抽取结果由测试给出，跑的是会话状态机、去重闸门、
覆盖度与统一算分公式。模型对"新说法"的语义判断力不在这套测试的能力范围内
（见 `docs/profile-test-matrix.md` 的分层说明）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import app.profile.modes as modes_module
from app.profile.component_model import (
    COVERAGE_THRESHOLD,
    COMPONENTS,
    AtomicEvidence,
    dimension_score,
)
from app.profile.modes import answer_profile_session, get_profile_session, start_profile_session
from app.profile.nodes import DialogueExtraction

USER = "matrix-user"

# ---------------------------------------------------------------------------
# 驱动骨架
# ---------------------------------------------------------------------------


class _Rig:
    """一个用户 + 一个会话 + 一串按轮次准备好的抽取结果。"""

    def __init__(self, tmp_path: Path, mode: str = "dialogue") -> None:
        self.db_path = tmp_path / f"{mode}.db"
        self.session = start_profile_session(USER, mode, db_path=self.db_path)

    def say(self, utterance: str, evidence: list[AtomicEvidence] | None = None) -> None:
        cards = evidence or []
        turn = self.session.step_index + 1

        async def fake_extract(**_kwargs):
            return (
                DialogueExtraction(
                    turn_id=turn,
                    atomic_evidence=cards,
                    combination_type="single_clear_self_report",
                    extraction_method="llm",
                ),
                "你刚才说的这些，能再具体一点吗？",
            )

        modes_module._dialogue_extract_and_question = fake_extract  # type: ignore[assignment]
        self.session, _ = asyncio.run(
            answer_profile_session(
                self.session.session_id,
                USER,
                self.session.current_step.id,
                {"utterance": utterance, "response_time_ms": 4000},
                db_path=self.db_path,
            )
        )

    def collected(self, dimension: str) -> set[str]:
        return modes_module._dialogue_collected_components(dimension, self.session.evidence_cards)  # type: ignore[arg-type]

    def cards(self, dimension: str) -> list:
        return [card for card in self.session.evidence_cards if card.dimension == dimension]

    def finish(self):
        return asyncio.run(
            modes_module.finish_profile_session_async(
                self.session.session_id, USER, db_path=self.db_path
            )
        )


@pytest.fixture(autouse=True)
def _restore_extractor(monkeypatch: pytest.MonkeyPatch):
    """每个用例结束后把被替换的抽取函数还原，避免串味。"""

    original = modes_module._dialogue_extract_and_question
    yield
    modes_module._dialogue_extract_and_question = original  # type: ignore[assignment]


def card(
    dimension: str,
    component: str,
    quote: str,
    *,
    claim: str | None = None,
    status: str = "scorable",
    relation: str = "new",
    group: str | None = None,
) -> AtomicEvidence:
    return AtomicEvidence(
        evidence_id=f"{dimension}-{component}-{abs(hash(quote)) % 10000}",
        dimension=dimension,  # type: ignore[arg-type]
        component=component,
        evidence_group_id=group or f"g-{dimension}-{component}",
        quote=quote,
        normalized_claim=claim or quote,
        evidence_type="self_report",
        collection_status=status,  # type: ignore[arg-type]
        semantic_relation=relation,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 场景一：一句话极简（"想学中医"型用户）
# ---------------------------------------------------------------------------


def test_scenario_only_one_line_never_lights_the_other_six(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    rig.say("想学中医", [card("learning_goal", "goal_specificity", "想学中医")])
    rig.say("就是想学", [])
    rig.say("没别的", [])

    assert rig.collected("learning_goal") == {"goal_specificity"}
    for dimension in COMPONENTS:
        if dimension == "learning_goal":
            continue
        assert rig.collected(dimension) == set(), f"{dimension} 凭空出了证据"

    # 1/5 = 20% < 60%，必须不出分，而不是给个默认分。
    outcome = dimension_score("learning_goal", {"goal_specificity": 60.0})
    assert outcome["coverage"] == pytest.approx(0.2)
    assert outcome["gate_ok"] is False


# ---------------------------------------------------------------------------
# 场景二：一段话把三件事都说清（"表达能力强的用户"）
# ---------------------------------------------------------------------------


def test_scenario_one_long_answer_can_fill_several_dimensions_at_once(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    rig.say(
        "我在中药房上班，明年想考执业药师，主要是工作逼着要证，平时喜欢看图谱。",
        [
            card("learning_goal", "goal_specificity", "明年想考执业药师"),
            card("learning_goal", "action_orientation", "明年"),
            card("motivation", "intrinsic_value", "工作逼着要证"),
            card("resource_preference", "preference_specificity", "平时喜欢看图谱"),
        ],
    )

    assert rig.collected("learning_goal") == {"goal_specificity", "action_orientation"}
    assert rig.collected("motivation") == {"intrinsic_value"}
    assert rig.collected("resource_preference") == {"preference_specificity"}
    # 一句话里的四条各自归位，没有互相串维。
    for dimension in COMPONENTS:
        assert len(rig.cards(dimension)) == len(rig.collected(dimension))


# ---------------------------------------------------------------------------
# 场景三：把同一句话再发一遍（"怕没被记住"的用户）
# ---------------------------------------------------------------------------


def test_scenario_repeating_the_same_sentence_counts_once(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    repeated = "我想考执业药师"
    rig.say(repeated, [card("learning_goal", "goal_specificity", repeated)])
    rig.say(repeated, [card("learning_goal", "goal_specificity", repeated)])
    rig.say(repeated, [card("learning_goal", "goal_specificity", repeated)])

    assert rig.collected("learning_goal") == {"goal_specificity"}
    assert len(rig.cards("learning_goal")) == 1, "同一表达变成了多条认定"
    assert len(rig.session.evidence_cards) == 1


# ---------------------------------------------------------------------------
# 场景四：把 AI 的话贴回来（"懒得打字"的用户）
# ---------------------------------------------------------------------------


def test_scenario_pasting_the_assistant_question_is_not_an_answer() -> None:
    """整段对话会一起送给模型，所以"引文来自 AI 那一句"必须在校验层被拦掉。

    这条**不能**靠 `_filter_dialogue_cards`：用户原样粘贴时，引文确实出现在
    本轮发言里，两道闸门都放行。真正管用的是"引文必须出自用户说过的话"
    ——`_dialogue_user_source_text` 只保留 user 行，AI 的问题不在其中。
    """

    question = "你平时更习惯看图谱还是看病例？"
    transcript = f"assistant: {question}\nuser: 嗯，都行吧"
    user_text = modes_module._dialogue_user_source_text(transcript, latest_utterance="嗯，都行吧")

    pasted = DialogueExtraction(
        turn_id=1,
        atomic_evidence=[card("resource_preference", "preference_specificity", question)],
        extraction_method="llm",
    )
    cleaned = modes_module._validate_dialogue_extraction(pasted, user_text)
    assert cleaned.atomic_evidence == [], "AI 的问句被当成了用户的表达"

    # 用户自己说的那句仍然要保留，不能因为拦粘贴就把真话一起丢了。
    real = DialogueExtraction(
        turn_id=1,
        atomic_evidence=[card("resource_preference", "preference_specificity", "都行吧")],
        extraction_method="llm",
    )
    kept = modes_module._validate_dialogue_extraction(real, user_text)
    assert len(kept.atomic_evidence) == 1


def test_scenario_retelling_an_old_turn_is_not_new_evidence(tmp_path: Path) -> None:
    """第二轮用户只说"就这些了"，模型却又把第一轮那句抽了一遍。

    整段对话都在上下文里，模型很容易把旧话当新证据再交一次。这条守的是
    "同一表达只形成一次认定"：卡数不涨，重复抽取不进入会话。
    """

    rig = _Rig(tmp_path)
    first = "我想考执业药师"
    rig.say(first, [card("learning_goal", "goal_specificity", first)])
    rig.say("就这些了", [card("learning_goal", "goal_specificity", first)])

    assert len(rig.cards("learning_goal")) == 1, "旧话被记成了第二条证据"
    assert rig.collected("learning_goal") == {"goal_specificity"}
    assert len(rig.session.evidence_cards) == 1


# ---------------------------------------------------------------------------
# 场景五：前后矛盾（"先说没时间，后说其实挺多"）
# ---------------------------------------------------------------------------


def test_scenario_contradiction_keeps_both_sides_instead_of_overwriting(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    rig.say("每天只有十分钟", [card("pace", "time_feasibility", "每天只有十分钟")])
    rig.say(
        "其实我每天能学两小时，刚才说少了",
        [
            card(
                "pace",
                "time_feasibility",
                "其实我每天能学两小时",
                claim="每天两小时",
                relation="conflict",
            )
        ],
    )

    cards = rig.cards("pace")
    assert len(cards) == 2, "矛盾陈述被静默覆盖成了一条"
    claims = {item.normalized_claim for item in cards}
    assert {"每天只有十分钟", "每天两小时"} <= claims
    # 冲突证据要能被人看见，不能只留最后一句。
    assert {item.semantic_relation for item in cards} & {"conflict", "new"}


# ---------------------------------------------------------------------------
# 场景六：跑题（"来聊天"的用户）
# ---------------------------------------------------------------------------


def test_scenario_off_topic_answer_yields_nothing_and_keeps_the_session_alive(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    rig.say("今天天气不错，外面在下雨")
    rig.say("我还没想好，随便吧")

    assert rig.session.evidence_cards == []
    assert rig.session.current_step is not None, "跑题不该把会话卡死"


# ---------------------------------------------------------------------------
# 场景七：留白（直接发空）
# ---------------------------------------------------------------------------


def test_scenario_blank_utterance_never_becomes_evidence(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    rig.say("")

    assert rig.session.evidence_cards == []
    assert rig.session.current_step is not None


# ---------------------------------------------------------------------------
# 场景八：只有一维证据时，其余维度不得出现在结论里
# ---------------------------------------------------------------------------


def test_scenario_single_dimension_profile_has_no_other_conclusions(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    rig.say("我想考执业药师", [card("learning_goal", "goal_specificity", "我想考执业药师")])

    finished = rig.finish()

    # 1/5 = 20%，连学习目标自己都过不了 60% 门槛，所以这一轮不该有任何维度出分。
    fusion = modes_module._dialogue_fusion(finished.evidence_cards)
    assert all(result.score is None for result in fusion.values()), "证据不足却出了分"

    # 没有维度的分数变动，就是"什么都没测出来"；有变动则必须是 0 且未点亮。
    changes = finished.step_data.get("radar_changes") or []
    for change in changes:
        assert change["newly_lit"] is False, f"{change['key']} 无证据却点亮了"
        assert change["delta"] == 0, f"{change['key']} 无证据却涨了 {change['delta']} 分"


# ---------------------------------------------------------------------------
# 剧情侧：六类真实学法
# ---------------------------------------------------------------------------


def _drive(session, payload: dict, db_path: Path):
    session, _ = asyncio.run(
        answer_profile_session(
            session.session_id, USER, session.current_step.id, payload, db_path=db_path
        )
    )
    return session


def _story(tmp_path: Path, *, correct: str = "all", identity: str = "临床学徒", extras: bool = True):
    """五幕全流程。`correct` 取 all / none / mixed，代表三类真实考生。"""

    db_path = tmp_path / "story.db"
    session = start_profile_session(USER, "apprentice", db_path=db_path)

    session = _drive(
        session,
        {
            "identity_card": identity,
            "utterance": "想跟门诊抄方，明年春天能上手",
            "story_answers": (
                {"extra_goal_scene": "想跟门诊抄方", "extra_goal_priority": "配伍剂量"}
                if extras
                else {}
            ),
            "response_time_ms": 6000,
        },
        db_path,
    )

    questions = get_profile_session(session.session_id, USER, db_path=db_path).step_data["questions"]
    answers = []
    for index, question in enumerate(questions):
        options = question.get("options") or [""]
        right = question.get("answer") or options[0]
        if correct == "all":
            picked = right
        elif correct == "none":
            # 不能直接用最后一个选项：抽到的题里它有时正好就是正确答案。
            picked = next((option for option in options if option != right), options[-1])
        else:
            picked = right if index % 2 == 0 else next((option for option in options if option != right), options[-1])
        answers.append(
            {
                "question_id": question["id"],
                "selected": picked,
                "response_time_ms": 5000,
                # 第一题补一句自述：知识基础的第 5 个组件（概念地基）只能从
                # 文字回答里读出来，只选选项拿不到 5/5——这是真实的答题形态。
                **({"free_text": "补气药都差不多，具体记不清"} if index == 0 else {}),
            }
        )

    session = _drive(
        session,
        {
            "answers": answers,
            "response_time_ms": 5000,
            "story_answers": {"extra_weak_cross_context": "黄芪和党参我总分不清"} if extras else {},
        },
        db_path,
    )
    session = _drive(
        session,
        {
            "case": "热毒咽痛",
            "prescription": "金银花15g，连翘12g",
            "preference_note": "我更喜欢按证型推",
            "response_time_ms": 7000,
        },
        db_path,
    )
    session = _drive(
        session,
        {
            "resource_type": "医案文选",
            "story_answers": (
                {
                    "extra_style_memory": "背口诀",
                    "extra_style_compare": "知识图谱",
                    "extra_resource_reason": "病例更接近真实门诊",
                    "extra_resource_constraint": "手机",
                }
                if extras
                else {}
            ),
            "response_time_ms": 4000,
        },
        db_path,
    )
    session = _drive(
        session,
        {
            "daily_minutes": 30,
            "target_days": 45,
            "accept_challenge": False,
            "story_answers": (
                {
                    "extra_pace_window": "晚上",
                    "extra_pace_recover": "从上次停下的那味药接着看",
                    "extra_motivation_conflict": "先把考试顶过去",
                    "extra_weak_recover": "把记错的药抄一遍",
                }
                if extras
                else {}
            ),
            "response_time_ms": 9000,
        },
        db_path,
    )
    return session, db_path


def _coverage(session, dimension: str) -> float:
    return len(modes_module._dialogue_collected_components(dimension, session.evidence_cards)) / 5


@pytest.mark.parametrize("correct", ["all", "mixed", "none"])
def test_scenario_story_three_kinds_of_examinee_still_reach_the_gate(tmp_path: Path, correct: str) -> None:
    """答对、答错、半对半错——三种人都必须能走完，且非知识维不受答题影响。"""

    session, _ = _story(tmp_path, correct=correct)

    assert session.finished is True
    assert _coverage(session, "knowledge_base") == 1.0
    for dimension in COMPONENTS:
        if dimension == "knowledge_base":
            continue
        assert _coverage(session, dimension) >= COVERAGE_THRESHOLD, f"{correct}：{dimension} 没过门槛"


def test_scenario_story_wrong_answers_cannot_look_competent(tmp_path: Path) -> None:
    """全错的人不能拿到"还不错"的知识基础分——这是最容易骗过自己的地方。"""

    session, _ = _story(tmp_path, correct="none")
    measured = [
        item
        for item in session.evidence_cards
        if item.dimension == "knowledge_base" and item.component == "measured_performance"
    ]
    assert measured
    assert all(item.objective_signal.get("correct") is False for item in measured)


def test_scenario_story_custom_identity_is_traceable(tmp_path: Path) -> None:
    """用户选一个我们没预设的身份，也要原样留痕，不能被替换成默认卡。

    身份卡落在 `learning_goal.expression_certainty`：它证明的是"他说得确定"，
    不是"他的目标是什么"，所以这里只查原话有没有被保存下来。
    """

    session, _ = _story(tmp_path, identity="民间草医")
    identity_cards = [
        item
        for item in session.evidence_cards
        if item.component == "expression_certainty"
    ]
    assert identity_cards, "自定义身份没有落成证据"
    # quote 与 fact 要分别看：quote 是用户的回答、fact 是这张卡的判定，
    # 卡名可能只出现在其中一边（写成 `quote or fact` 会漏掉后者）。
    assert any(
        "民间草医" in (item.quote or "") or "民间草医" in (item.fact or "")
        for item in identity_cards
    )
    # 卡名本身也要被原样记下来，不能只留一个"未知身份"。
    assert any(
        item.objective_signal.get("identity_card") == "民间草医" for item in identity_cards
    )


def test_scenario_story_skipping_every_follow_up_leaves_gaps_visible(tmp_path: Path) -> None:
    """全留白：会话仍要结束，但依赖追问的维度必须过不了门槛。"""

    session, _ = _story(tmp_path, extras=False)

    assert session.finished is True
    for dimension in ("learning_style", "resource_preference", "pace"):
        coverage = _coverage(session, dimension)
        assert coverage < COVERAGE_THRESHOLD, f"{dimension} 留白仍有 {coverage:.0%}"
