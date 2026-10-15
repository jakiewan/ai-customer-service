import json

from agent_service.app.model_factory import (
    AgentModel,
    KnowledgeSearch,
    _extract_json_object,
    _normalize_intent_payload,
)
from agent_service.app.state import recent_dialogue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest


def test_minimax_think_fenced_json_is_normalized_before_schema_validation() -> None:
    raw = '''<think>reasoning</think>
```json
{"intent":"REPAIR_STATUS","market":"cn","language":"zh","order_id":"ORD-CN-2001"}
```'''
    payload = _normalize_intent_payload(json.loads(_extract_json_object(raw) or "{}"))

    assert payload["market"] == "CN"
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
            decision = json.dumps({"intent": "FAQ", "market": "CN", "language": "zh-CN"})
            message = {"role": "assistant", "content": decision}
            if "智能客服小 T" in payload["messages"][0]["content"]:
                message["content"] = "你想进一步了解哪款商品的售后服务？"
            if payload.get("tools"):
                if payload["tools"][0]["function"]["name"] == "search_knowledge":
                    decision = json.dumps({"question": "手机保修多久", "goodsid": "65539"})
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
        assert decision.intent == "FAQ" and decision.market == "CN"
        assert decision.classification_source == "llm"
        assert len(requests) == 1
        assert requests[0].get("max_tokens", requests[0].get("max_completion_tokens")) == 512
        reference = await model.resolve_reference("它保修多久", [
            {"role": "user", "content": "我在看iPhone17"},
            {"role": "assistant", "content": "你想了解什么？"},
        ])
        # 此 fixture 只返回意图 JSON，不能把它当作有效的消解结果。
        assert reference is None
        assert "我在看iPhone17" in requests[1]["messages"][-1]["content"]
        assert "你想了解什么" in requests[1]["messages"][-1]["content"]
        reply = await model.fallback_reply("再说说", [{"role": "user", "content": "手机无法开机"}])
        assert reply == "你想进一步了解哪款商品的售后服务？"
        assert "手机无法开机" in requests[2]["messages"][-1]["content"]
        action = await model.agent_reply("想弄清保障年限", [], goodsid="65539", sales_channel="MALL")
        assert isinstance(action, KnowledgeSearch) and action.goodsid == "65539"
        assert requests[3]["tools"][0]["function"]["name"] == "search_knowledge"
        assert requests[3]["parallel_tool_calls"] is False
        assert "CHITCHAT" in requests[2]["messages"][-1]["content"]
        assert "UNKNOWN" in requests[3]["messages"][-1]["content"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_reference_window_counts_five_user_assistant_rounds():
    history = []
    assert recent_dialogue(history) == []
    for index in range(6):
        history.extend([
            {"role": "user", "content": f"问题{index}"},
            {"role": "assistant", "content": f"回复{index}"},
        ])
    assert recent_dialogue(history) == history[2:]
    assert recent_dialogue(history[:2]) == history[:2]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["", "https://untrusted.example", "www.untrusted.example", "答" * 2001, None])
async def test_fallback_invalid_output_or_provider_failure_is_not_a_successful_reply(reply):
    from types import SimpleNamespace

    class Chain:
        async def ainvoke(self, inputs):
            assert len(json.loads(inputs["history"])) == 10
            assert "旧问题" not in inputs["history"]
            if reply is None:
                raise TimeoutError("fixture timeout")
            return SimpleNamespace(content=reply)

    model = AgentModel(mode="mock", api_key="", base_url="", model_name="", temperature=0)
    model._fallback_chain = Chain()
    history = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回复"}]
    for index in range(5):
        history.extend([{"role": "user", "content": f"问题{index}"}, {"role": "assistant", "content": f"回复{index}"}])
    with pytest.raises(RuntimeError, match="模型回复暂不可用"):
        await model.fallback_reply("当前问题", history)


@pytest.mark.asyncio
@pytest.mark.parametrize("calls", [
    [("query_order", {"order_id": "ORD-12345"})],
    [("search_knowledge", {"question": "保修多久", "goodsid": "00000"})],
    [("search_knowledge", {"question": "保修多久", "sales_channel": "INVALID"})],
    [("search_knowledge", {"question": "   "})],
    [("search_knowledge", {"question": "保修多久", "url": "https://untrusted.example"})],
    [("search_knowledge", {"question": "保修多久"})] * 2,
])
async def test_agent_rejects_unauthorized_tools_invalid_arguments_and_multiple_calls(calls):
    from langchain_core.messages import AIMessage

    class Chain:
        async def ainvoke(self, inputs):
            return AIMessage(content="", tool_calls=[
                {"name": name, "args": args, "id": str(index)}
                for index, (name, args) in enumerate(calls)
            ])

    model = AgentModel(mode="mock", api_key="", base_url="", model_name="", temperature=0)
    model._agent_chain = Chain()
    with pytest.raises(ValueError):
        await model.agent_reply("咨询", [], goodsid=None, sales_channel=None)


@pytest.mark.asyncio
async def test_agent_can_reply_without_search_and_receives_only_five_history_rounds():
    from langchain_core.messages import AIMessage

    class Chain:
        async def ainvoke(self, inputs):
            history = json.loads(inputs["history"])
            assert len(history) == 10 and history[0]["content"] == "问题1"
            assert inputs["mode"] == "UNKNOWN"
            return AIMessage(content="你想具体了解哪方面？")

    model = AgentModel(mode="mock", api_key="", base_url="", model_name="", temperature=0)
    model._agent_chain = Chain()
    history = []
    for index in range(6):
        history.extend([{"role": "user", "content": f"问题{index}"}, {"role": "assistant", "content": "回复"}])
    assert await model.agent_reply("继续聊聊", history, goodsid=None, sales_channel=None) == "你想具体了解哪方面？"


@pytest.mark.asyncio
@pytest.mark.parametrize("referent,valid", [("iPhone17", True), ("iPhone16", False)])
async def test_reference_model_sees_both_roles_and_rejects_out_of_window_entity(referent, valid):
    from types import SimpleNamespace

    requests = []

    class Chain:
        async def ainvoke(self, inputs):
            requests.append(inputs)
            return SimpleNamespace(content=json.dumps({
                "resolved_question": f"{referent}保修多久", "referent": referent,
            }))

    model = AgentModel(mode="mock", api_key="", base_url="", model_name="", temperature=0)
    model._reference_chain = Chain()
    history = [{"role": "user", "content": "iPhone16"}, {"role": "assistant", "content": "旧商品"}]
    for index in range(5):
        history.extend([
            {"role": "user", "content": f"问题{index}"},
            {"role": "assistant", "content": "可以看看iPhone17" if index == 4 else "请继续"},
        ])
    result = await model.resolve_reference("它保修多久", history)
    assert (result is not None) is valid
    supplied = json.loads(requests[0]["history"])
    assert supplied == history[2:]
    assert len(supplied) == 10
    assert "iPhone16" not in requests[0]["history"]
    if valid:
        assert await model.resolve_reference("这款iPhone16保修多久", history) is None
    requests.clear()
    assert await model.resolve_reference("手机保修多久", history) is None
    assert requests == []
