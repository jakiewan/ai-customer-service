from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。敏感信息仅从环境变量读取，不落入仓库。"""

    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_prefix="GLOBALCARE_",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    app_name: str = "globalcare-agent"
    app_version: str = "0.1.0"
    environment: Literal["local", "development", "test", "staging", "production"] = (
        "local"
    )
    demo_token: str = Field(default="globalcare-demo-token", repr=False)
    auth_mode: Literal["demo", "jwt"] = "demo"
    jwt_public_key: str = ""
    jwt_issuer: str = ""
    jwt_audience: str = ""
    # 本地网页使用由运行入口提供的 JWT，Agent 仅验签，不负责签发身份。
    web_access_token: str = Field(default="", repr=False)
    # `null` 仅为本地 file:// 打开方式提供兼容；生产环境应覆盖为明确 HTTPS 来源。
    cors_origins: str = "http://127.0.0.1:4173,http://localhost:4173,null"

    llm_mode: Literal["mock", "minimax"] = "mock"
    minimax_api_key: str = Field(default="", repr=False)
    minimax_base_url: str = "https://api.minimax.cn/v1"
    minimax_model: str = "MiniMax-M3"
    llm_temperature: float = 0.1
    llm_allow_fallback: bool = True

    embedding_mode: Literal["hash", "huggingface"] = "hash"
    # BGE-M3 dense 向量 1024 维；hash 离线模式同维度，便于与 Milvus schema 对齐。
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimensions: int = Field(default=1024, ge=8, le=4096)
    policy_backend: Literal["memory", "milvus"] = "memory"
    policy_allow_fallback: bool = True
    policy_dir: str = "agent_service/data/policies"
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_db: str = "ai_customer_service"
    redis_url: str = Field(default="redis://127.0.0.1:56379/0", repr=False)
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/globalcare_agent",
        repr=False,
    )
    persistence_mode: Literal["memory", "postgres"] = "memory"
    checkpointer_mode: Literal["memory", "postgres"] = "memory"

    business_mode: Literal["mock", "http", "mcp"] = "mock"
    business_base_url: str = "http://127.0.0.1:8081"
    business_token: str = Field(default="", repr=False)
    business_health_path: str = "/internal/service-centers?market=CN"
    business_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    agent_run_timeout_seconds: float = Field(default=60, gt=0, le=300)
    message_lock_timeout_seconds: float = Field(default=5, gt=0, le=60)
    max_active_runs: int = Field(default=8, ge=1, le=128)
    mcp_mode: Literal["mock", "http", "streamable_http"] = "mock"
    mcp_url: str = "http://127.0.0.1:8001/mcp"
    mcp_token: str = Field(default="", repr=False)

    max_graph_steps: int = Field(default=12, ge=1, le=64)
    max_tool_calls: int = Field(default=3, ge=0, le=16)
    token_budget: int = Field(default=4096, ge=1)
    policy_top_k: int = 3
    purchase_url: str = ""
    aftersales_url: str = ""

    @model_validator(mode="after")
    def validate_auth(self):
        for url in (self.purchase_url, self.aftersales_url):
            parsed = urlparse(url)
            if url and (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("官方入口必须是无内嵌凭证的 HTTPS URL")
        if self.auth_mode == "jwt" and not all(
            (self.jwt_public_key, self.jwt_issuer, self.jwt_audience)
        ):
            raise ValueError("JWT 模式必须提供公钥、issuer 和 audience")
        if self.auth_mode == "jwt":
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
            from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

            try:
                key = load_pem_public_key(self.jwt_public_key.encode())
                if not isinstance(key, RSAPublicKey) or key.key_size < 2048:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError("JWT 公钥必须是至少 2048 位的 RSA 公钥") from None
        if self.environment in {"staging", "production"}:
            required = (
                self.auth_mode == "jwt",
                self.persistence_mode == "postgres",
                self.checkpointer_mode == "postgres",
                self.policy_backend == "milvus",
                self.embedding_mode == "huggingface",
                not self.policy_allow_fallback,
                self.llm_mode == "minimax",
                bool(self.minimax_api_key),
            )
            if not all(required):
                raise ValueError(
                    "生产/预发必须使用 JWT、PostgreSQL、语义检索、严格政策和真实模型"
                )
            origins = [
                origin.strip()
                for origin in self.cors_origins.split(",")
                if origin.strip()
            ]
            urls = [*origins, self.jwt_issuer, self.minimax_base_url]
            if not origins or any(
                urlparse(url).scheme != "https"
                or not urlparse(url).hostname
                or urlparse(url).username
                or urlparse(url).password
                or "*" in url
                for url in urls
            ):
                raise ValueError(
                    "生产/预发来源和远程服务必须使用明确 HTTPS 地址，不允许 null、通配符或 URL 内凭证"
                )
            database = urlparse(
                self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
            )
            if database.username == "postgres" or database.password in {
                None,
                "",
                "postgres",
                "password",
            }:
                raise ValueError("生产/预发数据库必须使用专用账号和非示例凭证")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
