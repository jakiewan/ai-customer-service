import re
from dataclasses import dataclass

from .constants import Intent, Language, Market

STATUS_TERMS = (
    "维修进度",
    "维修状态",
    "修到哪",
    "订单状态",
    "工单",
    "修好了吗",
    "repair status",
    "repair progress",
    "service status",
    "where is my repair",
)
CASE_TERMS = (
    "申请维修",
    "售后维修",
    "报修",
    "送修",
    "创建案例",
    "申请售后",
    "我要维修",
    "repair request",
    "send it for repair",
    "after-sales service",
)
TROUBLESHOOTING_TERMS = (
    "怎么办",
    "怎么处理",
    "无法开机",
    "不能开机",
    "开不了机",
    "黑屏",
    "不充电",
    "不能使用",
    "坏了",
    "故障",
    "troubleshoot",
    "won't turn on",
    "cannot power on",
    "not charging",
    "black screen",
)
POWER_TROUBLESHOOTING_TERMS = (
    "无法开机",
    "不能开机",
    "开不了机",
    "黑屏",
    "不充电",
    "cannot power on",
    "won't turn on",
    "not charging",
    "black screen",
)
HIGH_RISK_TERMS = (
    "退款",
    "退货",
    "换机",
    "赔偿",
    "赔付",
    "支付",
    "refund",
    "replacement",
    "compensation",
)
FAQ_TERMS = (
    "保修",
    "保多久",
    "政策",
    "维修范围",
    "需要什么材料",
    "warranty",
    "policy",
    "coverage",
    "eligible",
)


@dataclass(frozen=True)
class ExtractedContext:
    order_id: str | None = None
    serial_number: str | None = None
    product_model: str | None = None
    market: Market | None = None
    language: Language | None = None
    issue_category: str = "general_issue"


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def classify_intent(text: str) -> Intent:
    """离线确定性路由是安全底座，LLM 只作为可选增强，不决定写操作。"""

    # 高风险请求进入政策证据门禁，不能落入普通排查或 Case Draft 路径。
    if _contains(text, HIGH_RISK_TERMS):
        return Intent.FAQ
    if _contains(text, STATUS_TERMS):
        return Intent.REPAIR_STATUS
    if _contains(text, CASE_TERMS):
        return Intent.CASE_REQUEST
    if _contains(text, TROUBLESHOOTING_TERMS):
        return Intent.TROUBLESHOOTING
    if _contains(text, FAQ_TERMS):
        return Intent.FAQ
    return Intent.GENERAL


def _first_match(patterns: tuple[str, ...], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def extract_context(text: str) -> ExtractedContext:
    order_id = _first_match(
        (
            r"\b(ORD-[A-Z]{2}-[A-Z0-9-]+)\b",
            r"(?:订单号|订单|order(?:\s*id)?)\s*[:：#]?\s*([A-Z0-9-]{5,})",
        ),
        text,
    )
    serial_number = _first_match(
        (
            r"\b(SN-[A-Z]{2}-[A-Z0-9-]+)\b",
            r"(?:序列号|SN|serial(?:\s*number)?)\s*[:：#]?\s*([A-Z0-9-]{5,})",
        ),
        text,
    )
    product_model = _first_match(
        (r"(?:型号|机型|model)\s*[:：]?\s*([A-Za-z0-9 ._-]{2,30})",), text
    )

    lowered = text.lower()
    market = None
    if "马来西亚" in text or "malaysia" in lowered or re.search(r"\bMY\b", text, re.IGNORECASE):
        market = Market.MY
    elif "中国" in text or "china" in lowered or re.search(r"\bCN\b", text, re.IGNORECASE):
        market = Market.CN

    language = Language.EN_MY if re.search(r"\b(the|what|how|my|repair)\b", lowered) else Language.ZH_CN
    issue_category = "cannot_power_on" if _contains(text, POWER_TROUBLESHOOTING_TERMS) else "general_issue"
    return ExtractedContext(
        order_id=order_id,
        serial_number=serial_number,
        product_model=product_model,
        market=market,
        language=language,
        issue_category=issue_category,
    )


def issue_summary(text: str) -> str:
    if _contains(text, POWER_TROUBLESHOOTING_TERMS):
        # 保留与政策文档一致的核心术语，避免摘要改写后丢失 RAG 主题命中。
        return "设备无法开机（用户描述为无法正常开机或相关故障）"
    return text[:240]
