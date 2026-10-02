import logging
import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4
from pydantic import ValidationError

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .business import (
    BusinessGateway,
    BusinessGatewayError,
    draft_fingerprint,
    mask_identifier,
)
from .constants import (
    AgentPhase,
    ConversationStatus,
    Intent,
    ToolName,
    ToolStatus,
)
from .intent import case_cancelled, classify_intent, extract_context, issue_summary
from .model_factory import AgentModel
from .permissions import assert_application_write_allowed, assert_model_tool_allowed
from .policy_store import PolicyStore
from .state import AfterSalesState, bounded_messages
from .schemas import BusinessFact, DraftWriteResult
from .store import StoreRequestError

logger = logging.getLogger(__name__)


def _last_user_message(state: AfterSalesState) -> str:
    for message in reversed(state.get("messages", [])):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def _step(state: AfterSalesState, **patch: Any) -> dict[str, Any]:
    patch.setdefault("step_count", int(state.get("step_count", 0)) + 1)
    return patch


def _assistant_message(content: str) -> list[dict[str, Any]]:
    return [{"role": "assistant", "content": content}]


def _missing_fields(state: AfterSalesState) -> list[str]:
    intent = state.get("intent")
    missing: list[str] = []
    if intent in {Intent.REPAIR_STATUS.value, Intent.CASE_REQUEST.value} and not state.get(
        "order_id"
    ) and not state.get("serial_number"):
        missing.append("order_id_or_serial_number")
    if intent in {Intent.FAQ.value, Intent.TROUBLESHOOTING.value, Intent.CASE_REQUEST.value} and not state.get(
        "market"
    ):
        missing.append("market")
    return missing


def _format_missing_question(missing: list[str]) -> str:
    if "order_id_or_serial_number" in missing and "market" in missing:
        return "为了准确处理，请先提供购买国家（CN 或 MY），以及订单号或设备序列号。"
    if "order_id_or_serial_number" in missing:
        return "请提供订单号或设备序列号，我才能查询售后记录。"
    if "market" in missing:
        return "请告诉我手机购买国家（中国 CN 或马来西亚 MY），政策和售后范围按购买国家确定。"
    return "请补充具体的故障现象或售后诉求。"


def _status_text(result: dict[str, Any]) -> str:
    label = result.get("status_label") or result.get("status") or "暂无状态"
    return (
        f"我查询到订单 {mask_identifier(result.get('order_id')) or '未提供'} 的当前维修状态：{label}。"
        "该状态是系统当前记录，不代表承诺完成时间。"
    )


