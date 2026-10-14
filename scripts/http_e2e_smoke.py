"""验证运行中的 FastAPI、政策适用性、官方入口和 SSE 结果。"""

import json
import os

import httpx

BASE_URL = os.getenv("GLOBALCARE_BASE_URL", "http://127.0.0.1:8000")
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
        client.get(f"{BASE_URL}/healthz").raise_for_status()
        created = client.post(f"{BASE_URL}/api/v1/conversations", headers=HEADERS,
                              json={"market": "MY", "goodsid": "65539", "sales_channel": "MALL", "language": "zh-CN"})
        created.raise_for_status()
        cid = created.json()["data"]["conversation_id"]
        uri = f"{BASE_URL}/api/v1/conversations/{cid}"
        def ask(message, message_id):
            response = client.post(uri + "/messages/stream", headers=HEADERS,
                                   json={"client_message_id": message_id, "message": message})
            response.raise_for_status()
            events = parse_sse(response.text)
            assert events[-1][0] == "done" and not any(name == "error" for name, _ in events)
            assert not any(name == "tool_call" for name, _ in events)
            return events
        policy = ask("手机保修多久", "policy")
        evidence = next(data for name, data in policy if name == "policy_evidence")
        assert (evidence["market"], evidence["goodsid"], evidence["sales_channel"]) == ("MY", "65539", "MALL")
        assert policy[-1][1]["action_links"] == []
        entry = ask("我要申请维修", "entry")
        expected = os.getenv("GLOBALCARE_AFTERSALES_URL", "")
        assert entry[-1][1]["action_links"] == ([{"service_type": "aftersales", "label": "售后申请入口", "url": expected}] if expected else [])
        replay = ask("我要申请维修", "entry")
        assert entry[-1][1]["action_links"] == replay[-1][1]["action_links"]
        assert len(client.get(uri, headers=HEADERS).json()["data"]["messages"]) == 4
        audit = client.get(uri + "/audit", headers=HEADERS)
        audit.raise_for_status()
        assert sum(item["event_type"] == "AGENT_RUN_COMPLETED" for item in audit.json()["data"]) == 2
    print("HTTP_E2E_OK")


if __name__ == "__main__":
    run()
