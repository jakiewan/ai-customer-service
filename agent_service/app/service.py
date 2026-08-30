import asyncio
import logging
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
from .store import InMemoryStore

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
    def __init__(self, *, store: InMemoryStore, graph_service: GraphService) -> None:
        self.store = store
        self.graph_service = graph_service

    async def create_conversation(self, token: str, request: CreateConversationRequest) -> ConversationResponse:
        record = await self.store.create_conversation(
            token=token,
            channel=request.channel,
            market=request.market.value if request.market else None,
            language=request.language.value if request.language else None,
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
        record = await self.store.get_conversation(conversation_id, token)
        duplicate = await self.store.append_user_message(
            conversation_id, token, request.client_message_id, request.message
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
                for evidence in patch.get("policy_evidence", []):
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
                    yield {
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
        except AgentBudgetError as exc:
            logger.error("Agent 预算门禁触发: trace_id=%s error=%s", trace_id, exc)
            yield {
                "event": "error",
                "data": {
                    "code": ErrorCode.AGENT_BUDGET_EXCEEDED.value,
                    "message": str(exc),
                    "retryable": True,
                },
            }
            yield {"event": "done", "data": {"status": ConversationStatus.FAILED.value, "trace_id": trace_id}}
            return
        except AgentRunError as exc:
            logger.error("Agent 运行上限触发: trace_id=%s error=%s", trace_id, exc)
            yield {
                "event": "error",
                "data": {
                    "code": ErrorCode.AGENT_LIMIT_EXCEEDED.value,
                    "message": str(exc),
                    "retryable": True,
                },
            }
            yield {"event": "done", "data": {"status": ConversationStatus.FAILED.value, "trace_id": trace_id}}
            return
        except Exception as exc:  # noqa: BLE001 - SSE 入口必须屏蔽内部异常并返回统一错误。
            logger.error("Agent 运行失败: trace_id=%s error=%s", trace_id, exc)
            yield {
                "event": "error",
                "data": {
                    "code": ErrorCode.INTERNAL_ERROR.value,
                    "message": "Agent 暂时无法处理，请稍后重试",
                    "retryable": True,
                },
            }
            yield {"event": "done", "data": {"status": ConversationStatus.FAILED.value, "trace_id": trace_id}}
            return

        if final_state is None:
            yield {"event": "error", "data": {"code": ErrorCode.INTERNAL_ERROR.value, "message": "Agent 没有最终状态", "retryable": True}}
            return
        reply = str(final_state.get("reply") or "")
        for chunk in _chunks(reply, 28):
            await asyncio.sleep(0)
            yield {"event": "token", "data": {"delta": chunk}}
        if final_state.get("human_required"):
            yield {
                "event": "human_required",
                "data": {"reason": final_state.get("human_reason") or "当前 Agent 无法可靠处理"},
            }
        result = {
            "reply": reply,
            "citations": final_state.get("citations", []),
            "case_draft_id": final_state.get("case_draft_id"),
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
            citations=final_state.get("citations", []),
            result=result,
        )
        await self._audit(
            conversation_id,
            token,
            trace_id,
            "AGENT_RUN_COMPLETED",
            {"status": result["status"], "human_required": result["human_required"]},
        )
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
        reply = str(result.get("reply", ""))
        for chunk in _chunks(reply, 28):
            yield {"event": "token", "data": {"delta": chunk}}
        for evidence in result.get("citations", []):
            yield {"event": "policy_evidence", "data": evidence}
        if result.get("case_draft_id"):
            yield {
                "event": "case_draft_created",
                "data": {
                    "case_draft_id": result["case_draft_id"],
                    "status": ConversationStatus.CASE_DRAFT_CREATED.value,
                    "review_required": True,
                    "summary": "已复用现有 Case Draft",
                    "market": result.get("market"),
                    "idempotent_replay": True,
                },
            }
        if result.get("human_required"):
            yield {"event": "human_required", "data": {"reason": "上次请求未能可靠处理"}}
        yield {"event": "done", "data": {"status": result.get("status", "AI_ACTIVE"), "trace_id": trace_id}}

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
