from enum import StrEnum


class Market(StrEnum):
    CN = "CN"


class Language(StrEnum):
    ZH_CN = "zh-CN"
    EN = "en"


class SalesChannel(StrEnum):
    MALL = "MALL"
    DIRECT = "DIRECT"
    AUTHORIZED = "AUTHORIZED"


CATEGORY_NAMES = {
    "65539": "手机",
    "65540": "电脑",
    "65541": "空调",
    "65542": "冰箱",
    "65543": "洗衣机",
}
SALES_CHANNEL_NAMES = {"MALL": "商城", "DIRECT": "自营店", "AUTHORIZED": "授权店"}


class Intent(StrEnum):
    FAQ = "FAQ"
    TROUBLESHOOTING = "TROUBLESHOOTING"
    REPAIR_STATUS = "REPAIR_STATUS"
    AFTERSALES_LINK = "AFTERSALES_LINK"
    PURCHASE_LINK = "PURCHASE_LINK"
    CHITCHAT = "CHITCHAT"
    # 无可靠业务路由时，由模型结合上下文澄清或引导。
    UNKNOWN = "UNKNOWN"


SOP_INTENTS = frozenset(
    {Intent.PURCHASE_LINK, Intent.AFTERSALES_LINK, Intent.REPAIR_STATUS}
)
FAQ_INTENTS = frozenset({Intent.FAQ, Intent.TROUBLESHOOTING})


class RouteLayer(StrEnum):
    L0_RULE = "L0_RULE"
    L1_REDIS_EXACT = "L1_REDIS_EXACT"
    L2_MILVUS_SEMANTIC = "L2_MILVUS_SEMANTIC"
    L3_LLM = "L3_LLM"
    L3_UNKNOWN = "L3_UNKNOWN"
    L3_AGENT = "L3_AGENT"


class ConversationStatus(StrEnum):
    AI_ACTIVE = "AI_ACTIVE"
    AI_CANNOT_RESOLVE = "AI_CANNOT_RESOLVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AgentPhase(StrEnum):
    AI_ACTIVE = "AI_ACTIVE"
    COLLECTING_INFO = "COLLECTING_INFO"
    CONTEXT_READY = "CONTEXT_READY"
    RETRIEVING_POLICY = "RETRIEVING_POLICY"
    QUERYING_BUSINESS_DATA = "QUERYING_BUSINESS_DATA"
    RESOLVING_LINK = "RESOLVING_LINK"
    AI_CANNOT_RESOLVE = "AI_CANNOT_RESOLVE"
    COMPLETED = "COMPLETED"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Channel(StrEnum):
    WEB_CHAT = "WEB_CHAT"


class ToolName(StrEnum):
    QUERY_ORDER = "query_order"
    QUERY_DEVICE = "query_device"
    QUERY_REPAIR_STATUS = "query_repair_status"


class ToolStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    BLOCKED = "blocked"


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    POLICY_NOT_FOUND = "POLICY_NOT_FOUND"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    AGENT_LIMIT_EXCEEDED = "AGENT_LIMIT_EXCEEDED"
    AGENT_BUDGET_EXCEEDED = "AGENT_BUDGET_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ALLOWED_MODEL_TOOLS = frozenset(
    {
        ToolName.QUERY_ORDER.value,
        ToolName.QUERY_DEVICE.value,
        ToolName.QUERY_REPAIR_STATUS.value,
    }
)
