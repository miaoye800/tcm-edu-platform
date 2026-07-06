from functools import lru_cache

from pydantic import AliasChoices
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_DEV_AUTH_SECRET = "qihuang-dev-auth-secret-change-me"
_warned_default_auth_secret = False


class Settings(BaseSettings):
    """应用配置，优先读取环境变量和 .env 文件。"""

    app_name: str = Field(default="qihuang-backend", alias="APP_NAME")
    env: str = Field(default="dev", alias="ENV")
    llm_provider: str = Field(default="deepseek", alias="LLM_PROVIDER")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"],
        alias="CORS_ORIGINS",
    )
    deepseek_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "DEEPSEEK_KEY"),
    )
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_model_pro: str = Field(default="deepseek-v4-pro", alias="DEEPSEEK_MODEL_PRO")
    deepseek_model_flash: str = Field(default="deepseek-v4-flash", alias="DEEPSEEK_MODEL_FLASH")
    zhipu_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ZHIPU_API_KEY", "ZHIPUAI_API_KEY"),
    )
    llm_task_model_map: dict[str, str] = Field(
        default_factory=lambda: {
            "classify": "deepseek_model_flash",
            "route": "deepseek_model_flash",
            "reason": "deepseek_model_flash",
            "generate": "deepseek_model_flash",
            "profile_jury": "deepseek_model_pro",
            "profile_extract": "deepseek_model_flash",
            "profile_update": "deepseek_model_flash",
            "profile_narrative": "deepseek_model_flash",
            "vision": "glm-4.5v",
        },
        alias="LLM_TASK_MODEL_MAP",
    )
    mock_stream_delay: float = Field(default=0.03, alias="MOCK_STREAM_DELAY")
    llm_timeout_seconds: float = Field(default=60.0, alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=1, alias="LLM_MAX_RETRIES")
    llm_fallback_to_mock: bool = Field(default=True, alias="LLM_FALLBACK_TO_MOCK")
    retrieval_score_threshold: float = Field(default=0.35, alias="RETRIEVAL_SCORE_THRESHOLD")
    auth_secret: str = Field(default=DEFAULT_DEV_AUTH_SECRET, alias="AUTH_SECRET")
    auth_token_expire_days: int = Field(default=7, alias="AUTH_TOKEN_EXPIRE_DAYS")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """缓存配置实例，避免重复解析环境变量。"""

    return Settings()


def resolve_auth_secret(settings: Settings) -> str:
    return settings.auth_secret.strip() or DEFAULT_DEV_AUTH_SECRET


def warn_if_default_auth_secret(settings: Settings) -> None:
    global _warned_default_auth_secret
    if _warned_default_auth_secret:
        return
    if resolve_auth_secret(settings) == DEFAULT_DEV_AUTH_SECRET:
        print("WARNING: AUTH_SECRET 未配置，正在使用开发默认密钥，请勿用于生产环境。")
        _warned_default_auth_secret = True
