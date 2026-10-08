import json

from agent_service.app.model_factory import (
    AgentModel,
    _extract_json_object,
    _normalize_intent_payload,
)
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest


def test_minimax_think_fenced_json_is_normalized_before_schema_validation() -> None:
    raw = '''<think>reasoning</think>
```json
{"intent":"REPAIR_STATUS","market":"my","language":"zh","order_id":"ORD-MY-2001"}
```'''
    payload = _normalize_intent_payload(json.loads(_extract_json_object(raw) or "{}"))

    assert payload["market"] == "MY"
    assert payload["language"] == "zh-CN"


@pytest.mark.asyncio
async def test_langchain_provider_protocol_has_output_limit_and_parses_structured_intent():
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            decision = json.dumps({"intent": "FAQ", "market": "MY", "language": "zh-CN"})
            message = {"role": "assistant", "content": decision}
            if payload.get("tools"):
                message["tool_calls"] = [{"id": "fixture-call", "type": "function", "function": {
                    "name": payload["tools"][0]["function"]["name"], "arguments": decision}}]
            body = json.dumps({"id": "fixture-completion", "object": "chat.completion", "created": 1,
                               "model": "fixture", "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        model = AgentModel(mode="minimax", api_key="fixture-provider-token", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                           model_name="MiniMax-M3", temperature=0)
        decision = await model.classify("手机保修期多久", current_market=None, current_language=None)
        assert decision.intent == "FAQ" and decision.market == "MY"
        assert len(requests) == 1
        assert requests[0].get("max_tokens", requests[0].get("max_completion_tokens")) == 512
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
