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
        "sub": "user-one", "tenant_id": "tenant-one", "customer_id": "CUS-MY-001",
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
        created = client.post("/api/v1/conversations", headers=bearer(key), json={"market": "MY"})
        assert created.status_code == 200
        uri = "/api/v1/conversations/" + created.json()["data"]["conversation_id"]
        assert client.get(uri, headers=bearer(key, exp=int(time.time()) + 600)).status_code == 200
        assert client.get(uri, headers=bearer(key, tenant_id="tenant-two")).status_code == 401
        assert client.get(uri, headers=bearer(key, sub="user-two")).status_code == 401
        assert client.get(uri + "/audit", headers=bearer(key, sub="user-two")).status_code == 401
        assert client.get(uri, headers={"X-Demo-Token": "globalcare-demo-token"}).status_code == 401


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


@pytest.mark.parametrize("customer,tenant", [("other-customer", "tenant-one"), ("CUS-MY-001", "other-tenant")])
def test_valid_login_does_not_authorize_someone_elses_business_data(keys, customer, tenant):
    key, public_key = keys
    with TestClient(create_app(Settings(auth_mode="jwt", jwt_public_key=public_key,
                                       jwt_issuer="https://login.example.test", jwt_audience="globalcare-agent"))) as client:
        headers = bearer(key, customer_id=customer, tenant_id=tenant)
        created = client.post("/api/v1/conversations", headers=headers, json={"market": "MY"})
        uri = "/api/v1/conversations/" + created.json()["data"]["conversation_id"]
        response = client.post(uri + "/messages/stream", headers=headers,
                               json={"client_message_id": "forbidden-order", "message": "查询 ORD-MY-2001 的维修进度"})
        assert "event: human_required" in response.text
        assert "维修处理中" not in response.text
        response = client.post(uri + "/messages/stream", headers=headers,
                               json={"client_message_id": "forbidden-case", "message": "申请维修，订单 ORD-MY-2001，手机无法开机"})
        assert "event: case_draft_created" not in response.text
