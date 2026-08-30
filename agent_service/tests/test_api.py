from fastapi.testclient import TestClient

from agent_service.app.config import Settings
from agent_service.app.main import create_app


def test_api_creates_conversation_and_streams_sse() -> None:
    app = create_app(Settings(demo_token="api-test-token"))
    client = TestClient(app)
    headers = {"X-Demo-Token": "api-test-token"}
    created = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"channel": "WEB_CHAT", "market": "MY", "language": "zh-CN"},
    )
    assert created.status_code == 200
    conversation_id = created.json()["data"]["conversation_id"]

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        headers=headers,
        json={"client_message_id": "api-message-001", "message": "手机无法开机怎么办"},
    )
    assert response.status_code == 200
    assert "event: policy_evidence" in response.text
    assert "event: done" in response.text

    unauthenticated_draft = client.get("/api/v1/case-drafts/00000000-0000-0000-0000-000000000000")
    assert unauthenticated_draft.status_code == 401

    view = client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
    assert view.status_code == 200
    assert len(view.json()["data"]["messages"]) == 2

    audit = client.get(f"/api/v1/conversations/{conversation_id}/audit", headers=headers)
    assert audit.status_code == 200
    audit_types = [item["event_type"] for item in audit.json()["data"]]
    assert "GRAPH_NODE_COMPLETED" in audit_types
    assert "POLICY_EVIDENCE" in audit_types
