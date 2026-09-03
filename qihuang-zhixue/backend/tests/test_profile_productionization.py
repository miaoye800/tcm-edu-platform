import asyncio
from pathlib import Path

import app.profile.modes as modes
from app.profile.nodes import _fallback_dialogue_extraction


def test_configured_model_failure_marks_rule_fallback(monkeypatch, tmp_path: Path) -> None:
    extraction = _fallback_dialogue_extraction("我准备参加考试")
    monkeypatch.setattr(modes, "_has_profile_extract_key", lambda: True)

    async def no_llm(*args, **kwargs):
        return None

    async def structured(*args, **kwargs):
        return extraction

    monkeypatch.setattr(modes, "_try_llm_dialogue_turn", no_llm)
    monkeypatch.setattr("app.profile.nodes._extract_dialogue_structured", structured)
    result, _ = asyncio.run(
        modes._dialogue_extract_and_question(
            utterance="我准备参加考试",
            target="learning_goal",
            turn_index=1,
            evidence_cards=[],
            current_confidences={},
        )
    )
    assert result.extraction_method == "rule_fallback"


def test_visible_dialogue_sanitizes_internal_markers() -> None:
    assert modes._sanitize_visible_dialogue_text("[MOCK:model] 你好 [FallbackReason:HTTP402]") == "你好"


def test_dialogue_session_busy_lock_is_scoped_by_session_and_database(tmp_path: Path) -> None:
    lock = modes._dialogue_session_lock("session-a", tmp_path / "a.db")
    assert not modes.dialogue_session_busy("session-a", tmp_path / "b.db")
    async def check() -> None:
        await lock.acquire()
        try:
            assert modes.dialogue_session_busy("session-a", tmp_path / "a.db")
            assert not modes.dialogue_session_busy("session-b", tmp_path / "a.db")
        finally:
            lock.release()
    asyncio.run(check())
