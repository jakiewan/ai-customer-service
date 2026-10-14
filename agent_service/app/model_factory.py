import json
import logging
import re

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from .constants import Intent, Language, Market
from .intent import classify_intent, extract_context

logger = logging.getLogger(__name__)


class IntentDecision(BaseModel):
    intent: Intent
    market: Market | None = Market.CN
    language: Language | None = None
    order_id: str | None = None
    serial_number: str | None = None
    product_model: str | None = None
    issue_category: str = Field(default="general_issue")


class AgentModel:
    """LangChain 模型工厂：默认 mock 保证离线可测，MiniMax-M3 可通过配置开启。"""

    def __init__(
        self,
        *,
        mode: str,
        api_key: str,
        base_url: str,
        model_name: str,
        temperature: float
    ) -> None:
        self.mode = mode.lower()
        self._intent_chain = None
        self._raw_intent_chain = None
        if self.mode == "minimax":
            from langchain_openai import ChatOpenAI

            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        (
                            "你是售后客服路由器。只输出结构化字段，不回答用户问题。\n"
                            "意图只能是 FAQ、TROUBLESHOOTING、REPAIR_STATUS、AFTERSALES_LINK、PURCHASE_LINK、UNKNOWN；"
                            "无法确定时必须输出 UNKNOWN。\n"
                            "当前客服仅服务中国大陆，market 固定为 CN，不识别或询问购买国家。"
                        ),
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
                max_tokens=512,
            )
            self._raw_intent_chain = prompt | model
            self._intent_chain = prompt | model.with_structured_output(IntentDecision)

    async def classify(
        self, message: str, *, current_market: str | None, current_language: str | None
    ) -> IntentDecision:
        if self._intent_chain is not None:
            try:
                result = await self._intent_chain.ainvoke({"message": message})
                if isinstance(result, IntentDecision):
                    return result
                return IntentDecision.model_validate(result)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - Provider 失败必须回退到确定性安全路由。
                logger.error("结构化意图调用失败: type=%s", type(exc).__name__)
                parsed = await self._parse_raw_intent(message)
                if parsed is not None:
                    return parsed

        context = extract_context(message)
        return IntentDecision(
            intent=classify_intent(message),
            market=Market.CN,
            language=context.language or current_language,
            order_id=context.order_id,
            serial_number=context.serial_number,
            product_model=context.product_model,
            issue_category=context.issue_category,
        )

    async def _parse_raw_intent(self, message: str) -> IntentDecision | None:
        """兼容部分模型把 JSON 包在 think/fence 内的 OpenAI 兼容响应。"""

        if self._raw_intent_chain is None:
            return None
        try:
            response = await self._raw_intent_chain.ainvoke({"message": message})
            content = _message_text(response.content)
            candidate = _extract_json_object(content)
            if not candidate:
                return None
            payload = _normalize_intent_payload(json.loads(candidate))
            return IntentDecision.model_validate(payload)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - 无法通过 schema 校验时必须走确定性 fallback。
            logger.error("模型结构化内容不可用: type=%s", type(exc).__name__)
            return None


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)


def _extract_json_object(content: str) -> str | None:
    without_thinking = re.sub(
        r"<think>.*?</think>", "", content, flags=re.DOTALL
    ).strip()
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        without_thinking,
        flags=re.DOTALL | re.IGNORECASE,
    )
    candidate = fenced.group(1) if fenced else without_thinking
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end < start:
        return None
    raw_json = candidate[start : end + 1]
    # 先用标准库验证，再交由 Pydantic 做字段白名单与枚举校验。
    json.loads(raw_json)
    return raw_json


def _normalize_intent_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise TypeError("模型意图输出不是 JSON object")
    normalized = dict(payload)
    language_aliases = {
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "en": "en",
        "en-us": "en",
        "en-gb": "en",
    }
    language = normalized.get("language")
    if isinstance(language, str):
        normalized["language"] = language_aliases.get(language.lower(), language)
    market = normalized.get("market")
    if isinstance(market, str):
        normalized["market"] = market.upper()
    return normalized
