"""账号登录、注册与当前用户接口。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.auth.dependencies import get_auth_db_path, get_current_user
from app.auth.models import AuthResponse, AuthUser
from app.auth.storage import DuplicateUsernameError, authenticate_user, create_guest_user, create_user
from app.auth.tokens import create_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    """注册入参。"""

    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = Field(default=None, max_length=32)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class LoginRequest(BaseModel):
    """登录入参。"""

    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db_path: Path = Depends(get_auth_db_path)) -> AuthResponse:
    """创建账号并返回 token。"""

    try:
        user = create_user(
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
            db_path=db_path,
        )
    except DuplicateUsernameError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在") from exc
    return AuthResponse(token=create_token(user.user_id), user=user)


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, db_path: Path = Depends(get_auth_db_path)) -> AuthResponse:
    """校验账号密码并返回 token。"""

    user = authenticate_user(username=payload.username, password=payload.password, db_path=db_path)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    return AuthResponse(token=create_token(user.user_id), user=user)


@router.post("/guest", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def guest(db_path: Path = Depends(get_auth_db_path)) -> AuthResponse:
    """创建游客账号并返回 token。"""

    user = create_guest_user(db_path=db_path)
    return AuthResponse(token=create_token(user.user_id), user=user)


@router.get("/me", response_model=AuthUser)
async def me(current_user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """返回当前登录用户。"""

    return current_user
