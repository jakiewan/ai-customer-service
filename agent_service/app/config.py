from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。敏感信息仅从环境变量读取，不落入仓库。"""

    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_prefix="GLOBALCARE_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "globalcare-agent"
    app_version: str = "0.1.0"
    environment: str = "local"
    demo_token: str = Field(default="globalcare-demo-token", repr=False)
    auth_mode: Literal["demo", "jwt"] = "demo"
    jwt_public_key: str = ""
    jwt_issuer: str = ""
    jwt_audience: str = ""
    # `null` 仅为本地 file:// 打开方式提供兼容；生产环境应覆盖为明确 HTTPS 来源。
    cors_origins: str = "http://127.0.0.1:4173,http://localhost:4173,null"

    llm_mode: str = "mock"
    minimax_api_key: str = Field(default="", repr=False)
    minimax_base_url: str = "https://api.minimax.chat/v1"
    minimax_model: str = "MiniMax-M3"
    llm_temperature: float = 0.1

    embedding_mode: str = "hash"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimensions: int = 256
    policy_backend: str = "memory"
    policy_dir: str = "agent_service/data/policies"
    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/globalcare_agent"
    persistence_mode: Literal["memory", "postgres"] = "memory"
    checkpointer_mode: str = "memory"

    business_mode: str = "mock"
    business_base_url: str = "http://127.0.0.1:8081"
    business_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    agent_run_timeout_seconds: float = Field(default=60, gt=0, le=300)
    message_lock_timeout_seconds: float = Field(default=5, gt=0, le=60)
    mcp_mode: str = "mock"
    mcp_url: str = "http://127.0.0.1:8001/mcp"
    mcp_token: str = Field(default="", repr=False)

    max_graph_steps: int = Field(default=12, ge=1, le=64)
    max_tool_calls: int = Field(default=3, ge=0, le=16)
    token_budget: int = Field(default=4096, ge=1)
    policy_top_k: int = 3

    @model_validator(mode="after")
    def validate_auth(self):
        if self.auth_mode == "jwt" and not all((self.jwt_public_key, self.jwt_issuer, self.jwt_audience)):
            raise ValueError("JWT 模式必须提供公钥、issuer 和 audience")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