def build_graph(
    *,
    model: AgentModel,
    policy_store: PolicyStore,
    query_gateway: BusinessGateway,
    draft_gateway: BusinessGateway,
    max_steps: int = 12,
    max_tool_calls: int = 3,
    checkpointer: Any | None = None,
    draft_store: Any | None = None,
):
    """构造单一售后 Agent 状态图；写入节点与模型工具权限在应用层固定。"""

    async def classify_node(state: AfterSalesState) -> dict[str, Any]:
        previous_intent = state.get("intent")
        message = _last_user_message(state)
        explicit = extract_context(message)
        new_subject = bool(re.search(r"另一[台部个]|换一[台部]|新的故障|另一个问题|another (phone|device)|new (phone|device)", message, re.IGNORECASE))
        is_case_continuation = (
            state.get("intent") == Intent.CASE_REQUEST.value
            and bool(state.get("missing_fields"))
        )
        decision = await model.classify(
            _last_user_message(state),
            current_market=state.get("market"),
            current_language=state.get("language"),
        )
        # 模型建议不是写入授权；标识必须来自原话，市场来自用户选择/原话或业务核验。
        raw_intent = classify_intent(message)
        supplied_missing = bool(("order_id_or_serial_number" in state.get("missing_fields", []) and (explicit.order_id or explicit.serial_number)) or
                                ("market" in state.get("missing_fields", []) and explicit.market))
        is_case_continuation = is_case_continuation and supplied_missing and raw_intent in {Intent.GENERAL, Intent.TROUBLESHOOTING, Intent.CASE_REQUEST} and not case_cancelled(message)
        decision = decision.model_copy(update={
            "order_id": decision.order_id if decision.order_id and decision.order_id.lower() in message.lower() else explicit.order_id,
            "serial_number": decision.serial_number if decision.serial_number and decision.serial_number.lower() in message.lower() else explicit.serial_number,
            "issue_category": explicit.issue_category,
        })
        if decision.intent == Intent.CASE_REQUEST and raw_intent != Intent.CASE_REQUEST and not is_case_continuation:
            decision = decision.model_copy(update={"intent": raw_intent})
        if case_cancelled(message):
            decision = decision.model_copy(update={"intent": Intent.GENERAL})
        # 用户补订单号/SN 时延续上一轮受理，不把“无法开机”重新当成独立排查咨询。
        if is_case_continuation and decision.intent in {
            Intent.GENERAL, Intent.TROUBLESHOOTING, Intent.CASE_REQUEST
        }:
            decision = decision.model_copy(update={"intent": Intent.CASE_REQUEST})
        # 查询进度的标识不能默认为用户本轮同意报修的设备。
        reuse_identity = previous_intent == decision.intent.value or (
            previous_intent == Intent.TROUBLESHOOTING.value and decision.intent == Intent.CASE_REQUEST
        )
        reuse_identity = reuse_identity and not new_subject
        order_id = decision.order_id or (state.get("order_id") if reuse_identity else None)
        serial_number = decision.serial_number or (state.get("serial_number") if reuse_identity else None)
        if decision.intent == Intent.REPAIR_STATUS and state.get("case_draft_id") and not new_subject:
            known_draft = state.get("case_draft") or {}
            order_id = order_id or known_draft.get("order_id")
            serial_number = serial_number or known_draft.get("serial_number")
        if explicit.order_id and explicit.order_id != state.get("order_id"):
            serial_number = decision.serial_number
        if explicit.serial_number and explicit.serial_number != state.get("serial_number"):
            order_id = decision.order_id
        draft = state.get("case_draft") or {}
        different_device = new_subject or bool(
            (explicit.order_id and explicit.order_id != draft.get("order_id"))
            or (explicit.serial_number and explicit.serial_number != draft.get("serial_number"))
        )
        preserve_symptom = decision.intent == Intent.CASE_REQUEST and reuse_identity and decision.issue_category == "general_issue"
        return _step(
            state,
            intent=decision.intent.value,
            market=explicit.market.value if explicit.market else state.get("market"),
            language=state.get("language") or (decision.language.value if decision.language else None),
            order_id=order_id,
            serial_number=serial_number,
            product_model=decision.product_model or (state.get("product_model") if reuse_identity else None),
            issue_category=state.get("issue_category") if preserve_symptom else decision.issue_category,
            issue_summary=state.get("issue_summary") if preserve_symptom else issue_summary(message),
            case_draft=None if different_device else state.get("case_draft"),
            case_draft_id=None if different_device else state.get("case_draft_id"),
            missing_fields=[],
            phase=AgentPhase.AI_ACTIVE.value,
            conversation_status=ConversationStatus.AI_ACTIVE.value,
        )

    async def collect_info_node(state: AfterSalesState) -> dict[str, Any]:
        missing = _missing_fields(state)
        if not missing:
            missing = ["issue_description"] if state.get("intent") == Intent.GENERAL.value else []
        return _step(
            state,
            missing_fields=missing,
            phase=AgentPhase.COLLECTING_INFO.value,
            reply=_format_missing_question(missing),
        )

    async def retrieve_policy_node(state: AfterSalesState) -> dict[str, Any]:
        market = state.get("market")
        if not market:
            return _step(
                state,
                policy_evidence=[],
                human_required=True,
                human_reason="缺少购买市场，无法选择政策",
                phase=AgentPhase.AI_CANNOT_RESOLVE.value,
                conversation_status=ConversationStatus.AI_CANNOT_RESOLVE.value,
            )
        try:
            evidence = [] if policy_store.is_unsupported(_last_user_message(state)) else await asyncio.to_thread(
                policy_store.retrieve, state.get("issue_summary") or _last_user_message(state), market, top_k=3
            )
        except Exception:
            evidence = []
        if not evidence:
            return _step(
                state,
                policy_evidence=[],
                human_required=True,
                human_reason="没有匹配的政策证据",
                phase=AgentPhase.AI_CANNOT_RESOLVE.value,
                conversation_status=ConversationStatus.AI_CANNOT_RESOLVE.value,
            )
        return _step(
            state,
            policy_evidence=[item.model_dump(mode="json") for item in evidence],
            citations=[item.model_dump(mode="json") for item in evidence],
            phase=AgentPhase.RETRIEVING_POLICY.value,
        )

    async def query_business_node(state: AfterSalesState) -> dict[str, Any]:
        tool_events: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        intent = state.get("intent")
        tool_count = int(state.get("tool_call_count", 0))

        async def call_tool(tool_name: ToolName, payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal tool_count
            if tool_count >= max_tool_calls:
                raise AgentRunError("Agent 业务工具调用数已达到上限")
            tool_count += 1
            assert_model_tool_allowed(tool_name.value)
            tool_events.append(
                {"tool_name": tool_name.value, "status": ToolStatus.RUNNING.value, "trace_id": str(uuid4())}
            )
            try:
                if tool_name == ToolName.QUERY_ORDER:
                    result = await query_gateway.query_order(str(payload["order_id"]))
                elif tool_name == ToolName.QUERY_DEVICE:
                    result = await query_gateway.query_device(str(payload["serial_number"]))
                else:
                    result = await query_gateway.query_repair_status(
                        order_id=payload.get("order_id"), serial_number=payload.get("serial_number")
                    )
                try:
                    fact = BusinessFact.model_validate(result)
                except ValidationError:
                    raise BusinessGatewayError("业务结果缺少合法必需字段", code="TOOL_INVALID_RESPONSE", retryable=False) from None
                if ((payload.get("order_id") and fact.order_id.upper() != str(payload["order_id"]).upper()) or
                    (payload.get("serial_number") and fact.serial_number.upper() != str(payload["serial_number"]).upper()) or
                    (tool_name == ToolName.QUERY_REPAIR_STATUS and not fact.status)):
                    raise BusinessGatewayError("业务结果与查询标识不一致或缺少状态", code="TOOL_INVALID_RESPONSE", retryable=False)
                # 身份源是签名验证后的 JWT，不是用户在消息中自报的客户号。
                if state.get("authorized_customer_id") and (
                    result.get("customer_id") != state["authorized_customer_id"]
                    or result.get("tenant_id") != state.get("authorized_tenant_id")
                ):
                    raise BusinessGatewayError("无权访问该业务记录", code="BUSINESS_FORBIDDEN", retryable=False)
                tool_events[-1]["status"] = ToolStatus.SUCCESS.value
                return result
            except BusinessGatewayError as exc:
                tool_events[-1].update({"status": ToolStatus.ERROR.value, "error_code": exc.code})
                raise

        try:
            if intent == Intent.REPAIR_STATUS.value:
                results.append(
                    await call_tool(
                        ToolName.QUERY_REPAIR_STATUS,
                        {"order_id": state.get("order_id"), "serial_number": state.get("serial_number")},
                    )
                )
            else:
                if state.get("order_id"):
                    results.append(await call_tool(ToolName.QUERY_ORDER, {"order_id": state["order_id"]}))
                if state.get("serial_number"):
                    results.append(
                        await call_tool(ToolName.QUERY_DEVICE, {"serial_number": state["serial_number"]})
                    )
        except BusinessGatewayError as exc:
            return _step(
                state,
                business_results=results,
                tool_events=tool_events,
                tool_call_count=tool_count,
                human_required=True,
                human_reason=str(exc),
                error_code=exc.code,
                phase=AgentPhase.AI_CANNOT_RESOLVE.value,
                conversation_status=ConversationStatus.AI_CANNOT_RESOLVE.value,
            )

        if results and not state.get("market"):
            inferred_market = results[0].get("market")
        else:
            inferred_market = state.get("market")
        if results and (any(item.get("market") != inferred_market for item in results) or
                        len({(item["order_id"], item["serial_number"]) for item in results}) != 1):
            return _step(
                state,
                business_results=results,
                tool_events=tool_events,
                tool_call_count=tool_count,
                human_required=True,
                human_reason="订单所属市场与请求市场不一致",
                error_code="MARKET_MISMATCH",
                phase=AgentPhase.AI_CANNOT_RESOLVE.value,
                conversation_status=ConversationStatus.AI_CANNOT_RESOLVE.value,
            )
        return _step(
            state,
            business_results=results,
            tool_events=tool_events,
            tool_call_count=tool_count,
            market=inferred_market,
            product_model=results[0].get("product_model") if results else None,
            customer_id=results[0].get("customer_id") if results else None,
            customer_context=results[0] if results else None,
            phase=AgentPhase.QUERYING_BUSINESS_DATA.value,
        )

    async def build_case_draft_node(state: AfterSalesState) -> dict[str, Any]:
        facts = state.get("business_results", [])
        first_fact = facts[0] if facts else {}
        draft = {
            "conversation_id": state["conversation_id"],
            "source": "AI_AGENT",
            "market": state.get("market"),
            "language": state.get("language") or "zh-CN",
            "issue_category": state.get("issue_category") or "general_issue",
            "issue_summary": state.get("issue_summary") or _last_user_message(state),
            "suggested_resolution": "repair",
            "order_id": state.get("order_id") or first_fact.get("order_id"),
            "serial_number": state.get("serial_number") or first_fact.get("serial_number"),
            "product_model": state.get("product_model") or first_fact.get("product_model"),
            "customer_context": state.get("customer_context") or first_fact,
            "policy_evidence": state.get("policy_evidence", []),
            "tool_trace_ids": [event["trace_id"] for event in state.get("tool_events", [])],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        draft["draft_fingerprint"] = draft_fingerprint(draft)
        return _step(state, case_draft=draft, draft_fingerprint=draft["draft_fingerprint"], phase=AgentPhase.BUILDING_CASE_DRAFT.value)

    async def validate_draft_node(state: AfterSalesState) -> dict[str, Any]:
        draft = state.get("case_draft") or {}
        required = ("market", "issue_category", "issue_summary", "suggested_resolution")
        valid = all(draft.get(key) for key in required) and bool(draft.get("policy_evidence")) and bool(
            state.get("business_results")
        ) and all(evidence.get("market") == draft.get("market") and evidence.get("version") for evidence in draft.get("policy_evidence", []))
        if not valid:
            return _step(
                state,
                human_required=True,
                human_reason="Case Draft 缺少必需字段、政策证据或业务事实",
                error_code="CASE_DRAFT_VALIDATION_FAILED",
                phase=AgentPhase.AI_CANNOT_RESOLVE.value,
                conversation_status=ConversationStatus.AI_CANNOT_RESOLVE.value,
            )
        return _step(state, phase=AgentPhase.VALIDATING_DRAFT.value)

    async def create_case_draft_node(state: AfterSalesState) -> dict[str, Any]:
        assert_application_write_allowed(ToolName.CREATE_CASE_DRAFT.value)
        if int(state.get("tool_call_count", 0)) >= max_tool_calls:
            raise AgentRunError("Agent 业务工具调用数已达到上限")
        tool_count = int(state.get("tool_call_count", 0)) + 1
        try:
            result = await draft_store.reserve_draft_write(state["case_draft"]) if draft_store else None
            if result is None:
                result = await draft_gateway.create_case_draft(state["case_draft"])
            try:
                receipt = DraftWriteResult.model_validate(result)
            except ValidationError:
                raise BusinessGatewayError("草稿创建结果格式无效，需要核对下游结果", code="TOOL_INVALID_RESPONSE", retryable=False) from None
            if ((receipt.market and receipt.market.value != state["market"]) or
                (receipt.draft_fingerprint and receipt.draft_fingerprint != state["case_draft"]["draft_fingerprint"]) or
                (receipt.conversation_id and str(receipt.conversation_id) != state["conversation_id"])):
                raise BusinessGatewayError("草稿回执与请求不一致，需要核对下游结果", code="TOOL_INVALID_RESPONSE", retryable=False)
            if draft_store:
                await draft_store.confirm_draft_write(state["case_draft"]["draft_fingerprint"],
                                                      {**result, "created_at": receipt.created_at.isoformat() if receipt.created_at else state["case_draft"]["created_at"]})
        except (BusinessGatewayError, StoreRequestError) as exc:
            return _step(
                state,
                human_required=True,
                human_reason=str(exc),
                error_code=exc.code,
                tool_call_count=tool_count,
                phase=AgentPhase.AI_CANNOT_RESOLVE.value,
                conversation_status=ConversationStatus.AI_CANNOT_RESOLVE.value,
            )
        draft = {**state["case_draft"], "case_draft_id": str(receipt.case_draft_id),
                 "created_at": receipt.created_at.isoformat() if receipt.created_at else state["case_draft"]["created_at"],
                 "status": "DRAFT_CREATED", "idempotent_replay": receipt.idempotent_replay}
        return _step(
            state,
            case_draft=draft,
            case_draft_id=str(result["case_draft_id"]),
            phase=AgentPhase.CASE_DRAFT_CREATED.value,
            conversation_status=ConversationStatus.CASE_DRAFT_CREATED.value,
            review_required=True,
            tool_call_count=tool_count,
        )

    async def reuse_draft_node(state: AfterSalesState) -> dict[str, Any]:
        return _step(
            state,
            phase=AgentPhase.CASE_DRAFT_CREATED.value,
            conversation_status=ConversationStatus.CASE_DRAFT_CREATED.value,
            reply="当前会话已经存在一份 Case Draft，我会复用它，不重复创建新的草稿。",
        )

    async def render_response_node(state: AfterSalesState) -> dict[str, Any]:
        if state.get("reply"):
            reply = state["reply"]
        elif state.get("human_required"):
            reply = (
                "这次请求暂时无法基于可靠的政策或业务事实给出确定结论："
                f"{state.get('human_reason') or '超出当前 Agent 范围'}。"
                "你可以联系传统人工客服继续处理。"
            )
        elif state.get("intent") in {Intent.TROUBLESHOOTING.value, Intent.FAQ.value}:
            evidence = state.get("policy_evidence", [])
            content = evidence[0]["excerpt"]
            lines = [line.lstrip("- ") for line in content.splitlines() if line.startswith("- ")]
            terms = (("无法开机排查", "Cannot power on:") if state.get("intent") == Intent.TROUBLESHOOTING.value else
                     ("申请维修", "Repair request:") if "材料" in _last_user_message(state) else
                     ("有限保修期", "Limited warranty:"))
            selected = [line for line in lines if any(term in line for term in terms)]
            if state.get("language") == "zh-CN" and any(any("\u4e00" <= char <= "\u9fff" for char in line) for line in selected):
                selected = [line for line in selected if any("\u4e00" <= char <= "\u9fff" for char in line)]
            reply = f"根据{evidence[0]['title']}（版本 {evidence[0]['version']}），政策原文如下：\n" + "\n".join(selected or lines or [content])
        elif state.get("intent") == Intent.REPAIR_STATUS.value:
            reply = _status_text((state.get("business_results") or [{}])[0])
            if state.get("case_draft_id"):
                reply += "这是既有维修记录；保存草稿不等于已创建正式案例或维修工单。"
        elif state.get("intent") == Intent.CASE_REQUEST.value and state.get("case_draft_id"):
            reply = (
                f"已根据订单和政策信息保存 Case Draft（{state['case_draft_id']}）。"
                "Agent 侧任务已完成；正式 Case 和维修工单由既有售后系统继续处理。"
            )
        else:
            reply = "我可以帮你查询保修政策、排查故障、查询维修进度或整理维修受理草稿。请描述你的售后问题。"
        return _step(
            state,
            reply=reply,
            messages=_assistant_message(reply),
            phase=state.get("phase") or AgentPhase.COMPLETED.value,
        )

    def after_classify(state: AfterSalesState) -> Literal["collect_info", "retrieve_policy", "query_business", "reuse_draft", "render_response"]:
        intent = state.get("intent")
        if policy_store.is_unsupported(_last_user_message(state)):
            return "retrieve_policy"
        if intent == Intent.GENERAL.value:
            return "render_response"
        if intent == Intent.CASE_REQUEST.value and state.get("case_draft_id"):
            return "reuse_draft"
        missing = _missing_fields(state)
        if missing:
            return "collect_info"
        if intent == Intent.REPAIR_STATUS.value:
            return "query_business"
        if intent in {Intent.FAQ.value, Intent.TROUBLESHOOTING.value, Intent.CASE_REQUEST.value}:
            return "retrieve_policy"
        return "render_response"

    def after_collect(state: AfterSalesState) -> Literal["retrieve_policy", "query_business", "render_response"]:
        if state.get("missing_fields"):
            return "render_response"
        if state.get("intent") == Intent.REPAIR_STATUS.value:
            return "query_business"
        if state.get("intent") in {Intent.FAQ.value, Intent.TROUBLESHOOTING.value, Intent.CASE_REQUEST.value}:
            return "retrieve_policy"
        return "render_response"

    def after_policy(state: AfterSalesState) -> Literal["query_business", "render_response"]:
        if state.get("human_required") or not state.get("policy_evidence"):
            return "render_response"
        if state.get("intent") == Intent.CASE_REQUEST.value:
            return "query_business"
        return "render_response"

    def after_query(state: AfterSalesState) -> Literal["build_case_draft", "render_response"]:
        if state.get("human_required"):
            return "render_response"
        if state.get("intent") == Intent.CASE_REQUEST.value:
            return "build_case_draft"
        return "render_response"

    def after_validate(state: AfterSalesState) -> Literal["create_case_draft", "render_response"]:
        return "render_response" if state.get("human_required") else "create_case_draft"

    def after_create(state: AfterSalesState) -> Literal["render_response"]:
        return "render_response"

    graph = StateGraph(AfterSalesState)
    def bounded(node):
        async def run(state):
            # 门禁必须在节点执行前生效，不能写下游后才发现已超限。
            if int(state.get("step_count", 0)) >= max_steps:
                raise AgentRunError("Agent 最大步骤数已达到上限")
            return await node(state)
        return run

    for name, node in (
        ("classify_intent", classify_node), ("collect_info", collect_info_node),
        ("retrieve_policy", retrieve_policy_node), ("query_business", query_business_node),
        ("build_case_draft", build_case_draft_node), ("validate_draft", validate_draft_node),
        ("create_case_draft", create_case_draft_node), ("reuse_draft", reuse_draft_node),
        ("render_response", render_response_node),
    ):
        graph.add_node(name, bounded(node))
    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges("classify_intent", after_classify)
    graph.add_conditional_edges("collect_info", after_collect)
    graph.add_conditional_edges("retrieve_policy", after_policy)
    graph.add_conditional_edges("query_business", after_query)
    graph.add_edge("build_case_draft", "validate_draft")
    graph.add_conditional_edges("validate_draft", after_validate)
    graph.add_conditional_edges("create_case_draft", after_create)
    graph.add_edge("reuse_draft", "render_response")
    graph.add_edge("render_response", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


class AgentRunError(RuntimeError):
    pass


class AgentBudgetError(AgentRunError):
    pass


class GraphService:
    def __init__(self, graph: Any, *, max_steps: int, token_budget: int) -> None:
        self.graph = graph
        self.max_steps = max_steps
        self.token_budget = token_budget

    async def stream(self, initial_state: AfterSalesState, *, thread_id: str):
        if int(initial_state.get("step_count", 0)) >= self.max_steps:
            raise AgentRunError("Agent 最大步骤数已达到上限")
        estimated_tokens = sum(
            max(1, len(str(message.get("content", ""))) // 4)
            for message in initial_state.get("messages", [])
        )
        if estimated_tokens > self.token_budget:
            raise AgentBudgetError("本次消息超过 Agent 单轮预算")
        config = {"configurable": {"thread_id": thread_id}}
        state = dict(initial_state)
        emitted_nodes = 0
        async for update in self.graph.astream(initial_state, config=config, stream_mode="updates"):
            if not isinstance(update, dict):
                continue
            for node_name, patch in update.items():
                if not isinstance(patch, dict):
                    continue
                emitted_nodes += 1
                if emitted_nodes > self.max_steps:
                    raise AgentRunError("Agent 最大步骤数已达到上限")
                for key, value in patch.items():
                    if key == "messages":
                        state[key] = bounded_messages(state.get(key, []), value)
                    else:
                        state[key] = value
                yield node_name, patch, state
        if not state.get("reply"):
            raise AgentRunError("Agent 没有生成最终回复")
        yield "__done__", {}, state
