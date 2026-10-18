import pytest
from fastapi.testclient import TestClient

from agent_service.app.config import Settings
from agent_service.app.main import create_app
from agent_service.app.schemas import CreateConversationRequest, MessageRequest
from agent_service.app.state import CONTEXT_HISTORY_TURNS


def make_service(**overrides):
    settings = Settings(
        _env_file=None,
        purchase_url="https://shop.example.com",
        aftersales_url="https://care.example.com",
        **overrides,
    )
    return create_app(settings).state.conversation_service


async def conversation(service, **scope):
    return await service.create_conversation(
        "owner", CreateConversationRequest(language="zh-CN", **scope)
    )


async def run(service, conv, message, message_id="one"):
    return [
        event
        async for event in service.stream_message(
            conv.conversation_id,
            "owner",
            MessageRequest(client_message_id=message_id, message=message),
            "trace",
        )
    ]


def output(events):
    return "".join(event["data"].get("delta", "") for event in events)


def test_graph_visualization_contains_all_conditional_routes():
    from agent_service.app.graph import build_graph

    graph = build_graph(model=None, policy_store=None, max_steps=12).get_graph()
    assert {edge.target for edge in graph.edges if edge.source == "classify_intent"} == {
        "collect_info", "retrieve_policy", "resolve_link", "unknown",
    }
    assert {edge.target for edge in graph.edges if edge.source == "retrieve_policy"} == {
        "resolve_link", "render_response",
    }
    assert "classify_intent --> __end__" not in graph.draw_mermaid()
    assert {edge.target for edge in graph.edges if edge.source == "unknown"} == {
        "collect_info", "retrieve_policy", "render_response",
    }


@pytest.mark.asyncio
async def test_name_reference_uses_fifty_rounds_and_is_isolated_between_conversations(monkeypatch):
    from agent_service.app.model_factory import AgentModel, ReferenceResolution

    async def resolve(_self, message, history):
        assert sum(item["role"] == "user" for item in history) <= CONTEXT_HISTORY_TURNS
        if message == "我是谁?" and any(item["content"] == "我是万佳恒" for item in history):
            return ReferenceResolution(resolved_question="万佳恒是谁？", referent="万佳恒")
        return None

    monkeypatch.setattr(AgentModel, "resolve_reference", resolve)
    service = make_service()
    first = await conversation(service)
    other = await conversation(service)
    await run(service, first, "我是万佳恒", "name")
    events = await run(service, first, "我是谁?", "recall")
    assert "你刚才告诉我：我是万佳恒" in output(events)
    assert "万佳恒" not in output(await run(service, other, "我是谁?", "recall"))
    for index in range(CONTEXT_HISTORY_TURNS):
        await run(service, first, "你好", f"filler-{index}")
    events = await run(service, first, "我是谁?", "expired")
    assert "万佳恒" not in output(events)
    assert "最近50轮" in output(events)


@pytest.mark.asyncio
async def test_expired_product_reference_does_not_reuse_stale_state():
    service = make_service()
    conv = await conversation(service, sales_channel="MALL")
    await run(service, conv, "iPhone17在哪里买", "subject")
    for index in range(CONTEXT_HISTORY_TURNS):
        await run(service, conv, "你好", f"filler-{index}")
    events = await run(service, conv, "它保修多久", "expired")
    assert events[-1]["data"]["result_type"] == "CLARIFICATION"
    record = await service.store.get_conversation(conv.conversation_id, "owner")
    assert record.state["product_model"] is None
    assert record.state["reference_history_turns"] == CONTEXT_HISTORY_TURNS


@pytest.mark.asyncio
async def test_resolved_question_reaches_llm_and_original_history_is_preserved(monkeypatch):
    from agent_service.app.knowledge_base import KnowledgeStore
    from agent_service.app.model_factory import AgentModel, IntentDecision

    received = []

    async def classify(_self, message, **_kwargs):
        received.append(message)
        return IntentDecision(intent="UNKNOWN", classification_source="llm")

    monkeypatch.setattr(KnowledgeStore, "classify_intent", lambda *_args: None)
    monkeypatch.setattr(AgentModel, "classify", classify)
    service = make_service()
    first = await conversation(service)
    second = await conversation(service)
    await run(service, first, "iphone17有什么颜色?", "subject")
    await run(service, second, "iPhone16有什么颜色?", "subject")
    await run(service, first, "他内存有什么?", "followup")
    assert received[-1] == "iPhone 17内存有什么?"
    record = await service.store.get_conversation(first.conversation_id, "owner")
    assert record.state["product_model"] == "iPhone 17"
    assert record.messages[-2].content == "他内存有什么?"
    audits = await service.store.get_audits(first.conversation_id, "owner")
    classified = [a.event_payload for a in audits if a.event_payload.get("node") == "classify_intent"]
    assert classified[-1]["resolved_question"] == "iPhone 17内存有什么?"
    await run(service, first, "这款iPhone16有什么颜色?", "switch")
    await run(service, first, "这款手机内存有什么?", "second-followup")
    await run(service, first, "它保修有哪些条件?", "third-followup")
    assert record.state["resolved_question"] == "iPhone 16保修有哪些条件?"


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", [None, "iPhone17和iPhone16有什么区别"])
async def test_uncertain_reference_clarifies_and_resumes_original_question(subject):
    service = make_service()
    conv = await conversation(service, sales_channel="MALL")
    if subject:
        await run(service, conv, subject, "subject")
    events = await run(service, conv, "它保修多久", "ambiguous")
    assert events[-1]["data"]["result_type"] == "CLARIFICATION"
    assert "哪款商品" in output(events)
    assert not any(event["event"] == "policy_evidence" for event in events)
    events = await run(service, conv, "iPhone17", "selection")
    record = await service.store.get_conversation(conv.conversation_id, "owner")
    assert record.state["resolved_question"] == "iPhone 17保修多久"
    assert record.state["reference_question"] is None
    assert record.state["goodsid"] == "65539"
    assert events[-1]["data"]["result_type"] == "ANSWER"


@pytest.mark.asyncio
async def test_reference_switches_category_and_does_not_contaminate_new_question():
    service = make_service()
    conv = await conversation(service, sales_channel="MALL")
    await run(service, conv, "iPhone17保修多久", "phone")
    await run(service, conv, "电脑保修多久", "computer")
    events = await run(service, conv, "这台保修多久", "followup")
    evidence = next(event["data"] for event in events if event["event"] == "policy_evidence")
    assert evidence["goodsid"] == "65540"
    record = await service.store.get_conversation(conv.conversation_id, "owner")
    assert record.state["product_model"] is None
    await run(service, conv, "你好", "new-topic")
    assert record.state["resolved_question"] == "你好"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message,kind",
    [
        ("我要申请维修", "aftersales"),
        ("在哪里申请售后维修", "aftersales"),
        ("查询维修进度", "aftersales"),
        ("去官网商城购买", "purchase"),
    ],
)
async def test_entry_request_returns_fixed_link_without_order_information(
    message, kind
):
    service = make_service()
    conv = await conversation(service)
    events = await run(service, conv, message)
    assert events[-1]["data"]["action_links"][0]["service_type"] == kind
    assert events[-1]["data"]["result_type"] == "LINK_GUIDANCE"
    assert not any(event["event"] == "tool_call" for event in events)
    assert "订单号或设备序列号" not in output(events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "保修多久",
        "申请维修需要什么材料",
        "不申请维修，先了解保修",
        "不要帮我申请维修",
        "Please don't submit a repair request",
    ],
)
async def test_information_and_negative_requests_do_not_trigger_navigation(message):
    service = make_service()
    conv = await conversation(
        service, market="CN", goodsid="65539", sales_channel="MALL"
    )
    events = await run(service, conv, message)
    assert events[-1]["data"]["action_links"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message,item", [
    ("手机商城保修多久", "warranty_period"),
    ("哪里买的设备可以保修", "warranty_scope"),
    ("售后维修要多少钱", "repair_fee"),
])
async def test_policy_questions_are_not_changed_to_navigation(message, item):
    service = make_service()
    conv = await conversation(service, goodsid="65539", sales_channel="MALL")
    events = await run(service, conv, message)
    assert events[-1]["data"]["action_links"] == []
    assert events[-1]["data"]["result_type"] == "ANSWER"
    evidence = next(event["data"] for event in events if event["event"] == "policy_evidence")
    assert evidence["faq_item_id"] == item


@pytest.mark.asyncio
async def test_missing_scope_is_collected_across_turns():
    service = make_service()
    conv = await conversation(service)
    first = await run(service, conv, "保修多久", "first")
    assert "商品品类" in output(first) and "销售渠道" not in output(first)

    assert "购买国家" not in output(first)
    second = await run(service, conv, "65539", "second")
    assert "12 个月" in output(second)
    evidence = next(
        event["data"] for event in second if event["event"] == "policy_evidence"
    )
    assert (evidence["market"], evidence["goodsid"], evidence["sales_channel"]) == (
        "CN",
        "65539",
        "MALL",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_channel,text_channel,expected", [
    (None, "", "MALL"),
    ("DIRECT", "", "DIRECT"),
    ("AUTHORIZED", "", "AUTHORIZED"),
    ("MALL", "自营店", "DIRECT"),
])
async def test_power_issue_retrieves_answer_without_channel_clarification(
    scope_channel, text_channel, expected
):
    service = make_service()
    conv = await conversation(service, sales_channel=scope_channel)
    events = await run(service, conv, text_channel + "手机无法开机怎么办？")
    evidence = next(event["data"] for event in events if event["event"] == "policy_evidence")
    assert events[-1]["data"]["result_type"] == "ANSWER"
    assert events[-1]["data"]["action_links"] == []
    assert evidence["faq_item_id"] == "cannot_power_on"
    assert evidence["sales_channel"] == expected
    assert "无法开机排查" in output(events)
    assert "请补充" not in output(events)
    record = await service.store.get_conversation(conv.conversation_id, "owner")
    assert record.state["intent"] == "TROUBLESHOOTING"
    assert record.state["route_target"] == "FAQ"
    assert record.state["missing_fields"] == []


@pytest.mark.asyncio
async def test_channel_followups_rematch_completed_question_and_keep_sessions_isolated():
    service = make_service()
    conv = await conversation(service)
    await run(service, conv, "手机无法开机怎么办？", "question")
    for channel, expected in (("商城", "MALL"), ("自营店", "DIRECT"), ("授权店", "AUTHORIZED")):
        events = await run(service, conv, channel, expected)
        assert events[-1]["data"]["result_type"] == "ANSWER"
        evidence = next(event["data"] for event in events if event["event"] == "policy_evidence")
        assert evidence["sales_channel"] == expected
        assert evidence["faq_item_id"] == "cannot_power_on"
    other = await conversation(service)
    events = await run(service, other, "自营店")
    assert not any(event["event"] == "policy_evidence" for event in events)


@pytest.mark.asyncio
async def test_unknown_uses_model_reply_with_history_and_replay_does_not_call_again(monkeypatch):
    from agent_service.app.knowledge_base import KnowledgeStore
    from agent_service.app.model_factory import AgentModel

    calls = []
    reply = "你想继续了解刚才的开机故障，还是想咨询其他问题？"

    async def fallback(_self, message, history):
        calls.append((message, history))
        return reply

    monkeypatch.setattr(KnowledgeStore, "classify_intent", lambda *_args: None)
    monkeypatch.setattr(AgentModel, "fallback_reply", fallback)
    service = make_service()
    conv = await conversation(service)
    await run(service, conv, "手机无法开机怎么办？", "question")
    events = await run(service, conv, "能展开谈谈吗", "unknown")
    assert output(events) == reply
    assert events[-1]["data"]["result_type"] == "UNKNOWN"
    assert not any(event["event"] == "policy_evidence" for event in events)
    assert calls[0][0] == "能展开谈谈吗"
    assert calls[0][1][0]["content"] == "手机无法开机怎么办？"
    assert calls[0][1][1]["role"] == "assistant"
    assert output(await run(service, conv, "能展开谈谈吗", "unknown")) == reply
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("goodsid,question,expected", [
    ("65539", "想弄清保障年限", "65539"),
    (None, "想弄清保障年限", "65539"),
    ("65539", "电脑想弄清保障年限", "65540"),
])
async def test_unknown_agent_knowledge_tool_reuses_retrieval_scope_and_idempotency(
    monkeypatch, goodsid, question, expected
):
    from agent_service.app.knowledge_base import KnowledgeStore
    from agent_service.app.model_factory import AgentModel, IntentDecision, KnowledgeSearch

    calls = []

    async def classify(_self, *_args, **_kwargs):
        return IntentDecision(intent="UNKNOWN", classification_source="llm")

    async def agent(_self, message, history, **scope):
        calls.append(message)
        return KnowledgeSearch(question="保修多久", goodsid=goodsid)

    monkeypatch.setattr(KnowledgeStore, "classify_intent", lambda *_args: None)
    monkeypatch.setattr(AgentModel, "classify", classify)
    monkeypatch.setattr(AgentModel, "agent_reply", agent)
    service = make_service()
    conv = await conversation(service)
    events = await run(service, conv, question, "agent")
    if goodsid is None:
        assert events[-1]["data"]["result_type"] == "CLARIFICATION"
        assert not any(event["event"] == "tool_call" for event in events)
        events = await run(service, conv, "手机", "scope")
        replay = await run(service, conv, "手机", "scope")
    else:
        replay = await run(service, conv, question, "agent")
    assert events[-1]["data"]["result_type"] == "ANSWER"
    assert events[-1]["data"]["route_layer"] == "L3_AGENT"
    evidence = next(event["data"] for event in events if event["event"] == "policy_evidence")
    assert evidence["goodsid"] == expected and evidence["sales_channel"] == "MALL"
    assert evidence["faq_item_id"] == "warranty_period"
    assert output(events) == output(replay)
    assert calls == [question]
    tool_events = [event["data"] for event in events if event["event"] == "tool_call"]
    assert len(tool_events) == 1 and tool_events[0]["tool_name"] == "search_knowledge"
    assert tool_events[0]["status"] == "success"
    audits = await service.store.get_audits(conv.conversation_id, "owner")
    assert sum(event.event_type == "TOOL_CALL" for event in audits) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("budget,unavailable", [(0, False), (1, True)])
async def test_agent_tool_budget_and_backend_failure_do_not_claim_success(monkeypatch, budget, unavailable):
    from agent_service.app.knowledge_base import KnowledgeStore
    from agent_service.app.model_factory import AgentModel, IntentDecision, KnowledgeSearch

    async def classify(_self, *_args, **_kwargs):
        return IntentDecision(intent="UNKNOWN", classification_source="llm")

    async def agent(_self, *_args, **_kwargs):
        return KnowledgeSearch(question="保修多久", goodsid="65539")

    def answer(*_args):
        assert unavailable, "零预算不得执行知识查询"
        raise RuntimeError("fixture knowledge unavailable")

    monkeypatch.setattr(KnowledgeStore, "classify_intent", lambda *_args: None)
    monkeypatch.setattr(KnowledgeStore, "answer", answer)
    monkeypatch.setattr(AgentModel, "classify", classify)
    monkeypatch.setattr(AgentModel, "agent_reply", agent)
    service = make_service(max_tool_calls=budget)
    conv = await conversation(service)
    events = await run(service, conv, "想弄清保障年限")
    assert not any(event["event"] == "policy_evidence" for event in events)
    if unavailable:
        assert events[-1]["data"]["result_type"] == "UNSUPPORTED"
        assert "知识检索暂不可用" in output(events)
        assert next(event["data"] for event in events if event["event"] == "tool_call")["status"] == "error"
    else:
        assert events[-1]["data"]["status"] == "FAILED"
        assert not any(event["event"] == "tool_call" for event in events)


@pytest.mark.asyncio
async def test_policy_question_and_entry_can_be_returned_together():
    service = make_service()
    conv = await conversation(
        service, market="CN", goodsid="65539", sales_channel="DIRECT"
    )
    events = await run(service, conv, "申请维修需要什么材料？请给申请入口")
    assert "官方售后页面" in output(events)
    assert any(event["event"] == "policy_evidence" for event in events)
    assert events[-1]["data"]["action_links"][0]["url"] == "https://care.example.com"


@pytest.mark.asyncio
async def test_repair_fee_question_with_entry_keeps_both_evidence_and_link():
    service = make_service()
    conv = await conversation(service, goodsid="65539", sales_channel="MALL")
    events = await run(service, conv, "申请维修需要多少钱，请给申请入口")
    evidence = next(event["data"] for event in events if event["event"] == "policy_evidence")
    assert evidence["faq_item_id"] == "repair_fee"
    assert events[-1]["data"]["action_links"][0]["service_type"] == "aftersales"


@pytest.mark.asyncio
async def test_link_and_citations_survive_history_and_idempotent_replay():
    service = make_service()
    conv = await conversation(
        service, market="CN", goodsid="65539", sales_channel="MALL"
    )
    message = "申请维修材料说明和申请入口"
    first = await run(service, conv, message)
    replay = await run(service, conv, message)
    assert output(first) == output(replay)
    assert first[-1]["data"]["action_links"] == replay[-1]["data"]["action_links"]
    view = await service.conversation_view(conv.conversation_id, "owner")
    assert len(view.messages) == 2
    assert view.messages[-1].action_links[0].url == "https://care.example.com"
    assert view.messages[-1].citations


@pytest.mark.asyncio
async def test_unconfigured_entry_reports_unavailable_instead_of_guessing_url():
    service = create_app(Settings(_env_file=None)).state.conversation_service
    conv = await conversation(service)
    events = await run(service, conv, "我要申请维修")
    assert "暂未配置" in output(events)
    assert events[-1]["data"]["action_links"] == []


@pytest.mark.asyncio
async def test_personal_business_queries_are_not_called(monkeypatch):
    from agent_service.app.business import MockBusinessGateway

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("入口流程不得查询个人业务")

    for name in ("query_order", "query_device", "query_repair_status"):
        monkeypatch.setattr(MockBusinessGateway, name, forbidden)
    service = make_service()
    conv = await conversation(service)
    events = await run(service, conv, "申请维修，订单 ORD-CN-2001")
    assert events[-1]["data"]["action_links"]
    assert not any(event["event"] == "error" for event in events)


@pytest.mark.asyncio
async def test_different_category_does_not_keep_phone_policy():
    service = make_service()
    conv = await conversation(
        service, market="CN", goodsid="65539", sales_channel="MALL"
    )
    await run(service, conv, "手机保修", "phone")
    events = await run(service, conv, "电脑保修", "computer")
    evidence = next(event["data"] for event in events if event["event"] == "policy_evidence")
    assert evidence["goodsid"] == "65540"
    assert evidence["policy_id"].startswith("CN-PC-")


@pytest.mark.asyncio
async def test_laptop_alias_uses_computer_policy_instead_of_phone_policy():
    service = make_service()
    conv = await conversation(service, goodsid="65539", sales_channel="MALL")
    events = await run(service, conv, "笔记本保修多久")
    evidence = next(event["data"] for event in events if event["event"] == "policy_evidence")
    assert evidence["goodsid"] == "65540"
    assert evidence["policy_id"].startswith("CN-PC-")


@pytest.mark.asyncio
async def test_new_purchase_question_during_scope_collection_is_not_resumed():
    service = make_service()
    conv = await conversation(service)
    await run(service, conv, "保修多久", "policy")
    events = await run(service, conv, "手机在哪里买", "purchase")
    assert events[-1]["data"]["result_type"] == "LINK_GUIDANCE"
    assert events[-1]["data"]["action_links"][0]["service_type"] == "purchase"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["你好", "今天天气如何呀?", "今天心情挺好，想聊聊天"])
