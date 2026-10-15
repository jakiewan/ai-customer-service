from typing import Annotated, Any, TypedDict


def bounded_messages(previous: list, added: list) -> list:
    """只留最近十轮；完整历史仍存消息表，checkpoint 不无限膨胀。"""
    return (previous + added)[-20:]


def recent_dialogue(messages: list[dict]) -> list[dict]:
    """最近五轮历史；一轮包含用户输入及随后助手回复，不按消息条数截断。"""

    dialogue = [item for item in messages if item.get("role") in {"user", "assistant"}]
    starts = [index for index, item in enumerate(dialogue) if item["role"] == "user"]
    return dialogue[starts[max(0, len(starts) - 5)]:] if starts else []


class AfterSalesState(TypedDict, total=False):
    conversation_id: str
    messages: Annotated[list[dict[str, Any]], bounded_messages]
    market: str | None
    language: str | None
    intent: str | None
    # 四层路由结果：命中层、SOP/FAQ 目标与 FAQ 三元组。
    route_layer: str | None
    route_target: str | None
    faq_group_id: str | None
    faq_item_id: str | None
    route_score: float | None
    classification_source: str | None
    # 分差过小时给用户选择的候选 faq_item_id
    faq_candidates: list[str]
    authorized_customer_id: str | None
    authorized_tenant_id: str | None
    product_model: str | None
    resolved_question: str
    reference_question: str | None
    reference_candidates: list[str]
    reference_clarification: str | None
    reference_source: str | None
    reference_history_turns: int
    goodsid: str | None
    sales_channel: str | None
    issue_category: str | None
    issue_summary: str | None
    policy_evidence: list[dict[str, Any]]
    missing_fields: list[str]
    action_links: list[dict[str, Any]]
    result_type: str
    reply: str | None
    citations: list[dict[str, Any]]
    human_required: bool
    human_reason: str | None
    conversation_status: str
    phase: str
    trace_id: str
    step_count: int
    tool_call_count: int
    tool_events: list[dict[str, Any]]
    error_code: str | None
