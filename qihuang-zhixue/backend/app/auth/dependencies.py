"""FastAPI 鉴权依赖。"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, HTTPException, Request, status

from app.auth.models import AuthUser
from app.auth.storage import get_user_by_id
from app.auth.tokens import AuthTokenError, decode_token
from app.profile.store import DB_PATH


def get_auth_db_path() -> Path:
    """返回用户数据库路径，测试可覆盖。"""

    return DB_PATH


async def get_current_user(request: Request, db_path: Path = Depends(get_auth_db_path)) -> AuthUser:
    """从 Authorization Bearer token 中解析当前登录用户。"""

    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("缺少登录凭证")

    try:
        user_id = decode_token(token.strip())
    except AuthTokenError as exc:
        raise _unauthorized(str(exc)) from exc

    user = get_user_by_id(user_id, db_path=db_path)
    if user is None:
        raise _unauthorized("用户不存在")
    return user


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )
