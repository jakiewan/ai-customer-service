import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx

from .constants import CaseDraftStatus, Market, ToolName, ToolStatus


class BusinessGatewayError(RuntimeError):
    def __init__(self, message: str, *, code: str = "TOOL_UNAVAILABLE", retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class BusinessGateway(Protocol):
    async def query_order(self, order_id: str) -> dict[str, Any]: ...

    async def query_device(self, serial_number: str) -> dict[str, Any]: ...

    async def query_repair_status(self, *, order_id: str | None, serial_number: str | None) -> dict[str, Any]: ...

    async def create_case_draft(self, payload: dict[str, Any]) -> dict[str, Any]: ...


MOCK_ORDERS: dict[str, dict[str, Any]] = {
    "ORD-CN-1001": {
        "order_id": "ORD-CN-1001",
        "customer_id": "CUS-CN-001",
        "market": Market.CN.value,
        "product_model": "Xiaomi 15",
        "serial_number": "SN-CN-1001",
        "purchase_date": "2026-02-10",
        "repair_status": "RECEIVED",
    },
    "ORD-MY-2001": {
        "order_id": "ORD-MY-2001",
        "customer_id": "CUS-MY-001",
        "market": Market.MY.value,
        "product_model": "Xiaomi 15",
        "serial_number": "SN-MY-2001",
        "purchase_date": "2026-03-12",
        "repair_status": "IN_REPAIR",
    },
}

MOCK_DEVICES = {
    value["serial_number"]: value for value in MOCK_ORDERS.values()
}


def mask_identifier(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:3]}{'*' * max(len(value) - 5, 1)}{value[-2:]}"


def draft_fingerprint(payload: dict[str, Any]) -> str:
    stable_fields = "|".join(
        str(payload.get(key, ""))
        for key in (
            "conversation_id",
            "market",
            "issue_category",
            "issue_summary",
            "order_id",
            "serial_number",
            "suggested_resolution",
        )
    )
    return hashlib.sha256(stable_fields.encode("utf-8")).hexdigest()


class MockBusinessGateway:
    """模拟 Java 下游；接口和幂等语义与 HTTP gateway 保持一致。"""

    def __init__(self) -> None:
        self._drafts_by_fingerprint: dict[str, dict[str, Any]] = {}

    async def query_order(self, order_id: str) -> dict[str, Any]:
        await asyncio.sleep(0)
        result = MOCK_ORDERS.get(order_id.upper())
        if not result:
            raise BusinessGatewayError("订单不存在", code="ORDER_NOT_FOUND", retryable=False)
        return dict(result)

    async def query_device(self, serial_number: str) -> dict[str, Any]:
        await asyncio.sleep(0)
        result = MOCK_DEVICES.get(serial_number.upper())
        if not result:
            raise BusinessGatewayError("设备不存在", code="DEVICE_NOT_FOUND", retryable=False)
        return dict(result)

    async def query_repair_status(self, *, order_id: str | None, serial_number: str | None) -> dict[str, Any]:
        record = await self.query_order(order_id) if order_id else await self.query_device(serial_number or "")
        return {
            "order_id": record["order_id"],
            "serial_number": record["serial_number"],
            "market": record["market"],
            "product_model": record["product_model"],
            "status": record["repair_status"],
            "status_label": {
                "RECEIVED": "已收件，等待检测",
                "IN_REPAIR": "维修处理中",
                "COMPLETED": "维修完成",
            }.get(record["repair_status"], "状态已更新"),
        }

    async def create_case_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        fingerprint = payload["draft_fingerprint"]
        existing = self._drafts_by_fingerprint.get(fingerprint)
        if existing:
            return {**existing, "idempotent_replay": True}
        result = {
            "case_draft_id": str(uuid4()),
            "status": CaseDraftStatus.DRAFT_CREATED.value,
            "created_at": payload.get("created_at"),
            "idempotent_replay": False,
            **payload,
        }
        self._drafts_by_fingerprint[fingerprint] = result
        return result


