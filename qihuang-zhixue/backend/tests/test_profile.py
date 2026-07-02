import asyncio
from pathlib import Path
from uuid import uuid4

from app.profile.agent import cold_start_profile, export_mermaid, run_cold_start, update_from_event
from app.profile.models import DIMENSION_KEYS, ProfileEvent
from app.profile.radar import to_radar
from app.profile.storage import get_profile, save_profile


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

    assert [getattr(profile, key).score for key in DIMENSION_KEYS]
    assert len(DIMENSION_KEYS) == 7
    assert all(hasattr(profile, key) for key in DIMENSION_KEYS)
    assert len(to_radar(profile)["dimensions"]) == 7
    assert len(to_radar(profile)["values"]) == 7


def test_wrong_differentiation_event_adds_weak_point_and_lowers_scores(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    try:
        profile = asyncio.run(
            cold_start_profile(
                user_id="test-update",
                first_message="我是中医学专业学生，基础一般，想系统学习中药学。",
                db_path=db_path,
            )
        )
        old_weak_score = profile.weak_points.score
        old_knowledge_score = profile.knowledge_base.score

        updated = update_from_event(
            profile,
            ProfileEvent(
                user_id=profile.user_id,
                event_type="quiz_answer",
                is_correct=False,
                topic="麻黄-桂枝鉴别",
                skill="鉴别题",
                detail="把麻黄与桂枝的发汗和温通经脉特点混淆。",
            ),
            db_path=db_path,
        )
    finally:
        _cleanup_db(db_path)

    assert "麻黄-桂枝鉴别" in updated.weak_points.items
    assert updated.weak_points.score < old_weak_score
    assert updated.knowledge_base.score < old_knowledge_score


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


def test_profile_persistence_accepts_direct_weak_points_list_assignment(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    try:
        profile = get_profile("verify_user", db_path=db_path)
        profile.knowledge_base.score = 99
        profile.weak_points = ["麻黄vs桂枝鉴别"]

        save_profile(profile, db_path=db_path)
        loaded = get_profile("verify_user", db_path=db_path)
    finally:
        _cleanup_db(db_path)

    assert loaded.knowledge_base.score == 99
    assert loaded.weak_points.items == ["麻黄vs桂枝鉴别"]


def test_profile_persistence_clamps_direct_nested_score_assignment(tmp_path: Path) -> None:
    db_path = _test_db_path(tmp_path)
    try:
        profile = get_profile("verify_user_score", db_path=db_path)
        profile.knowledge_base.score = 10086
        profile.pace.score = -10

        save_profile(profile, db_path=db_path)
        loaded = get_profile("verify_user_score", db_path=db_path)
    finally:
        _cleanup_db(db_path)

    assert loaded.knowledge_base.score == 100
    assert loaded.pace.score == 0


def _test_db_path(tmp_path: Path) -> Path:
    root = tmp_path / ".data"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"profile_test_{uuid4().hex}.db"


def _cleanup_db(db_path: Path) -> None:
    for path in [db_path, db_path.with_suffix(".db-wal"), db_path.with_suffix(".db-shm")]:
        if path.exists():
            path.unlink()
