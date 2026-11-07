from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.auth.dependencies import get_auth_db_path
from app.auth.storage import upsert_seed_user
from app.auth.tokens import create_token
from app.config import Settings
from app.main import app


def keyless_settings() -> Settings:
    """造一份确定没有模型 key 的配置，用于验证离线兜底链路。

    只清 ``DEEPSEEK_API_KEY`` / ``ZHIPU_API_KEY`` 是不够的：开发机的 ``.env``
    往往还配了 ``QWEN_API_KEY``，``ModelRouter.select`` 只要拿到任意一个 key
    就会返回真实 client，测试于是变成「打了真模型再断言 mock 结构」——既慢
    又不稳定。这里把三家 key 全清掉，``select`` 才会稳定返回 ``MockClient``。
    """

    return Settings(
        DEEPSEEK_API_KEY=None,
        ZHIPU_API_KEY=None,
        QWEN_API_KEY=None,
        LLM_MODE="mock",
    )


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
