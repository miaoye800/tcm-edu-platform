"""真实模型抽样后暴露的三处偏差的回归测试。

来历：用 8 次真实调用跑 ``scripts/probe_live_semantics.py``（剧情三批 + 对话四句
+ 单维评分一次），发现三处「模型给了、我们没接住 / 接得太宽松」：

1. ``dimension_scan`` 返回成 ``{found, basis}`` 字典时被整表落成 pending，
   理由还写成"模型未返回证据"——与同一份 JSON 里的原子证据自相矛盾。
2. 同一次返回里 scan 缺行、但原子证据是全的，界面显示"这一维没抽到"。
3. 评分对「本轮无证据」的组件照收模型给的中性 50 分，覆盖度凭空 5/5。

这里不联网：全部用纯函数回放录制下来的返回形状。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.profile.evidence import EvidenceTier
from app.profile.jury import _parse_jury_output
from app.profile.modes import _normalize_dialogue_extraction_payload
from app.profile.schema import EvidenceCard


def _atomic(dimension: str, component: str, quote: str) -> dict:
    return {
        "evidence_id": f"{dimension}-{component}",
        "dimension": dimension,
        "component": component,
        "evidence_group_id": f"turn-1-{dimension}",
        "source_turn_ids": [1],
        "quote": quote,
        "normalized_claim": quote,
        "evidence_type": "self_report",
        "collection_status": "confirmed",
        "semantic_relation": "new",
        "quality_factors": {"expression_clarity": 0.6, "content_specificity": 0.6, "consistency": 0.5},
    }


def _scan_by_dimension(payload: dict) -> dict[str, dict]:
    return {row["dimension"]: row for row in payload["dimension_scan"]}


def test_dict_scan_with_found_and_basis_is_not_pending() -> None:
    """模型用 found/basis 表达扫描结论时，不能全部落成待取证。"""

    normalized = _normalize_dialogue_extraction_payload(
        {
            "dimension_scan": {
                "weak_points": {"found": True, "basis": "「方剂背不住」是自述记忆困难"},
                "learning_goal": {"found": False, "basis": "原话未表达学习目的"},
            },
            "atomic_evidence": [_atomic("weak_points", "recall_correctness", "方剂背不住")],
        }
    )

    rows = _scan_by_dimension(normalized)
    assert rows["weak_points"]["status"] == "evidence_found"
    assert "方剂背不住" in rows["weak_points"]["reason"]
    assert rows["learning_goal"]["status"] == "no_usable_evidence"
    assert "未表达学习目的" in rows["learning_goal"]["reason"]
    # 旧实现会写"模型未返回该维度的可追溯原子证据"，与卡片区自相矛盾。
    assert "未返回该维度的可追溯" not in rows["weak_points"]["reason"]


def test_scan_pending_is_backfilled_from_atomic_evidence() -> None:
    """扫描说 pending、原子证据却是 scorable 时，以证据为准并说明回填。"""

    normalized = _normalize_dialogue_extraction_payload(
        {
            "dimension_scan": [{"dimension": "weak_points", "status": "pending", "reason": ""}],
            "atomic_evidence": [_atomic("weak_points", "recall_correctness", "方剂背不住")],
        }
    )

    row = _scan_by_dimension(normalized)["weak_points"]
    assert row["status"] == "evidence_found"
    assert "原子证据回填" in row["reason"]
    assert row["components"] == ["recall_correctness"]


def test_scan_claim_without_any_card_is_downgraded() -> None:
    """扫描说"找到了"、却没有任何原子卡时，不能据此宣称有证据。"""

    normalized = _normalize_dialogue_extraction_payload(
        {
            "dimension_scan": {
                "resource_preference": {"found": True, "basis": "「B站看视频多」是平台偏好"},
            },
            "atomic_evidence": [_atomic("learning_style", "preference_clarity", "B站上看视频多")],
        }
    )

    row = _scan_by_dimension(normalized)["resource_preference"]
    assert row["status"] == "pending"
    assert "没有给出可追溯的证据卡" in row["reason"]


def test_tentative_evidence_shows_pending_not_missing() -> None:
    """有卡但只到 tentative/pending 时，状态应是"待确认"，不是"没有证据"。"""

    tentative = {**_atomic("motivation", "intrinsic_value", "以后能自己看病"), "collection_status": "tentative"}
    normalized = _normalize_dialogue_extraction_payload(
        {
            "dimension_scan": {"motivation": {"basis": "「以后能自己看病」是价值取向"}},
            "atomic_evidence": [tentative],
        }
    )

    row = _scan_by_dimension(normalized)["motivation"]
    assert row["status"] == "pending"
    assert "待确认" in row["reason"]


def test_missing_scan_row_is_added_for_dimension_with_evidence() -> None:
    """模型漏掉整行扫描时，界面仍要能看到这一维有证据。"""

    normalized = _normalize_dialogue_extraction_payload(
        {
            "dimension_scan": [{"dimension": "learning_goal", "status": "no_usable_evidence", "reason": "未表达目标"}],
            "atomic_evidence": [_atomic("motivation", "intrinsic_value", "我想治病救人")],
        }
    )

    rows = _scan_by_dimension(normalized)
    assert rows["motivation"]["status"] == "evidence_found"
    assert rows["motivation"]["components"] == ["intrinsic_value"]
    assert rows["learning_goal"]["status"] == "no_usable_evidence"


def _card(component: str) -> EvidenceCard:
    return EvidenceCard(
        dimension="learning_goal",
        component=component,
        source="五幕回顾",
        tier=EvidenceTier.SELF_REPORT,
        fact="用户计划两年后考中医内科研究生",
        normalized_claim="用户计划两年后考中医内科研究生",
        objective_signal={"quote": "两年后想考中医内科的研究生", "evidence_type": "self_report"},
        weight=1.0,
        quote="两年后想考中医内科的研究生",
        extraction_confidence=0.8,
        extraction_method="llm",
        collection_status="scorable",
        evidence_group_id=f"turn-1-{component}",
        timestamp=datetime.now(UTC),
    )


def _jury_raw(components: list[str]) -> str:
    def block(component: str, score: float) -> dict:
        return {
            "component": component,
            "tier": "medium",
            "jury": {
                "严师": {"score": score - 8, "reason": "证据只支持中档"},
                "宽师": {"score": score + 8, "reason": "动作已经出现"},
                "中立": {"score": score, "reason": "方向清楚但缺验收"},
            },
            "score": score,
            "missing": None,
        }

    return json.dumps(
        {
            "dimension": "学习目标",
            "dimension_tier": "medium",
            "narrative": "有方向但缺验收标准。",
            "component_scores": [block(component, 55.0) for component in components],
        },
        ensure_ascii=False,
    )


def test_jury_ignores_score_for_component_without_evidence() -> None:
    """无证据组件即使模型给了分，也不进覆盖度（否则 4/5 会显示成 5/5）。"""

    cards = [_card(component) for component in ("goal_specificity", "expression_certainty", "action_orientation", "priority_signal")]
    # 模型对第 5 个组件（本轮没有任何卡）也给了 50 分，并自认缺多轮证据。
    result = _parse_jury_output(
        "learning_goal",
        cards,
        _jury_raw(["goal_specificity", "expression_certainty", "action_orientation", "priority_signal", "cross_turn_stability"]),
    )

    assert "cross_turn_stability" not in result.component_scores
    assert result.coverage == 0.8
    assert any("估分未采信" in reason for reason in result.unknown_reasons)


def test_jury_keeps_carried_score_when_component_has_no_new_evidence() -> None:
    """上一轮评过的组件仍可沿用旧分，这条不能被"无证据"规则一起拦掉。"""

    cards = [_card("goal_specificity"), _card("expression_certainty")]
    cards[0].objective_signal = {**cards[0].objective_signal, "component_score": 61.0}

    result = _parse_jury_output("learning_goal", cards, _jury_raw(["expression_certainty"]))

    assert result.component_scores["goal_specificity"] == 61.0
    assert "goal_specificity" in result.carried_components


def test_fallback_signals_keep_legacy_path_alive_without_legacy_contract() -> None:
    """新契约只回 atomic_evidence；拆不出来的整体标签走 fallback_signals。

    旧契约要求每轮同时输出 11 个 legacy 对象（每个还带 7 项 assessment），
    单轮输出被撑到 1 万多字符。现在改成"能拆成原子证据就不填"，但
    "拆不出来"这条兜底路径必须还在——否则模型一旦只说了一个整体标签，
    这一轮就什么都不会留下。
    """

    from app.profile.nodes import DialogueExtraction, _dialogue_cards

    payload = _normalize_dialogue_extraction_payload(
        {
            "turn_id": 2,
            "atomic_evidence": [],
            "dimension_scan": [],
            "fallback_signals": {
                "learning_goal": {"value": "学术深造", "evidence": ["两年后想考中医内科的研究生"]},
                # 只给字符串也要能收下。
                "base_level": "入门",
            },
        }
    )

    assert payload["learning_goal"]["confidence"] == 0.6
    assert payload["learning_goal"]["basis"] == "explicit"
    assert payload["base_level"]["value"] == "入门"

    extraction = DialogueExtraction.model_validate(payload)
    assert extraction.learning_goal.value == "学术深造"
    cards = _dialogue_cards(extraction)
    assert {card.dimension for card in cards} >= {"learning_goal"}, "兜底标签必须真的落成卡片"
    goal_card = next(card for card in cards if card.dimension == "learning_goal")
    assert goal_card.quote == "两年后想考中医内科的研究生"
