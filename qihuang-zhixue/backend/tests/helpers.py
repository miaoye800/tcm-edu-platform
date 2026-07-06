from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.auth.dependencies import get_auth_db_path
from app.auth.storage import upsert_seed_user
from app.auth.tokens import create_token
from app.main import app


def auth_headers(client: TestClient, username: str, db_path: Path) -> dict[str, str]:
    session = auth_session(client, username, db_path)
    return session["headers"]


def auth_headers_for_user_id(client: TestClient, user_id: str, db_path: Path) -> dict[str, str]:
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    username = user_id.replace("_", "-").lower()
    upsert_seed_user(
        username=username,
        password="demo1234",
        display_name=username,
        user_id=user_id,
        db_path=db_path,
    )
    return {"Authorization": f"Bearer {create_token(user_id)}"}


def auth_session(client: TestClient, username: str, db_path: Path) -> dict[str, Any]:
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "demo1234",
            "display_name": username,
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return {
        "headers": {"Authorization": f"Bearer {payload['token']}"},
        "token": payload["token"],
        "user": payload["user"],
    }
