from uuid import UUID

import pytest

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
    view = await service.draft_view(UUID(first_draft_id), "test-token")
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
    assert not any(event["event"] == "case_draft_created" for event in first)

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


@pytest.mark.asyncio
async def test_case_request_does_not_reuse_identifier_from_status_intent() -> None:
    app = create_app(Settings(demo_token="test-token"))
    service = app.state.conversation_service
    conversation = await service.create_conversation(
        "test-token", CreateConversationRequest(channel="WEB_CHAT", market="MY", language="zh-CN")
    )
    await collect_events(
        service,
        conversation.conversation_id,
        "message-011",
        "查询维修进度，订单号 ORD-MY-2001",
    )
    next_events = await collect_events(
        service,
        conversation.conversation_id,
        "message-012",
        "我要申请售后维修",
    )
    output = "".join(event["data"].get("delta", "") for event in next_events)
    assert "订单号或设备序列号" in output
    assert not any(event["event"] == "case_draft_created" for event in next_events)


@pytest.mark.asyncio
async def test_existing_draft_does_not_override_unrelated_intents() -> None:
    app = create_app(Settings(demo_token="test-token"))
    service = app.state.conversation_service
    conversation = await service.create_conversation(
        "test-token", CreateConversationRequest(channel="WEB_CHAT", market="MY", language="zh-CN")
    )
    await collect_events(
        service,
        conversation.conversation_id,
        "message-013",
        "我要申请售后维修，订单号 ORD-MY-2001，手机无法开机",
    )

    unsupported = await collect_events(
        service, conversation.conversation_id, "message-014", "智能手表维修"
    )
    unsupported_text = "".join(event["data"].get("delta", "") for event in unsupported)
    assert any(event["event"] == "human_required" for event in unsupported)
    assert "可靠的政策或业务事实" in unsupported_text
    assert not any(event["event"] == "case_draft_created" for event in unsupported)

    general = await collect_events(service, conversation.conversation_id, "message-015", "你好")
    general_text = "".join(event["data"].get("delta", "") for event in general)
    assert "查询保修政策" in general_text
    assert not any(event["event"] == "case_draft_created" for event in general)


@pytest.mark.asyncio
async def test_multiple_turns_do_not_consume_next_turn_step_budget() -> None:
    app = create_app(Settings(demo_token="test-token"))
    service = app.state.conversation_service
    conversation = await service.create_conversation(
        "test-token", CreateConversationRequest(channel="WEB_CHAT", market="MY", language="zh-CN")
    )
    await collect_events(service, conversation.conversation_id, "message-007", "手机无法开机怎么办")
    await collect_events(service, conversation.conversation_id, "message-008", "保修政策是什么")
    third = await collect_events(
        service, conversation.conversation_id, "message-009", "查询订单 ORD-MY-2001 的维修进度"
    )
    assert not any(event["event"] == "error" for event in third)
    text = "".join(event["data"].get("delta", "") for event in third)
    assert "维修处理中" in text


@pytest.mark.asyncio
async def test_message_over_budget_stops_before_graph_execution() -> None:
    app = create_app(Settings(demo_token="test-token", token_budget=1))
    service = app.state.conversation_service
    conversation = await service.create_conversation(
        "test-token", CreateConversationRequest(channel="WEB_CHAT", market="MY", language="zh-CN")
    )
    events = await collect_events(service, conversation.conversation_id, "message-010", "手机无法开机怎么办")
    errors = [event for event in events if event["event"] == "error"]
    assert errors[0]["data"]["code"] == "AGENT_BUDGET_EXCEEDED"


@pytest.mark.asyncio
async def test_another_phone_collects_identifier_instead_of_reusing_old_draft():
    app = create_app(Settings(_env_file=None, demo_token="test-token"))
    service = app.state.conversation_service
    conversation = await service.create_conversation("test-token", CreateConversationRequest(market="MY"))
    await collect_events(service, conversation.conversation_id, "old-phone", "申请维修，订单 ORD-MY-2001，手机无法开机")
    events = await collect_events(service, conversation.conversation_id, "new-phone", "另一台手机我要申请维修")
    text = "".join(event["data"].get("delta", "") for event in events)
    assert "订单号或设备序列号" in text
    assert "复用" not in text
    assert (await service.conversation_view(conversation.conversation_id, "test-token")).case_draft_id is None


@pytest.mark.asyncio
async def test_market_only_response_resumes_case_but_cancel_does_not():
    app = create_app(Settings(_env_file=None, demo_token="test-token"))
    service = app.state.conversation_service
    conversation = await service.create_conversation("test-token", CreateConversationRequest())
    await collect_events(service, conversation.conversation_id, "missing-market", "申请维修，订单 ORD-CN-1001，手机无法开机")
    continued = await collect_events(service, conversation.conversation_id, "country-only", "中国 CN")
    assert any(event["event"] == "case_draft_created" for event in continued)
    other = await service.create_conversation("test-token", CreateConversationRequest(market="MY"))
    await collect_events(service, other.conversation_id, "ask-order", "我要申请维修")
    cancelled = await collect_events(service, other.conversation_id, "cancel-with-order", "不要申请维修，订单 ORD-MY-2001")
    assert not any(event["event"] == "case_draft_created" for event in cancelled)


@pytest.mark.asyncio
async def test_how_to_apply_is_information_not_write_consent():
    app = create_app(Settings(_env_file=None, demo_token="test-token"))
    service = app.state.conversation_service
    conversation = await service.create_conversation("test-token", CreateConversationRequest(market="MY"))
    await collect_events(service, conversation.conversation_id, "existing-context", "订单 ORD-MY-2001 手机无法开机怎么办")
    events = await collect_events(service, conversation.conversation_id, "procedure-question", "申请维修需要什么材料")
    assert not any(event["event"] == "case_draft_created" for event in events)
