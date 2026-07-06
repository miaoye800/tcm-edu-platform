import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.llm.clients import ChatMessage
from app.llm.router import ModelRouter
from app.profile.fusion import decay_factor, fuse_dimension
from app.profile.guardrail import apply_guardrail
from app.profile.jury import score_dimension
from app.profile.schema import EvidenceCard


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


def test_jury_router_call_uses_pro_model_with_thinking_high() -> None:
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
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
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


def _card(
    dimension: str,
    signal: dict[str, Any],
    weight: float,
    timestamp: datetime | None = None,
) -> EvidenceCard:
    return EvidenceCard(
        dimension=dimension,  # type: ignore[arg-type]
        source="测试",
        fact="测试证据",
        objective_signal=signal,
        weight=weight,
        timestamp=timestamp or datetime(2026, 7, 2, tzinfo=UTC),
    )


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


class CapturingRouter(ModelRouter):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings=settings)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, task_type: str, messages: Sequence[ChatMessage], **kw: Any) -> str:
        self.calls.append(
            {
                "task_type": task_type,
                "thinking": kw.get("thinking"),
                "reasoning_effort": kw.get("reasoning_effort"),
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
