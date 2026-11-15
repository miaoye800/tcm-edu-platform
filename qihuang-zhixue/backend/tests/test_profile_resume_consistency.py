"""「退出重进」与「档案一致」的回归测试。

现场表现：聊完一局对话收档，再进弟子档案是空的；重新点开对话式，七张牌
全部停在"待取证"，之前的对话也看不到。

两条根因：

1. 组分分是评分阶段写在卡片上的派生值，而证据行是在评分**之前**落库的。
   新开一局时从库里捞回来的卡因此没有 `component_score`，融合判定"一个组件
   都没评分"，所有牌按"评分暂缓"盖住——画像明明已经存在。
2. 薄弱点牌面按 `cards[0]["label"]` 取主值，而组成卡的字段叫 `display_name`。
   这条路径只在"薄弱点已评分并翻开"时走到，于是最亮的那一维直接把接口打成
   500，前端只能显示"没有内容"。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.profile.modes import dialogue_session_summary, start_profile_session, _profile_from_fusion
from app.profile.nodes import _profile_from_scoring_results
from app.profile.schema import DIMENSION_LABELS, create_blank_profile
from app.profile.store import add_evidence, get_profile, upsert_profile
from app.profile.update import profile_to_response, read_profile_snapshot
from app.profile.evidence import EvidenceCard
from app.profile.evidence import EvidenceTier


def _card(dimension: str, component: str, quote: str, **signal: object) -> EvidenceCard:
    return EvidenceCard(
        dimension=dimension,
        source="对话原子证据",
        tier=EvidenceTier.SELF_REPORT,
        fact=quote,
        quote=quote,
        weight=1.0,
        component=component,
        collection_status="scorable",
        objective_signal={"evidence": [quote], "question_type": "自述", **signal},
    )


KNOWLEDGE_COMPONENTS = {
    "concept_foundation": 40.0,
    "knowledge_scope": 50.0,
    "retrieval_application": 60.0,
    "boundary_awareness": 70.0,
}


def _seed_scored_user(db_path: Path, user_id: str) -> None:
    profile = create_blank_profile(user_id)
    profile.knowledge_base.component_scores = dict(KNOWLEDGE_COMPONENTS)
    profile.knowledge_base.scoring_phase = "scored"
    profile.knowledge_base.confidence = 0.6
    profile.knowledge_base.radar_score = 55.0
    upsert_profile(profile, db_path=db_path)
    for component in KNOWLEDGE_COMPONENTS:
        add_evidence(
            user_id,
            _card("knowledge_base", component, f"{component} 的原话"),
            db_path=db_path,
        )


def test_reopening_a_dialogue_restores_the_component_scores(tmp_path: Path) -> None:
    db_path = tmp_path / "resume.db"
    _seed_scored_user(db_path, "resume-user")

    session = start_profile_session("resume-user", "dialogue", db_path=db_path)

    assert session.evidence_cards, "已落库的证据必须接进这次对话"
    missing = [
        card.component
        for card in session.evidence_cards
        if not isinstance(card.objective_signal.get("component_score"), (int, float))
    ]
    assert missing == [], f"继承来的证据缺组分分：{missing}"

    summary = dialogue_session_summary(session, db_path=db_path)
    axis = next(item for item in summary["radar"]["axes"] if item["key"] == "knowledge_base")
    assert axis["lit"] is True
    assert axis["value"] > 0
    card = next(item for item in summary["identity_cards"] if item["key"] == "knowledge_base")
    assert card["revealed"] is True, "档案里已经评过的维度，回到对话里不该再是待取证"


def test_dialogue_and_archive_report_the_same_number(tmp_path: Path) -> None:
    db_path = tmp_path / "same-number.db"
    _seed_scored_user(db_path, "same-user")

    session = start_profile_session("same-user", "dialogue", db_path=db_path)
    summary = dialogue_session_summary(session, db_path=db_path)
    archive = profile_to_response(read_profile_snapshot("same-user", db_path=db_path))

    live = {axis["key"]: axis["value"] for axis in summary["radar"]["axes"]}
    stored = {axis["key"]: axis["value"] for axis in archive["radar"]["axes"]}
    assert live["knowledge_base"] == stored["knowledge_base"]


def test_low_confidence_but_scored_dimension_still_shows_its_conclusion(tmp_path: Path) -> None:
    """置信度低 ≠ 没有结论。

    `add_evidence` 会给自述类证据打 `claim_status=pending`，评分阶段因此把置信度
    压到 0.35。档案里这一维仍然有分（`confidence_status` 是"已收集到"），对话里
    却按 0.5 阈值退回"待取证"——同一份证据两个答案。翻开与否只看"有没有结论"，
    置信度只影响措辞。
    """

    db_path = tmp_path / "low-confidence.db"
    user_id = "low-user"
    profile = create_blank_profile(user_id)
    profile.knowledge_base.component_scores = dict(KNOWLEDGE_COMPONENTS)
    profile.knowledge_base.scoring_phase = "scored"
    profile.knowledge_base.confidence = 0.35
    profile.knowledge_base.score = 33.25
    profile.knowledge_base.radar_score = 33.25
    profile.knowledge_base.coverage = 0.8
    upsert_profile(profile, db_path=db_path)
    for component in KNOWLEDGE_COMPONENTS:
        add_evidence(user_id, _card("knowledge_base", component, f"{component} 的原话"), db_path=db_path)

    session = start_profile_session(user_id, "dialogue", db_path=db_path)
    summary = dialogue_session_summary(session, db_path=db_path)
    axis = next(item for item in summary["radar"]["axes"] if item["key"] == "knowledge_base")
    card = next(item for item in summary["identity_cards"] if item["key"] == "knowledge_base")

    assert axis["confidence_status"] == "已收集到"
    assert card["revealed"] is True
    assert card["confidence_status"] == axis["confidence_status"], "档案与对话必须同一套口径"
    assert card["confidence"] == axis["confidence"]
    assert abs(float(card["score"]) - float(axis["value"])) < 0.1, "牌面分与雷达值必须是同一个数"


def test_unscored_dimension_shows_no_number_anywhere(tmp_path: Path) -> None:
    """覆盖度不足的维度在档案里不能显示分。

    `derived_score` 会把"只有一个标签"算成 95，而对话里同一维显示"待取证"，
    两边就会打架。
    """

    db_path = tmp_path / "unscored.db"
    user_id = "unscored-user"
    profile = create_blank_profile(user_id)
    profile.learning_goal.label = "学术深造"
    profile.learning_goal.distribution = {"学术深造": 1.0}
    profile.learning_goal.scoring_phase = "collecting"
    profile.learning_goal.component_matrix = {"goal_specificity": {"status": "collected", "score": None}}
    profile.learning_goal.confidence = 0.6
    upsert_profile(profile, db_path=db_path)
    add_evidence(
        user_id,
        _card("learning_goal", "goal_specificity", "学医是为了治病救人"),
        db_path=db_path,
    )

    axis = next(
        item
        for item in profile_to_response(read_profile_snapshot(user_id, db_path=db_path))["radar"]["axes"]
        if item["key"] == "learning_goal"
    )
    assert axis["lit"] is False
    assert axis["value"] == 0


def test_weak_point_card_never_crashes_and_ignores_page_skips(tmp_path: Path) -> None:
    db_path = tmp_path / "weak-points.db"
    user_id = "weak-user"
    profile = create_blank_profile(user_id)
    profile.weak_points.component_scores = {
        "recall_correctness": 30.0,
        "application_correctness": 40.0,
        "measured_validation": 50.0,
    }
    profile.weak_points.scoring_phase = "scored"
    profile.weak_points.confidence = 0.6
    profile.weak_points.radar_score = 35.0
    upsert_profile(profile, db_path=db_path)

    add_evidence(user_id, _card("weak_points", "recall_correctness", "清热药剂量总记混"), db_path=db_path)
    add_evidence(user_id, _card("weak_points", "application_correctness", "相似药分不清"), db_path=db_path)
    add_evidence(user_id, _card("weak_points", "measured_validation", "小测错了三道"), db_path=db_path)
    # 页面级埋点：不是学业薄弱点，不能变成牌面上的标签。
    add_evidence(
        user_id,
        EvidenceCard(
            dimension="weak_points",
            source="纯被动埋点",
            tier=EvidenceTier.BEHAVIORAL,
            fact="skip：画像引导",
            weight=1.0,
            objective_signal={"event_type": "skip", "topic": "画像引导", "question_type": "行为"},
        ),
        db_path=db_path,
    )

    session = start_profile_session(user_id, "dialogue", db_path=db_path)
    summary = dialogue_session_summary(session, db_path=db_path)
    card = next(item for item in summary["identity_cards"] if item["key"] == "weak_points")

    assert card["revealed"] is True
    assert card["display_value"], "翻开的牌面必须有主值"
    assert "%" in card["composition_summary"], "薄弱点也要说清什么占了多少"
    labels = [str(item.get("display_name") or "") for item in card.get("items") or []]
    assert "画像引导" not in labels


def test_dialogue_turn_keeps_the_rest_of_the_archive(tmp_path: Path) -> None:
    """对话轮次只该改写"这一局谈到"的维度，不能把整份档案换成快照。

    现场表现：确认完学习目标、结束对话进弟子档案，目标、规划参数全没了——
    因为每轮都用"这一局证据能推出来的画像"整表覆盖，没谈到的部分是默认值。
    """

    db_path = tmp_path / "keep.db"
    user_id = "keep-user"
    profile = create_blank_profile(user_id)
    profile.goal.raw_text = "两年后考研，先把中药学补起来"
    profile.goal.normalized = "学术深造"
    profile.goal.confirmed_by_user = True
    profile.goal.confidence = 0.8
    profile.planning_params.depth = "深"
    profile.planning_params.pace = "紧"
    profile.learning_goal.label = "学术深造"
    profile.learning_goal.confidence = 0.72
    upsert_profile(profile, db_path=db_path)

    only_knowledge = [
        _card("knowledge_base", "concept_foundation", "麻黄汤的君药是麻黄"),
    ]
    merged = _profile_from_fusion(
        user_id,
        only_knowledge,
        base=get_profile(user_id, db_path=db_path),
    )

    assert merged.goal.confirmed_by_user is True
    assert merged.goal.raw_text == "两年后考研，先把中药学补起来"
    assert merged.planning_params.depth == "深"
    assert merged.learning_goal.label == "学术深造", "这一局没谈目标，不能把目标抹掉"
    assert merged.learning_goal.confidence == 0.72
    assert merged.knowledge_base.evidence, "谈到的那一维要跟着更新"


def test_finishing_a_dialogue_keeps_confirmed_goal_and_planning(tmp_path: Path) -> None:
    """收尾重判同样只覆盖本局证据涉及的维度。"""

    from app.profile.jury import score_profile

    db_path = tmp_path / "finish.db"
    user_id = "finish-user"
    profile = create_blank_profile(user_id)
    profile.goal.raw_text = "考公，同时把中基补一补"
    profile.goal.confirmed_by_user = True
    profile.planning_params.breadth = "考点导向"
    profile.learning_goal.label = "应试提分"
    upsert_profile(profile, db_path=db_path)

    cards = [_card("knowledge_base", "concept_foundation", "四气五味我还分不太清")]
    scoring = asyncio.run(score_profile(cards))
    merged = _profile_from_scoring_results(
        user_id=user_id,
        scoring=scoring,
        evidence_cards=cards,
        base=get_profile(user_id, db_path=db_path),
    )

    assert merged.goal.confirmed_by_user is True
    assert merged.goal.raw_text == "考公，同时把中基补一补"
    assert merged.planning_params.breadth == "考点导向"
    assert merged.learning_goal.label == "应试提分"


def test_three_of_five_components_is_a_conclusion_not_a_pending_score() -> None:
    """覆盖度够就出分，相位必须跟着分走。

    现场是"只聊出了学习目标"：3/5 个组件、覆盖 60%、分已经算出来 53。
    旧实现要求 5 个组件全齐才写 `scored`，于是同一维在对话牌面是 53 分、
    进弟子档案变成"派生值·待补充"。
    """

    from app.profile.jury import ScoringResult

    components = {
        "expression_certainty": 55.0,
        "goal_specificity": 53.0,
        "action_orientation": 58.33,
    }
    cards = [_card("learning_goal", component, f"{component} 原话") for component in components]
    scoring = {
        dimension: ScoringResult(dimension=DIMENSION_LABELS[dimension], dimension_key=dimension)
        for dimension in DIMENSION_LABELS
    }
    scoring["learning_goal"] = ScoringResult(
        dimension=DIMENSION_LABELS["learning_goal"],
        dimension_key="learning_goal",
        aggregated_score=53.14,
        radar_score=53.14,
        component_scores=components,
        confidence=0.68,
        coverage=0.6,
    )

    profile = _profile_from_scoring_results(user_id="partial-user", scoring=scoring, evidence_cards=cards)
    goal = profile.learning_goal

    assert goal.scoring_phase == "scored", "有分就是有结论，缺两项不能再把结论判回去"
    assert goal.radar_score == 53.14
    assert set(goal.pending_components) == {"priority_signal", "cross_turn_stability"}

    payload = profile_to_response(profile)
    assert payload["learning_goal"]["score"] == 53.14, "分类维度没有 score 字段，接口要补同一个分"
    axis = next(item for item in payload["radar"]["axes"] if item["key"] == "learning_goal")
    assert axis["lit"] is True
    assert round(axis["value"]) == 53
