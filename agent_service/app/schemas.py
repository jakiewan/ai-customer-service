from datetime import datetime, timezone
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .constants import (
    CaseDraftStatus,
    Channel,
    ConversationStatus,
    ErrorCode,
    Language,
    Market,
    MessageRole,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CreateConversationRequest(BaseModel):
    channel: Literal[Channel.WEB_CHAT.value] = Channel.WEB_CHAT.value
    market: Market | None = None
    language: Language | None = None
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
    market: Market | None = None
    language: Language | None = None

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


class DraftWriteResult(BaseModel):
    case_draft_id: UUID
    created_at: datetime | None = None
    status: Literal["DRAFT_CREATED", "CASE_DRAFT_CREATED"] | None = None
    idempotent_replay: bool = Field(default=False, strict=True)
    draft_fingerprint: str | None = None
    market: Market | None = None
    conversation_id: UUID | None = None


class CaseDraftView(BaseModel):
    case_draft_id: UUID
    status: CaseDraftStatus
    conversation_id: UUID
    issue_category: str
    issue_summary: str
    suggested_resolution: str
    market: Market
    product_model: str | None = None
    order_id_masked: str | None = None
    serial_number_masked: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    policy_evidence: list[PolicyEvidence] = Field(default_factory=list)
    review_required: bool = True
    created_at: datetime


class MessageView(BaseModel):
    message_id: UUID
    role: MessageRole
    content: str
    created_at: datetime
    citations: list[PolicyEvidence] = Field(default_factory=list)


class ConversationView(BaseModel):
    conversation_id: UUID
    status: ConversationStatus
    market: Market | None = None
    language: Language | None = None
    messages: list[MessageView] = Field(default_factory=list)
    case_draft_id: UUID | None = None
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
