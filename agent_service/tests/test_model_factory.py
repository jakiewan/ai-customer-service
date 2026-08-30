import json

from agent_service.app.model_factory import (
    _extract_json_object,
    _normalize_intent_payload,
)


def test_minimax_think_fenced_json_is_normalized_before_schema_validation() -> None:
    raw = '''<think>reasoning</think>
```json
{"intent":"REPAIR_STATUS","market":"my","language":"zh","order_id":"ORD-MY-2001"}
```'''
    payload = _normalize_intent_payload(json.loads(_extract_json_object(raw) or "{}"))

    assert payload["market"] == "MY"
    assert payload["language"] == "zh-CN"
