"""GlobalCare MCP 工具层。

只暴露查询类工具。Case Draft 写入保留在 Agent 应用节点，由受控业务适配器完成。
"""

import os
from typing import Any

from fastmcp import FastMCP

try:
    from .app.business import HttpBusinessGateway, MockBusinessGateway
    from .app.constants import ToolName
    from .app.permissions import assert_model_tool_allowed
    from .app.mcp_auth import BusinessTokenVerifier
except ImportError:  # 兼容 `python agent_service/mcp_server.py` 本地启动方式。
    from app.business import HttpBusinessGateway, MockBusinessGateway
    from app.constants import ToolName
    from app.permissions import assert_model_tool_allowed
    from app.mcp_auth import BusinessTokenVerifier

service_token = os.getenv("GLOBALCARE_MCP_TOKEN", "")
business_mode = os.getenv("GLOBALCARE_BUSINESS_MODE", "mock").lower()
if business_mode not in {"mock", "http"}:
    raise ValueError("MCP 服务业务网关只能选择 mock 或 http，不能递归调用 MCP 自身")
if os.getenv("GLOBALCARE_ENVIRONMENT", "local").lower() == "production" and (
    business_mode != "http" or len(service_token) < 32
):
    raise ValueError("生产 MCP 服务必须使用 HTTP 业务接口和至少 32 字符的服务凭证")

mcp = FastMCP(
    "globalcare-business-tools",
    instructions="GlobalCare 售后只读业务查询工具；不得用于创建正式 Case 或 Work Order。",
    auth=BusinessTokenVerifier(service_token) if service_token else None,
)
gateway = (HttpBusinessGateway(os.getenv("GLOBALCARE_BUSINESS_BASE_URL", "http://127.0.0.1:8081"),
                               float(os.getenv("GLOBALCARE_BUSINESS_TIMEOUT_SECONDS", "3")))
           if business_mode == "http" else MockBusinessGateway())


@mcp.tool
async def query_order(order_id: str) -> dict[str, Any]:
    """按订单号查询脱敏订单、购买市场和设备事实。"""

    assert_model_tool_allowed(ToolName.QUERY_ORDER.value)
    return await gateway.query_order(order_id)


@mcp.tool
async def query_device(serial_number: str) -> dict[str, Any]:
    """按设备序列号查询脱敏设备和关联订单事实。"""

    assert_model_tool_allowed(ToolName.QUERY_DEVICE.value)
    return await gateway.query_device(serial_number)


@mcp.tool
async def query_repair_status(order_id: str | None = None, serial_number: str | None = None) -> dict[str, Any]:
    """查询维修状态；订单号和序列号至少提供一个。"""

    assert_model_tool_allowed(ToolName.QUERY_REPAIR_STATUS.value)
    return await gateway.query_repair_status(order_id=order_id, serial_number=serial_number)


if __name__ == "__main__":
    host = os.getenv("GLOBALCARE_MCP_HOST", "127.0.0.1")
    port = int(os.getenv("GLOBALCARE_MCP_PORT", "8001"))
    mcp.run(transport="streamable-http", host=host, port=port)
