from fastapi.testclient import TestClient

from agent_service.app.config import Settings
from agent_service.app.main import create_app


def send(client, settings, message):
    headers = {"X-Demo-Token": settings.demo_token}
    created = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"market": "CN", "goodsid": "65539", "sales_channel": "MALL"},
    )
    uri = "/api/v1/conversations/" + created.json()["data"]["conversation_id"]
    return client.post(
        uri + "/messages/stream",
        headers=headers,
        json={"client_message_id": "limited-request", "message": message},
    )


def test_step_budget_stops_before_entry_result_is_published():
    settings = Settings(
        _env_file=None, max_graph_steps=1, aftersales_url="https://care.example.com"
    )
    with TestClient(create_app(settings)) as client:
        response = send(client, settings, "我要申请维修")
        assert '"code": "AGENT_LIMIT_EXCEEDED"' in response.text
        assert '"action_links"' not in response.text


def test_token_budget_stops_before_graph_execution():
    settings = Settings(_env_file=None, token_budget=1)
    with TestClient(create_app(settings)) as client:
        response = send(client, settings, "手机无法开机怎么办，请解释售后保修政策")
        assert '"code": "AGENT_BUDGET_EXCEEDED"' in response.text
        assert "event: policy_evidence" not in response.text


def test_each_new_message_gets_its_own_step_budget():
    settings = Settings(_env_file=None, max_graph_steps=3)
    with TestClient(create_app(settings)) as client:
        headers = {"X-Demo-Token": settings.demo_token}
        created = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"market": "CN", "goodsid": "65539", "sales_channel": "MALL"},
        )
        uri = "/api/v1/conversations/" + created.json()["data"]["conversation_id"]
        for i in range(4):
            response = client.post(
                uri + "/messages/stream",
                headers=headers,
                json={"client_message_id": str(i), "message": "保修期多久"},
            )
            assert "event: error" not in response.text
            assert "event: policy_evidence" in response.text
