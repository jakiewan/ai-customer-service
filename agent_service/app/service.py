import asyncio
import hashlib
import json
import logging
from contextlib import aclosing
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from .constants import ConversationStatus, ErrorCode
from .graph import AgentBudgetError, AgentRunError, GraphService
from .schemas import (
    AuditEventView,
    CaseDraftView,
    ConversationResponse,
    ConversationView,
    CreateConversationRequest,
    MessageRequest,
    MessageView,
    PolicyEvidence,
    utc_now,
)
from .store import InMemoryStore, StoreRequestError
from .postgres_store import PostgresStore

logger = logging.getLogger(__name__)


NODE_LABELS = {
    "classify_intent": "识别用户意图",
    "collect_info": "收集必要信息",
    "retrieve_policy": "检索购买市场政策",
    "query_business": "查询业务事实",
    "build_case_draft": "生成结构化 Case Draft",
    "validate_draft": "校验草稿字段与证据",
    "create_case_draft": "保存 Case Draft",
    "reuse_draft": "复用已有 Case Draft",
    "render_response": "生成客服回复",
}


class ConversationService:
    def __init__(self, *, store: InMemoryStore | PostgresStore, graph_service: GraphService, run_timeout_seconds: float = 60, max_active_runs: int = 8) -> None:
        self.store = store
        self.graph_service = graph_service
        self.run_timeout_seconds = run_timeout_seconds
        self.max_active_runs = max_active_runs
        self._active_runs = 0

    async def create_conversation(self, token: str, request: CreateConversationRequest, *, actor_context: dict[str, str] | None = None) -> ConversationResponse:
        record = await self.store.create_conversation(
            token=token,
            channel=request.channel,
            market=request.market.value if request.market else None,
            language=request.language.value if request.language else None,
            actor_context=actor_context,
        )
        return ConversationResponse(
            conversation_id=record.conversation_id,
            status=record.status,
            channel=record.channel,
            market=record.market,
            language=record.language,
            created_at=record.created_at,
        )

    async def stream_message(
        self, conversation_id: UUID, token: str, request: MessageRequest, trace_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        if self._active_runs >= self.max_active_runs:
            yield {"event": "error", "data": {"code": "AGENT_BUSY", "message": "Agent 当前繁忙，请稍后重试", "retryable": True, "trace_id": trace_id}}
            yield {"event": "done", "data": {"status": "FAILED", "trace_id": trace_id}}
            return
        self._active_runs += 1
        try:
            async with self.store.message_lock(conversation_id, token):
                try:
                    async with asyncio.timeout(self.run_timeout_seconds):
                        async with aclosing(self._stream_message(conversation_id, token, request, trace_id)) as events:
                            async for event in events:
                                yield event
                except TimeoutError:
                    await self.store.record_unknown(conversation_id, token, trace_id, request.client_message_id)
                    raise
        except StoreRequestError as exc:
            yield {"event": "error", "data": {"code": exc.code, "message": str(exc), "retryable": exc.retryable, "trace_id": trace_id}}
            yield {"event": "done", "data": {"status": "FAILED", "trace_id": trace_id}}
        except TimeoutError:
            yield {"event": "error", "data": {"code": "AGENT_RUN_TIMEOUT", "message": "Agent 本轮处理超时，结果需核对后再继续", "retryable": False, "trace_id": trace_id}}
            yield {"event": "done", "data": {"status": "FAILED", "trace_id": trace_id}}
        except Exception as exc:
            logger.error("消息处理异常: trace_id=%s type=%s", trace_id, type(exc).__name__)
            yield {"event": "error", "data": {"code": "INTERNAL_ERROR", "message": "服务暂时不可用，结果需核对后再继续", "retryable": False, "trace_id": trace_id}}
            yield {"event": "done", "data": {"status": "FAILED", "trace_id": trace_id}}
        finally:
            self._active_runs -= 1

    async def _stream_message(
        self, conversation_id: UUID, token: str, request: MessageRequest, trace_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        record = await self.store.get_conversation(conversation_id, token)
        duplicate = await self.store.append_user_message(
            conversation_id, token, request.client_message_id, request.message,
            request_fingerprint=hashlib.sha256(json.dumps(request.model_dump(mode="json"), sort_keys=True).encode()).hexdigest(),
        )
        if duplicate is not None:
            await self._audit(
                conversation_id, token, trace_id, "MESSAGE_IDEMPOTENT_REPLAY", {"client_message_id": request.client_message_id}
            )
            yield {"event": "status", "data": {"phase": record.status.value, "message": "幂等重放：返回上次处理结果"}}
            for event in self._replay_result(duplicate, trace_id):
                yield event
            return

        await self._audit(
            conversation_id, token, trace_id, "MESSAGE_RECEIVED", {"client_message_id": request.client_message_id}
        )

        persisted_state = dict(record.state)
        state: dict[str, Any] = {
            **persisted_state,
            "conversation_id": str(conversation_id),
            # 仅把当前新增消息交给 reducer，历史上下文由 checkpoint/Store 维护。
            "messages": [{"role": "user", "content": request.message}],
            "market": request.market.value if request.market else record.market,
            "language": request.language.value if request.language else record.language,
            "trace_id": trace_id,
            "reply": None,
            "citations": [],
            "policy_evidence": [],
            "business_results": [],
            "human_required": False,
            "human_reason": None,
            "error_code": None,
            "tool_events": [],
            # 运行预算按单条用户消息计算；订单、草稿和缺失字段才是跨轮状态。
            "step_count": 0,
            "tool_call_count": 0,
        }
        yield {"event": "status", "data": {"phase": "AI_ACTIVE", "message": "正在理解你的售后问题"}}
        final_state: dict[str, Any] | None = None
        created_draft = False
        draft_event: dict[str, Any] | None = None
        evidence_sent: set[str] = set()
        try:
            async for node_name, patch, current_state in self.graph_service.stream(
                state, thread_id=str(conversation_id)
            ):
                if node_name == "__done__":
                    final_state = current_state
                    continue
                phase = patch.get("phase") or current_state.get("phase") or node_name
                await self._audit(
                    conversation_id,
                    token,
                    trace_id,
                    "GRAPH_NODE_COMPLETED",
                    {"node": node_name, "phase": str(phase)},
                )
                yield {
                    "event": "status",
                    "data": {
                        "phase": str(phase),
                        "node": node_name,
                        "message": NODE_LABELS.get(node_name, node_name),
                    },
                }
                for tool_event in patch.get("tool_events", []):
                    await self._audit(
                        conversation_id,
                        token,
                        trace_id,
                        "TOOL_CALL",
                        {
                            "tool_name": tool_event.get("tool_name"),
                            "status": tool_event.get("status"),
                            "tool_trace_id": tool_event.get("trace_id"),
                            "error_code": tool_event.get("error_code"),
                        },
                    )
                    yield {"event": "tool_call", "data": tool_event}
                evidence_to_send = patch.get("policy_evidence", [])
                if current_state.get("intent") == "CASE_REQUEST":
                    evidence_to_send = current_state.get("policy_evidence", []) if node_name == "query_business" and not current_state.get("human_required") else []
                for evidence in evidence_to_send:
                    policy_id = str(evidence.get("policy_id"))
                    if policy_id not in evidence_sent:
                        evidence_sent.add(policy_id)
                        await self._audit(
                            conversation_id,
                            token,
                            trace_id,
                            "POLICY_EVIDENCE",
                            {
                                "policy_id": evidence.get("policy_id"),
                                "market": evidence.get("market"),
                                "version": evidence.get("version"),
                            },
                        )
                        yield {"event": "policy_evidence", "data": evidence}
                if node_name == "create_case_draft" and patch.get("case_draft_id"):
                    created_draft = True
                    draft = patch.get("case_draft") or {}
                    await self._audit(
                        conversation_id,
                        token,
                        trace_id,
                        "CASE_DRAFT_CREATED",
                        {"case_draft_id": patch["case_draft_id"], "status": "DRAFT_CREATED"},
                    )
                    draft_event = {
                        "event": "case_draft_created",
                        "data": {
                        "case_draft_id": patch["case_draft_id"],
                        "status": ConversationStatus.CASE_DRAFT_CREATED.value,
                        "review_required": True,
                        "summary": draft.get("issue_summary", "已保存售后受理草稿"),
                        "market": draft.get("market"),
                        "idempotent_replay": bool(draft.get("idempotent_replay", False)),
                        },
                    }
        except Exception as exc:
            logger.error("Agent 运行失败: trace_id=%s type=%s", trace_id, type(exc).__name__)
            code = (ErrorCode.AGENT_BUDGET_EXCEEDED if isinstance(exc, AgentBudgetError) else
                    ErrorCode.AGENT_LIMIT_EXCEEDED if isinstance(exc, AgentRunError) else ErrorCode.INTERNAL_ERROR)
            message = str(exc) if isinstance(exc, AgentRunError) else "Agent 暂时无法处理，结果需核对后再继续"
            async for event in self._failure(conversation_id, token, request.client_message_id, trace_id, code.value, message):
                yield event
            return

        if final_state is None:
            async for event in self._failure(conversation_id, token, request.client_message_id, trace_id, "INTERNAL_ERROR", "Agent 没有最终状态"):
                yield event
            return
        reply = str(final_state.get("reply") or "")
        result = {
            "reply": reply,
            "citations": [] if final_state.get("human_required") else final_state.get("citations", []),
            "case_draft_id": final_state.get("case_draft_id"),
            # 会话历史草稿 ID 不代表本轮发过草稿事件；保存实际事件用于幂等重放。
            "case_draft_event": draft_event["data"] if draft_event else None,
            "market": final_state.get("case_draft", {}).get("market") if final_state.get("case_draft") else final_state.get("market"),
            "status": final_state.get("conversation_status", ConversationStatus.AI_ACTIVE.value),
            "human_required": bool(final_state.get("human_required")),
            "trace_id": trace_id,
        }
        await self.store.save_agent_result(
            conversation_id,
            token,
            client_message_id=request.client_message_id,
            state=final_state,
            reply=reply,
            citations=result["citations"],
            result=result,
        )
        if draft_event:
            yield draft_event
        for chunk in _chunks(reply, 28):
            yield {"event": "token", "data": {"delta": chunk}}
        if final_state.get("human_required"):
            yield {"event": "human_required", "data": {"reason": final_state.get("human_reason") or "当前 Agent 无法可靠处理"}}
        yield {
            "event": "done",
            "data": {
                "status": result["status"],
                "trace_id": trace_id,
                "case_draft_id": result.get("case_draft_id"),
                "created_draft": created_draft,
            },
        }

    @staticmethod
    def _replay_result(result: dict[str, Any], trace_id: str) -> Any:
        if result.get("error"):
            yield {"event": "error", "data": {**result["error"], "trace_id": trace_id}}
            yield {"event": "done", "data": {"status": "FAILED", "trace_id": trace_id}}
            return
        reply = str(result.get("reply", ""))
        for chunk in _chunks(reply, 28):
            yield {"event": "token", "data": {"delta": chunk}}
        for evidence in result.get("citations", []):
            yield {"event": "policy_evidence", "data": evidence}
        draft_data = result.get("case_draft_event")
        if "case_draft_event" not in result and result.get("case_draft_id") and result.get("status") == ConversationStatus.CASE_DRAFT_CREATED.value:
            # 兼容旧缓存；常规 FAQ/进度的 AI_ACTIVE 状态不能推导出草稿事件。
            draft_data = {
                "case_draft_id": result["case_draft_id"],
                "status": ConversationStatus.CASE_DRAFT_CREATED.value,
                "review_required": True,
                "summary": "已复用现有 Case Draft",
                "market": result.get("market"),
            }
        if draft_data:
            yield {
                "event": "case_draft_created",
                "data": {**draft_data, "idempotent_replay": True},
            }
        if result.get("human_required"):
            yield {"event": "human_required", "data": {"reason": "上次请求未能可靠处理"}}
        yield {"event": "done", "data": {"status": result.get("status", "AI_ACTIVE"), "trace_id": trace_id}}

    async def _failure(self, conversation_id: UUID, token: str, message_id: str, trace_id: str, code: str, message: str):
        previous = await self.store.get_conversation(conversation_id, token)
        error = {"code": code, "message": message, "retryable": False, "trace_id": trace_id}
        result = {"reply": message, "status": "FAILED", "trace_id": trace_id, "error": error}
        await self.store.save_agent_result(
            conversation_id, token, client_message_id=message_id,
            state={**previous.state, "conversation_status": "FAILED"}, reply=message, citations=[], result=result,
        )
        yield {"event": "error", "data": error}
        yield {"event": "done", "data": {"status": "FAILED", "trace_id": trace_id}}

    async def conversation_view(self, conversation_id: UUID, token: str) -> ConversationView:
        record = await self.store.get_conversation(conversation_id, token)
        messages = [
            MessageView(
                message_id=item.message_id,
                role=item.role,
                content=item.content,
                created_at=item.created_at,
                citations=[PolicyEvidence.model_validate(citation) for citation in item.citations],
            )
            for item in record.messages
        ]
        return ConversationView(
            conversation_id=record.conversation_id,
            status=record.status,
            market=record.market,
            language=record.language,
            messages=messages,
            case_draft_id=record.case_draft_id,
            updated_at=record.updated_at,
        )

    async def draft_view(self, draft_id: UUID, token: str) -> CaseDraftView:
        draft = await self.store.get_draft(draft_id)
        await self.store.get_conversation(UUID(str(draft["conversation_id"])), token)
        return CaseDraftView(
            case_draft_id=draft_id,
            status=draft.get("status", "DRAFT_CREATED"),
            conversation_id=draft["conversation_id"],
            issue_category=draft.get("issue_category", "general_issue"),
            issue_summary=draft.get("issue_summary", ""),
            suggested_resolution=draft.get("suggested_resolution", "repair"),
            market=draft["market"],
            product_model=draft.get("product_model"),
            order_id_masked=draft.get("order_id_masked") or _mask(draft.get("order_id")),
            serial_number_masked=draft.get("serial_number_masked") or _mask(draft.get("serial_number")),
            missing_fields=draft.get("missing_fields", []),
            policy_evidence=[PolicyEvidence.model_validate(item) for item in draft.get("policy_evidence", [])],
            review_required=True,
            created_at=draft.get("created_at") or utc_now(),
        )

    async def audit_views(self, conversation_id: UUID, token: str) -> list[AuditEventView]:
        records = await self.store.get_audits(conversation_id, token)
        return [
            AuditEventView(
                audit_id=item.audit_id,
                trace_id=item.trace_id,
                event_type=item.event_type,
                event_payload=item.event_payload,
                created_at=item.created_at,
            )
            for item in records
        ]

    async def _audit(
        self,
        conversation_id: UUID,
        token: str,
        trace_id: str,
        event_type: str,
        event_payload: dict[str, Any],
    ) -> None:
        await self.store.record_audit(
            conversation_id,
            token,
            trace_id=trace_id,
            event_type=event_type,
            event_payload=event_payload,
        )


def _chunks(text: str, size: int) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    return f"{value[:3]}{'*' * max(len(value) - 5, 1)}{value[-2:]}"
