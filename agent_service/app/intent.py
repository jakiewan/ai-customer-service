import re
from dataclasses import dataclass

from .constants import CATEGORY_NAMES, SALES_CHANNEL_NAMES, Intent, Language, Market

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
AFTERSALES_TERMS = (
    "申请维修",
    "售后维修",
    "报修",
    "送修",
    "申请售后",
    "售后申请",
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
    "免费",
    "材料",
)
LINK_CANCEL_TERMS = ("不要申请", "不想维修", "取消维修", "取消申请", "不申请", "do not want", "don't want", "cancel my repair")


@dataclass(frozen=True)
class ExtractedContext:
    order_id: str | None = None
    serial_number: str | None = None
    product_model: str | None = None
    market: Market | None = None
    language: Language | None = None
    issue_category: str = "general_issue"
    goodsid: str | None = None
    sales_channel: str | None = None


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def classify_intent(text: str) -> Intent:
    """确定性区分知识咨询和入口诉求，保留否定语义。"""

    if link_cancelled(text):
        if _contains(text, FAQ_TERMS):
            return Intent.FAQ
        if _contains(text, TROUBLESHOOTING_TERMS):
            return Intent.TROUBLESHOOTING
        return Intent.GENERAL
    if _contains(text, HIGH_RISK_TERMS):
        return Intent.FAQ
    if _contains(text, STATUS_TERMS):
        return Intent.REPAIR_STATUS
    if _contains(text, ("我要买", "我想买", "在哪买", "哪里买", "购买链接", "商城", "官网商城", "where to buy", "purchase link", "buy now")):
        return Intent.PURCHASE_LINK
    if _contains(text, AFTERSALES_TERMS):
        if _contains(text, ("需要什么", "哪些材料", "材料", "条件", "多久", "免费", "what documents", "requirements")) and not _contains(text, ("入口", "链接", "url", "link")):
            return Intent.FAQ
        return Intent.AFTERSALES_LINK
    if _contains(text, FAQ_TERMS):
        return Intent.FAQ
    if _contains(text, TROUBLESHOOTING_TERMS):
        return Intent.TROUBLESHOOTING
    return Intent.GENERAL


def link_cancelled(text: str) -> bool:
    # 否定与申请动作之间允许短修饰语，但不跨句匹配，避免误伤后续明确申请。
    return _contains(text, LINK_CANCEL_TERMS) or bool(re.search(
        r"(?:不要|不想|不打算|不需要|不愿|暂不|先别|别)[^，。！？,.;!?]{0,8}"
        r"(?:申请(?:售后)?维修|申请售后|报修|送修)", text
    )) or bool(re.search(
        r"\b(?:do\s+not|don't|not|never|would\s+not|wouldn't)\s+"
        r"(?:(?:want|like|intend|ask|asking|you|to|help|me|submit|create|make|send|apply|for|a|any)\s+){0,6}"
        r"(?:repair\s+request|after-sales\s+service|send\s+it\s+for\s+repair)\b", text, re.IGNORECASE
    ))


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
    market_text = re.sub(r"\b(?:ORD|SN)-[A-Z0-9-]+\b", "", text, flags=re.IGNORECASE)
    market = None
    # 英语所有格 my/My 不是 MY 国家代码；单独代码或 country/market 标注仍兼容小写。
    my_code = (market_text.strip().lower() == "my" or
               re.search(r"(?:country|market|国家|市场)\s*[:：=]?\s*my\b", market_text, re.IGNORECASE) or
               re.search(r"\b(?:in|from)\s+MY\b", market_text) or
               (re.search(r"\bMY\b", market_text) and re.search(r"[\u4e00-\u9fff]", market_text)))
    if "马来西亚" in text or "malaysia" in market_text.lower() or my_code:
        market = Market.MY
    elif "中国" in text or "china" in market_text.lower() or re.search(r"\bCN\b", market_text, re.IGNORECASE):
        market = Market.CN

    language_text = re.sub(r"\b(?:ord|sn)-[a-z0-9-]+\b", "", lowered)
    language = Language.EN_MY if re.search(r"\b(the|what|how|my|repair)\b", language_text) else Language.ZH_CN
    issue_category = "cannot_power_on" if _contains(text, POWER_TROUBLESHOOTING_TERMS) else "general_issue"
    goodsid = _first_match((r"(?:goodsid|三级品类|品类)\s*[:：=]?\s*(\d{5})\b",), text)
    if text.strip() in CATEGORY_NAMES:
        goodsid = text.strip()
    if goodsid is None:
        goodsid = next((key for key, name in CATEGORY_NAMES.items() if name in text), None)
    sales_channel = next((key for key, name in SALES_CHANNEL_NAMES.items() if name in text or key in text.upper().split()), None)
    return ExtractedContext(
        order_id=order_id,
        serial_number=serial_number,
        product_model=product_model,
        market=market,
        language=language,
        issue_category=issue_category,
        goodsid=goodsid,
        sales_channel=sales_channel,
    )


def issue_summary(text: str) -> str:
    if _contains(text, POWER_TROUBLESHOOTING_TERMS):
        # 保留与政策文档一致的核心术语，避免摘要改写后丢失 RAG 主题命中。
        return "设备无法开机（用户描述为无法正常开机或相关故障）"
    return text[:240]
