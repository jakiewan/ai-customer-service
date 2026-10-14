import pytest
from fastapi.testclient import TestClient

from agent_service.app.config import Settings
from agent_service.app.main import create_app
from agent_service.app.schemas import CreateConversationRequest, MessageRequest


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
async def test_missing_scope_is_collected_across_turns():
    service = make_service()
    conv = await conversation(service)
    first = await run(service, conv, "保修多久", "first")
    assert "三级品类" in output(first) and "销售渠道" in output(first)
    assert "购买国家" not in output(first)
    third = await run(service, conv, "65539", "third")
    assert "销售渠道" in output(third)
    fourth = await run(service, conv, "商城", "fourth")
    assert "12 个月" in output(fourth)
    evidence = next(
        event["data"] for event in fourth if event["event"] == "policy_evidence"
    )
    assert (evidence["market"], evidence["goodsid"], evidence["sales_channel"]) == (
        "CN",
        "65539",
        "MALL",
    )


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
    assert "没有匹配" in output(events)
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
