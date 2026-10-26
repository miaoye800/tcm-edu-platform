import asyncio
import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import Settings
from app.llm.clients import ChatMessage
from app.llm.router import ModelRouter
from app.profile.fusion import decay_factor, effective_card_weights, fuse_dimension
from app.profile.guardrail import apply_guardrail
from app.profile.jury import score_dimension
from app.profile.modes import _dialogue_fusion
from app.profile.schema import EvidenceCard, EvidenceTier
from app.profile.store import add_evidence
from app.profile.update import _init_update_state, get_latest_profile, profile_to_response, update_profile_from_event


def test_fusion_uses_weighted_decay_formula() -> None:
    now = datetime(2026, 7, 2, tzinfo=UTC)
    cards = [
        _card("knowledge_base", {"value": 100}, 1.0, now),
        _card("knowledge_base", {"value": 0}, 1.0, now - timedelta(days=30)),
    ]

    result = fuse_dimension("knowledge_base", cards, now=now)

    assert decay_factor(cards[1], now=now) == 0.5
    assert result.score == 66.67
    assert result.evidence_refs == ["测试#1", "测试#2"]
    assert result.confidence < 1


def test_agent_assessment_keeps_observed_card_ratio_separate_from_confirmation() -> None:
    assessment = {
        "radar_score": 72,
        "confidence_contribution": 0.31,
        "coverage": 0.24,
        "priority_strength": 0.7,
        "independence": 0.9,
        "unknown_reasons": ["其他目标尚未谈及"],
    }
    cards = [
        _card("learning_goal", {"label": "学术深造", "agent_assessment": assessment, "evidence_unit_id": "turn-1"}, 1.0),
        # A second card from the same answer must not become a second independent confirmation.
        _card("learning_goal", {"label": "学术深造", "agent_assessment": {**assessment, "confidence_contribution": 0.2}, "evidence_unit_id": "turn-1"}, 1.0),
    ]

    result = fuse_dimension("learning_goal", cards)

    assert result.distribution == {"学术深造": 1.0}
    assert result.radar_score == 72
    assert result.confidence == 0.279
    assert result.coverage == 0.24
    assert result.unknown_reasons == ["其他目标尚未谈及"]
    assert result.confidence_breakdown["independent_units"] == 1.0


def test_no_key_skips_jury_and_uses_rule_fusion() -> None:
    result = asyncio.run(
        score_dimension(
            "knowledge_base",
            [_card("knowledge_base", {"correct": True}, 1.2)],
            settings=_no_key_settings(),
        )
    )

    assert result.method == "fusion"
    assert result.jury == {}
    assert result.aggregated_score == 100
    assert result.provenance == "no_key_rule_fallback"


def test_no_key_provenance_persists_from_update_to_profile_response(tmp_path: Path) -> None:
    result = asyncio.run(
        update_profile_from_event(
            "provenance-user",
            {"type": "quiz", "payload": {"correct": True}},
            db_path=tmp_path / "provenance.db",
            jury_weight_threshold=0,
            settings=_no_key_settings(),
        )
    )
    payload = profile_to_response(result.profile)

    assert result.profile.knowledge_base.evidence[0].objective_signal["scoring_provenance"] == "no_key_rule_fallback"
    assert payload["provenance"]["knowledge_base"] == "no_key_rule_fallback"
    assert payload["radar"]["axes"][0]["provenance"] == "no_key_rule_fallback"


def test_blank_key_skips_jury_and_uses_rule_fusion() -> None:
    settings = _no_key_settings()
    settings.deepseek_api_key = "   "

    result = asyncio.run(
        score_dimension(
            "knowledge_base",
            [_card("knowledge_base", {"correct": True}, 1.2)],
            settings=settings,
        )
    )

    assert result.method == "fusion"
    assert result.jury == {}


def test_jury_falls_back_to_fusion_when_router_returns_mock_text() -> None:
    settings = _key_settings()
    router = ModelRouter(settings=_no_key_settings())

    result = asyncio.run(
        score_dimension(
            "knowledge_base",
            [_card("knowledge_base", {"correct": True}, 1.2)],
            settings=settings,
            router=router,
        )
    )

    assert result.method == "fusion"
    assert result.jury == {}
    assert "回退规则融合" in result.narrative


def test_invalid_injected_jury_json_also_falls_back_to_rules() -> None:
    async def invalid_json(_messages: Sequence[ChatMessage]) -> str:
        return "not-json"

    result = asyncio.run(
        score_dimension(
            "knowledge_base",
            [_card("knowledge_base", {"correct": True}, 1.2)],
            settings=_key_settings(),
            llm_call=invalid_json,
        )
    )

    assert result.method == "fusion"
    assert result.provenance == "rule_fallback"


