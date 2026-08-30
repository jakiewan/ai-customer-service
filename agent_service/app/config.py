from functools import lru_cache

from pydantic import Field
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
    demo_token: str = "globalcare-demo-token"
    cors_origins: str = "http://127.0.0.1:4173,http://localhost:4173"

    llm_mode: str = "mock"
    minimax_api_key: str = Field(default="", repr=False)
    minimax_base_url: str = "https://api.minimax.chat/v1"
    minimax_model: str = "MiniMax-M3"
    llm_temperature: float = 0.1

    embedding_mode: str = "hash"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    policy_backend: str = "memory"
    policy_dir: str = "agent_service/data/policies"
    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/globalcare_agent"
    persistence_mode: str = "memory"

    business_mode: str = "mock"
    business_base_url: str = "http://127.0.0.1:8081"
    business_timeout_seconds: float = 3.0
    mcp_mode: str = "mock"
    mcp_url: str = "http://127.0.0.1:8001/mcp"

    max_graph_steps: int = 12
    max_tool_calls: int = 3
    token_budget: int = 4096
    policy_top_k: int = 3


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
