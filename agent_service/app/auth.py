"""仅校验现有登录系统的凭证，不在 Agent 内实现登录或签发 Token。"""

import hashlib
import json
import secrets
from dataclasses import dataclass

import jwt
from fastapi import HTTPException

from .config import Settings


@dataclass(frozen=True)
class Principal:
    subject_key: str
    customer_id: str | None = None
    tenant_id: str | None = None

    @property
    def actor_context(self) -> dict[str, str]:
        if self.customer_id is None or self.tenant_id is None:
            return {}
        return {"authorized_customer_id": self.customer_id, "authorized_tenant_id": self.tenant_id}


def authenticate(settings: Settings, authorization: str | None, demo_token: str | None) -> Principal:
    if settings.auth_mode == "demo":
        if demo_token and secrets.compare_digest(demo_token, settings.demo_token):
            return Principal(demo_token)
    elif authorization and authorization.startswith("Bearer ") and len(authorization) <= 8192:
        try:
            claims = jwt.decode(
                authorization[7:], settings.jwt_public_key, algorithms=["RS256"],
                issuer=settings.jwt_issuer, audience=settings.jwt_audience,
                options={"require": ["iss", "aud", "sub", "iat", "exp", "tenant_id", "customer_id"]},
            )
            for field in ("sub", "tenant_id", "customer_id"):
                if not isinstance(claims[field], str) or not claims[field].strip() or len(claims[field]) > 128:
                    raise ValueError("无效身份字段")
            identity = json.dumps(
                [claims["iss"], claims["tenant_id"], claims["sub"], claims["customer_id"]],
                ensure_ascii=False, separators=(",", ":"),
            )
            return Principal(hashlib.sha256(identity.encode()).hexdigest(), claims["customer_id"], claims["tenant_id"])
        except (jwt.PyJWTError, ValueError, TypeError):
            pass
    raise HTTPException(status_code=401, detail="身份凭证无效")
