"""GlobalCare MVP 回归矩阵：每个参数都是一条独立、可执行的测试用例。"""


import pytest
from fastapi.testclient import TestClient

from agent_service.app.business import MockBusinessGateway
from agent_service.app.config import Settings
from agent_service.app.constants import Intent, Market
from agent_service.app.intent import classify_intent
from agent_service.app.main import create_app
from agent_service.app.policy_store import PolicyStore
from agent_service.app.schemas import CreateConversationRequest, MessageRequest

INTENT_CASES = [
    ("手机无法开机怎么办", Intent.TROUBLESHOOTING),
    ("手机开不了机", Intent.TROUBLESHOOTING),
    ("黑屏了怎么处理", Intent.TROUBLESHOOTING),
    ("手机不充电", Intent.TROUBLESHOOTING),
    ("设备出现故障", Intent.TROUBLESHOOTING),
    ("我的手机不能使用", Intent.TROUBLESHOOTING),
    ("How do I troubleshoot a black screen?", Intent.TROUBLESHOOTING),
    ("My phone won't turn on", Intent.TROUBLESHOOTING),
    ("The phone is not charging", Intent.TROUBLESHOOTING),
    ("如何排查故障", Intent.TROUBLESHOOTING),
    ("查询维修进度", Intent.REPAIR_STATUS),
    ("查询维修状态", Intent.REPAIR_STATUS),
    ("我的维修到哪了", Intent.REPAIR_STATUS),
    ("查订单状态", Intent.REPAIR_STATUS),
    ("Repair progress please", Intent.REPAIR_STATUS),
    ("Where is my repair?", Intent.REPAIR_STATUS),
    ("Can I check repair status", Intent.REPAIR_STATUS),
    ("工单修好了吗", Intent.REPAIR_STATUS),
    ("我要申请售后维修", Intent.AFTERSALES_LINK),
    ("我要申请维修", Intent.AFTERSALES_LINK),
    ("手机需要报修", Intent.AFTERSALES_LINK),
    ("请帮我送修", Intent.AFTERSALES_LINK),
    ("售后申请入口", Intent.AFTERSALES_LINK),
    ("I need a repair request", Intent.AFTERSALES_LINK),
    ("I want after-sales service", Intent.AFTERSALES_LINK),
    ("保修期多久", Intent.FAQ),
    ("保修政策是什么", Intent.FAQ),
    ("维修需要什么材料", Intent.FAQ),
    ("What is the warranty coverage?", Intent.FAQ),
    ("How long is the warranty?", Intent.FAQ),
]


@pytest.mark.parametrize("message,expected", INTENT_CASES, ids=[f"intent-{index:02d}" for index in range(1, 31)])
def test_intent_regression_matrix(message: str, expected: Intent) -> None:
    assert classify_intent(message) == expected


