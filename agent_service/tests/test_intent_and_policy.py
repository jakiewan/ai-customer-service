import pytest

from agent_service.app.constants import Intent, Market
from agent_service.app.intent import classify_intent, extract_context
from agent_service.app.knowledge_base import KnowledgeStore


def test_intent_layers_route_to_different_paths() -> None:
    assert classify_intent("手机无法开机怎么办") == Intent.TROUBLESHOOTING
    assert classify_intent("查询维修进度") == Intent.REPAIR_STATUS
    assert classify_intent("我要申请售后维修") == Intent.AFTERSALES_LINK
    assert classify_intent("保修期多久") == Intent.FAQ


def test_context_extracts_order_with_fixed_mainland_market() -> None:
    context = extract_context("订单号 ORD-CN-2001，无法开机")
    assert context.order_id == "ORD-CN-2001"
    assert context.market == Market.CN


def test_layer1_exact_match_ignores_punctuation_and_scope_words() -> None:
    store = KnowledgeStore("agent_service/data/policies")
    ref = store.exact_match("请问，手机商城保修期多久？")
    assert ref is not None
    assert (ref.intent_id, ref.faq_group_id, ref.faq_item_id) == (
        "FAQ",
        "grp_policy",
        "warranty_period",
    )
    assert store.exact_match("今天天气怎么样") is None


def test_layer2_matches_faq_within_group_and_returns_mainland_answer() -> None:
    store = KnowledgeStore("agent_service/data/policies")
    match = store.match_faq(
        "手机无法开机，需要怎么处理", "grp_troubleshoot", "65539", "MALL"
    )
    assert match.item is not None and match.item.faq_item_id == "cannot_power_on"
    evidence = store.answer(match.item.faq_item_id, "65539", "MALL", match.score)
    assert evidence is not None and evidence.market == Market.CN
    assert evidence.policy_id.startswith("CN-")


@pytest.mark.parametrize(
    "text",
    [
        "手机保修",
        "my phone warranty",
        "My phone cannot power on",
        "ORD-CN-2001",
        "SN-CN-1001",
        "country: US",
    ],
)
def test_text_does_not_switch_mainland_market(text):
    assert extract_context(text).market == Market.CN
