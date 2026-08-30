import operator
from typing import Annotated, Any, TypedDict


class AfterSalesState(TypedDict, total=False):
    conversation_id: str
    messages: Annotated[list[dict[str, Any]], operator.add]
    market: str | None
    language: str | None
    intent: str | None
    customer_id: str | None
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

