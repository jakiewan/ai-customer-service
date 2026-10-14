import asyncio
import hashlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from .constants import Channel, ConversationStatus, MessageRole


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class StoredMessage:
    message_id: UUID
    role: MessageRole
    content: str
    created_at: datetime = field(default_factory=now_utc)
    citations: list[dict[str, Any]] = field(default_factory=list)
    action_links: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ConversationRecord:
    conversation_id: UUID
    token: str
    channel: Channel
    market: str | None
    language: str | None
    status: ConversationStatus = ConversationStatus.AI_ACTIVE
    messages: list[StoredMessage] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    processed_messages: dict[str, dict[str, Any]] = field(default_factory=dict)
    request_fingerprints: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)


@dataclass
class AuditEventRecord:
    audit_id: UUID
    trace_id: str
    event_type: str
    event_payload: dict[str, Any]
    created_at: datetime = field(default_factory=now_utc)


class ConversationNotFoundError(LookupError):
    pass


class StoreRequestError(ValueError):
    def __init__(self, message: str, *, code: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class InMemoryStore:
    """MVP 的可替换存储端口；生产配置可替换为 PostgreSQL repository。"""

    def __init__(self, lock_timeout_seconds: float = 5) -> None:
        self._conversations: dict[UUID, ConversationRecord] = {}
        self._audits: dict[UUID, list[AuditEventRecord]] = {}
        self._lock = asyncio.Lock()
        self.lock_timeout_seconds = lock_timeout_seconds
        self._message_locks: WeakValueDictionary = WeakValueDictionary()

    @asynccontextmanager
    async def message_lock(self, conversation_id: UUID, token: str):
        await self.get_conversation(conversation_id, token)
        lock = self._message_locks.setdefault(conversation_id, asyncio.Lock())
        try:
            await asyncio.wait_for(lock.acquire(), self.lock_timeout_seconds)
        except TimeoutError as exc:
            raise StoreRequestError("会话正在处理中，请稍后重试", code="CONVERSATION_BUSY", retryable=True) from exc
        try:
            yield
        finally:
            lock.release()

    async def create_conversation(self, *, token: str, channel: Channel, market: str | None, language: str | None, actor_context: dict[str, str] | None = None, goodsid: str | None = None, sales_channel: str | None = None) -> ConversationRecord:
        async with self._lock:
            conversation = ConversationRecord(
                conversation_id=uuid4(), token=token, channel=channel, market=market, language=language
            )
            conversation.state = {
                "conversation_id": str(conversation.conversation_id),
                "messages": [],
                "market": market,
                "language": language,
                "goodsid": goodsid,
                "sales_channel": sales_channel,
                "status": conversation.status.value,
                **(actor_context or {}),
            }
            self._conversations[conversation.conversation_id] = conversation
            self._audits[conversation.conversation_id] = []
            return conversation

    async def get_conversation(self, conversation_id: UUID, token: str | None = None) -> ConversationRecord:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(str(conversation_id))
        if token is not None and conversation.token != token:
            raise PermissionError(str(conversation_id))
        return conversation

    async def append_user_message(self, conversation_id: UUID, token: str, client_message_id: str, content: str, request_fingerprint: str | None = None) -> dict[str, Any] | None:
        async with self._lock:
            conversation = await self.get_conversation(conversation_id, token)
            fingerprint = request_fingerprint or hashlib.sha256(content.encode()).hexdigest()
            previous = conversation.request_fingerprints.get(client_message_id)
            if previous is not None and previous != fingerprint:
                raise StoreRequestError("消息 ID 已绑定不同请求", code="IDEMPOTENCY_CONFLICT")
            existing = conversation.processed_messages.get(client_message_id)
            if existing is not None:
                return existing
            if previous is not None:
                raise StoreRequestError("上次请求结果未确定，不可自动重跑", code="REQUEST_INDETERMINATE")
            conversation.request_fingerprints[client_message_id] = fingerprint
            conversation.messages.append(StoredMessage(uuid4(), MessageRole.USER, content))
            conversation.updated_at = now_utc()
            return None

    async def save_agent_result(
        self,
        conversation_id: UUID,
        token: str,
        *,
        client_message_id: str,
        state: dict[str, Any],
        reply: str,
        citations: list[dict[str, Any]],
        result: dict[str, Any],
    ) -> None:
        async with self._lock:
            conversation = await self.get_conversation(conversation_id, token)
            conversation.state = state
            conversation.status = ConversationStatus(state.get("conversation_status", ConversationStatus.AI_ACTIVE.value))
            conversation.market = state.get("market") or conversation.market
            conversation.language = state.get("language") or conversation.language
            conversation.messages.append(
                StoredMessage(uuid4(), MessageRole.ASSISTANT, reply, citations=citations, action_links=result.get("action_links", []))
            )
            conversation.processed_messages[client_message_id] = result
            self._audits[conversation_id].append(AuditEventRecord(
                uuid4(), result.get("trace_id", ""),
                "AGENT_RUN_FAILED" if result.get("error") else "AGENT_RUN_COMPLETED",
                {"status": result["status"], "human_required": bool(result.get("human_required")),
                 "error_code": (result.get("error") or {}).get("code")},
            ))
            conversation.updated_at = now_utc()
    async def record_audit(
        self,
        conversation_id: UUID,
        token: str,
        *,
        trace_id: str,
        event_type: str,
        event_payload: dict[str, Any],
    ) -> None:
        async with self._lock:
            await self.get_conversation(conversation_id, token)
            self._audits.setdefault(conversation_id, []).append(
                AuditEventRecord(uuid4(), trace_id, event_type, event_payload)
            )

    async def get_audits(self, conversation_id: UUID, token: str) -> list[AuditEventRecord]:
        await self.get_conversation(conversation_id, token)
        return list(self._audits.get(conversation_id, []))

    async def record_unknown(self, conversation_id: UUID, token: str, trace_id: str, message_id: str):
        async with self._lock:
            conversation = await self.get_conversation(conversation_id, token)
            conversation.status = ConversationStatus.FAILED
            conversation.state["conversation_status"] = "FAILED"
            self._audits[conversation_id].append(AuditEventRecord(uuid4(), trace_id, "AGENT_RUN_INDETERMINATE",
                                                                 {"client_message_id": message_id, "code": "AGENT_RUN_TIMEOUT"}))

    async def get_state(self, conversation_id: UUID, token: str) -> dict[str, Any]:
        conversation = await self.get_conversation(conversation_id, token)
        return dict(conversation.state)