class HttpBusinessGateway:
    def __init__(self, base_url: str, timeout_seconds: float = 3.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.request(method, f"{self.base_url}{path}", **kwargs)
            if response.status_code >= 400:
                body = response.json() if response.content else {}
                raise BusinessGatewayError(
                    body.get("message", "下游业务服务返回错误"),
                    code=body.get("code", "TOOL_UNAVAILABLE"),
                    retryable=response.status_code >= 500,
                )
            return response.json()
        except httpx.TimeoutException as exc:
            raise BusinessGatewayError("下游业务服务超时", code="TOOL_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise BusinessGatewayError("下游业务服务不可用", code="TOOL_UNAVAILABLE") from exc

    async def query_order(self, order_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/internal/orders/{order_id}")

    async def query_device(self, serial_number: str) -> dict[str, Any]:
        return await self._request("GET", f"/internal/devices/{serial_number}")

    async def query_repair_status(self, *, order_id: str | None, serial_number: str | None) -> dict[str, Any]:
        params = {key: value for key, value in {"order_id": order_id, "serial_number": serial_number}.items() if value}
        return await self._request("GET", "/internal/repair-cases/status", params=params)

    async def create_case_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/internal/case-drafts", json=payload)


class McpBusinessGateway:
    """通过 langchain-mcp-adapters 调用 FastMCP，只选择白名单内工具。"""

    def __init__(self, mcp_url: str) -> None:
        self.mcp_url = mcp_url
        self._client: Any = None
        self._tools: dict[str, Any] = {}

    async def _load_tools(self) -> None:
        if self._client is not None:
            return
        from langchain_mcp_adapters.client import MultiServerMCPClient

        self._client = MultiServerMCPClient(
            {
                "globalcare": {
                    "transport": "streamable_http",
                    "url": self.mcp_url,
                }
            }
        )
        tools = await self._client.get_tools()
        self._tools = {tool.name: tool for tool in tools}

    async def _invoke(self, tool_name: ToolName, payload: dict[str, Any]) -> dict[str, Any]:
        if tool_name.value not in {item.value for item in ToolName}:
            raise BusinessGatewayError("工具不在权限白名单", code="TOOL_FORBIDDEN", retryable=False)
        if tool_name.value == ToolName.CREATE_CASE_DRAFT.value:
            raise BusinessGatewayError("模型不可调用写入工具", code="TOOL_FORBIDDEN", retryable=False)
        await self._load_tools()
        tool = self._tools.get(tool_name.value)
        if tool is None:
            raise BusinessGatewayError("MCP 工具不存在", code="TOOL_UNAVAILABLE")
        try:
            result = await tool.ainvoke(payload)
            if isinstance(result, dict):
                return result
            if isinstance(result, list):
                text_blocks = [item.get("text") for item in result if isinstance(item, dict) and item.get("text")]
                if text_blocks:
                    try:
                        decoded = json.loads(text_blocks[0])
                        if isinstance(decoded, dict):
                            return decoded
                    except json.JSONDecodeError:
                        pass
            return {"result": result}
        except Exception as exc:
            raise BusinessGatewayError("MCP 工具调用失败", code="TOOL_UNAVAILABLE") from exc

    async def query_order(self, order_id: str) -> dict[str, Any]:
        return await self._invoke(ToolName.QUERY_ORDER, {"order_id": order_id})

    async def query_device(self, serial_number: str) -> dict[str, Any]:
        return await self._invoke(ToolName.QUERY_DEVICE, {"serial_number": serial_number})

    async def query_repair_status(self, *, order_id: str | None, serial_number: str | None) -> dict[str, Any]:
        return await self._invoke(
            ToolName.QUERY_REPAIR_STATUS,
            {"order_id": order_id, "serial_number": serial_number},
        )

    async def create_case_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise BusinessGatewayError("Case Draft 写入必须由应用节点调用", code="TOOL_FORBIDDEN", retryable=False)


def build_business_gateway(mode: str, *, base_url: str, timeout_seconds: float, mcp_url: str) -> BusinessGateway:
    normalized = mode.lower()
    if normalized == "http":
        return HttpBusinessGateway(base_url, timeout_seconds)
    if normalized == "mcp":
        return McpBusinessGateway(mcp_url)
    return MockBusinessGateway()
