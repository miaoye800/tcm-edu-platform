import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.profile.agent import cold_start_profile, export_mermaid, run_cold_start
from app.profile.radar import to_radar
from app.profile.schema import DIMENSION_LABELS, WeakPoint
from app.profile.store import get_profile, upsert_profile
from app.profile.update import ProfileUpdateEvent, update_profile_from_event


def test_cold_start_builds_complete_7_dimension_profile(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    try:
        profile = asyncio.run(
            cold_start_profile(
                user_id="test-cold-start",
                first_message="我是中医学专业学生，基础一般，目标是期末考试，喜欢图表。",
                db_path=db_path,
            )
        )
    finally:
        _cleanup_db(db_path)

    assert len(DIMENSION_LABELS) == 7
    assert all(hasattr(profile, key) for key in DIMENSION_LABELS)
    assert profile.user_id == "test-cold-start"
    assert len(to_radar(profile)["dimensions"]) == 7
    assert len(to_radar(profile)["values"]) == 7


def test_wrong_differentiation_event_adds_weak_point_and_updates_scores(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    try:
        profile = get_profile("test-update", db_path=db_path)
        upsert_profile(profile, db_path=db_path)

        result = asyncio.run(
            update_profile_from_event(
                "test-update",
                ProfileUpdateEvent(
                    type="quiz",
                    payload={
                        "question_id": "q1",
                        "correct": False,
                        "topic": "麻黄-桂枝鉴别",
                        "difficulty": "hard",
                    },
                ),
                db_path=db_path,
                jury_weight_threshold=99,
            )
        )
    finally:
        _cleanup_db(db_path)

    labels = [item.label for item in result.profile.weak_points.items]
    assert "麻黄-桂枝鉴别" in labels
    assert result.profile.knowledge_base.score == 0
    assert result.affected_dimensions == ["knowledge_base", "weak_points"]


def test_profile_graph_routes_with_trace_and_exports_mermaid(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    try:
        state = asyncio.run(
            run_cold_start(
                user_id="test-trace",
                first_message="请根据我的基础和学习目标建立画像，我是中医学专业，喜欢图表。",
                db_path=db_path,
            )
        )
    finally:
        _cleanup_db(db_path)
    nodes = [item["node"] for item in state["trace"]]

    assert nodes[0] == "profile_router"
    assert "dialogue_extract_agent" in nodes
    assert "diagnostic_question_agent" in nodes
    assert "diagnostic_eval_agent" in nodes
    assert "profile_synthesis_agent" in nodes
    assert export_mermaid(tmp_path / "graph_profile.mmd").exists()


def test_profile_persistence_round_trips_weak_points(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    try:
        profile = get_profile("verify_user", db_path=db_path)
        profile.knowledge_base.score = 99
        profile.weak_points.items = [WeakPoint(label="麻黄vs桂枝鉴别", strength=2)]

        upsert_profile(profile, db_path=db_path)
        loaded = get_profile("verify_user", db_path=db_path)
    finally:
        _cleanup_db(db_path)

    assert loaded.knowledge_base.score == 99
    assert loaded.weak_points.items[0].label == "麻黄vs桂枝鉴别"
    assert loaded.weak_points.items[0].strength == 2


def test_profile_dimension_assignment_validates_score_bounds(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    try:
        profile = get_profile("verify_user_score", db_path=db_path)
        with pytest.raises(ValidationError):
            profile.knowledge_base.score = 10086
        with pytest.raises(ValidationError):
            profile.pace.score = -10
    finally:
        _cleanup_db(db_path)


def _test_db_path(tmp_path: Path) -> Path:
    root = tmp_path / ".data"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"profile_test_{uuid4().hex}.db"


def _cleanup_db(db_path: Path) -> None:
    for path in [db_path, db_path.with_suffix(".db-wal"), db_path.with_suffix(".db-shm")]:
        if path.exists():
            path.unlink()
