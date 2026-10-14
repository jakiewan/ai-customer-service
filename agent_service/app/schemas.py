from datetime import datetime, timezone
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .constants import (
    Channel,
    ConversationStatus,
    ErrorCode,
    Language,
    Market,
    SalesChannel,
    MessageRole,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CreateConversationRequest(BaseModel):
    channel: Literal[Channel.WEB_CHAT.value] = Channel.WEB_CHAT.value
    market: Market | None = Market.CN
    language: Language | None = None
    goodsid: str | None = Field(default=None, pattern=r"^[0-9]{5}$")
    sales_channel: SalesChannel | None = None
    client_session_id: str | None = Field(default=None, max_length=64)


class ConversationResponse(BaseModel):
    conversation_id: UUID
    status: ConversationStatus
    channel: Channel
    market: Market | None = None
    language: Language | None = None
    created_at: datetime


class MessageRequest(BaseModel):
    client_message_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    market: Market | None = Market.CN
    language: Language | None = None
    goodsid: str | None = Field(default=None, pattern=r"^[0-9]{5}$")
    sales_channel: SalesChannel | None = None

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message 不能为空")
        return cleaned


class PolicyEvidence(BaseModel):
    policy_id: str
    title: str
    version: str
    market: Market
    language: str
    goodsid: str = ""
    sales_channel: str = ""
    excerpt: str
    score: float = Field(ge=0, le=1)
    score_kind: Literal["token_overlap_heuristic"] = "token_overlap_heuristic"
    retrieval_backend: Literal["memory", "pgvector"] = "memory"
    degraded: bool = False


class BusinessFact(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    order_id: str = Field(min_length=1, max_length=128, strict=True)
    serial_number: str = Field(min_length=1, max_length=128, strict=True)
    market: Market
    product_model: str = Field(min_length=1, max_length=128, strict=True)
    customer_id: str | None = Field(default=None, min_length=1, max_length=128, strict=True)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128, strict=True)
    status: str | None = Field(default=None, min_length=1, max_length=128, strict=True)
    status_label: str | None = Field(default=None, max_length=256, strict=True)


class ActionLink(BaseModel):
    service_type: Literal["purchase", "aftersales"]
    label: str
    url: str


class MessageView(BaseModel):
    message_id: UUID
    role: MessageRole
    content: str
    created_at: datetime
    citations: list[PolicyEvidence] = Field(default_factory=list)
    action_links: list[ActionLink] = Field(default_factory=list)


class ConversationView(BaseModel):
    conversation_id: UUID
    status: ConversationStatus
    market: Market | None = None
    language: Language | None = None
    messages: list[MessageView] = Field(default_factory=list)
    goodsid: str | None = None
    sales_channel: str | None = None
    updated_at: datetime


class AuditEventView(BaseModel):
    audit_id: UUID
    trace_id: str
    event_type: str
    event_payload: dict[str, Any]
    created_at: datetime


class AgentEvent(BaseModel):
    event: str
    data: dict[str, Any]


T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    code: str = "OK"
    message: str = "success"
    data: T
    trace_id: str


class ErrorResponse(BaseModel):
    code: ErrorCode
    message: str
    retryable: bool = False
    trace_id: str
