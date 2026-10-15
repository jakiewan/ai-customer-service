"""生产身份契约：签发端为测试 RSA 密钥，不代表真实登录系统联调。"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from agent_service.app.config import Settings
from agent_service.app.main import create_app


@pytest.fixture(scope="module")
def keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


def bearer(key, **patch):
    claims = {
        "iss": "https://login.example.test", "aud": "globalcare-agent",
        "sub": "user-one", "tenant_id": "tenant-one", "customer_id": "CUS-CN-002",
        "iat": int(time.time()), "exp": int(time.time()) + 300,
        **patch,
    }
    return {"Authorization": "Bearer " + jwt.encode(claims, key, algorithm="RS256")}


def test_jwt_refresh_keeps_identity_but_other_tenant_cannot_read_conversation(keys):
    key, public_key = keys
    app = create_app(Settings(
        auth_mode="jwt", jwt_public_key=public_key,
        jwt_issuer="https://login.example.test", jwt_audience="globalcare-agent",
    ))
    with TestClient(app) as client:
        created = client.post("/api/v1/conversations", headers=bearer(key), json={"market": "CN"})
        assert created.status_code == 200
        uri = "/api/v1/conversations/" + created.json()["data"]["conversation_id"]
        assert client.get(uri, headers=bearer(key, exp=int(time.time()) + 600)).status_code == 200
        assert client.get(uri, headers=bearer(key, tenant_id="tenant-two")).status_code == 401
        assert client.get(uri, headers=bearer(key, sub="user-two")).status_code == 401
        assert client.get(uri + "/audit", headers=bearer(key, sub="user-two")).status_code == 401
        assert client.get(uri, headers={"X-Demo-Token": "globalcare-demo-token"}).status_code == 401


def test_local_web_uses_httponly_jwt_cookie_and_keeps_api_protected(keys):
    key, public_key = keys
    access = bearer(key)["Authorization"].removeprefix("Bearer ")
    settings = Settings(
        _env_file=None, auth_mode="jwt", jwt_public_key=public_key,
        jwt_issuer="https://login.example.test", jwt_audience="globalcare-agent",
        web_access_token=access,
    )
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/v1/conversations", json={}).status_code == 401
        page = client.get("/")
        assert page.status_code == 200 and "HttpOnly" in page.headers["set-cookie"]
        assert access not in page.text
        assert client.post("/api/v1/conversations", json={}).status_code == 200


@pytest.mark.parametrize("patch", [
    {"iss": "https://wrong.test"}, {"aud": "other-service"}, {"exp": 1},
    {"iat": 9999999999}, {"sub": ""}, {"tenant_id": ""}, {"customer_id": None},
    {"customer_id": 123}, {"sub": "x" * 129},
])
def test_invalid_jwt_claims_are_rejected(keys, patch):
    key, public_key = keys
    with TestClient(create_app(Settings(auth_mode="jwt", jwt_public_key=public_key,
                                       jwt_issuer="https://login.example.test", jwt_audience="globalcare-agent"))) as client:
        assert client.post("/api/v1/conversations", headers=bearer(key, **patch), json={}).status_code == 401
