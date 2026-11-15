from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import path as path_api
from app.main import app
from app.path.plan_store import list_plan_versions, save_plan_version
from app.path.state import list_states
from app.profile.update import ProfileUpdateEvent
from tests.helpers import auth_headers_for_user_id


def seed(db_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "seed_knowledge_tree.py"
    spec = importlib.util.spec_from_file_location("path_state_seed", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.seed(db_path)


def setup_client(tmp_path: Path) -> tuple[TestClient, dict[str, str], Path]:
    db_path = tmp_path / "path-state.db"
    seed(db_path)
    app.dependency_overrides[path_api.get_path_db_path] = lambda: db_path
    client = TestClient(app)
    return client, auth_headers_for_user_id(client, "path-state-user", db_path), db_path


def test_reorder_free_order_is_accepted(tmp_path: Path) -> None:
    client, headers, _ = setup_client(tmp_path)
    assert client.post("/api/path/plan", headers=headers, json={}).status_code == 200
    response = client.post("/api/path/reorder", headers=headers, json={"node_ids": ["d02", "d01"]})
    assert response.status_code == 200
    assert response.json()["ordered_node_ids"] == ["d02", "d01"]
    assert response.json()["version"] == 2


def test_reorder_strict_order_is_rejected_with_edge(tmp_path: Path) -> None:
    client, headers, db_path = setup_client(tmp_path)
    assert client.post("/api/path/plan", headers=headers, json={}).status_code == 200
    assert client.post("/api/path/expand", headers=headers, json={"domain_id": "general"}).status_code == 200
    response = client.post("/api/path/reorder", headers=headers, json={"node_ids": ["general.p2", "general.p1"]})
    assert response.status_code == 409
    assert response.json()["detail"]["edges"][0]["from_node_id"] == "general.p1"
    assert len(list_plan_versions("path-state-user", db_path)) == 2


def test_reorder_by_chapter_is_rejected_once_a_chapter_has_several_steps(tmp_path: Path) -> None:
    """一章拆成多节之后，按章调序会把多行压成一行——必须 409，而且一行都不许丢。

    地图上原来那排"章级芯片 + ↑↓"正是按 `domain_id` 调序的。293 节的新计划里
    `d01` 有 7 个单元，一次调序就会把它们并成一行——用户看到的是"计划少了十几节"，
    没有任何报错。
    """

    client, headers, db_path = setup_client(tmp_path)
    save_plan_version(
        user_id="path-state-user",
        plan_kind="l1",
        payload={
            "stages": [
                {"stage_id": "d01.s1", "unit_id": "d01", "domain_id": "d01", "name": "辛温解表药", "unit_title": "辛温解表药", "order": 1, "order_locked": False, "point_ids": ["d01.s1.p1"], "point_count": 1},
                {"stage_id": "d01.s2", "unit_id": "d01", "domain_id": "d01", "name": "辛凉解表药", "unit_title": "辛凉解表药", "order": 2, "order_locked": False, "point_ids": ["d01.s2.p1"], "point_count": 1},
            ]
        },
        db_path=db_path,
    )

    rejected = client.post("/api/path/reorder", headers=headers, json={"node_ids": ["d01"]})
    assert rejected.status_code == 409
    assert "章" in rejected.json()["detail"]["reason"]

    accepted = client.post("/api/path/reorder", headers=headers, json={"node_ids": ["d01.s2"]})
    assert accepted.status_code == 200
    stages = client.get("/api/path/current", headers=headers).json()["plan"]["stages"]
    assert [row["unit_title"] for row in stages] == ["辛凉解表药", "辛温解表药"]


def test_custom_node_attaches_to_graph(tmp_path: Path) -> None:
    client, headers, _ = setup_client(tmp_path)
    response = client.post("/api/path/custom-node", headers=headers, json={"text": "learn d01"})
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["state"]["status"] == "custom"


def test_custom_node_outside_graph_returns_rejection_suggestion(tmp_path: Path) -> None:
    client, headers, _ = setup_client(tmp_path)
    response = client.post("/api/path/custom-node", headers=headers, json={"text": "quantum programming"})
    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert response.json()["suggestion"]


async def _noop_profile_update(user_id: str, event, **kwargs):
    return None


def test_defer_writes_paused_state(tmp_path: Path, monkeypatch) -> None:
    client, headers, db_path = setup_client(tmp_path)
    monkeypatch.setattr("app.path.service.update_profile_from_event", _noop_profile_update)
    response = client.post("/api/path/defer", headers=headers, json={"node_id": "d01", "reason": "太难"})
    assert response.status_code == 200
    state = next(item for item in list_states("path-state-user", db_path) if item["node_id"] == "d01")
    assert state["status"] == "paused"
    assert state["updated_at"]


def test_defer_emits_interaction_skip_or_retry_event(tmp_path: Path, monkeypatch) -> None:
    client, headers, _ = setup_client(tmp_path)
    events: list[ProfileUpdateEvent] = []

    async def capture_event(user_id: str, event, **kwargs):
        events.append(event if isinstance(event, ProfileUpdateEvent) else ProfileUpdateEvent.model_validate(event))

    monkeypatch.setattr("app.path.service.update_profile_from_event", capture_event)
    response = client.post("/api/path/defer", headers=headers, json={"node_id": "d01", "reason": "太难"})
    assert response.status_code == 200
    assert events
    assert events[0].type == "interaction"
    assert events[0].payload["subtype"] == "skip_or_retry"


def test_complete_node_triggers_adjust_after_node(tmp_path: Path) -> None:
    client, headers, _ = setup_client(tmp_path)
    assert client.post("/api/path/expand", headers=headers, json={"domain_id": "d01"}).status_code == 200
    response = client.post("/api/path/complete-node", headers=headers, json={"node_id": "d01.s1.p1", "bkt_snapshot": {"d01.s1.p1": {"p_mastery": 0.2}}})
    assert response.status_code == 200
    assert response.json()["diff"]["action"] == "remedial"
    assert response.json()["version"] == 2


def test_complete_node_inserts_remedial_and_points_current_to_it(tmp_path: Path) -> None:
    client, headers, db_path = setup_client(tmp_path)
    assert client.post("/api/path/expand", headers=headers, json={"domain_id": "d01"}).status_code == 200
    response = client.post("/api/path/complete-node", headers=headers, json={"node_id": "d01.s1.p1", "bkt_snapshot": {"d01.s1.p1": {"p_mastery": 0.2}}})
    assert response.status_code == 200
    next_node = response.json()["next_node"]
    assert next_node.startswith("remedial:")
    states = {item["node_id"]: item for item in list_states("path-state-user", db_path)}
    assert states["d01.s1.p1"]["status"] == "completed"
    assert states[next_node]["status"] == "current"
