import logging
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .business import BusinessGateway, BusinessGatewayError, draft_fingerprint, mask_identifier
from .constants import (
    AgentPhase,
    ConversationStatus,
    Intent,
    ToolName,
    ToolStatus,
)
from .intent import issue_summary
from .model_factory import AgentModel
from .permissions import assert_application_write_allowed, assert_model_tool_allowed
from .policy_store import PolicyStore
from .state import AfterSalesState

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
    if intent in {Intent.REPAIR_STATUS.value, Intent.CASE_REQUEST.value}:
        if not state.get("order_id") and not state.get("serial_number"):
            missing.append("order_id_or_serial_number")
    if intent in {Intent.FAQ.value, Intent.TROUBLESHOOTING.value, Intent.CASE_REQUEST.value}:
        if not state.get("market"):
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
    checkpointer: Any | None = None,
):
    """构造单一售后 Agent 状态图；写入节点与模型工具权限在应用层固定。"""

    async def classify_node(state: AfterSalesState) -> dict[str, Any]:
        is_case_continuation = (
            state.get("intent") == Intent.CASE_REQUEST.value
            and "order_id_or_serial_number" in state.get("missing_fields", [])
        )
        decision = await model.classify(
            _last_user_message(state),
            current_market=state.get("market"),
            current_language=state.get("language"),
        )
        # 用户补订单号/SN 时延续上一轮受理，不把“无法开机”重新当成独立排查咨询。
        if is_case_continuation and (decision.order_id or decision.serial_number):
            decision = decision.model_copy(update={"intent": Intent.CASE_REQUEST})
        return _step(
            state,
            intent=decision.intent.value,
            market=decision.market.value if decision.market else state.get("market"),
            language=decision.language.value if decision.language else state.get("language"),
            order_id=decision.order_id or state.get("order_id"),
            serial_number=decision.serial_number or state.get("serial_number"),
            product_model=decision.product_model or state.get("product_model"),
            issue_category=decision.issue_category,
            issue_summary=issue_summary(_last_user_message(state)),
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
        evidence = policy_store.retrieve(
            state.get("issue_summary") or _last_user_message(state), market, top_k=3
        )
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

        async def call_tool(tool_name: ToolName, payload: dict[str, Any]) -> dict[str, Any]:
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
        if state.get("market") and results and results[0].get("market") != state.get("market"):
            return _step(
                state,
                business_results=results,
                tool_events=tool_events,
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
            market=inferred_market,
            product_model=state.get("product_model") or (results[0].get("product_model") if results else None),
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
        )
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
        try:
            result = await draft_gateway.create_case_draft(state["case_draft"])
        except BusinessGatewayError as exc:
            return _step(
                state,
                human_required=True,
                human_reason=str(exc),
                error_code=exc.code,
                phase=AgentPhase.AI_CANNOT_RESOLVE.value,
                conversation_status=ConversationStatus.AI_CANNOT_RESOLVE.value,
            )
        draft = {**state["case_draft"], **result}
        return _step(
            state,
            case_draft=draft,
            case_draft_id=str(result["case_draft_id"]),
            phase=AgentPhase.CASE_DRAFT_CREATED.value,
            conversation_status=ConversationStatus.CASE_DRAFT_CREATED.value,
            review_required=True,
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
        elif state.get("intent") == Intent.TROUBLESHOOTING.value:
            reply = (
                "可以先按以下顺序排查：\n"
                "1. 使用合规充电器充电 30 分钟并检查线缆、充电口；\n"
                "2. 长按电源键 10 秒尝试强制重启；\n"
                "3. 如果设备发热、冒烟或进液，请立即停止操作并联系人工客服。"
            )
        elif state.get("intent") == Intent.FAQ.value:
            evidence = state.get("policy_evidence", [])
            reply = f"根据{evidence[0]['title']}（版本 {evidence[0]['version']}），当前市场适用的手机有限保修期为 12 个月，以订单和购买凭证核验结果为准。"
        elif state.get("intent") == Intent.REPAIR_STATUS.value:
            reply = _status_text((state.get("business_results") or [{}])[0])
        elif state.get("case_draft_id"):
            reply = (
                f"已根据订单和政策信息保存 Case Draft（{state['case_draft_id']}）。"
                "Agent 侧任务已完成；正式 Case 和维修工单由既有售后系统继续处理。"
            )
        else:
            reply = "我可以帮你查询保修政策、排查故障、查询维修进度或整理维修受理草稿。请描述你的问题。"
        return _step(
            state,
            reply=reply,
            messages=_assistant_message(reply),
            phase=state.get("phase") or AgentPhase.COMPLETED.value,
        )

    def after_classify(state: AfterSalesState) -> Literal["collect_info", "retrieve_policy", "query_business", "reuse_draft", "render_response"]:
        intent = state.get("intent")
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
    graph.add_node("classify_intent", classify_node)
    graph.add_node("collect_info", collect_info_node)
    graph.add_node("retrieve_policy", retrieve_policy_node)
    graph.add_node("query_business", query_business_node)
    graph.add_node("build_case_draft", build_case_draft_node)
    graph.add_node("validate_draft", validate_draft_node)
    graph.add_node("create_case_draft", create_case_draft_node)
    graph.add_node("reuse_draft", reuse_draft_node)
    graph.add_node("render_response", render_response_node)
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
                        state[key] = [*state.get(key, []), *value]
                    else:
                        state[key] = value
                yield node_name, patch, state
        if not state.get("reply"):
            raise AgentRunError("Agent 没有生成最终回复")
        yield "__done__", {}, state
