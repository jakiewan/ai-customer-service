import asyncio
import json
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from .constants import Market, ToolName


class BusinessGatewayError(RuntimeError):
    def __init__(self, message: str, *, code: str = "TOOL_UNAVAILABLE", retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def public_business_error(code: str) -> str:
    # 错误正文可能包含 SQL、凭证或内部地址，不能直接回显给客户。
    return {"ORDER_NOT_FOUND": "订单不存在", "DEVICE_NOT_FOUND": "设备不存在"}.get(code, "下游业务暂时不可用")


class BusinessGateway(Protocol):
    async def query_order(self, order_id: str) -> dict[str, Any]: ...

    async def query_device(self, serial_number: str) -> dict[str, Any]: ...

    async def query_repair_status(self, *, order_id: str | None, serial_number: str | None) -> dict[str, Any]: ...


MOCK_ORDERS: dict[str, dict[str, Any]] = {
    "ORD-CN-1001": {
        "order_id": "ORD-CN-1001",
        "customer_id": "CUS-CN-001",
        "tenant_id": "tenant-one",
        "market": Market.CN.value,
        "product_model": "Xiaomi 15",
        "serial_number": "SN-CN-1001",
        "purchase_date": "2026-02-10",
        "repair_status": "RECEIVED",
    },
    "ORD-CN-2001": {
        "order_id": "ORD-CN-2001",
        "customer_id": "CUS-CN-002",
        "tenant_id": "tenant-one",
        "market": Market.CN.value,
        "product_model": "Xiaomi 15",
        "serial_number": "SN-CN-2001",
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


class MockBusinessGateway:
    """Java 下游只读查询的离线适配。"""

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
            "customer_id": record["customer_id"],
            "tenant_id": record["tenant_id"],
            "status": record["repair_status"],
            "status_label": {
                "RECEIVED": "已收件，等待检测",
                "IN_REPAIR": "维修处理中",
                "COMPLETED": "维修完成",
            }.get(record["repair_status"], "状态已更新"),
        }



class HttpBusinessGateway:
    def __init__(self, base_url: str, timeout_seconds: float = 3.0, service_token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._headers = {"Authorization": "Bearer " + service_token} if service_token else {}

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.request(method, f"{self.base_url}{path}", headers=self._headers, **kwargs)
            try:
                body = response.json()
            except ValueError as exc:
                raise BusinessGatewayError("下游返回非 JSON 数据", code="TOOL_INVALID_RESPONSE", retryable=False) from exc
            if not isinstance(body, dict):
                raise BusinessGatewayError("下游返回格式无效", code="TOOL_INVALID_RESPONSE", retryable=False)
            if response.status_code >= 400:
                raise BusinessGatewayError(
                    public_business_error(str(body.get("code", "TOOL_UNAVAILABLE"))),
                    code=body.get("code", "TOOL_UNAVAILABLE"),
                    retryable=response.status_code >= 500,
                )
            return body
        except httpx.TimeoutException as exc:
            raise BusinessGatewayError("下游业务服务超时", code="TOOL_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise BusinessGatewayError("下游业务服务不可用", code="TOOL_UNAVAILABLE") from exc

    async def query_order(self, order_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/internal/orders/{quote(order_id, safe='')}")

    async def query_device(self, serial_number: str) -> dict[str, Any]:
        return await self._request("GET", f"/internal/devices/{quote(serial_number, safe='')}")

    async def query_repair_status(self, *, order_id: str | None, serial_number: str | None) -> dict[str, Any]:
        params = {key: value for key, value in {"order_id": order_id, "serial_number": serial_number}.items() if value}
        return await self._request("GET", "/internal/repair-cases/status", params=params)



class McpBusinessGateway:
    """通过 langchain-mcp-adapters 调用 FastMCP，只选择白名单内工具。"""

    def __init__(self, mcp_url: str, timeout_seconds: float = 3, service_token: str = "") -> None:
        self.mcp_url = mcp_url
        self.timeout_seconds = timeout_seconds
        self._service_token = service_token
        self._load_lock = asyncio.Lock()
        self._client: Any = None
        self._tools: dict[str, Any] = {}

    async def _load_tools(self) -> None:
        if self._client is not None:
            return
        async with self._load_lock:
            if self._client is not None:
                return
            from langchain_mcp_adapters.client import MultiServerMCPClient
            connection = {"transport": "streamable_http", "url": self.mcp_url}
            if self._service_token:
                connection["headers"] = {"Authorization": "Bearer " + self._service_token}
            client = MultiServerMCPClient({"globalcare": connection})
            tools = await client.get_tools()
            self._tools = {tool.name: tool for tool in tools}
            # 发现成功后才发布缓存；失败不能留下永久不可恢复的半初始化实例。
            self._client = client

    async def _invoke(self, tool_name: ToolName, payload: dict[str, Any]) -> dict[str, Any]:
        if tool_name.value not in {item.value for item in ToolName}:
            raise BusinessGatewayError("工具不在权限白名单", code="TOOL_FORBIDDEN", retryable=False)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                await self._load_tools()
                tool = self._tools.get(tool_name.value)
                if tool is None:
                    raise BusinessGatewayError("MCP 工具不存在", code="TOOL_UNAVAILABLE")
                result = await tool.ainvoke(payload)
            if isinstance(result, dict):
                if result.get("isError"):
                    raise BusinessGatewayError(
                        public_business_error(str(result.get("code") or "TOOL_UNAVAILABLE")),
                        code=str(result.get("code") or "TOOL_UNAVAILABLE"),
                        retryable=False,
                    )
                return result
            if isinstance(result, list):
                text_blocks = [item.get("text") for item in result if isinstance(item, dict) and item.get("text")]
                if text_blocks:
                    text = str(text_blocks[0])
                    try:
                        decoded = json.loads(text)
                        if isinstance(decoded, dict):
                            if decoded.get("isError"):
                                raise BusinessGatewayError(
                                    public_business_error(str(decoded.get("code") or "TOOL_UNAVAILABLE")),
                                    code=str(decoded.get("code") or "TOOL_UNAVAILABLE"), retryable=False,
                                )
                            return decoded
                    except json.JSONDecodeError:
                        if text.lower().startswith("error calling tool"):
                            message = text.split(":", 1)[-1].strip() or "MCP 工具调用失败"
                            if tool_name == ToolName.QUERY_ORDER:
                                code = "ORDER_NOT_FOUND" if "订单不存在" in message else "TOOL_UNAVAILABLE"
                            elif tool_name == ToolName.QUERY_DEVICE:
                                code = "DEVICE_NOT_FOUND" if "设备不存在" in message else "TOOL_UNAVAILABLE"
                            else:
                                code = "ORDER_NOT_FOUND" if "订单不存在" in message else (
                                    "DEVICE_NOT_FOUND" if "设备不存在" in message else "TOOL_UNAVAILABLE"
                                )
                            raise BusinessGatewayError(public_business_error(code), code=code, retryable=False)
            raise BusinessGatewayError("MCP 返回格式无效", code="TOOL_INVALID_RESPONSE", retryable=False)
        except TimeoutError as exc:
            raise BusinessGatewayError("MCP 工具发现或调用超时", code="TOOL_TIMEOUT") from exc
        except BusinessGatewayError:
            raise
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



def build_business_gateway(mode: str, *, base_url: str, timeout_seconds: float, mcp_url: str, mcp_token: str = "", business_token: str = "") -> BusinessGateway:
    normalized = mode.lower()
    if normalized == "http":
        return HttpBusinessGateway(base_url, timeout_seconds, business_token)
    if normalized == "mcp":
        return McpBusinessGateway(mcp_url, timeout_seconds, mcp_token)
    return MockBusinessGateway()