RAG_CASES = [
    ("中国手机保修期多久", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机保修政策", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机无法开机怎么办", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机黑屏排查", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机不充电如何处理", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机申请维修需要什么", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机如何报修", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机送修流程", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机维修进度查询规则", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机维修状态怎么查", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("China phone warranty coverage", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("China phone cannot power on", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("China phone repair request", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("China repair status", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国购买手机保修", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国购买手机售后", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国购买手机故障维修", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国购买手机维修进度", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机售后服务", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机 warranty repair", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
]


@pytest.mark.parametrize("query,market,expected_policy", RAG_CASES, ids=[f"rag-positive-{index:02d}" for index in range(1, len(RAG_CASES) + 1)])
def test_rag_market_and_evidence_matrix(query: str, market: Market, expected_policy: str) -> None:
    store = PolicyStore("agent_service/data/policies")
    evidence = store.retrieve(query, market, top_k=2, goodsid="65539", sales_channel="MALL")
    assert evidence
    assert evidence[0].policy_id == expected_policy
    assert all(item.market == market for item in evidence)
    assert all(item.version == "2026.01" and item.excerpt for item in evidence)


NEGATIVE_RAG_CASES = [
    "退款政策是什么",
    "我要退货",
    "可以换机吗",
    "赔偿金额是多少",
    "如何申请赔付",
    "支付失败怎么办",
    "refund policy",
    "replacement eligibility",
    "compensation claim",
    "发票税务怎么处理",
    "屏幕颜色怎么调",
    "手机壁纸怎么更换",
    "忘记了游戏密码",
    "天气预报",
    "附近的餐厅",
    "耳机蓝牙怎么配对",
    "笔记本电脑电池政策",
    "智能手表维修",
    "路由器网络配置",
    "账户注销流程",
]


@pytest.mark.parametrize("query", NEGATIVE_RAG_CASES, ids=[f"rag-negative-{index:02d}" for index in range(1, 21)])
def test_rag_negative_and_high_risk_matrix(query: str) -> None:
    store = PolicyStore("agent_service/data/policies")
    assert store.retrieve(query, Market.CN, goodsid="65539", sales_channel="MALL") == []


BUSINESS_CASES = [
    ("order", "ORD-CN-1001", "CN"),
    ("order", "ORD-CN-2001", "CN"),
    ("device", "SN-CN-1001", "CN"),
    ("device", "SN-CN-2001", "CN"),
    ("status_order", "ORD-CN-1001", "RECEIVED"),
    ("status_order", "ORD-CN-2001", "IN_REPAIR"),
    ("status_device", "SN-CN-1001", "RECEIVED"),
    ("status_device", "SN-CN-2001", "IN_REPAIR"),
    ("order", "ord-cn-2001", "CN"),
    ("device", "sn-cn-1001", "CN"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,identifier,expected", BUSINESS_CASES, ids=[f"business-{index:02d}" for index in range(1, 11)])
async def test_business_gateway_matrix(operation: str, identifier: str, expected: str) -> None:
    gateway = MockBusinessGateway()
    if operation == "order":
        result = await gateway.query_order(identifier)
        assert result["market"] == expected
    elif operation == "device":
        result = await gateway.query_device(identifier)
        assert result["market"] == expected
    elif operation == "status_order":
        result = await gateway.query_repair_status(order_id=identifier, serial_number=None)
        assert result["status"] == expected
    else:
        result = await gateway.query_repair_status(order_id=None, serial_number=identifier)
        assert result["status"] == expected


GRAPH_CASES = [
    ("手机无法开机怎么办", "faq_steps", "充电 30 分钟"),
    ("保修期多久", "policy_answer", "12 个月"),
    ("查询订单 ORD-CN-2001 的维修进度", "status", "官方售后入口"),
    ("我要申请售后维修，手机无法开机", "application", "官方售后入口"),
    ("我要申请售后维修", "application_simple", "官方售后入口"),
    ("退款要怎么处理", "human", "没有匹配"),
    ("在哪里申请维修", "navigation", "官方售后入口"),
    ("去官网商城购买", "purchase", "官网商城"),
    ("查询维修进度", "status_simple", "官方售后入口"),
    ("你好", "general", "请描述你的问题"),
]

async def _run_graph_case(message: str, expected_kind: str) -> str:
    app = create_app(Settings(_env_file=None, demo_token="matrix-token", purchase_url="https://shop.example.com", aftersales_url="https://care.example.com"))
    service = app.state.conversation_service
    conversation = await service.create_conversation(
        "matrix-token", CreateConversationRequest(channel="WEB_CHAT", market="CN", language="zh-CN", goodsid="65539", sales_channel="MALL")
    )
    events = [
        event
        async for event in service.stream_message(
            conversation.conversation_id,
            "matrix-token",
            MessageRequest(client_message_id=f"matrix-{expected_kind}", message=message),
            "matrix-trace",
        )
    ]
    return "".join(event["data"].get("delta", "") for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("message,expected_kind,expected_text", GRAPH_CASES, ids=[f"graph-{index:02d}" for index in range(1, 11)])
async def test_graph_function_matrix(message: str, expected_kind: str, expected_text: str) -> None:
    output = await _run_graph_case(message, expected_kind)
    assert expected_text in output


API_CASES = [
    ("bad_token", 401),
    ("missing_token", 401),
    ("invalid_channel", 400),
    ("invalid_market", 400),
    ("invalid_language", 400),
    ("healthz", 200),
    ("readyz", 200),
    ("cors", 200),
    ("missing_conversation", 404),
    ("invalid_message_body", 400),
    ("empty_message", 400),
    ("invalid_message_id", 400),
    ("invalid_conversation_uuid", 400),
    ("removed_endpoint", 404),
    ("invalid_goodsid", 400),
    ("conversation_view_with_token", 200),
    ("stream_with_token", 200),
    ("audit_with_token", 200),
    ("openapi", 200),
    ("docs", 200),
]


@pytest.mark.parametrize("case,expected_status", API_CASES, ids=[f"api-{index:02d}" for index in range(1, 21)])
def test_api_contract_and_security_matrix(case: str, expected_status: int) -> None:
    app = create_app(Settings(demo_token="matrix-api-token"))
    client = TestClient(app)
    headers = {"X-Demo-Token": "matrix-api-token"}
    if case == "bad_token":
        response = client.post("/api/v1/conversations", headers={"X-Demo-Token": "wrong"}, json={"channel": "WEB_CHAT"})
    elif case == "missing_token":
        response = client.post("/api/v1/conversations", json={"channel": "WEB_CHAT"})
    elif case == "invalid_channel":
        response = client.post("/api/v1/conversations", headers=headers, json={"channel": "PHONE"})
    elif case == "invalid_market":
        response = client.post("/api/v1/conversations", headers=headers, json={"channel": "WEB_CHAT", "market": "US"})
    elif case == "invalid_language":
        response = client.post("/api/v1/conversations", headers=headers, json={"channel": "WEB_CHAT", "language": "fr-FR"})
    elif case == "healthz":
        response = client.get("/healthz")
    elif case == "readyz":
        response = client.get("/readyz")
    elif case == "cors":
        response = client.options(
            "/api/v1/conversations",
            headers={"Origin": "http://127.0.0.1:4173", "Access-Control-Request-Method": "POST"},
        )
    elif case == "missing_conversation":
        response = client.get("/api/v1/conversations/00000000-0000-0000-0000-000000000000", headers=headers)
    elif case == "invalid_message_body":
        response = client.post(
            "/api/v1/conversations/00000000-0000-0000-0000-000000000000/messages/stream",
            headers=headers,
            json={},
        )
    elif case == "empty_message":
        response = client.post(
            "/api/v1/conversations/00000000-0000-0000-0000-000000000000/messages/stream",
            headers=headers,
            json={"client_message_id": "x", "message": "  "},
        )
    elif case == "invalid_message_id":
        response = client.post(
            "/api/v1/conversations/00000000-0000-0000-0000-000000000000/messages/stream",
            headers=headers,
            json={"client_message_id": "", "message": "hello"},
        )
    elif case == "invalid_conversation_uuid":
        response = client.get("/api/v1/conversations/not-a-uuid", headers=headers)
    elif case == "removed_endpoint":
        response = client.get("/api/v1/case-drafts/00000000-0000-0000-0000-000000000000")
    elif case == "invalid_goodsid":
        response = client.post("/api/v1/conversations", headers=headers, json={"goodsid": "phone"})
    elif case == "openapi":
        response = client.get("/openapi.json")
    elif case == "docs":
        response = client.get("/docs")
    else:
        created = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"channel": "WEB_CHAT", "market": "CN", "language": "zh-CN", "goodsid": "65539", "sales_channel": "MALL"},
        )
        conversation_id = created.json()["data"]["conversation_id"]
        if case == "conversation_view_with_token":
            response = client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
        elif case == "stream_with_token":
            response = client.post(
                f"/api/v1/conversations/{conversation_id}/messages/stream",
                headers=headers,
                json={"client_message_id": "matrix-api-message", "message": "保修期多久"},
            )
        else:
            response = client.get(f"/api/v1/conversations/{conversation_id}/audit", headers=headers)
    assert response.status_code == expected_status
