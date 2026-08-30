from agent_service.app.constants import Intent, Market
from agent_service.app.intent import classify_intent, extract_context
from agent_service.app.policy_store import PolicyStore


def test_intent_layers_route_to_different_paths() -> None:
    assert classify_intent("手机无法开机怎么办") == Intent.TROUBLESHOOTING
    assert classify_intent("查询维修进度") == Intent.REPAIR_STATUS
    assert classify_intent("我要申请售后维修") == Intent.CASE_REQUEST
    assert classify_intent("保修期多久") == Intent.FAQ


def test_context_extracts_order_and_market() -> None:
    context = extract_context("我在马来西亚购买，订单号 ORD-MY-2001，无法开机")
    assert context.order_id == "ORD-MY-2001"
    assert context.market == Market.MY


def test_policy_retrieval_is_market_isolated() -> None:
    store = PolicyStore("agent_service/data/policies")
    evidence = store.retrieve("无法开机需要怎么处理", Market.MY)
    assert evidence
    assert all(item.market == Market.MY for item in evidence)
    assert all(item.policy_id.startswith("MY-") for item in evidence)
