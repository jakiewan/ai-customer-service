"""PostgreSQL 会话存储；通过现有 Store 接口接入，不再依赖进程内字典。"""

import hashlib
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .checkpointer import to_psycopg_dsn
from .constants import Channel, ConversationStatus, MessageRole
from .store import (
    AuditEventRecord, CaseDraftNotFoundError, ConversationNotFoundError,
    ConversationRecord, StoredMessage, StoreRequestError,
)


def subject_hash(subject: str) -> str:
    return hashlib.sha256(subject.encode()).hexdigest()


class PostgresStore:
    def __init__(self, database_url: str, lock_timeout_seconds: float = 5) -> None:
        self.lock_timeout_seconds = lock_timeout_seconds
        self._bound_connection: ContextVar = ContextVar("globalcare_store_connection", default=None)
        self.pool = AsyncConnectionPool(
            to_psycopg_dsn(database_url), open=False, min_size=1, max_size=12, timeout=3,
            kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": 3,
                    "options": "-c statement_timeout=5000"},
        )

    async def open(self) -> None:
        await self.pool.open(wait=True, timeout=5)
        async with self.pool.connection() as connection:
            await connection.execute("SELECT state, case_draft_id FROM conversations LIMIT 0")
            await connection.execute("SELECT request_fingerprint, result FROM agent_message_requests LIMIT 0")

    async def close(self) -> None:
        await self.pool.close()

    @asynccontextmanager
    async def _connection(self):
        bound = self._bound_connection.get()
        if bound is not None:
            yield bound
            return
        async with self.pool.connection() as connection:
            yield connection

    @asynccontextmanager
    async def message_lock(self, conversation_id: UUID, token: str):
        async with self.pool.connection() as connection:
            bound = self._bound_connection.set(connection)
            acquired = False
            lock_key = int.from_bytes(hashlib.sha256(conversation_id.bytes).digest()[:8], "big", signed=True)
            try:
                await self.get_conversation(conversation_id, token)
                try:
                    async with asyncio.timeout(self.lock_timeout_seconds):
                        while not acquired:
                            row = await (await connection.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (lock_key,))).fetchone()
                            acquired = bool(row["acquired"])
                            if not acquired:
                                await asyncio.sleep(0.05)
                except TimeoutError as exc:
                    raise StoreRequestError("会话正在处理中，请稍后重试", code="CONVERSATION_BUSY", retryable=True) from exc
                yield
            finally:
                try:
                    # 即使获取锁的响应被取消，也可能已在服务器拿到锁；始终尝试释放。
                    await connection.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
                except BaseException:
                    # 连接不能带着 session lock 回到连接池；关闭连接由服务器释放锁。
                    await connection.close()
                    raise
                finally:
                    self._bound_connection.reset(bound)

    async def create_conversation(self, *, token: str, channel: Channel, market: str | None,
                                  language: str | None, actor_context: dict[str, str] | None = None):
        conversation_id = uuid4()
        state = {"conversation_id": str(conversation_id), "messages": [], "market": market,
                 "language": language, "status": "AI_ACTIVE", **(actor_context or {})}
        async with self._connection() as connection:
            await connection.execute(
                "INSERT INTO conversations (conversation_id,demo_subject_hash,channel,market,language,status,state) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (conversation_id, subject_hash(token), str(channel), market, language, "AI_ACTIVE", Jsonb(state)),
            )
        return await self.get_conversation(conversation_id, token)

    async def get_conversation(self, conversation_id: UUID, token: str | None = None):
        async with self._connection() as connection:
            row = await (await connection.execute(
                "SELECT * FROM conversations WHERE conversation_id=%s", (conversation_id,)
            )).fetchone()
            if row is None:
                raise ConversationNotFoundError(str(conversation_id))
            if token is not None and row["demo_subject_hash"] != subject_hash(token):
                raise PermissionError("会话无权访问")
            messages = await (await connection.execute(
                "SELECT * FROM conversation_messages WHERE conversation_id=%s ORDER BY created_at,message_id",
                (conversation_id,),
            )).fetchall()
        return ConversationRecord(
            conversation_id=conversation_id, token=token or "", channel=Channel(row["channel"]),
            market=row["market"], language=row["language"], status=ConversationStatus(row["status"]),
            state=row["state"], case_draft_id=row["case_draft_id"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            messages=[StoredMessage(item["message_id"], MessageRole(item["role"]), item["content"],
                                    item["created_at"], item["citations"]) for item in messages],
        )

    async def append_user_message(self, conversation_id: UUID, token: str, client_message_id: str,
                                  content: str, request_fingerprint: str | None = None):
        await self.get_conversation(conversation_id, token)
        fingerprint = request_fingerprint or subject_hash(content)
        async with self._connection() as connection, connection.transaction():
            request = await (await connection.execute(
                "SELECT request_fingerprint,result FROM agent_message_requests WHERE conversation_id=%s AND client_message_id=%s",
                (conversation_id, client_message_id),
            )).fetchone()
            if request is not None:
                if request["request_fingerprint"] != fingerprint:
                    raise StoreRequestError("消息 ID 已绑定不同请求", code="IDEMPOTENCY_CONFLICT")
                if request["result"] is None:
                    raise StoreRequestError("上次请求结果未确定，不可自动重跑", code="REQUEST_INDETERMINATE")
                return request["result"]
            await connection.execute(
                "INSERT INTO agent_message_requests (conversation_id,client_message_id,request_fingerprint) VALUES (%s,%s,%s)",
                (conversation_id, client_message_id, fingerprint),
            )
            await connection.execute(
                "INSERT INTO conversation_messages (message_id,conversation_id,role,content) VALUES (%s,%s,%s,%s)",
                (uuid4(), conversation_id, "user", content),
            )
        return None

    async def save_agent_result(self, conversation_id: UUID, token: str, *, client_message_id: str,
                                state: dict[str, Any], reply: str, citations: list[dict[str, Any]], result: dict[str, Any]):
        await self.get_conversation(conversation_id, token)
        async with self._connection() as connection, connection.transaction():
            await connection.execute(
                "UPDATE conversations SET state=%s,status=%s,market=%s,language=%s,case_draft_id=%s,updated_at=now() "
                "WHERE conversation_id=%s",
                (Jsonb(state), state.get("conversation_status", "AI_ACTIVE"), state.get("market"),
                 state.get("language"), state.get("case_draft_id"), conversation_id),
            )
            await connection.execute(
                "INSERT INTO conversation_messages (message_id,conversation_id,role,content,citations) VALUES (%s,%s,%s,%s,%s)",
                (uuid4(), conversation_id, "assistant", reply, Jsonb(citations)),
            )
            draft = state.get("case_draft")
            if draft and state.get("case_draft_id"):
                await connection.execute(
                    "INSERT INTO case_drafts (case_draft_id,conversation_id,draft_fingerprint,status,payload) "
                    "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (case_draft_id) DO UPDATE SET payload=EXCLUDED.payload",
                    (state["case_draft_id"], conversation_id, draft["draft_fingerprint"], "DRAFT_CREATED", Jsonb(draft)),
                )
            await connection.execute(
                "UPDATE agent_message_requests SET result=%s,updated_at=now() WHERE conversation_id=%s AND client_message_id=%s",
                (Jsonb(result), conversation_id, client_message_id),
            )
            await connection.execute(
                "INSERT INTO agent_audit_events (audit_id,conversation_id,trace_id,event_type,event_payload) VALUES (%s,%s,%s,%s,%s)",
                (uuid4(), conversation_id, result.get("trace_id", ""),
                 "AGENT_RUN_FAILED" if result.get("error") else "AGENT_RUN_COMPLETED",
                 Jsonb({"status": result["status"], "human_required": bool(result.get("human_required")),
                        "error_code": (result.get("error") or {}).get("code")})),
            )

    async def get_draft(self, draft_id: UUID):
        async with self._connection() as connection:
            row = await (await connection.execute("SELECT payload FROM case_drafts WHERE case_draft_id=%s", (draft_id,))).fetchone()
        if row is None:
            raise CaseDraftNotFoundError(str(draft_id))
        return row["payload"]

    async def record_audit(self, conversation_id: UUID, token: str, *, trace_id: str,
                           event_type: str, event_payload: dict[str, Any]):
        await self.get_conversation(conversation_id, token)
        async with self._connection() as connection:
            await connection.execute(
                "INSERT INTO agent_audit_events (audit_id,conversation_id,trace_id,event_type,event_payload) VALUES (%s,%s,%s,%s,%s)",
                (uuid4(), conversation_id, trace_id, event_type, Jsonb(event_payload)),
            )

    async def get_audits(self, conversation_id: UUID, token: str):
        await self.get_conversation(conversation_id, token)
        async with self._connection() as connection:
            rows = await (await connection.execute(
                "SELECT * FROM agent_audit_events WHERE conversation_id=%s ORDER BY created_at,audit_id", (conversation_id,)
            )).fetchall()
        return [AuditEventRecord(row["audit_id"], row["trace_id"], row["event_type"], row["event_payload"], row["created_at"]) for row in rows]

    async def get_state(self, conversation_id: UUID, token: str):
        return dict((await self.get_conversation(conversation_id, token)).state)
