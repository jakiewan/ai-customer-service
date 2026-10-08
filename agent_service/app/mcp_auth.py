"""MCP 服务账号凭证；生产须通过环境注入并配合 TLS/私网部署。"""

import hashlib
import secrets

from fastmcp.server.auth import AccessToken, TokenVerifier


class BusinessTokenVerifier(TokenVerifier):
    def __init__(self, service_token: str):
        super().__init__(required_scopes=["business:read"])
        self._expected_digest = hashlib.sha256(service_token.encode()).digest()

    async def verify_token(self, token: str):
        if secrets.compare_digest(hashlib.sha256(token.encode()).digest(), self._expected_digest):
            return AccessToken(token=token, client_id="globalcare-agent", scopes=["business:read"])
        return None
