from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.profile import get_profile_db_path
from app.auth.dependencies import get_auth_db_path
from app.auth.tokens import create_token
from app.main import app
from app.profile.update import update_profile_from_event


def test_register_then_login_and_wrong_password_401(tmp_path: Path) -> None:
    db_path = tmp_path / ".data" / "auth_register.db"
    client = TestClient(app)
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    try:
        register_response = client.post(
            "/api/auth/register",
            json={"username": "disciple_new", "password": "demo1234", "display_name": "新弟子"},
        )
        login_response = client.post(
            "/api/auth/login",
            json={"username": "disciple_new", "password": "demo1234"},
        )
        wrong_response = client.post(
            "/api/auth/login",
            json={"username": "disciple_new", "password": "bad-password"},
        )
    finally:
        app.dependency_overrides.clear()

    assert register_response.status_code == 201
    assert register_response.json()["token"]
    assert login_response.status_code == 200
    assert login_response.json()["user"]["username"] == "disciple_new"
    assert wrong_response.status_code == 401


def test_get_current_user_parses_token_and_rejects_bad_tokens(tmp_path: Path) -> None:
    db_path = tmp_path / ".data" / "auth_token.db"
    client = TestClient(app)
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    try:
        created = client.post(
            "/api/auth/register",
            json={"username": "token_user", "password": "demo1234"},
        ).json()
        good_headers = {"Authorization": f"Bearer {created['token']}"}
        me_response = client.get("/api/auth/me", headers=good_headers)

        forged_response = client.get("/api/auth/me", headers={"Authorization": "Bearer forged.token.value"})
        expired = create_token(
            created["user"]["user_id"],
            expires_delta=timedelta(seconds=-1),
            now=datetime.now(UTC) - timedelta(days=1),
        )
        expired_response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    finally:
        app.dependency_overrides.clear()

    assert me_response.status_code == 200
    assert me_response.json()["user_id"] == created["user"]["user_id"]
    assert forged_response.status_code == 401
    assert expired_response.status_code == 401


def test_profile_data_is_isolated_by_current_user_token(tmp_path: Path) -> None:
    db_path = tmp_path / ".data" / "auth_isolation.db"
    client = TestClient(app)
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    try:
        user_a = client.post(
            "/api/auth/register",
            json={"username": "disciple_a_test", "password": "demo1234"},
        ).json()
        user_b = client.post(
            "/api/auth/register",
            json={"username": "disciple_b_test", "password": "demo1234"},
        ).json()

        asyncio.run(
            update_profile_from_event(
                user_a["user"]["user_id"],
                {"type": "quiz", "payload": {"correct": False, "difficulty": "hard", "topic": "清热药·剂量"}},
                db_path=db_path,
                jury_weight_threshold=99,
            )
        )
        asyncio.run(
            update_profile_from_event(
                user_b["user"]["user_id"],
                {"type": "quiz", "payload": {"correct": False, "difficulty": "hard", "topic": "补虚药·功效"}},
                db_path=db_path,
                jury_weight_threshold=99,
            )
        )

        response_a = client.get("/api/profile", headers=_headers(user_a["token"]))
        response_b = client.get("/api/profile", headers=_headers(user_b["token"]))
    finally:
        app.dependency_overrides.clear()

    payload_a = response_a.json()
    payload_b = response_b.json()
    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert payload_a["user_id"] == user_a["user"]["user_id"]
    assert payload_b["user_id"] == user_b["user"]["user_id"]
    assert "清热药·剂量" in payload_a["weak_points"]["items"][0]["label"]
    assert "补虚药·功效" in payload_b["weak_points"]["items"][0]["label"]


def test_guest_login_gets_token_and_default_blank_profile(tmp_path: Path) -> None:
    db_path = tmp_path / ".data" / "auth_guest.db"
    client = TestClient(app)
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    app.dependency_overrides[get_profile_db_path] = lambda: db_path
    try:
        guest_response = client.post("/api/auth/guest")
        profile_response = client.get("/api/profile", headers=_headers(guest_response.json()["token"]))
    finally:
        app.dependency_overrides.clear()

    assert guest_response.status_code == 201
    assert guest_response.json()["user"]["is_guest"] is True
    assert profile_response.status_code == 200
    assert profile_response.json()["user_id"] == guest_response.json()["user"]["user_id"]
    assert profile_response.json()["confidence"]["knowledge_base"] == 0


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
