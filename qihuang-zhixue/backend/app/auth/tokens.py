"""JWT 签发与解析。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config import Settings, get_settings

DEFAULT_DEV_SECRET = "qihuang-dev-auth-secret-change-me"
JWT_ALGORITHM = "HS256"

_warned_default_secret = False


class AuthTokenError(ValueError):
    """Token 无效或过期。"""


def create_token(
    user_id: str,
    *,
    settings: Settings | None = None,
    expires_delta: timedelta | None = None,
    now: datetime | None = None,
) -> str:
    """为 user_id 签发本地 JWT。"""

    active_settings = settings or get_settings()
    issued_at = now or datetime.now(UTC)
    ttl = expires_delta or timedelta(days=active_settings.auth_token_expire_days)
    payload: dict[str, Any] = {
        "sub": user_id,
        "iat": issued_at,
        "exp": issued_at + ttl,
    }
    return str(jwt.encode(payload, _auth_secret(active_settings), algorithm=JWT_ALGORITHM))


def decode_token(token: str, *, settings: Settings | None = None) -> str:
    """解析 JWT 并返回 sub 中的 user_id。"""

    active_settings = settings or get_settings()
    try:
        payload = jwt.decode(token, _auth_secret(active_settings), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AuthTokenError("token 已过期") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthTokenError("token 无效") from exc

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise AuthTokenError("token 缺少用户标识")
    return user_id


def _auth_secret(settings: Settings) -> str:
    global _warned_default_secret
    secret = settings.auth_secret.strip() or DEFAULT_DEV_SECRET
    if secret == DEFAULT_DEV_SECRET and not _warned_default_secret:
        print("WARNING: AUTH_SECRET 未配置，正在使用开发默认密钥，请勿用于生产环境。")
        _warned_default_secret = True
    return secret
