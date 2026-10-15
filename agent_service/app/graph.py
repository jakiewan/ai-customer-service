import asyncio
import logging
import re
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .constants import (
    FAQ_INTENTS,
    SOP_INTENTS,
    AgentPhase,
    ConversationStatus,
    Intent,
    Market,
    RouteLayer,
    SalesChannel,
)
from .intent import (
    FAQ_TERMS,
    classify_intent,
    extract_context,
    issue_summary,
    link_cancelled,
    product_mentions,
    resolve_product_reference,
)
from .knowledge_base import KnowledgeStore
from .faq_catalog import normalize_question
from .model_factory import AgentModel, KNOWLEDGE_TOOL_NAME, KnowledgeSearch
from .state import AfterSalesState, bounded_messages, recent_dialogue

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


def _current_question(state: AfterSalesState) -> str:
    return state.get("resolved_question") or _last_user_message(state)


def _missing_fields(state: AfterSalesState) -> list[str]:
    if state.get("reference_question"):
        return ["product_reference"]
    if state.get("intent") not in FAQ_INTENTS:
        return []
    return [] if state.get("goodsid") else ["goodsid"]


def _route(
    layer: RouteLayer,
    intent: Intent | str,
    *,
    target: str | None = None,
    group: str | None = None,
    item: str | None = None,
    score: float | None = None,
) -> dict[str, Any]:
    intent = Intent(str(intent))
    if target is None:
        target = (
            "SOP" if intent in SOP_INTENTS else "FAQ" if intent in FAQ_INTENTS else None
        )
    return {
        "route_layer": layer.value,
        "intent": intent.value,
        "route_target": target,
        "faq_group_id": group,
        "faq_item_id": item,
        "route_score": score,
        "classification_source": None,
    }


