import pytest

from agent_service.app.constants import Intent, Market
from agent_service.app.intent import classify_intent, extract_context
from agent_service.app.policy_store import PolicyStore


def test_intent_layers_route_to_different_paths() -> None:
    assert classify_intent("手机无法开机怎么办") == Intent.TROUBLESHOOTING
    assert classify_intent("查询维修进度") == Intent.REPAIR_STATUS
    assert classify_intent("我要申请售后维修") == Intent.AFTERSALES_LINK
    assert classify_intent("保修期多久") == Intent.FAQ


def test_context_extracts_order_with_fixed_mainland_market() -> None:
    context = extract_context("订单号 ORD-CN-2001，无法开机")
    assert context.order_id == "ORD-CN-2001"
    assert context.market == Market.CN


def test_policy_retrieval_defaults_to_mainland() -> None:
    store = PolicyStore("agent_service/data/policies")
    evidence = store.retrieve("无法开机需要怎么处理", goodsid="65539", sales_channel="MALL")
    assert evidence
    assert all(item.market == Market.CN for item in evidence)
    assert all(item.policy_id.startswith("CN-") for item in evidence)


@pytest.mark.parametrize("text", [
    "手机保修", "my phone warranty", "My phone cannot power on",
    "ORD-CN-2001", "SN-CN-1001", "country: US",
])
def test_text_does_not_switch_mainland_market(text):
    assert extract_context(text).market == Market.CN
