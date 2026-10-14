"""对运行中的客服服务检查适用范围、接口边界和入口行为。"""

import httpx
from scripts.http_e2e_smoke import BASE_URL, HEADERS, parse_sse, run as smoke


def run():
    smoke()
    cases = 0
    with httpx.Client(timeout=20) as client:
        for market in ("CN", "MY"):
            for channel in ("MALL", "DIRECT", "AUTHORIZED"):
                response = client.post(BASE_URL + "/api/v1/conversations", headers=HEADERS,
                                       json={"market": market, "goodsid": "65539", "sales_channel": channel})
                response.raise_for_status()
                cid = response.json()["data"]["conversation_id"]
                reply = client.post(BASE_URL + f"/api/v1/conversations/{cid}/messages/stream", headers=HEADERS,
                                    json={"client_message_id": "policy", "message": "手机保修"})
                evidence = next(data for name, data in parse_sse(reply.text) if name == "policy_evidence")
                assert (evidence["market"], evidence["goodsid"], evidence["sales_channel"]) == (market, "65539", channel)
                cases += 1
        for payload in ({"goodsid": "phone"}, {"sales_channel": "UNKNOWN"}, {"market": "US"}):
            assert client.post(BASE_URL + "/api/v1/conversations", headers=HEADERS, json=payload).status_code == 400
            cases += 1
        assert client.post(BASE_URL + "/api/v1/conversations", json={}).status_code == 401
        cases += 1
    print(f"LIVE_BOUNDARY_OK cases={cases}")


if __name__ == "__main__":
    run()
