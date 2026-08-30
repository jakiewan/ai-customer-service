"""验证 FastAPI → LangGraph → MCP → Spring Boot Case Draft 的本地链路。"""

import json
from collections import defaultdict

import httpx

BASE_URL = "http://127.0.0.1:8000"
HEADERS = {"X-Demo-Token": "globalcare-demo-token", "X-Request-Id": "http-e2e-smoke"}


def parse_sse(payload: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    current_event = "message"
    for block in payload.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("event: "):
                current_event = line.removeprefix("event: ")
            if line.startswith("data: "):
                events.append((current_event, json.loads(line.removeprefix("data: "))))
    return events


def run() -> None:
    with httpx.Client(timeout=20) as client:
        response = client.get(f"{BASE_URL}/healthz")
        response.raise_for_status()
        assert response.json()["data"]["status"] == "ok"

        created = client.post(
            f"{BASE_URL}/api/v1/conversations",
            headers=HEADERS,
            json={"channel": "WEB_CHAT", "market": "MY", "language": "zh-CN"},
        )
        created.raise_for_status()
        conversation_id = created.json()["data"]["conversation_id"]

        repair_request = client.post(
            f"{BASE_URL}/api/v1/conversations/{conversation_id}/messages/stream",
            headers=HEADERS,
            json={
                "client_message_id": "e2e-message-001",
                "message": "我要申请售后维修，订单号 ORD-MY-2001，手机无法开机",
            },
        )
        repair_request.raise_for_status()
        repair_events = parse_sse(repair_request.text)
        names = [name for name, _ in repair_events]
        assert "policy_evidence" in names
        assert "tool_call" in names
        created_events = [data for name, data in repair_events if name == "case_draft_created"]
        assert len(created_events) == 1
        draft_id = created_events[0]["case_draft_id"]

        draft = client.get(f"{BASE_URL}/api/v1/case-drafts/{draft_id}", headers=HEADERS)
        draft.raise_for_status()
        assert draft.json()["data"]["status"] == "DRAFT_CREATED"

        progress = client.post(
            f"{BASE_URL}/api/v1/conversations/{conversation_id}/messages/stream",
            headers=HEADERS,
            json={"client_message_id": "e2e-message-002", "message": "查询维修进度"},
        )
        progress.raise_for_status()
        progress_events = parse_sse(progress.text)
        progress_text = "".join(data.get("delta", "") for name, data in progress_events if name == "token")
        assert "维修处理中" in progress_text
        assert not [data for name, data in progress_events if name == "case_draft_created"]

    print("HTTP_E2E_OK")


if __name__ == "__main__":
    run()