async def test_chitchat_calls_model_instead_of_fixed_service_introduction(monkeypatch, message):
    from agent_service.app.knowledge_base import KnowledgeStore
    from agent_service.app.model_factory import AgentModel, IntentDecision

    calls = []

    async def classify(_self, *_args, **_kwargs):
        return IntentDecision(intent="CHITCHAT", classification_source="llm")

    async def reply(_self, question, history):
        calls.append(question)
        return "我目前无法查询实时天气，不过可以陪你聊聊出行安排。"

    monkeypatch.setattr(KnowledgeStore, "classify_intent", lambda *_args: None)
    monkeypatch.setattr(AgentModel, "classify", classify)
    monkeypatch.setattr(AgentModel, "fallback_reply", reply)
    service = make_service()
    conv = await conversation(service)
    events = await run(service, conv, message)
    assert output(events) == "我目前无法查询实时天气，不过可以陪你聊聊出行安排。"
    assert calls == [message]
    assert events[-1]["data"]["result_type"] == "CHITCHAT"
    assert events[-1]["data"]["action_links"] == []
    assert not any(event["event"] == "policy_evidence" for event in events)


@pytest.mark.asyncio
async def test_capability_question_after_product_switch_does_not_retrieve_policy():
    service = make_service()
    conv = await conversation(service, goodsid="65539", sales_channel="MALL")
    await run(service, conv, "空调不制冷怎么办", "ac")
    events = await run(service, conv, "请用一句话概括你能提供哪些协助", "capabilities")
    assert "保修" in output(events)
    assert events[-1]["data"]["result_type"] == "CHITCHAT"
    assert not any(event["event"] == "policy_evidence" for event in events)


@pytest.mark.asyncio
async def test_english_possessive_preserves_confirmed_cn_market():
    service = make_service()
    conv = await conversation(
        service, market="CN", goodsid="65539", sales_channel="MALL"
    )
    events = await run(service, conv, "How long is my phone warranty?")
    assert (
        await service.conversation_view(conv.conversation_id, "owner")
    ).market == "CN"
    assert all(
        event["data"]["market"] == "CN"
        for event in events
        if event["event"] == "policy_evidence"
    )


def test_removed_business_endpoint_is_not_in_openapi():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert not any(
            "case-drafts" in path
            for path in client.get("/openapi.json").json()["paths"]
        )
        assert (
            client.get(
                "/api/v1/case-drafts/00000000-0000-0000-0000-000000000000"
            ).status_code
            == 404
        )


@pytest.mark.asyncio
async def test_policy_defaults_to_cn_without_country_input_and_keeps_channel_isolation():
    service = make_service()
    conv = await conversation(service, goodsid="65539", sales_channel="DIRECT")
    events = await run(service, conv, "手机保修多久")
    assert conv.market == "CN"
    assert "购买国家" not in output(events)
    evidence = next(event["data"] for event in events if event["event"] == "policy_evidence")
    assert (evidence["market"], evidence["sales_channel"]) == ("CN", "DIRECT")
    assert events[-1]["data"]["result_type"] == "ANSWER"


def test_api_defaults_to_mainland_and_rejects_foreign_scope():
    settings = Settings(_env_file=None)
    with TestClient(create_app(settings)) as client:
        headers = {"X-Demo-Token": settings.demo_token}
        created = client.post("/api/v1/conversations", headers=headers, json={})
        assert created.status_code == 200
        assert created.json()["data"]["market"] == "CN"
        uri = "/api/v1/conversations/" + created.json()["data"]["conversation_id"]
        assert client.post("/api/v1/conversations", headers=headers, json={"market": "US"}).status_code == 400
        response = client.post(uri + "/messages/stream", headers=headers,
                               json={"client_message_id": "foreign", "message": "保修多久", "market": "US"})
        assert response.status_code == 400


def test_legacy_foreign_conversation_is_rejected_without_relabeling_or_replaying():
    settings = Settings(_env_file=None)
    app = create_app(settings)
    with TestClient(app) as client:
        headers = {"X-Demo-Token": settings.demo_token}
        created = client.post("/api/v1/conversations", headers=headers, json={})
        cid = created.json()["data"]["conversation_id"]
        from uuid import UUID
        record = app.state.store._conversations[UUID(cid)]
        record.market = "US"
        uri = "/api/v1/conversations/" + cid
        assert client.get(uri, headers=headers).status_code == 409
        response = client.post(uri + "/messages/stream", headers=headers,
                               json={"client_message_id": "legacy", "message": "手机保修"})
        assert response.status_code == 409
        assert response.json()["code"] == "UNSUPPORTED_MARKET"
        assert record.market == "US" and record.messages == []
