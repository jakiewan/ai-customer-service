import asyncio
import logging
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .constants import AgentPhase, ConversationStatus, Intent, Market
from .intent import (
    FAQ_TERMS,
    classify_intent,
    extract_context,
    issue_summary,
    link_cancelled,
)
from .model_factory import AgentModel
from .policy_store import PolicyStore
from .state import AfterSalesState, bounded_messages

logger = logging.getLogger(__name__)


def _last_user_message(state: AfterSalesState) -> str:
    return next(
        (
            str(message.get("content", ""))
            for message in reversed(state.get("messages", []))
            if message.get("role") == "user"
        ),
        "",
    )


def _step(state: AfterSalesState, **patch: Any) -> dict[str, Any]:
    return {"step_count": int(state.get("step_count", 0)) + 1, **patch}


def _missing_fields(state: AfterSalesState) -> list[str]:
    if state.get("intent") not in {Intent.FAQ, Intent.TROUBLESHOOTING}:
        return []
    return [key for key in ("goodsid", "sales_channel") if not state.get(key)]


def build_graph(
    *,
    model: AgentModel,
    policy_store: PolicyStore,
    max_steps: int,
    purchase_url: str = "",
    aftersales_url: str = "",
    checkpointer: Any = None,
):
    async def classify_node(state: AfterSalesState) -> dict[str, Any]:
        message = _last_user_message(state)
        explicit = extract_context(message)
        decision = await model.classify(
            message,
            current_market=state.get("market"),
            current_language=state.get("language"),
        )
        rule_intent = classify_intent(message)
        intent = (
            rule_intent
            if rule_intent != Intent.GENERAL or link_cancelled(message)
            else decision.intent
        )
        resuming = (
            bool(state.get("missing_fields"))
            and bool(explicit.goodsid or explicit.sales_channel)
            and not link_cancelled(message)
            and not any(
                term in message.lower()
                for term in (
                    "购买",
                    "我要买",
                    "我想买",
                    "申请",
                    "入口",
                    "链接",
                    "保修",
                    "故障",
                    "维修",
                    "warranty",
                    "repair",
                )
            )
        )
        if resuming:
            intent = state.get("intent", Intent.GENERAL)
        return _step(
            state,
            intent=str(intent),
            market=Market.CN.value,
            language=state.get("language") or str(decision.language or "zh-CN"),
            goodsid=explicit.goodsid or state.get("goodsid"),
            sales_channel=explicit.sales_channel or state.get("sales_channel"),
            product_model=explicit.product_model or state.get("product_model"),
            issue_summary=state.get("issue_summary")
            if resuming
            else issue_summary(message),
            missing_fields=[],
            phase=AgentPhase.AI_ACTIVE.value,
            conversation_status=ConversationStatus.AI_ACTIVE.value,
        )

    async def collect_info_node(state: AfterSalesState) -> dict[str, Any]:
        missing = _missing_fields(state)
        labels = {
            "goodsid": "三级品类 goodsid（如手机 65539）",
            "sales_channel": "销售渠道（商城 / 自营店 / 授权店）",
        }
        return _step(
            state,
            missing_fields=missing,
            reply="请补充"
            + "、".join(labels[key] for key in missing)
            + "，以便匹配政策。",
            result_type="CLARIFICATION",
            phase=AgentPhase.COLLECTING_INFO.value,
        )

    async def retrieve_policy_node(state: AfterSalesState) -> dict[str, Any]:
        try:
            evidence = await asyncio.to_thread(
                policy_store.retrieve,
                state.get("issue_summary") or _last_user_message(state),
                Market.CN,
                top_k=3,
                goodsid=state.get("goodsid"),
                sales_channel=state.get("sales_channel"),
            )
        except Exception as exc:
            logger.warning("政策检索不可用: type=%s", type(exc).__name__)
            evidence = []
        if not evidence:
            return _step(
                state,
                policy_evidence=[],
                human_required=True,
                human_reason="没有匹配三级品类和销售渠道的中国大陆政策",
                result_type="UNSUPPORTED",
                phase=AgentPhase.AI_CANNOT_RESOLVE.value,
                conversation_status=ConversationStatus.AI_CANNOT_RESOLVE.value,
            )
        return _step(
            state,
            policy_evidence=[item.model_dump(mode="json") for item in evidence],
            citations=[item.model_dump(mode="json") for item in evidence],
            result_type="ANSWER",
            phase=AgentPhase.RETRIEVING_POLICY.value,
        )

    async def resolve_link_node(state: AfterSalesState) -> dict[str, Any]:
        purchase = state.get("intent") == Intent.PURCHASE_LINK
        url = purchase_url if purchase else aftersales_url
        label = "官网商城" if purchase else "售后申请入口"
        if not url:
            return _step(
                state,
                reply=f"{label}暂未配置，请联系官方客服获取入口。",
                action_links=[],
                result_type="UNSUPPORTED",
                phase=AgentPhase.RESOLVING_LINK.value,
            )
        return _step(
            state,
            action_links=[
                {
                    "service_type": "purchase" if purchase else "aftersales",
                    "label": label,
                    "url": url,
                }
            ],
            result_type="LINK_GUIDANCE",
            phase=AgentPhase.RESOLVING_LINK.value,
        )

    async def render_response_node(state: AfterSalesState) -> dict[str, Any]:
        reply = state.get("reply")
        if not reply and state.get("human_required"):
            reply = f"暂时无法给出可靠的政策结论：{state.get('human_reason')}。请联系官方客服继续处理。"
        if not reply and state.get("policy_evidence"):
            evidence = state["policy_evidence"][0]
            lines = [
                line.lstrip("- ")
                for line in evidence["excerpt"].splitlines()
                if line.startswith("- ")
            ]
            terms = (
                ("无法开机排查", "Cannot power on:")
                if state.get("intent") == Intent.TROUBLESHOOTING
                else ("申请维修", "Repair request:")
                if "材料" in _last_user_message(state)
                else ("有限保修期", "Limited warranty:")
            )
            selected = [line for line in lines if any(term in line for term in terms)]
            if state.get("language") == "zh-CN" and any(
                any("\u4e00" <= char <= "\u9fff" for char in line) for line in selected
            ):
                selected = [
                    line
                    for line in selected
                    if any("\u4e00" <= char <= "\u9fff" for char in line)
                ]
            reply = (
                f"根据{evidence['title']}（版本 {evidence['version']}），政策原文如下：\n"
                + "\n".join(selected or lines or [evidence["excerpt"]])
            )
        links = state.get("action_links", [])
        if links:
            purchase = links[0]["service_type"] == "purchase"
            guidance = (
                "请前往官网商城选购商品。"
                if purchase
                else "请通过官方售后入口申请或查询，填写及提交由你在官方页面完成。"
            )
            reply = (reply + "\n" if reply else "") + guidance
        reply = (
            reply
            or "我可以帮你查询保修政策、排查故障，或提供官网商城和售后申请入口。请描述你的问题。"
        )
        status = (
            state.get("conversation_status")
            if state.get("human_required")
            else ConversationStatus.COMPLETED.value
        )
        return _step(
            state,
            reply=reply,
            messages=[{"role": "assistant", "content": reply}],
            conversation_status=status,
            phase=state.get("phase") or AgentPhase.COMPLETED.value,
        )

    def after_classify(state: AfterSalesState) -> str:
        intent = state.get("intent")
        if intent in {
            Intent.PURCHASE_LINK,
            Intent.AFTERSALES_LINK,
            Intent.REPAIR_STATUS,
        }:
            message = _last_user_message(state).lower()
            if all(
                state.get(key) for key in ("goodsid", "sales_channel")
            ) and any(term.lower() in message for term in FAQ_TERMS):
                return "retrieve_policy"
            return "resolve_link"
        if intent in {Intent.FAQ, Intent.TROUBLESHOOTING}:
            if policy_store.is_unsupported(_last_user_message(state)):
                return "retrieve_policy"
            return "collect_info" if _missing_fields(state) else "retrieve_policy"
        return "render_response"

    graph = StateGraph(AfterSalesState)

    def bounded(node):
        async def run(state):
            if int(state.get("step_count", 0)) >= max_steps:
                raise AgentRunError("Agent 最大步骤数已达到上限")
            return await node(state)

        return run

    for name, node in (
        ("classify_intent", classify_node),
        ("collect_info", collect_info_node),
        ("retrieve_policy", retrieve_policy_node),
        ("resolve_link", resolve_link_node),
        ("render_response", render_response_node),
    ):
        graph.add_node(name, bounded(node))
    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges("classify_intent", after_classify)
    graph.add_conditional_edges(
        "retrieve_policy",
        lambda state: (
            "resolve_link"
            if state.get("intent")
            in {Intent.PURCHASE_LINK, Intent.AFTERSALES_LINK, Intent.REPAIR_STATUS}
            else "render_response"
        ),
    )
    for name in ("collect_info", "resolve_link"):
        graph.add_edge(name, "render_response")
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
        async for update in self.graph.astream(
            initial_state, config=config, stream_mode="updates"
        ):
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
