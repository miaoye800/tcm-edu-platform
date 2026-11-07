from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_DEV_AUTH_SECRET = "qihuang-dev-auth-secret-change-me"
_warned_default_auth_secret = False

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = BACKEND_ROOT / ".data" / "qihuang.db"


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
    qwen_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
    )
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="QWEN_BASE_URL",
    )
    # 百炼给每个模型独立赠送 100 万 token，换模型即换额度，所以按档位留三个槽位。
    # 注意：qwen-flash 的免费额度已耗尽（403 Free quota exhausted），
    # 默认改用 qwen3.8-flash；额度再耗尽时改这里的默认值或设 QWEN_MODEL_FLASH 即可。
    qwen_model_flash: str = Field(default="qwen3.8-flash", alias="QWEN_MODEL_FLASH")
    qwen_model_plus: str = Field(default="qwen-plus", alias="QWEN_MODEL_PLUS")
    qwen_model_max: str = Field(default="qwen-max", alias="QWEN_MODEL_MAX")
    llm_task_model_map: dict[str, str] = Field(
        default_factory=lambda: {
            "classify": "deepseek_model_flash",
            "route": "deepseek_model_flash",
            "reason": "deepseek_model_flash",
            "generate": "deepseek_model_flash",
            # 画像评分是有固定 JSON schema、规则融合和护栏兜底的批量任务。
            # 默认使用 Flash，避免一次完成画像时对七个维度重复消耗 Pro 推理额度。
            # 如需更强的人工复核，可通过 LLM_TASK_MODEL_MAP 单独改回
            # "profile_jury": "deepseek_model_pro"。
            "profile_jury": "deepseek_model_flash",
            "profile_extract": "deepseek_model_flash",
            "profile_update": "deepseek_model_flash",
            "profile_narrative": "deepseek_model_flash",
            "profile_goal": "deepseek_model_flash",
            "vision": "glm-4.5v",
        },
        alias="LLM_TASK_MODEL_MAP",
    )
    # 录制回放开关：live=真调并录制，replay=只回放（e2e 用），auto=先回放后真调，
    # mock=强制 MockClient。默认 auto，本地开发无感，测试脚本显式设 replay。
    llm_mode: str = Field(default="auto", alias="LLM_MODE")
    # 默认关闭思考模式。未显式指定 thinking 的调用点一律走这个值，
    # 避免抽取/评分类任务误用推理 token。需要推理的任务在调用侧显式传 enabled。
    llm_thinking_default: str = Field(default="disabled", alias="LLM_THINKING_DEFAULT")
    mock_stream_delay: float = Field(default=0.03, alias="MOCK_STREAM_DELAY")
    llm_timeout_seconds: float = Field(default=60.0, alias="LLM_TIMEOUT_SECONDS")
    # 请求在服务端已受理后发生网络超时仍可能产生费用；默认不自动重发。
    llm_max_retries: int = Field(default=0, alias="LLM_MAX_RETRIES")
    llm_fallback_to_mock: bool = Field(default=True, alias="LLM_FALLBACK_TO_MOCK")
    retrieval_score_threshold: float = Field(default=0.35, alias="RETRIEVAL_SCORE_THRESHOLD")
    # GraphRAG 图扩展（P5）。三个开关分开，是为了出问题时能只关扩展、
    # 不动基础检索：基础检索坏了是「查不到药」，扩展坏了只是「少几条关系」。
    kg_expand_enabled: bool = Field(default=True, alias="KG_EXPAND_ENABLED")
    #: 一次检索最多补几味「图上有关系、但向量没召回」的药。太大等于把图摊成
    #: 一张毛毯，讲解文档会被无关药材撑散；小一点才有「补充」的意思。
    kg_expand_max_docs: int = Field(default=2, ge=0, le=8, alias="KG_EXPAND_MAX_DOCS")
    #: 只有分数够高的命中才配当「扩展起点」。阈值比基础检索低，因为这里的
    #: 分数只用来筛种子，不决定「要不要回答」——筛错了只是少扩一跳。
    kg_seed_threshold: float = Field(default=0.28, alias="KG_SEED_THRESHOLD")
    # GraphRAG 主问答链路（M1-S4）。和上面三个 ``kg_expand_*`` 不是一件事：
    # 那组是**资源生成**时沿图补药材，这组是**问答**时沿图补关系依据。
    # 分开配的理由很实际——问答补进来的是「为什么配」，资源补进来的是「还有哪味药」，
    # 出问题时得能只关一半。
    kg_agent_enabled: bool = Field(default=True, alias="KG_AGENT_ENABLED")
    #: 本地只读 SQLite 单次查询正常 <20ms。2 秒是「明显出问题了」的界线，
    #: 超过它宁可退化成普通检索，也不让用户对着转圈等图。
    kg_agent_timeout_seconds: float = Field(default=2.0, gt=0, le=10, alias="KG_AGENT_TIMEOUT_SECONDS")
    #: 一张关系卡最多几条关系。太多会把讲解挤成配伍清单，用户要的是依据不是目录。
    kg_agent_max_relations: int = Field(default=6, ge=0, le=20, alias="KG_AGENT_MAX_RELATIONS")
    #: 图扩展时每个种子最多取多少条边。
    kg_agent_limit: int = Field(default=40, ge=4, le=200, alias="KG_AGENT_LIMIT")
    auth_secret: str = Field(default=DEFAULT_DEV_AUTH_SECRET, alias="AUTH_SECRET")
    auth_token_expire_days: int = Field(default=7, alias="AUTH_TOKEN_EXPIRE_DAYS")
    qihuang_db_path: Path = Field(default=DEFAULT_DATABASE_PATH, alias="QIHUANG_DB_PATH")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """缓存配置实例，避免重复解析环境变量。"""

    return Settings()


def resolve_auth_secret(settings: Settings) -> str:
    return settings.auth_secret.strip() or DEFAULT_DEV_AUTH_SECRET


def resolve_database_path(settings: Settings | None = None) -> Path:
    """Resolve the shared SQLite path; relative overrides are backend-relative."""

    configured = (settings or get_settings()).qihuang_db_path
    path = Path(configured).expanduser()
    return path if path.is_absolute() else BACKEND_ROOT / path


def warn_if_default_auth_secret(settings: Settings) -> None:
    global _warned_default_auth_secret
    if _warned_default_auth_secret:
        return
    if resolve_auth_secret(settings) == DEFAULT_DEV_AUTH_SECRET:
        print("WARNING: AUTH_SECRET 未配置，正在使用开发默认密钥，请勿用于生产环境。")
        _warned_default_auth_secret = True
