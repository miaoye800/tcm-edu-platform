import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.profile import get_profile_db_path
from app.llm.clients import ChatMessage
from app.main import app
from app.profile.schema import EvidenceCard
from app.profile.store import list_evidence
from app.profile.update import get_latest_profile, update_profile_from_event
from tests.helpers import auth_headers_for_user_id


def test_incremental_update_calls_jury_only_after_weight_threshold(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    calls: list[str] = []

    result = asyncio.run(
        update_profile_from_event(
            "student-threshold",
            {"type": "quiz", "payload": {"correct": True, "difficulty": "easy"}},
            db_path=db_path,
            jury_weight_threshold=2.0,
            llm_call=_counting_llm(calls),
            force_llm=True,
            now=_now(),
        )
    )

    assert result.jury_call_count == 0
    assert calls == []
    assert result.profile.knowledge_base.score == 100

    result = asyncio.run(
        update_profile_from_event(
            "student-threshold",
            {"type": "quiz", "payload": {"correct": False, "difficulty": "hard", "topic": "清热药·剂量"}},
            db_path=db_path,
            jury_weight_threshold=2.0,
            llm_call=_counting_llm(calls),
            force_llm=True,
            now=_now(),
        )
    )

    assert result.jury_called_dimensions == ["knowledge_base"]
    assert len(calls) == 1
    assert result.profile.knowledge_base.score == 66
    assert len(list_evidence("student-threshold", db_path=db_path)) == 3


def test_user_view_profile_triggers_jury_rescore_and_resets_pending_weight(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    calls: list[str] = []

    asyncio.run(
        update_profile_from_event(
            "student-view",
            {"type": "quiz", "payload": {"correct": True, "difficulty": "easy"}},
            db_path=db_path,
            jury_weight_threshold=2.0,
            llm_call=_counting_llm(calls),
            force_llm=True,
            now=_now(),
        )
    )
    assert calls == []

    profile = asyncio.run(
        get_latest_profile(
            "student-view",
            db_path=db_path,
            llm_call=_counting_llm(calls),
            force_llm=True,
            now=_now(),
        )
    )

    assert len(calls) == 1
    assert profile.knowledge_base.score == 66

    asyncio.run(
        update_profile_from_event(
            "student-view",
            {"type": "quiz", "payload": {"correct": True, "difficulty": "easy"}},
            db_path=db_path,
            jury_weight_threshold=2.0,
            llm_call=_counting_llm(calls),
            force_llm=True,
            now=_now(),
        )
    )

    assert len(calls) == 1


def test_get_profile_endpoint_returns_latest_profile_and_confidence(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    client = TestClient(app)
    headers = auth_headers_for_user_id(client, "student-api", db_path)
    try:
        asyncio.run(
            update_profile_from_event(
                "student-api",
                {"type": "quiz", "payload": {"correct": True, "difficulty": "easy"}},
                db_path=db_path,
                jury_weight_threshold=99,
                now=_now(),
            )
        )
        response = client.get("/api/profile", headers=headers)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "student-api"
    assert payload["knowledge_base"]["score"] == 100
    assert payload["confidence"]["knowledge_base"] > 0


def test_three_evidence_updates_score_confidence_and_decays_old_evidence(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    calls: list[str] = []
    now = _now()

    first = asyncio.run(
        update_profile_from_event(
            "student-decay",
            {
                "type": "quiz",
                "timestamp": now - timedelta(days=60),
                "payload": {"correct": False, "difficulty": "hard", "topic": "清热药·剂量"},
            },
            db_path=db_path,
            jury_weight_threshold=99,
            llm_call=_counting_llm(calls),
            force_llm=True,
            now=now,
        )
    )
    initial_score = first.profile.knowledge_base.score
    initial_confidence = first.profile.knowledge_base.confidence

    second = asyncio.run(
        update_profile_from_event(
            "student-decay",
            {"type": "quiz", "payload": {"correct": True, "difficulty": "easy", "topic": "清热药·剂量"}},
            db_path=db_path,
            jury_weight_threshold=99,
            llm_call=_counting_llm(calls),
            force_llm=True,
            now=now,
        )
    )
    third = asyncio.run(
        update_profile_from_event(
            "student-decay",
            {"type": "quiz", "payload": {"correct": True, "difficulty": "easy", "topic": "清热药·剂量"}},
            db_path=db_path,
            jury_weight_threshold=99,
            llm_call=_counting_llm(calls),
            force_llm=True,
            now=now,
        )
    )

    assert calls == []
    assert second.jury_call_count == 0
    assert third.jury_call_count == 0
    assert third.profile.knowledge_base.score is not None
    assert initial_score is not None
    assert third.profile.knowledge_base.score > initial_score
    assert third.profile.knowledge_base.score > 75
    assert third.profile.knowledge_base.confidence > initial_confidence
    assert len(third.profile.knowledge_base.evidence) == 3


def _counting_llm(calls: list[str]):
    async def llm_call(messages: Sequence[ChatMessage]) -> str:
        calls.append(str(messages[1]["content"]))
        return json.dumps(
            {
                "dimension": "知识基础",
                "jury": {
                    "严师": {"score": 62, "reasoning_chain": ["严"], "comment": "严"},
                    "宽师": {"score": 72, "reasoning_chain": ["宽"], "comment": "宽"},
                    "中立": {"score": 66, "reasoning_chain": ["中"], "comment": "中"},
                },
                "narrative": "阈值控制测试画像",
            },
            ensure_ascii=False,
        )

    return llm_call


def _now() -> datetime:
    return datetime(2026, 7, 3, tzinfo=UTC)


def _test_db_path(tmp_path: Path) -> Path:
    root = tmp_path / ".data"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"profile_update_test_{uuid4().hex}.db"
