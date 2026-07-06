import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.profile import get_profile_db_path, get_profile_settings
from app.config import Settings
from app.llm.clients import ChatMessage
from app.main import app
from app.profile.narrative import build_dimension_facts, synthesize_profile_narrative
from app.profile.schema import WeakPoint
from app.profile.update import update_profile_from_event
from tests.helpers import auth_headers_for_user_id


def test_template_narrative_uses_real_dimensions_and_marks_low_confidence() -> None:
    profile = _profile_with_manual_dimensions()
    profile.knowledge_base.confidence = 0.33

    narrative = asyncio.run(
        synthesize_profile_narrative(
            profile,
            settings=_no_key_settings(),
        )
    )

    assert narrative.method == "template"
    assert "清热药·剂量" in narrative.description
    assert "待补充" in narrative.description
    low_confidence_names = {item.name for item in narrative.low_confidence_dimensions}
    weak_dimension_keys = {item.dimension_key for item in narrative.weak_points}
    assert {"知识基础", "学习目标"}.issubset(low_confidence_names)
    assert "knowledge_base" not in weak_dimension_keys
    assert "learning_goal" not in weak_dimension_keys
    assert "weak_points" in weak_dimension_keys
    assert all(item.status == "待补充" for item in narrative.low_confidence_dimensions)
    assert "不存在" not in narrative.description
    assert len(narrative.dimensions) == 7


def test_ready_numeric_score_below_threshold_enters_weak_points() -> None:
    profile = _profile_with_manual_dimensions()
    profile.knowledge_base.score = 35
    profile.knowledge_base.confidence = 0.82

    narrative = asyncio.run(
        synthesize_profile_narrative(
            profile,
            settings=_no_key_settings(),
        )
    )

    weak_dimension_keys = {item.dimension_key for item in narrative.weak_points}
    assert "knowledge_base" in weak_dimension_keys
    assert "学习目标" in {item.name for item in narrative.low_confidence_dimensions}
    assert "learning_goal" not in weak_dimension_keys
    assert "清热药·剂量" in narrative.description
    assert "知识基础为 35.0 分" in narrative.description


def test_llm_plan_filters_invented_dimension_and_weak_point() -> None:
    profile = _profile_with_manual_dimensions()
    calls: list[str] = []

    async def llm_call(messages: Sequence[ChatMessage]) -> str:
        calls.append(str(messages[1]["content"]))
        return json.dumps(
            {
                "strength_dimension_keys": ["knowledge_base", "alchemy"],
                "weakness_dimension_keys": ["weak_points", "made_up_dimension"],
                "weak_point_labels": ["清热药·剂量", "凭空·标签"],
                "advice_dimension_keys": ["weak_points", "unknown"],
                "description": "这里试图编造一个不存在的薄弱点：凭空·标签",
            },
            ensure_ascii=False,
        )

    narrative = asyncio.run(
        synthesize_profile_narrative(
            profile,
            settings=_key_settings(),
            llm_call=llm_call,
        )
    )

    assert calls
    assert narrative.method == "llm"
    assert "知识基础" in [item.dimension for item in narrative.advantages]
    assert "清热药·剂量" in narrative.description
    assert "凭空·标签" not in narrative.description
    assert "alchemy" not in narrative.description
    assert all(item.dimension_key != "made_up_dimension" for item in narrative.weak_points)


def test_build_dimension_facts_preserves_7_dimension_structured_fields() -> None:
    profile = _profile_with_manual_dimensions()

    facts = build_dimension_facts(profile)
    fact_by_key = {fact.key: fact for fact in facts}

    assert list(fact_by_key) == [
        "knowledge_base",
        "learning_goal",
        "learning_style",
        "weak_points",
        "pace",
        "motivation",
        "resource_preference",
    ]
    assert fact_by_key["knowledge_base"].score == 72
    assert fact_by_key["knowledge_base"].anchor == "功效+配伍较熟，能应对多数病例"
    assert fact_by_key["learning_style"].label == "视觉型"
    assert fact_by_key["weak_points"].weak_points[0].label == "清热药·剂量"
    assert fact_by_key["learning_goal"].status == "待补充"


def test_profile_narrative_endpoint_returns_description_and_structured_fields(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    app.dependency_overrides[get_profile_settings] = _no_key_settings
    client = TestClient(app)
    headers = auth_headers_for_user_id(client, "student-narrative-api", db_path)
    try:
        asyncio.run(
            update_profile_from_event(
                "student-narrative-api",
                {
                    "type": "quiz",
                    "payload": {
                        "answers": [
                            {"correct": False, "difficulty": "hard", "topic": "清热药·剂量"},
                            {"correct": False, "difficulty": "hard", "topic": "清热药·剂量"},
                        ]
                    },
                },
                db_path=db_path,
                jury_weight_threshold=99,
                settings=_no_key_settings(),
                now=_now(),
            )
        )
        response = client.get("/api/profile/narrative", headers=headers)
        user_response = client.get("/api/profile/student-narrative-api/narrative", headers=headers)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert user_response.status_code == 200
    payload = response.json()
    user_payload = user_response.json()
    assert payload["user_id"] == "student-narrative-api"
    assert user_payload["user_id"] == "student-narrative-api"
    assert payload["description"]
    assert user_payload["description"]
    assert payload["advice_intro"].startswith("学习建议引子")
    assert len(payload["dimensions"]) == 7
    assert len(user_payload["dimensions"]) == 7
    assert payload["method"] == "template"
    assert payload["weak_points"]
    assert "清热药·剂量" in payload["description"]
    assert payload["dimensions"][0]["evidence_refs"]


def _profile_with_manual_dimensions():
    from app.profile.schema import create_blank_profile

    profile = create_blank_profile("student-narrative")
    profile.knowledge_base.score = 72
    profile.knowledge_base.confidence = 0.82
    profile.knowledge_base.narrative = "知识基础证据稳定。"
    profile.learning_goal.label = "应试提分"
    profile.learning_goal.confidence = 0.2
    profile.learning_style.label = "视觉型"
    profile.learning_style.confidence = 0.72
    profile.weak_points.items = [WeakPoint(label="清热药·剂量", strength=2)]
    profile.weak_points.label = "清热药·剂量"
    profile.weak_points.confidence = 0.88
    profile.pace.score = 85
    profile.pace.confidence = 0.7
    profile.motivation.score = 78
    profile.motivation.label = "内驱"
    profile.motivation.confidence = 0.76
    profile.resource_preference.label = "知识图谱"
    profile.resource_preference.distribution = {"知识图谱": 1.0}
    profile.resource_preference.confidence = 0.74
    return profile


def _no_key_settings() -> Settings:
    return Settings(DEEPSEEK_API_KEY=None, ZHIPU_API_KEY=None, MOCK_STREAM_DELAY=0.0)


def _key_settings() -> Settings:
    settings = _no_key_settings()
    settings.deepseek_api_key = "test-key"
    return settings


def _now() -> datetime:
    return datetime(2026, 7, 4, tzinfo=UTC)


def _test_db_path(tmp_path: Path) -> Path:
    root = tmp_path / ".data"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"profile_narrative_test_{uuid4().hex}.db"
