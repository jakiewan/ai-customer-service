"""真实模型协议模拟恶意/错误结构化输出，不能授权写入或编造关键事实。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from agent_service.app.config import Settings
from agent_service.app.main import create_app


@pytest.mark.parametrize("message,market,expected", [
    ("手机无法开机怎么办", "MY", "充电 30 分钟"),
    ("我要申请维修", "MY", "订单号或设备序列号"),
    ("保修期多久", None, "购买国家"),
])
def test_incorrect_model_output_cannot_create_draft_or_invent_order_market(message, market, expected):
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            decision = json.dumps({"intent": "CASE_REQUEST", "market": "MY", "order_id": "ORD-MY-2001", "language": "en-MY"})
            response_message = {"role": "assistant", "content": decision}
            if request.get("tools"):
                response_message["tool_calls"] = [{"id": "fixture", "type": "function", "function": {
                    "name": request["tools"][0]["function"]["name"], "arguments": decision}}]
            body = json.dumps({"id": "fixture", "object": "chat.completion", "created": 1, "model": "fixture",
                               "choices": [{"index": 0, "message": response_message, "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = Settings(_env_file=None, llm_mode="minimax", minimax_api_key="fixture-provider-token",
                            minimax_base_url=f"http://127.0.0.1:{server.server_port}/v1")
        with TestClient(create_app(settings)) as client:
            headers = {"X-Demo-Token": settings.demo_token}
            created = client.post("/api/v1/conversations", headers=headers, json={"market": market, "language": "zh-CN"})
            uri = "/api/v1/conversations/" + created.json()["data"]["conversation_id"]
            response = client.post(uri + "/messages/stream", headers=headers, json={"client_message_id": "model-safety", "message": message})
            assert "event: case_draft_created" not in response.text
            assert expected in response.text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