def test_jury_single_call_outputs_three_views_and_median_score() -> None:
    async def llm_call(messages: Sequence[ChatMessage]) -> str:
        assert "严师、宽师、中立三视角" in str(messages[0]["content"])
        return json.dumps(
            {
                "dimension": "知识基础",
                "jury": {
                    "严师": {"score": 62, "reasoning_chain": ["剂量证据扣分"], "comment": "清热药剂量把握不足"},
                    "宽师": {"score": 72, "reasoning_chain": ["配伍证据加分"], "comment": "解表配伍已见功底"},
                    "中立": {"score": 66, "reasoning_chain": ["整体中等偏上"], "comment": "证据有优有弱"},
                },
                "evidence_refs": ["抓药实战#1"],
                "narrative": "该弟子解表药辨识与配伍扎实，但清热药剂量偏保守。",
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        score_dimension(
            "knowledge_base",
            [_card("knowledge_base", {"对症": True, "配伍": True, "剂量": False, "误用毒烈": False}, 1.5)],
            settings=_key_settings(),
            llm_call=llm_call,
        )
    )

    assert result.method == "jury"
    assert result.aggregated_score == 66
    assert result.variance == 16.89
    assert result.confidence < 0.5
    assert result.jury["严师"].reasoning_chain == ["剂量证据扣分"]


def test_jury_router_call_uses_flash_model_without_thinking() -> None:
    router = CapturingRouter(settings=_key_settings())

    result = asyncio.run(
        score_dimension(
            "knowledge_base",
            [_card("knowledge_base", {"value": 66}, 1.0)],
            settings=_key_settings(),
            router=router,
        )
    )

    assert result.method == "jury"
    assert router.calls == [
        {
            "task_type": "profile_jury",
            "thinking": {"type": "disabled"},
            "max_tokens": 1200,
            "temperature": 0,
        }
    ]


def test_jury_self_check_samples_once_only_when_variance_crosses_threshold() -> None:
    calls: list[str] = []

    async def llm_call(messages: Sequence[ChatMessage]) -> str:
        calls.append(str(messages[1]["content"]))
        if len(calls) == 1:
            scores = (10, 90, 50)
        else:
            scores = (60, 70, 65)
        return json.dumps(
            {
                "dimension": "知识基础",
                "jury": {
                    "严师": {"score": scores[0], "reasoning_chain": ["严"], "comment": "严"},
                    "宽师": {"score": scores[1], "reasoning_chain": ["宽"], "comment": "宽"},
                    "中立": {"score": scores[2], "reasoning_chain": ["中"], "comment": "中"},
                },
                "narrative": "自洽采样测试",
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        score_dimension(
            "knowledge_base",
            [_card("knowledge_base", {"value": 65}, 1.0)],
            settings=_key_settings(),
            llm_call=llm_call,
            variance_threshold=100,
        )
    )

    assert len(calls) == 2
    assert "自洽补采" in calls[1]
    assert result.self_check_sampled is True
    assert result.aggregated_score == 65
    assert result.variance == 16.67


def test_guardrail_clamps_objectively_impossible_knowledge_score() -> None:
    cards = [
        _card("knowledge_base", {"correct": False}, 0.8),
        _card("knowledge_base", {"对症": False, "配伍": False, "剂量": False, "误用毒烈": True}, 1.5),
    ]

    result = apply_guardrail("knowledge_base", evidence_cards=cards, score=82)

    assert result.score == 35
    assert result.warnings
    assert "正确率为 0%" in result.warnings[0].reason


def test_zero_percent_correct_knowledge_never_exceeds_35() -> None:
    async def llm_call(messages: Sequence[ChatMessage]) -> str:
        return _jury_json((88, 95, 92), "高分必须被知识护栏拦截")

    result = asyncio.run(
        score_dimension(
            "knowledge_base",
            [
                _card("knowledge_base", {"correct": False, "topic": "解表药"}, 0.8),
                _card("knowledge_base", {"对症": False, "配伍": False, "剂量": False, "误用毒烈": True}, 1.5),
            ],
            settings=_key_settings(),
            llm_call=llm_call,
        )
    )

    assert result.aggregated_score <= 35
    assert result.warnings


def test_full_correct_knowledge_is_at_least_60() -> None:
    async def llm_call(messages: Sequence[ChatMessage]) -> str:
        return _jury_json((10, 20, 30), "低分必须被知识护栏抬升")

    result = asyncio.run(
        score_dimension(
            "knowledge_base",
            [
                _card("knowledge_base", {"correct": True, "topic": "解表药"}, 0.8),
                _card("knowledge_base", {"对症": True, "配伍": True, "剂量": True, "误用毒烈": False}, 1.5),
            ],
            settings=_key_settings(),
            llm_call=llm_call,
        )
    )

    assert result.aggregated_score >= 60
    assert result.warnings


def test_large_jury_disagreement_substantially_lowers_confidence() -> None:
    async def stable_call(messages: Sequence[ChatMessage]) -> str:
        return _jury_json((64, 66, 65), "稳定三师")

    async def divided_call(messages: Sequence[ChatMessage]) -> str:
        return _jury_json((10, 90, 50), "分歧三师")

    cards = [_card("knowledge_base", {"value": 65}, 1.0)]

    stable = asyncio.run(score_dimension("knowledge_base", cards, settings=_key_settings(), llm_call=stable_call))
    divided = asyncio.run(
        score_dimension(
            "knowledge_base",
            cards,
            settings=_key_settings(),
            llm_call=divided_call,
            variance_threshold=9999,
        )
    )

    assert divided.variance > stable.variance
    assert divided.confidence < stable.confidence * 0.2


def test_high_score_without_evidence_is_clamped() -> None:
    async def llm_call(messages: Sequence[ChatMessage]) -> str:
        return _jury_json((96, 99, 98), "没有证据也不应给高分")

    result = asyncio.run(
        score_dimension(
            "knowledge_base",
            [],
            settings=_key_settings(),
            llm_call=llm_call,
        )
    )

    assert result.aggregated_score <= 50
    assert result.warnings
    assert "缺少可追溯证据卡" in result.warnings[0].reason


def test_guardrail_blocks_new_category_labels() -> None:
    cards = [
        _card("learning_goal", {"label": "临床实用"}, 1.0),
    ]

    result = apply_guardrail(
        "learning_goal",
        evidence_cards=cards,
        label="玄学修炼",
        distribution={"玄学修炼": 0.7, "临床实用": 0.3},
    )

    assert result.label == "临床实用"
    assert result.distribution == {"临床实用": 1.0}
    assert len(result.warnings) == 2


def test_guardrail_removes_untraceable_weak_points() -> None:
    cards = [
        _card("weak_points", {"label": "清热药·剂量", "correct": False}, 1.0),
    ]

    result = apply_guardrail(
        "weak_points",
        evidence_cards=cards,
        weak_points=["清热药·剂量", "不存在·幻觉标签"],
    )

    assert result.weak_points == ["清热药·剂量"]
    assert result.label == "清热药·剂量"
    assert result.warnings


def test_category_fusion_keeps_allowed_labels_only() -> None:
    cards = [
        _card("resource_preference", {"label": "知识图谱"}, 1.0),
        _card("resource_preference", {"label": "拟人化短剧"}, 1.0),
        _card("resource_preference", {"label": "自造资源"}, 99.0),
    ]

    result = fuse_dimension("resource_preference", cards)

    assert result.distribution == {"拟人化短剧": 0.5, "知识图谱": 0.5}
    assert result.label in {"知识图谱", "拟人化短剧"}


def test_learning_goal_other_bucket_is_allowed_by_fusion_and_guardrail() -> None:
    cards = [
        _card("learning_goal", {"label": "其他"}, 1.0),
    ]

    fused = fuse_dimension("learning_goal", cards)
    guarded = apply_guardrail(
        "learning_goal",
        evidence_cards=cards,
        label="其他",
        distribution={"其他": 1.0},
    )

    assert fused.label == "其他"
    assert fused.distribution == {"其他": 1.0}
    assert guarded.label == "其他"
    assert guarded.distribution == {"其他": 1.0}
    assert guarded.warnings == []


def test_knowledge_prior_without_score_uses_base_level_mapping_only() -> None:
    low = fuse_dimension("knowledge_base", [_card("knowledge_base", {"prior": True, "label": "零基础"}, 0.3)])
    unknown = fuse_dimension("knowledge_base", [_card("knowledge_base", {"prior": True, "label": "未知基础"}, 0.3)])
    scored = fuse_dimension("knowledge_base", [_card("knowledge_base", {"prior": True, "prior_score": 64}, 0.3)])

    assert low.score == 11.5
    assert unknown.score is None
    assert scored.score == 64


def test_legacy_sources_migrate_to_evidence_tiers() -> None:
    assert _card("knowledge_base", {"correct": True}, 1.0, source="quiz").tier == EvidenceTier.MEASURED
    assert _card("pace", {"daily_minutes": 30}, 1.0, source="interaction").tier == EvidenceTier.BEHAVIORAL
    assert _card("knowledge_base", {"base_level": "一般"}, 1.0, source="dialogue").tier == EvidenceTier.SELF_REPORT
    assert _card("knowledge_base", {"prior": True}, 0.3, source="身份卡+30秒速判", fact="身份卡先验").tier == EvidenceTier.SELF_REPORT
    assert _card("knowledge_base", {"correct": True}, 0.8, source="身份卡+30秒速判", fact="诊断题答对").tier == EvidenceTier.MEASURED


def test_self_report_only_objective_dimensions_stay_below_solid_confidence() -> None:
    cases = {
        "knowledge_base": {"base_level": "较好"},
        "pace": {"daily_minutes": 30},
        "weak_points": {"label": "配伍", "correct": False},
    }
    for dimension, signal in cases.items():
        cards = [
            _card(dimension, signal, 1.0, tier=EvidenceTier.SELF_REPORT, fact=f"同一自述 {index}")
            for index in range(3)
        ]
        assert fuse_dimension(dimension, cards).confidence < 0.5


def test_high_self_report_score_stays_low_confidence_until_measured() -> None:
    self_report = _card(
        "knowledge_base",
        {"value": 92},
        1.0,
        tier=EvidenceTier.SELF_REPORT,
        fact="我基础很好，能独立开方",
    )
    reported = fuse_dimension("knowledge_base", [self_report])
    measured = fuse_dimension(
        "knowledge_base",
        [self_report, _card("knowledge_base", {"correct": True}, 1.2, tier=EvidenceTier.MEASURED)],
    )

    assert reported.score == 92
    assert reported.confidence <= 0.35
    assert measured.confidence > reported.confidence


def test_jury_confidence_cannot_replace_evidence_tier_confidence() -> None:
    async def unanimous(_messages: Sequence[ChatMessage]) -> str:
        return _jury_json((92, 92, 92), "three teachers agree")

    result = asyncio.run(
        score_dimension(
            "knowledge_base",
            [_card("knowledge_base", {"value": 92}, 1.0, tier=EvidenceTier.SELF_REPORT)],
            settings=_key_settings(),
            llm_call=unanimous,
        )
    )

    assert result.aggregated_score == 92
    assert result.confidence <= 0.35


def test_repeated_homogeneous_self_report_uses_geometric_weight_decay() -> None:
    cards = [
        _card(
            "knowledge_base",
            {"base_level": "较好", "turn": index},
            1.0,
            tier=EvidenceTier.SELF_REPORT,
            fact=f"第 {index} 次说基础较好",
        )
        for index in range(1, 4)
    ]

    assert effective_card_weights(cards) == [1.0, 0.5, 0.25]
    assert fuse_dimension("knowledge_base", cards).metadata["effective_total_weight"] == 1.75


def test_dialogue_knowledge_base_keeps_global_k() -> None:
    cards = [
        _card("knowledge_base", {"value": 70}, 1.5, tier=EvidenceTier.MEASURED),
        _card("learning_goal", {"label": "临床实用"}, 1.5, tier=EvidenceTier.SELF_REPORT),
    ]

    fused = _dialogue_fusion(cards)

    assert fused["knowledge_base"].confidence == 0.5
    assert fused["learning_goal"].confidence == 1.0


def test_jury_can_distinguish_details_within_same_anchor_interval() -> None:
    prompts: list[str] = []

    async def llm_call(messages: Sequence[ChatMessage]) -> str:
        prompt = str(messages[1]["content"])
        prompts.append(prompt)
        scores = (63, 65, 64) if "只会辨认常见药名" in prompt else (74, 76, 75)
        return _jury_json(scores, "同锚点内依据细节取具体分")

    cautious = asyncio.run(
        score_dimension(
            "knowledge_base",
            [
                _card(
                    "knowledge_base",
                    {"base_level": "较好"},
                    1.0,
                    tier=EvidenceTier.SELF_REPORT,
                    fact="自评较好，但只会辨认常见药名",
                )
            ],
            settings=_key_settings(),
            llm_call=llm_call,
        )
    )
    experienced = asyncio.run(
        score_dimension(
            "knowledge_base",
            [
                _card(
                    "knowledge_base",
                    {"base_level": "较好"},
                    1.0,
                    tier=EvidenceTier.SELF_REPORT,
                    fact="自评较好，能解释常用药配伍禁忌",
                )
            ],
            settings=_key_settings(),
            llm_call=llm_call,
        )
    )

    assert "强制评分区间：[62, 78]" in prompts[0]
    assert 62 <= cautious.aggregated_score <= 78  # type: ignore[operator]
    assert 62 <= experienced.aggregated_score <= 78  # type: ignore[operator]
    assert cautious.aggregated_score != experienced.aggregated_score


def test_guardrail_clamps_to_central_anchor_boundaries() -> None:
    cards = [_card("pace", {"daily_minutes": 45}, 1.0, tier=EvidenceTier.SELF_REPORT)]

    assert apply_guardrail("pace", evidence_cards=cards, score=20).score == 75
    assert apply_guardrail("pace", evidence_cards=cards, score=99).score == 95


def test_first_evaluation_with_key_bypasses_weight_threshold(tmp_path: Path) -> None:
    calls: list[str] = []

    result = asyncio.run(
        update_profile_from_event(
            "first-review-threshold",
            {"type": "quiz", "payload": {"correct": True, "difficulty": "easy"}},
            db_path=tmp_path / "threshold.db",
            jury_weight_threshold=99,
            settings=_key_settings(),
            llm_call=_counting_jury(calls),
            now=datetime(2026, 7, 2, tzinfo=UTC),
        )
    )

    assert result.jury_called_dimensions == ["knowledge_base"]
    assert len(calls) == 1


def test_first_evaluation_with_key_bypasses_24_hour_throttle(tmp_path: Path) -> None:
    db_path = tmp_path / "throttle.db"
    now = datetime(2026, 7, 2, tzinfo=UTC)
    add_evidence(
        "first-review-throttle",
        _card("knowledge_base", {"correct": True}, 0.8, tier=EvidenceTier.MEASURED),
        db_path=db_path,
    )
    _init_update_state(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO profile_update_state(user_id, dimension, pending_weight, updated_at, last_scored_at)
            VALUES (?, ?, 0, ?, ?)
            """,
            ("first-review-throttle", "knowledge_base", now.isoformat(), now.isoformat()),
        )
        conn.commit()
    calls: list[str] = []

    profile = asyncio.run(
        get_latest_profile(
            "first-review-throttle",
            db_path=db_path,
            settings=_key_settings(),
            llm_call=_counting_jury(calls),
            now=now + timedelta(hours=1),
        )
    )

    assert len(calls) == 1
    assert profile.knowledge_base.score == 66


def _card(
    dimension: str,
    signal: dict[str, Any],
    weight: float,
    timestamp: datetime | None = None,
    *,
    tier: EvidenceTier | None = None,
    source: str = "测试",
    fact: str = "测试证据",
) -> EvidenceCard:
    payload: dict[str, Any] = {
        "dimension": dimension,
        "source": source,
        "fact": fact,
        "objective_signal": signal,
        "weight": weight,
        "timestamp": timestamp or datetime(2026, 7, 2, tzinfo=UTC),
    }
    if tier is not None:
        payload["tier"] = tier
    return EvidenceCard.model_validate(payload)


def _no_key_settings() -> Settings:
    return Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None, MOCK_STREAM_DELAY=0.0)


def _key_settings() -> Settings:
    settings = _no_key_settings()
    settings.deepseek_api_key = "test-key"
    return settings


def _jury_json(scores: tuple[int, int, int], narrative: str) -> str:
    return json.dumps(
        {
            "dimension": "知识基础",
            "jury": {
                "严师": {"score": scores[0], "reasoning_chain": ["严师依据证据卡"], "comment": "严师评语"},
                "宽师": {"score": scores[1], "reasoning_chain": ["宽师依据证据卡"], "comment": "宽师评语"},
                "中立": {"score": scores[2], "reasoning_chain": ["中立依据证据卡"], "comment": "中立评语"},
            },
            "narrative": narrative,
        },
        ensure_ascii=False,
    )


def _counting_jury(calls: list[str]):
    async def llm_call(messages: Sequence[ChatMessage]) -> str:
        calls.append(str(messages[1]["content"]))
        return _jury_json((62, 72, 66), "首评强制评审")

    return llm_call


class CapturingRouter(ModelRouter):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings=settings)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, task_type: str, messages: Sequence[ChatMessage], **kw: Any) -> str:
        self.calls.append(
            {
                "task_type": task_type,
                "thinking": kw.get("thinking"),
                "max_tokens": kw.get("max_tokens"),
                "temperature": kw.get("temperature"),
            }
        )
        return json.dumps(
            {
                "dimension": "知识基础",
                "jury": {
                    "严师": {"score": 60, "reasoning_chain": ["严"], "comment": "严"},
                    "宽师": {"score": 70, "reasoning_chain": ["宽"], "comment": "宽"},
                    "中立": {"score": 66, "reasoning_chain": ["中"], "comment": "中"},
                },
                "narrative": "路由参数测试",
            },
            ensure_ascii=False,
        )
