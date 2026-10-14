"""启动和 readiness 门禁：未配置真实依赖不能伪装 production ready。"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent_service.app.config import Settings
from agent_service.app.main import create_app
from agent_service.tests.test_production_auth import keys


def test_production_rejects_demo_defaults():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production")


@pytest.mark.parametrize(
    "field,value",
    [
        ("llm_mode", "typo"),
        ("policy_backend", "typo"),
        ("checkpointer_mode", "typo"),
        ("business_mode", "typo"),
    ],
)
def test_unknown_mode_does_not_fall_back_to_mock(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_optional_business_tools_do_not_block_consultation_readiness():
    with TestClient(
        create_app(
            Settings(_env_file=None, mcp_mode="http", mcp_url="http://127.0.0.1:1/mcp")
        )
    ) as client:
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "ready"


def test_milvus_initialization_fallback_is_not_reported_as_ready():
    settings = Settings(
        _env_file=None,
        policy_backend="milvus",
        milvus_uri="http://127.0.0.1:1",
        redis_url="redis://127.0.0.1:1/0",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["data"]["milvus"] == "down"
        assert response.json()["data"]["redis"] == "down"


@pytest.fixture
def production_config(keys):
    return dict(
        _env_file=None,
        environment="production",
        auth_mode="jwt",
        jwt_public_key=keys[1],
        jwt_issuer="https://login.example.test",
        jwt_audience="agent",
        cors_origins="https://care.example.test",
        persistence_mode="postgres",
        checkpointer_mode="postgres",
        policy_backend="milvus",
        embedding_mode="huggingface",
        embedding_dimensions=1024,
        policy_allow_fallback=False,
        llm_mode="minimax",
        minimax_api_key="fixture-key-never-a-real-credential",
        business_mode="http",
        business_base_url="https://business.example.test",
        mcp_mode="http",
        mcp_url="https://tools.example.test/mcp",
        business_token="fixture-service-credential-1234567890",
        mcp_token="fixture-mcp-credential-12345678901234",
        database_url="postgresql+psycopg://agent:fixture-db-credential@127.0.0.1/fixture",
    )


def test_explicit_production_configuration_is_accepted_without_claiming_live_connectivity(
    production_config,
):
    assert Settings(**production_config).environment == "production"


@pytest.mark.parametrize(
    "field,value",
    [
        ("policy_allow_fallback", True),
        ("embedding_mode", "hash"),
        ("persistence_mode", "memory"),
        ("checkpointer_mode", "memory"),
        ("llm_mode", "mock"),
        ("cors_origins", "null"),
        ("cors_origins", "https://*.example.test"),
        ("database_url", "postgresql+psycopg://postgres:postgres@127.0.0.1/fixture"),
    ],
)
def test_production_rejects_each_unsafe_override_without_exposing_key(
    production_config, field, value
):
    with pytest.raises(ValidationError) as error:
        Settings(**{**production_config, field: value})
    assert production_config["minimax_api_key"] not in str(error.value)
