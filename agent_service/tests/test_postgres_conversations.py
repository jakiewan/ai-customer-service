"""真实数据库的会话恢复：测试独立实例，不把内存状态当持久化。"""

import os
import asyncio
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from agent_service.app.config import Settings
from agent_service.app.main import create_app
from agent_service.app.schemas import CreateConversationRequest, MessageRequest


@pytest.fixture(scope="module")
def database_url():
    url = os.getenv("GLOBALCARE_TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@127.0.0.1:55432/globalcare_hardening_test")
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as connection:
        for migration in sorted((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql")):
            connection.execute(migration.read_text())
    return url


def test_new_app_recovers_messages_draft_audit_and_completed_request(database_url):
    settings = Settings(_env_file=None, persistence_mode="postgres", database_url=database_url, demo_token="restart-test")
    headers = {"X-Demo-Token": "restart-test"}
    body = {"client_message_id": "persistent-request", "message": "我要申请维修，订单 ORD-MY-2001，手机无法开机"}
    with TestClient(create_app(settings)) as first:
        created = first.post("/api/v1/conversations", headers=headers, json={"market": "MY"})
        uri = "/api/v1/conversations/" + created.json()["data"]["conversation_id"]
        response = first.post(uri + "/messages/stream", headers=headers, json=body)
        assert "event: case_draft_created" in response.text
        before = first.get(uri, headers=headers).json()["data"]
    with TestClient(create_app(settings)) as restarted:
        restored = restarted.get(uri, headers=headers)
        assert restored.status_code == 200
        assert restored.json()["data"] == before
        draft_id = before["case_draft_id"]
        assert restarted.get("/api/v1/case-drafts/" + draft_id, headers=headers).status_code == 200
        replay = restarted.post(uri + "/messages/stream", headers=headers, json=body)
        assert '"idempotent_replay": true' in replay.text
        assert restarted.get(uri, headers=headers).json()["data"]["messages"] == before["messages"]
        audit = restarted.get(uri + "/audit", headers=headers).json()["data"]
        assert sum(item["event_type"] == "AGENT_RUN_COMPLETED" for item in audit) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "postgres"])
async def test_concurrent_same_request_has_one_message_pair_and_one_completion(database_url, backend):
    settings = Settings(_env_file=None, persistence_mode=backend, database_url=database_url)
    first_app, second_app = create_app(settings), create_app(settings)
    async with first_app.router.lifespan_context(first_app), second_app.router.lifespan_context(second_app):
        first = first_app.state.conversation_service
        second = second_app.state.conversation_service if backend == "postgres" else first
        conversation = await first.create_conversation("owner", CreateConversationRequest(market="MY"))
        request = MessageRequest(client_message_id="concurrent", message="申请维修，订单 ORD-MY-2001，手机无法开机")

        async def run(service):
            return [event async for event in service.stream_message(conversation.conversation_id, "owner", request, "concurrent-trace")]

        results = await asyncio.gather(*(run(first if index % 2 == 0 else second) for index in range(5)))
        assert all(events[-1]["event"] == "done" for events in results)
        view = await first.conversation_view(conversation.conversation_id, "owner")
        assert len(view.messages) == 2
        audit = await first.audit_views(conversation.conversation_id, "owner")
        assert sum(item.event_type == "AGENT_RUN_COMPLETED" for item in audit) == 1


@pytest.mark.parametrize("message", ["保修政策是什么", "我还是要申请维修"])
def test_restart_replay_does_not_report_historical_draft_as_new_event(database_url, message):
    settings = Settings(_env_file=None, persistence_mode="postgres", database_url=database_url, demo_token="restart-test")
    headers = {"X-Demo-Token": "restart-test"}
    body = {"client_message_id": "not-created-this-round", "message": message}
    with TestClient(create_app(settings)) as first:
        created = first.post("/api/v1/conversations", headers=headers, json={"market": "MY"})
        uri = "/api/v1/conversations/" + created.json()["data"]["conversation_id"]
        draft = first.post(uri + "/messages/stream", headers=headers, json={
            "client_message_id": "original-draft", "message": "申请维修，订单 ORD-MY-2001，手机无法开机",
        })
        assert "event: case_draft_created" in draft.text
        response = first.post(uri + "/messages/stream", headers=headers, json=body)
        assert "event: case_draft_created" not in response.text
        before = first.get(uri, headers=headers).json()["data"]
        assert before["case_draft_id"] is not None
    with TestClient(create_app(settings)) as restarted:
        replay = restarted.post(uri + "/messages/stream", headers=headers, json=body)
        assert replay.status_code == 200
        assert "event: done" in replay.text
        assert "event: case_draft_created" not in replay.text
        assert restarted.get(uri, headers=headers).json()["data"]["messages"] == before["messages"]


@pytest.mark.parametrize("backend", ["memory", "postgres"])
def test_failed_run_is_queryable_and_same_id_replays_failure(database_url, backend):
    settings = Settings(_env_file=None, persistence_mode=backend, database_url=database_url, token_budget=1)
    headers = {"X-Demo-Token": settings.demo_token}
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/v1/conversations", headers=headers, json={"market": "MY"})
        uri = "/api/v1/conversations/" + created.json()["data"]["conversation_id"]
        body = {"client_message_id": "over-budget", "message": "手机无法开机怎么办，需要查询售后政策"}
        first = client.post(uri + "/messages/stream", headers=headers, json=body)
        assert '"code": "AGENT_BUDGET_EXCEEDED"' in first.text
        assert client.get(uri, headers=headers).json()["data"]["status"] == "FAILED"
        replay = client.post(uri + "/messages/stream", headers=headers, json=body)
        assert '"code": "AGENT_BUDGET_EXCEEDED"' in replay.text
        assert replay.text.count("event: done") == 1
        audit = client.get(uri + "/audit", headers=headers).json()["data"]
        assert any(event["event_type"] == "AGENT_RUN_FAILED" for event in audit)


@pytest.mark.parametrize("backend", ["memory", "postgres"])
@pytest.mark.parametrize("changed", [{"message": "你好"}, {"market": "CN"}, {"language": "en-MY"}])
def test_request_id_cannot_replay_different_content_or_context(database_url, backend, changed):
    settings = Settings(_env_file=None, persistence_mode=backend, database_url=database_url)
    headers = {"X-Demo-Token": settings.demo_token}
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/v1/conversations", headers=headers, json={"market": "MY"})
        uri = "/api/v1/conversations/" + created.json()["data"]["conversation_id"]
        original = {"client_message_id": "bound-id", "message": "保修期多久"}
        client.post(uri + "/messages/stream", headers=headers, json=original)
        response = client.post(uri + "/messages/stream", headers=headers, json={**original, **changed})
        assert '"code": "IDEMPOTENCY_CONFLICT"' in response.text
        assert len(client.get(uri, headers=headers).json()["data"]["messages"]) == 2
