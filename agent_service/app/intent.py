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
LINK_CANCEL_TERMS = (
    "不要申请",
    "不想维修",
    "取消维修",
    "取消申请",
    "不申请",
    "do not want",
    "don't want",
    "cancel my repair",
)


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

    social = re.sub(r"[\s，。！？!?,.]+", "", text.lower())
    capabilities = re.fullmatch(
        r"(?:请(?:用一句话)?(?:概括|介绍)|介绍一下|告诉我)?"
        r"(?:你|您)(?:是谁|能做什么|可以做什么|(?:能|可以)提供(?:什么|哪些)(?:帮助|协助|服务))",
        social,
    )
    if capabilities or social in {
        "你好", "您好", "嗨", "hello", "hi", "谢谢", "感谢", "谢谢你",
        "好的", "收到", "再见", "晚安", "早上好", "晚上好",
    }:
        return Intent.CHITCHAT
    if link_cancelled(text):
        if _contains(text, FAQ_TERMS):
            return Intent.FAQ
        if _contains(text, TROUBLESHOOTING_TERMS):
            return Intent.TROUBLESHOOTING
        return Intent.UNKNOWN
    if _contains(text, HIGH_RISK_TERMS):
        return Intent.FAQ
    if _contains(text, STATUS_TERMS):
        return Intent.REPAIR_STATUS
    if _contains(
        text,
        (
            "我要买",
            "我想买",
            "购买链接",
            "去官网商城购买",
            "where to buy",
            "purchase link",
            "buy now",
        ),
    ) or (
        _contains(text, ("在哪买", "哪里买", "商城", "官网商城"))
        and not _contains(text, FAQ_TERMS)
    ):
        return Intent.PURCHASE_LINK
    if _contains(text, AFTERSALES_TERMS):
        if _contains(
            text,
            (
                "需要什么",
                "哪些材料",
                "材料",
                "条件",
                "多久",
                "免费",
                "费用",
                "收费",
                "多少钱",
                "价格",
                "what documents",
                "requirements",
            ),
        ) and not _contains(text, ("入口", "链接", "url", "link")):
            return Intent.FAQ
        return Intent.AFTERSALES_LINK
    if _contains(text, FAQ_TERMS):
        return Intent.FAQ
    if _contains(text, TROUBLESHOOTING_TERMS):
        return Intent.TROUBLESHOOTING
    return Intent.UNKNOWN


def link_cancelled(text: str) -> bool:
    # 否定与申请动作之间允许短修饰语，但不跨句匹配，避免误伤后续明确申请。
    return (
        _contains(text, LINK_CANCEL_TERMS)
        or bool(
            re.search(
                r"(?:不要|不想|不打算|不需要|不愿|暂不|先别|别)[^，。！？,.;!?]{0,8}"
                r"(?:申请(?:售后)?维修|申请售后|报修|送修)",
                text,
            )
        )
        or bool(
            re.search(
                r"\b(?:do\s+not|don't|not|never|would\s+not|wouldn't)\s+"
                r"(?:(?:want|like|intend|ask|asking|you|to|help|me|submit|create|make|send|apply|for|a|any)\s+){0,6}"
                r"(?:repair\s+request|after-sales\s+service|send\s+it\s+for\s+repair)\b",
                text,
                re.IGNORECASE,
            )
        )
    )