def build_graph(
    *,
    model: AgentModel,
    policy_store: KnowledgeStore,
    max_steps: int,
    max_tool_calls: int = 3,
    purchase_url: str = "",
    aftersales_url: str = "",
    checkpointer: Any = None,
):
    async def route_turn(state: AfterSalesState, message: str) -> dict[str, Any]:
        """四层漏斗：L0 规则 → L1 Redis 精确匹配 → L2 Milvus 语义 → L3 LLM / UNKNOWN。"""

        cancelled = link_cancelled(message)
        rule_intent = classify_intent(message)
        if rule_intent == Intent.CHITCHAT:
            return _route(RouteLayer.L0_RULE, rule_intent)
        # 交互选择：上一轮分差过小给出候选，用户回复序号即确定 faq_item_id。
        candidates = state.get("faq_candidates") or []
        if message.strip() in {str(index + 1) for index in range(len(candidates))}:
            ref = policy_store.item(candidates[int(message.strip()) - 1])
            if ref is not None:
                return _route(
                    RouteLayer.L2_MILVUS_SEMANTIC,
                    ref.intent_id,
                    group=ref.faq_group_id,
                    item=ref.faq_item_id,
                )
        # L0：SOP 操作类与高风险/非支持产品硬规则，不进入检索。
        if rule_intent in SOP_INTENTS and not cancelled:
            return _route(RouteLayer.L0_RULE, rule_intent)
        if policy_store.is_unsupported(message):
            return _route(RouteLayer.L0_RULE, Intent.FAQ)
        # L1：Redis 标准问/相似问 hash 精确匹配，直接得到 intent/group/item 三元组。
        ref = await asyncio.to_thread(policy_store.exact_match, message)
        if ref is not None:
            return _route(
                RouteLayer.L1_REDIS_EXACT,
                ref.intent_id,
                group=ref.faq_group_id,
                item=ref.faq_item_id,
                score=1.0,
            )
        # L2 第 1-3 步：意图语料检索并按 intent_id 聚合；FAQ 意图映射到 faq_group_id。
        hit = await asyncio.to_thread(policy_store.classify_intent, message)
        meta = policy_store.intent_meta(hit.intent_id) if hit else None
        if meta is not None and not (
            meta.route_target == "SOP"
            and (cancelled or rule_intent in FAQ_INTENTS)
        ):
            return _route(
                RouteLayer.L2_MILVUS_SEMANTIC,
                meta.intent_id,
                target=meta.route_target,
                group=meta.faq_group_id,
                score=hit.score,
            )
        # 关键词只给出 FAQ 方向，FAQ 条目仍由 L2 组内检索 + 适用性审查决定。
        if rule_intent in FAQ_INTENTS:
            return _route(RouteLayer.L0_RULE, rule_intent)
        # L3：模型分类仍无法确定时，交给 UNKNOWN 节点结合上下文回应。
        decision = await model.classify(
            message,
            current_market=state.get("market"),
            current_language=state.get("language"),
        )
        if decision.intent != Intent.UNKNOWN and not (
            cancelled and decision.intent in SOP_INTENTS
        ):
            return {
                **_route(RouteLayer.L3_LLM, decision.intent),
                "classification_source": decision.classification_source,
            }
        return {
            **_route(RouteLayer.L3_UNKNOWN, Intent.UNKNOWN),
            "classification_source": decision.classification_source,
        }

    async def classify_node(state: AfterSalesState) -> dict[str, Any]:
        raw_message = _last_user_message(state)
        history = recent_dialogue(state.get("messages", [])[:-1])
        resolved, candidates = resolve_product_reference(
            raw_message, history, state.get("reference_question")
        )
        reference = await model.resolve_reference(raw_message, history)
        clarification = None
        context_reply = None
        identity_question = re.fullmatch(
            r"(?:我是谁|我叫什么(?:名字)?|我的名字(?:是什么)?)[？?。\s]*", raw_message
        )
        if reference is not None:
            resolved = None if reference.needs_clarification else reference.resolved_question
            candidates = reference.candidates
            clarification = reference.clarification_question or "请明确你指的是哪个对象。"
            if not reference.needs_clarification and identity_question and reference.referent:
                for item in reversed(history):
                    if item.get("role") != "user":
                        continue
                    fact = re.search(
                        r"(?:我叫|我是|我的名字(?:是|叫))\s*" + re.escape(reference.referent),
                        str(item.get("content", "")),
                        flags=re.IGNORECASE,
                    )
                    if fact:
                        context_reply = f"你刚才告诉我：{fact[0]}。"
                        break
        if context_reply is None and identity_question:
            context_reply = "暂时无法从最近5轮对话确认你的称呼，请再告诉我一次。"
            resolved = raw_message
        message = resolved or raw_message
        explicit = extract_context(message)
        remaining = normalize_question(message)
        for term in ("goodsid", "三级品类", "品类", explicit.goodsid, explicit.sales_channel):
            if term:
                remaining = remaining.replace(term.lower(), "")
        resuming = (
            (
                bool(state.get("missing_fields"))
                or (state.get("intent") in FAQ_INTENTS and bool(state.get("issue_summary")))
            )
            and bool(explicit.goodsid or explicit.sales_channel)
            and not link_cancelled(message)
            and not remaining
        )
        if context_reply:
            route = _route(RouteLayer.L0_RULE, Intent.CHITCHAT)
            route["classification_source"] = "context"
        elif resolved is None:
            route = {
                **_route(RouteLayer.L0_RULE, Intent.UNKNOWN),
                "classification_source": "context",
            }
        elif resuming:
            # 补充品类/渠道时沿用上一轮的路由结果，不重新分类。
            route = {
                key: state.get(key)
                for key in (
                    "route_layer",
                    "intent",
                    "route_target",
                    "faq_group_id",
                    "faq_item_id",
                    "route_score",
                )
            }
            route["classification_source"] = "context"
        else:
            route = await route_turn(state, message)
        return _step(
            state,
            **route,
            resolved_question=message,
            reference_question=raw_message if resolved is None else None,
            reference_candidates=candidates,
            reference_clarification=clarification,
            reference_source="llm" if reference is not None else "rule",
            reference_history_turns=sum(item["role"] == "user" for item in history),
            faq_candidates=[],
            market=Market.CN.value,
            language=state.get("language") or str(explicit.language or "zh-CN"),
            goodsid=explicit.goodsid or state.get("goodsid"),
            sales_channel=(
                explicit.sales_channel or state.get("sales_channel") or SalesChannel.MALL.value
            ),
            product_model=(
                explicit.product_model
                or (None if resolved is None or product_mentions(message) else state.get("product_model"))
            ),
            issue_summary=(
                state.get("issue_summary") if resuming else issue_summary(message)
            ),
            reply=context_reply,
            policy_evidence=[],
            citations=[],
            action_links=[],
            human_required=False,
            human_reason=None,
            missing_fields=[],
            phase=AgentPhase.AI_ACTIVE.value,
            conversation_status=ConversationStatus.AI_ACTIVE.value,
        )

    async def collect_info_node(state: AfterSalesState) -> dict[str, Any]:
        missing = _missing_fields(state)
        if "product_reference" in missing:
            candidates = state.get("reference_candidates") or []
            options = "（" + "、".join(candidates) + "）" if candidates else ""
            return _step(
                state,
                missing_fields=missing,
                reply=state.get("reference_clarification") or f"你指的是哪款商品{options}？请直接回复商品型号或品类。",
                result_type="CLARIFICATION",
                phase=AgentPhase.COLLECTING_INFO.value,
            )
        labels = {
            "goodsid": "商品品类（手机 / 电脑 / 空调 / 冰箱 / 洗衣机）",
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

    def _resolve_faq(state: AfterSalesState):
        """L2 第 4-6 步：组内检索 → 聚合/分差 → Redis 取答案 → 适用性审查。返回 (evidence, 候选)。"""

        message = _current_question(state)
        if policy_store.is_unsupported(message):
            return None, ()
        goodsid, channel = state.get("goodsid"), state.get("sales_channel")
        item_id, score = state.get("faq_item_id"), state.get("route_score") or 1.0
        exact_item = False
        if not item_id:
            query = state.get("issue_summary") or message
            # 混合咨询保留知识分句，避免“请给入口”干扰条目定位。
            if state.get("intent") in SOP_INTENTS:
                query = re.sub(
                    r"[，。！？,.!?]\s*(?:请|麻烦|并请|同时请|再)?(?:给|提供)"
                    r"[^，。！？,.!?]*(?:入口|链接)[^，。！？,.!?]*",
                    "", query,
                )
            ref = policy_store.exact_match(query)
            if ref and ref.intent_id in FAQ_INTENTS:
                item_id, score, exact_item = ref.faq_item_id, 1.0, True
            else:
                match = policy_store.match_faq(
                    query, state.get("faq_group_id"), goodsid, channel,
                )
                if match.candidates:
                    return None, match.candidates
                if match.item is None:
                    return None, ()
                item_id, score = match.item.faq_item_id, match.score
        evidence = policy_store.answer(item_id, goodsid, channel, score)
        if evidence and exact_item:
            evidence = evidence.model_copy(update={"score_kind": "exact_match"})
        return evidence, ()

    async def retrieve_policy_node(state: AfterSalesState) -> dict[str, Any]:
        agent_tool = state.get("route_layer") == RouteLayer.L3_AGENT
        if agent_tool and int(state.get("tool_call_count", 0)) >= max_tool_calls:
            raise AgentRunError("Agent 知识工具调用预算已耗尽")
        retrieval_error = False
        try:
            evidence, candidates = await asyncio.to_thread(_resolve_faq, state)
        except Exception as exc:
            logger.warning("知识检索不可用: type=%s", type(exc).__name__)
            evidence, candidates = None, ()
            retrieval_error = True
        tool_patch = {}
        if agent_tool:
            tool_patch = {
                "tool_call_count": int(state.get("tool_call_count", 0)) + 1,
                "tool_events": [{
                    "tool_name": KNOWLEDGE_TOOL_NAME,
                    "status": "error" if retrieval_error else "success",
                    "trace_id": state.get("trace_id"),
                    "error_code": "TOOL_UNAVAILABLE" if retrieval_error else None,
                }],
            }
        if candidates and state.get("intent") not in SOP_INTENTS:
            # 分差过小：交互选择，下一轮用户回复序号即确定 faq_item_id。
            options = "\n".join(
                f"{index + 1}. {policy_store.standard_question(item)}"
                for index, item in enumerate(candidates)
            )
            return _step(
                state,
                **tool_patch,
                faq_candidates=list(candidates),
                reply="你想咨询的是以下哪个问题？请回复序号：\n" + options,
                result_type="CLARIFICATION",
                phase=AgentPhase.COLLECTING_INFO.value,
            )
        if evidence is None:
            return _step(
                state,
                **tool_patch,
                policy_evidence=[],
                human_required=True,
                human_reason=(
                    "知识检索暂不可用" if agent_tool and retrieval_error
                    else "没有匹配三级品类和销售渠道的中国大陆政策"
                ),
                result_type="UNSUPPORTED",
                phase=AgentPhase.AI_CANNOT_RESOLVE.value,
                conversation_status=ConversationStatus.AI_CANNOT_RESOLVE.value,
            )
        payload = evidence.model_dump(mode="json")
        if state.get("route_layer") == RouteLayer.L1_REDIS_EXACT and state.get(
            "faq_item_id"
        ):
            payload["score_kind"] = "exact_match"
        return _step(
            state,
            **tool_patch,
            faq_item_id=evidence.faq_item_id,
            policy_evidence=[payload],
            citations=[payload],
            result_type="ANSWER",
            phase=AgentPhase.RETRIEVING_POLICY.value,
        )

    async def unknown_node(state: AfterSalesState) -> dict[str, Any]:
        """闲聊自然回复；UNKNOWN 由 Agent 决定直接回复或调用知识工具。"""

        message = _last_user_message(state)
        history = recent_dialogue(state.get("messages", [])[:-1])
        chitchat = state.get("intent") == Intent.CHITCHAT
        if state.get("reply"):
            result = state["reply"]
        elif chitchat:
            result = await model.fallback_reply(message, history)
        else:
            result = await model.agent_reply(
                message, history, goodsid=state.get("goodsid"), sales_channel=state.get("sales_channel")
            )
        if isinstance(result, KnowledgeSearch):
            if max_tool_calls < 1:
                raise AgentRunError("Agent 知识工具调用预算已耗尽")
            explicit = extract_context(_current_question(state))
            intent = classify_intent(result.question)
            route = _route(
                RouteLayer.L3_AGENT, intent if intent in FAQ_INTENTS else Intent.FAQ
            )
            route["classification_source"] = "llm"
            return _step(
                state,
                **route,
                goodsid=explicit.goodsid or result.goodsid or state.get("goodsid"),
                sales_channel=(
                    explicit.sales_channel or result.sales_channel
                    or state.get("sales_channel") or SalesChannel.MALL.value
                ),
                resolved_question=result.question,
                issue_summary=result.question,
                reply=None,
                result_type="ANSWER",
            )
        return _step(
            state,
            reply=result,
            result_type="CHITCHAT" if chitchat else "UNKNOWN",
            phase=AgentPhase.COMPLETED.value,
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
            # 答案变体已是单条款，直接按政策原文输出，不做模型改写。
            evidence = state.get("policy_evidence", [])[0]
            reply = (
                f"根据{evidence['title']}（版本 {evidence['version']}），政策原文如下：\n"
                + evidence["excerpt"]
            )
        links = state.get("action_links", [])
        if links:
            purchase = links[0]["service_type"] == "purchase"
            guidance = (
                (
                    "请在官网商城查看具体商品的实时价格、配置和库存。"
                    if any(term in _current_question(state) for term in ("多少钱", "价格", "库存"))
                    else "请前往官网商城选购商品。"
                )
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
        if state.get("reference_question"):
            return "collect_info"
        intent = state.get("intent")
        if intent in {
            Intent.PURCHASE_LINK,
            Intent.AFTERSALES_LINK,
            Intent.REPAIR_STATUS,
        }:
            message = _current_question(state).lower()
            policy_question = any(term.lower() in message for term in FAQ_TERMS) or (
                "维修" in message
                and any(term in message for term in ("费用", "收费", "多少钱", "价格"))
            )
            if all(state.get(key) for key in ("goodsid", "sales_channel")) and policy_question:
                return "retrieve_policy"
            return "resolve_link"
        if intent in FAQ_INTENTS:
            if policy_store.is_unsupported(_current_question(state)):
                return "retrieve_policy"
            return "collect_info" if _missing_fields(state) else "retrieve_policy"
        return "unknown"

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
        ("unknown", unknown_node),
        ("render_response", render_response_node),
    ):
        graph.add_node(name, bounded(node))
    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        after_classify,
        path_map=["collect_info", "retrieve_policy", "resolve_link", "unknown"],
    )
    graph.add_conditional_edges(
        "retrieve_policy",
        lambda state: (
            "resolve_link" if state.get("intent") in SOP_INTENTS else "render_response"
        ),
        path_map=["resolve_link", "render_response"],
    )
    graph.add_conditional_edges(
        "unknown",
        lambda state: after_classify(state) if state.get("intent") in FAQ_INTENTS else "render_response",
        path_map=["collect_info", "retrieve_policy", "render_response"],
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
