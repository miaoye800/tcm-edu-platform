"""P8 双向上下文：定位要确定性，站位提示只进 system。"""

from __future__ import annotations

from pathlib import Path

from app.assistant import router as rules
from app.assistant.loop import _with_node_context
from app.assistant.prompts import build_messages
from app.path.focus import resolve
from app.path.store import upsert_node


def _seed(db_path: Path) -> None:
    upsert_node({"id": "d01", "name": "解表药", "level": "domain"}, db_path=db_path)
    upsert_node({"id": "d01.s1", "name": "发散风寒药", "level": "subtype", "parent_id": "d01"}, db_path=db_path)
    upsert_node({"id": "d01.s1.p1", "name": "麻黄", "level": "point", "parent_id": "d01.s1",
                 "extra": {"aliases": ["麻黄汤证"]}}, db_path=db_path)
    upsert_node({"id": "d02", "name": "清热药", "level": "domain"}, db_path=db_path)
    upsert_node({"id": "d02.s1", "name": "清热燥湿药", "level": "subtype", "parent_id": "d02"}, db_path=db_path)


# ── 定位 ─────────────────────────────────────────────────────────────────


def test_node_id_shortcut_wins(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)

    result = resolve("d01.s1.p1", db_path=db_path)

    assert result["ok"] is True
    assert result["node_id"] == "d01.s1.p1"
    assert result["confidence"] >= 0.95


def test_wrapped_question_is_unwrapped(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)

    result = resolve("我想学麻黄", db_path=db_path)

    assert result["ok"] is True
    assert result["node_name"] == "麻黄"


def test_alias_resolves_to_the_point(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)

    result = resolve("麻黄汤证", db_path=db_path)

    assert result["node_id"] == "d01.s1.p1"


def test_longest_name_wins_on_partial_match(tmp_path: Path) -> None:
    """说「清热」时命中的是「清热燥湿药」还是「清热药」？取长的那个。"""

    db_path = tmp_path / "plans.db"
    _seed(db_path)

    result = resolve("清热燥湿药在哪", db_path=db_path)

    assert result["node_name"] == "清热燥湿药"


def test_unknown_returns_candidates_instead_of_guessing(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    _seed(db_path)

    result = resolve("我想学中医", db_path=db_path)

    assert result["ok"] is False
    assert result["node_id"] == ""
    assert isinstance(result["candidates"], list)


# ── 站位提示只进 system ──────────────────────────────────────────────────


def test_node_hint_goes_into_system_not_user_text() -> None:
    messages = build_messages(
        "这个怎么记",
        [],
        profile_hint="",
        node_hint="中药学 · 解表药 · 发散风寒药；知识点「麻黄」",
    )

    system = messages[0]["content"]
    user = messages[-1]["content"]
    assert "他此刻站在哪一关" in system
    assert "麻黄" in system
    assert "麻黄" not in user
    assert user == "这个怎么记"


def test_missing_node_hint_leaves_the_prompt_clean() -> None:
    messages = build_messages("你好", [], profile_hint="", node_hint="")

    assert "他此刻站在哪一关" not in messages[0]["content"]


# ── 工具参数兜底 ─────────────────────────────────────────────────────────


def test_node_id_is_filled_only_when_missing() -> None:
    assert _with_node_context("mindmap.generate", {"query": "麻黄"}, "d01.s1.p1")["node_id"] == "d01.s1.p1"

    explicit = _with_node_context("mindmap.generate", {"query": "桂枝", "node_id": "d02.s1"}, "d01.s1.p1")
    assert explicit["node_id"] == "d02.s1"


def test_query_capabilities_get_node_id_in_context_not_as_a_param() -> None:
    args = _with_node_context("kg.lookup", {"name": "麻黄"}, "d01.s1.p1")

    assert args["context"]["node_id"] == "d01.s1.p1"
    assert "node_id" not in args


# ── 无模型分支 ───────────────────────────────────────────────────────────


def test_keyless_route_maps_position_questions_to_path_focus() -> None:
    route = rules.route("合谷在哪")

    assert route is not None
    assert route.capability == "path.focus"
    assert route.args["query"] == "合谷在哪"


def test_keyless_route_does_not_steal_generation_requests() -> None:
    route = rules.route("我想学麻黄，帮我做一张思维导图")

    assert route is not None
    assert route.capability == "mindmap.generate"
