"""故障注入：取消/未知结果不得自动再写，失败不占住会话锁。"""

import asyncio
import os
import subprocess
import sys
import select
from pathlib import Path
from fastapi.testclient import TestClient

import pytest

from agent_service.app.config import Settings
from agent_service.app.main import create_app
from agent_service.app.schemas import CreateConversationRequest, MessageRequest
from agent_service.tests.test_postgres_conversations import database_url


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "postgres"])
async def test_disconnected_request_is_indeterminate_and_does_not_block_next_request(database_url, backend):
    app = create_app(Settings(_env_file=None, persistence_mode=backend, database_url=database_url))
    async with app.router.lifespan_context(app):
        service = app.state.conversation_service
        conversation = await service.create_conversation("owner", CreateConversationRequest(market="MY"))
        request = MessageRequest(client_message_id="disconnected", message="申请维修，订单 ORD-MY-2001，手机无法开机")
        stream = service.stream_message(conversation.conversation_id, "owner", request, "disconnect-trace")
        assert (await anext(stream))["event"] == "status"
        await stream.aclose()
        repeated = [event async for event in service.stream_message(conversation.conversation_id, "owner", request, "repeat-trace")]
        assert repeated[0]["data"]["code"] == "REQUEST_INDETERMINATE"
        assert not any(event["event"] == "case_draft_created" for event in repeated)
        following = [event async for event in service.stream_message(conversation.conversation_id, "owner",
                    MessageRequest(client_message_id="next", message="你好"), "next-trace")]
        assert following[-1]["event"] == "done"
        assert not any(event["event"] == "error" for event in following)


@pytest.mark.asyncio
async def test_graph_checkpoint_history_is_bounded_but_api_history_is_preserved():
    app = create_app(Settings(_env_file=None))
    service = app.state.conversation_service
    conversation = await service.create_conversation("owner", CreateConversationRequest(market="MY"))
    for index in range(25):
        _ = [event async for event in service.stream_message(conversation.conversation_id, "owner",
             MessageRequest(client_message_id=f"turn-{index}", message="你好"), "history-trace")]
    snapshot = await service.graph_service.graph.aget_state({"configurable": {"thread_id": str(conversation.conversation_id)}})
    assert len(snapshot.values["messages"]) == 20
    assert len((await service.conversation_view(conversation.conversation_id, "owner")).messages) == 50


@pytest.mark.asyncio
async def test_capacity_rejection_does_not_claim_request_and_disconnect_releases_slot():
    app = create_app(Settings(_env_file=None, max_active_runs=1))
    service = app.state.conversation_service
    conversation = await service.create_conversation("owner", CreateConversationRequest(market="MY"))
    first = service.stream_message(conversation.conversation_id, "owner", MessageRequest(client_message_id="held", message="你好"), "held")
    await anext(first)
    request = MessageRequest(client_message_id="capacity", message="你好")
    rejected = [event async for event in service.stream_message(conversation.conversation_id, "owner", request, "busy")]
    assert rejected[0]["data"]["code"] == "AGENT_BUSY"
    await first.aclose()
    recovered = [event async for event in service.stream_message(conversation.conversation_id, "owner", request, "recovered")]
    assert not any(event["event"] == "error" for event in recovered)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "postgres"])
async def test_total_timeout_is_audited_and_uncertain_request_is_not_reexecuted(database_url, backend):
    app = create_app(Settings(_env_file=None, persistence_mode=backend, database_url=database_url, agent_run_timeout_seconds=0.1))
    async with app.router.lifespan_context(app):
        service = app.state.conversation_service
        graph = service.graph_service.graph
        original = graph.astream
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
            async for event in original(*args, **kwargs):
                yield event
        graph.astream = slow
        conv = await service.create_conversation("owner", CreateConversationRequest(market="MY"))
        request = MessageRequest(client_message_id="timed-out", message="你好")
        events = [event async for event in service.stream_message(conv.conversation_id, "owner", request, "timeout")]
        assert any(event["event"] == "error" and event["data"]["code"] == "AGENT_RUN_TIMEOUT" for event in events)
        assert (await service.conversation_view(conv.conversation_id, "owner")).status == "FAILED"
        repeated = [event async for event in service.stream_message(conv.conversation_id, "owner", request, "repeat")]
        assert repeated[0]["data"]["code"] == "REQUEST_INDETERMINATE"
        assert any(event.event_type == "AGENT_RUN_INDETERMINATE" for event in await service.audit_views(conv.conversation_id, "owner"))



def test_process_death_releases_database_lock_but_does_not_reexecute_orphan(database_url):
    code = """
import asyncio, os, hashlib, json
from agent_service.app.postgres_store import PostgresStore
from agent_service.app.schemas import MessageRequest
async def run():
    store = PostgresStore(os.environ['GLOBALCARE_TEST_DATABASE_URL'])
    await store.open()
    conversation = await store.create_conversation(token='owner',channel='WEB_CHAT',market='MY',language='zh-CN')
    request = MessageRequest(client_message_id='orphan',message='申请维修，订单 ORD-MY-2001，手机无法开机')
    async with store.message_lock(conversation.conversation_id,'owner'):
        fingerprint = hashlib.sha256(json.dumps(request.model_dump(mode='json'),sort_keys=True).encode()).hexdigest()
        await store.append_user_message(conversation.conversation_id,'owner','orphan',request.message,fingerprint)
        print(str(conversation.conversation_id),flush=True)
        await asyncio.Event().wait()
asyncio.run(run())
"""
    process = subprocess.Popen([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2],
                               env={**os.environ, "GLOBALCARE_TEST_DATABASE_URL": database_url},
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        assert select.select([process.stdout], [], [], 10)[0], "故障子进程未到达持锁检查点"
        conversation_id = process.stdout.readline().strip()
        assert conversation_id and process.poll() is None
        process.terminate()
        process.wait(timeout=5)
        settings = Settings(_env_file=None, persistence_mode="postgres", database_url=database_url, demo_token="owner")
        with TestClient(create_app(settings)) as restarted:
            uri = "/api/v1/conversations/" + conversation_id
            headers = {"X-Demo-Token": "owner"}
            response = restarted.post(uri + "/messages/stream", headers=headers,
                                      json={"client_message_id": "orphan", "message": "申请维修，订单 ORD-MY-2001，手机无法开机"})
            assert '"code": "REQUEST_INDETERMINATE"' in response.text
            following = restarted.post(uri + "/messages/stream", headers=headers, json={"client_message_id": "next", "message": "你好"})
            assert "event: error" not in following.text
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
