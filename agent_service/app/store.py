import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

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
    case_draft_id: UUID | None = None
    processed_messages: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)


class ConversationNotFoundError(LookupError):
    pass


class CaseDraftNotFoundError(LookupError):
    pass


class InMemoryStore:
    """MVP 的可替换存储端口；生产配置可替换为 PostgreSQL repository。"""

    def __init__(self) -> None:
        self._conversations: dict[UUID, ConversationRecord] = {}
        self._drafts: dict[UUID, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def create_conversation(self, *, token: str, channel: Channel, market: str | None, language: str | None) -> ConversationRecord:
        async with self._lock:
            conversation = ConversationRecord(
                conversation_id=uuid4(), token=token, channel=channel, market=market, language=language
            )
            conversation.state = {
                "conversation_id": str(conversation.conversation_id),
                "messages": [],
                "market": market,
                "language": language,
                "status": conversation.status.value,
            }
            self._conversations[conversation.conversation_id] = conversation
            return conversation

    async def get_conversation(self, conversation_id: UUID, token: str | None = None) -> ConversationRecord:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(str(conversation_id))
        if token is not None and conversation.token != token:
            raise PermissionError(str(conversation_id))
        return conversation

    async def append_user_message(self, conversation_id: UUID, token: str, client_message_id: str, content: str) -> dict[str, Any] | None:
        async with self._lock:
            conversation = await self.get_conversation(conversation_id, token)
            existing = conversation.processed_messages.get(client_message_id)
            if existing is not None:
                return existing
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
            case_id = state.get("case_draft_id")
            conversation.case_draft_id = UUID(case_id) if case_id else conversation.case_draft_id
            conversation.messages.append(
                StoredMessage(uuid4(), MessageRole.ASSISTANT, reply, citations=citations)
            )
            conversation.processed_messages[client_message_id] = result
            conversation.updated_at = now_utc()
            draft = state.get("case_draft")
            if draft and conversation.case_draft_id:
                self._drafts[conversation.case_draft_id] = draft

    async def get_draft(self, draft_id: UUID) -> dict[str, Any]:
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise CaseDraftNotFoundError(str(draft_id))
        return draft

    async def get_state(self, conversation_id: UUID, token: str) -> dict[str, Any]:
        conversation = await self.get_conversation(conversation_id, token)
        return dict(conversation.state)

