"""账号鉴权领域模型。"""

from __future__ import annotations

from pydantic import BaseModel


class AuthUser(BaseModel):
    """接口返回与依赖注入使用的当前用户。"""

    user_id: str
    username: str
    display_name: str
    is_guest: bool
    created_at: str


class AuthResponse(BaseModel):
    """登录、注册、游客入口的统一响应。"""

    token: str
    user: AuthUser
