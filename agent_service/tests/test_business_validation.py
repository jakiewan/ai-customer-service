"""不可信业务响应经真实 HTTP 边界送入 Agent，不能转成草稿成功。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent_service.app.business import MOCK_ORDERS
from agent_service.app.config import Settings
from agent_service.app.main import create_app


@pytest.mark.parametrize("fault", ["missing_market", "invalid_market", "wrong_order", "wrong_sn", "empty_model", "not_object", "conflicting_device", "invalid_receipt", "wrong_receipt_market", "wrong_receipt_fingerprint", "invalid_receipt_date", "invalid_receipt_status", "invalid_receipt_boolean"])
def test_invalid_business_data_or_receipt_never_reports_draft_created(fault):
    class BusinessServer(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            data = dict(MOCK_ORDERS["ORD-MY-2001"])
            if fault == "missing_market": data.pop("market")
            if fault == "invalid_market": data["market"] = "US"
            if fault == "wrong_order": data["order_id"] = "ORD-MY-9999"
            if fault == "wrong_sn": data["serial_number"] = "SN-MY-9999"
            if fault == "empty_model": data["product_model"] = " "
            if fault == "conflicting_device" and "/devices/" in self.path: data["order_id"] = "ORD-MY-9999"
            self.reply([] if fault == "not_object" else data)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            receipt = {**payload, "case_draft_id": str(uuid4())}
            if fault == "invalid_receipt": receipt["case_draft_id"] = "not-a-uuid"
            if fault == "wrong_receipt_market": receipt["market"] = "CN"
            if fault == "wrong_receipt_fingerprint": receipt["draft_fingerprint"] = "different"
            if fault == "invalid_receipt_date": receipt["created_at"] = "not-a-date"
            if fault == "invalid_receipt_status": receipt["status"] = "WORK_ORDER_CREATED"
            if fault == "invalid_receipt_boolean": receipt["idempotent_replay"] = "false"
            self.reply(receipt)

    server = ThreadingHTTPServer(("127.0.0.1", 0), BusinessServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = Settings(_env_file=None, business_mode="http", business_base_url=f"http://127.0.0.1:{server.server_port}")
        headers = {"X-Demo-Token": settings.demo_token}
        with TestClient(create_app(settings)) as client:
            created = client.post("/api/v1/conversations", headers=headers, json={"market": "MY"})
            uri = "/api/v1/conversations/" + created.json()["data"]["conversation_id"]
            response = client.post(uri + "/messages/stream", headers=headers,
                                   json={"client_message_id": "bad-facts", "message": "申请维修，订单 ORD-MY-2001，序列号 SN-MY-2001，手机无法开机"})
            assert "event: case_draft_created" not in response.text
            assert "event: human_required" in response.text
            assert response.text.count("event: done") == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
