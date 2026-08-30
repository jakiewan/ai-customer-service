import logging

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from .constants import Intent, Language, Market
from .intent import classify_intent, extract_context

logger = logging.getLogger(__name__)


class IntentDecision(BaseModel):
    intent: Intent
    market: Market | None = None
    language: Language | None = None
    order_id: str | None = None
    serial_number: str | None = None
    product_model: str | None = None
    issue_category: str = Field(default="general_issue")


class AgentModel:
    """LangChain 模型工厂：默认 mock 保证离线可测，MiniMax-M3 可通过配置开启。"""

    def __init__(self, *, mode: str, api_key: str, base_url: str, model_name: str, temperature: float) -> None:
        self.mode = mode.lower()
        self._intent_chain = None
        if self.mode == "minimax":
            from langchain_openai import ChatOpenAI

            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "你是售后客服路由器。只输出结构化字段，不回答用户问题。\n"
                        "意图只能是 FAQ、TROUBLESHOOTING、REPAIR_STATUS、CASE_REQUEST、GENERAL。\n"
                        "哪个国家购买就使用哪个国家售后，不推断未知市场。",
                    ),
                    ("human", "用户消息：{message}"),
                ]
            )
            model = ChatOpenAI(
                api_key=api_key,
                base_url=base_url,
                model=model_name,
                temperature=temperature,
                timeout=20,
                max_retries=0,
            )
            self._intent_chain = prompt | model.with_structured_output(IntentDecision)

    async def classify(self, message: str, *, current_market: str | None, current_language: str | None) -> IntentDecision:
        if self._intent_chain is not None:
            try:
                result = await self._intent_chain.ainvoke({"message": message})
                if isinstance(result, IntentDecision):
                    return result
                return IntentDecision.model_validate(result)
            except Exception as exc:
                logger.error("MiniMax 结构化意图调用失败，回退离线路由: %s", exc)

        context = extract_context(message)
        return IntentDecision(
            intent=classify_intent(message),
            market=context.market or current_market,
            language=context.language or current_language,
            order_id=context.order_id,
            serial_number=context.serial_number,
            product_model=context.product_model,
            issue_category=context.issue_category,
        )

