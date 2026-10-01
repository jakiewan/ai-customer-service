"""通过 HTTP/SSE 检查预算在业务副作用之前生效。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

from fastapi.testclient import TestClient

from agent_service.app.business import MOCK_ORDERS
from agent_service.app.config import Settings
from agent_service.app.main import create_app


def send(client, settings, message):
    headers = {"X-Demo-Token": settings.demo_token}
    created = client.post("/api/v1/conversations", headers=headers, json={"market": "MY"})
    uri = "/api/v1/conversations/" + created.json()["data"]["conversation_id"]
    return client.post(uri + "/messages/stream", headers=headers,
                       json={"client_message_id": "limited-request", "message": message})


def test_tool_budget_stops_second_query_and_draft_write():
    settings = Settings(_env_file=None, max_tool_calls=1)
    with TestClient(create_app(settings)) as client:
        response = send(client, settings, "申请维修，订单 ORD-MY-2001，序列号 SN-MY-2001，手机无法开机")
        assert '"code": "AGENT_LIMIT_EXCEEDED"' in response.text
        assert "event: case_draft_created" not in response.text


def test_step_limit_prevents_external_write_instead_of_detecting_it_afterwards():
    writes = []

    class Downstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(MOCK_ORDERS["ORD-MY-2001"])

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            writes.append(payload)
            self.reply({**payload, "case_draft_id": str(uuid4())})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Downstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = Settings(_env_file=None, max_graph_steps=5, business_mode="http",
                            business_base_url=f"http://127.0.0.1:{server.server_port}")
        with TestClient(create_app(settings)) as client:
            response = send(client, settings, "申请维修，订单 ORD-MY-2001，手机无法开机")
            assert '"code": "AGENT_LIMIT_EXCEEDED"' in response.text
            assert writes == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
