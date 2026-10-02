from typing import Annotated, Any, TypedDict


def bounded_messages(previous: list, added: list) -> list:
    """只留最近十轮；完整历史仍存消息表，checkpoint 不无限膨胀。"""
    return (previous + added)[-20:]


class AfterSalesState(TypedDict, total=False):
    conversation_id: str
    messages: Annotated[list[dict[str, Any]], bounded_messages]
    market: str | None
    language: str | None
    intent: str | None
    customer_id: str | None
    authorized_customer_id: str | None
    authorized_tenant_id: str | None
    order_id: str | None
    serial_number: str | None
    product_model: str | None
    issue_category: str | None
    issue_summary: str | None
    customer_context: dict[str, Any] | None
    policy_evidence: list[dict[str, Any]]
    business_results: list[dict[str, Any]]
    missing_fields: list[str]
    case_draft: dict[str, Any] | None
    case_draft_id: str | None
    draft_fingerprint: str | None
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
