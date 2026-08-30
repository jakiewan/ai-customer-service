import pytest
from uuid import UUID

from agent_service.app.config import Settings
from agent_service.app.main import create_app
from agent_service.app.schemas import CreateConversationRequest, MessageRequest


async def collect_events(service, conversation_id, message_id: str, message: str) -> list[dict]:
    return [
        event
        async for event in service.stream_message(
            conversation_id,
            "test-token",
            MessageRequest(client_message_id=message_id, message=message),
            "trace-test",
        )
    ]


@pytest.mark.asyncio
async def test_troubleshooting_does_not_create_case_draft() -> None:
    app = create_app(Settings(demo_token="test-token"))
    service = app.state.conversation_service
    conversation = await service.create_conversation(
        "test-token", CreateConversationRequest(channel="WEB_CHAT", market="MY", language="zh-CN")
    )
    events = await collect_events(service, conversation.conversation_id, "message-001", "手机无法开机怎么办")
    output = "".join(event["data"].get("delta", "") for event in events)
    assert "充电 30 分钟" in output
    assert not any(event["event"] == "case_draft_created" for event in events)


@pytest.mark.asyncio
async def test_case_request_creates_then_reuses_draft() -> None:
    app = create_app(Settings(demo_token="test-token"))
    service = app.state.conversation_service
    conversation = await service.create_conversation(
        "test-token", CreateConversationRequest(channel="WEB_CHAT", market="MY", language="zh-CN")
    )
    first = await collect_events(
        service,
        conversation.conversation_id,
        "message-002",
        "我要申请售后维修，订单号 ORD-MY-2001，手机无法开机",
    )
    created = [event for event in first if event["event"] == "case_draft_created"]
    assert len(created) == 1
    first_draft_id = created[0]["data"]["case_draft_id"]

    second = await collect_events(service, conversation.conversation_id, "message-003", "我还是要申请维修")
    second_text = "".join(event["data"].get("delta", "") for event in second)
    assert "复用" in second_text
    assert not any(event["event"] == "case_draft_created" for event in second)
    view = await service.draft_view(UUID(first_draft_id))
    assert str(view.case_draft_id) == first_draft_id


@pytest.mark.asyncio
async def test_case_request_continues_when_user_supplies_missing_order_in_next_turn() -> None:
    app = create_app(Settings(demo_token="test-token"))
    service = app.state.conversation_service
    conversation = await service.create_conversation(
        "test-token", CreateConversationRequest(channel="WEB_CHAT", market="MY", language="zh-CN")
    )
    first = await collect_events(service, conversation.conversation_id, "message-005", "我要申请售后维修")
    first_text = "".join(event["data"].get("delta", "") for event in first)
    assert "订单号或设备序列号" in first_text

    second = await collect_events(
        service,
        conversation.conversation_id,
        "message-006",
        "订单号 ORD-MY-2001，手机无法开机",
    )
    assert any(event["event"] == "case_draft_created" for event in second)


@pytest.mark.asyncio
async def test_repair_status_returns_fact_instead_of_new_draft() -> None:
    app = create_app(Settings(demo_token="test-token"))
    service = app.state.conversation_service
    conversation = await service.create_conversation(
        "test-token", CreateConversationRequest(channel="WEB_CHAT", market="MY", language="zh-CN")
    )
    events = await collect_events(
        service, conversation.conversation_id, "message-004", "查询订单 ORD-MY-2001 的维修进度"
    )
    output = "".join(event["data"].get("delta", "") for event in events)
    assert "维修处理中" in output
    assert not any(event["event"] == "case_draft_created" for event in events)