def _first_match(patterns: tuple[str, ...], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


PRODUCT_REFERENCE = re.compile(
    r"^(?:(?:那|那么|请问)[，,\s]*)?"
    r"(?:它们|他们|这两款|那两款|它|他|[这那](?:款|台|部|个)(?:手机|电脑|空调|冰箱|洗衣机|设备|商品)?)"
)
IPHONE_MODEL = re.compile(
    r"(?<![a-z0-9])iphone\s*(\d{1,2})(?:\s*(pro(?:\s*max)?|plus|mini|air|e))?(?![a-z0-9])",
    re.IGNORECASE,
)
LABELLED_MODEL = re.compile(
    r"(?:型号|机型|model)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9 ._-]{1,29})",
    re.IGNORECASE,
)


def product_mentions(text: str) -> list[str]:
    """只抽取用户明确写出的商品，不把助手回答或未识别的名称当作事实。"""

    models = []
    for match in IPHONE_MODEL.finditer(text):
        suffix = re.sub(r"\s*max", " Max", match[2].title()) if match[2] else ""
        models.append(f"iPhone {match[1]}" + (f" {suffix}" if suffix else ""))
    labelled = [match[1].strip(" ._-") for match in LABELLED_MODEL.finditer(text)]
    models.extend(model for model in labelled if not IPHONE_MODEL.fullmatch(model))
    categories = [name for name in CATEGORY_NAMES.values() if name in text]
    if IPHONE_MODEL.search(text):
        categories = [name for name in categories if name != "手机"]
    return list(dict.fromkeys([*models, *categories]))


def resolve_product_reference(
    message: str, history: list[dict], pending_question: str | None = None
) -> tuple[str | None, list[str]]:
    """唯一候选补全；无候选或多个候选交由现有 collect_info 追问。"""

    subjects = product_mentions(message)
    if pending_question and len(subjects) == 1:
        selection = re.sub(r"^(?:型号|机型|model)\s*[:：]?", "", message, flags=re.IGNORECASE)
        selected = re.sub(r"[\s，。！？,.!?]", "", selection).lower()
        if selected == re.sub(r"\s", "", subjects[0]).lower():
            return PRODUCT_REFERENCE.sub(subjects[0], pending_question, count=1), []
    reference = PRODUCT_REFERENCE.match(message)
    if reference is None:
        return message, []
    if (
        IPHONE_MODEL.match(message, reference.end())
        or LABELLED_MODEL.match(message, reference.end())
    ):
        return message, []
    category = next((name for name in CATEGORY_NAMES.values() if name in reference[0]), None)
    for index in range(len(history) - 1, -1, -1):
        previous = history[index]
        text = str(previous.get("content", ""))
        if previous.get("role") != "user":
            continue
        subjects = product_mentions(text)
        if PRODUCT_REFERENCE.match(text):
            previous_question, previous_candidates = resolve_product_reference(text, history[:index])
            subjects = product_mentions(previous_question) if previous_question else previous_candidates
        if subjects:
            if category:
                matching = [
                    subject for subject in subjects
                    if extract_context(subject).goodsid == extract_context(category).goodsid
                ]
                if not matching:
                    return PRODUCT_REFERENCE.sub(category, message, count=1), []
                subjects = matching
            if len(subjects) == 1 and not reference[0].endswith(("们", "两款")):
                return PRODUCT_REFERENCE.sub(subjects[0], message, count=1), []
            return None, subjects
    if category:
        return PRODUCT_REFERENCE.sub(category, message, count=1), []
    return None, []


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
    subjects = product_mentions(text)
    product_model = (
        subjects[0]
        if len(subjects) == 1 and subjects[0] not in CATEGORY_NAMES.values()
        else None
    )

    lowered = text.lower()
    language_text = re.sub(r"\b(?:ord|sn)-[a-z0-9-]+\b", "", lowered)
    language = (
        Language.EN
        if re.search(r"\b(the|what|how|my|repair)\b", language_text)
        else Language.ZH_CN
    )
    issue_category = (
        "cannot_power_on"
        if _contains(text, POWER_TROUBLESHOOTING_TERMS)
        else "general_issue"
    )
    goodsid = _first_match((r"(?:goodsid|三级品类|品类)\s*[:：=]?\s*(\d{5})\b",), text)
    if text.strip() in CATEGORY_NAMES:
        goodsid = text.strip()
    if goodsid is None:
        goodsid = next(
            (key for key, name in CATEGORY_NAMES.items() if name in text), None
        )
    if goodsid is None and _contains(text, ("笔记本", "laptop", "computer")):
        goodsid = "65540"
    if goodsid is None and IPHONE_MODEL.search(text):
        goodsid = "65539"
    sales_channel = next(
        (
            key
            for key, name in SALES_CHANNEL_NAMES.items()
            if name in text or key in text.upper().split()
        ),
        None,
    )
    return ExtractedContext(
        order_id=order_id,
        serial_number=serial_number,
        product_model=product_model,
        market=Market.CN,
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
