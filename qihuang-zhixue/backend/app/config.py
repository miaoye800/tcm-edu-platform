from functools import lru_cache

from pydantic import AliasChoices
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置，优先读取环境变量和 .env 文件。"""

    app_name: str = Field(default="qihuang-backend", alias="APP_NAME")
    env: str = Field(default="dev", alias="ENV")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"],
        alias="CORS_ORIGINS",
    )
    deepseek_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "DEEPSEEK_KEY"),
    )
    zhipu_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ZHIPU_API_KEY", "ZHIPUAI_API_KEY"),
    )
    llm_task_model_map: dict[str, str] = Field(
        default_factory=lambda: {
            "classify": "deepseek-flash",
            "route": "deepseek-flash",
            "reason": "deepseek-pro",
            "generate": "deepseek-pro",
            "vision": "glm-4.5v",
        },
        alias="LLM_TASK_MODEL_MAP",
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    """缓存配置实例，避免重复解析环境变量。"""

    return Settings()
