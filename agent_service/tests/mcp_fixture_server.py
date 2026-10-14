"""仅供集成测试的真实 MCP 协议进程，所有返回数据均为模拟数据。"""

import asyncio
import sys
import os

from fastmcp import FastMCP

from agent_service.app.business import MOCK_ORDERS
from agent_service.app.mcp_auth import BusinessTokenVerifier

service_token = os.getenv("GLOBALCARE_MCP_TOKEN", "")
mcp = FastMCP("test-business-boundary", auth=BusinessTokenVerifier(service_token) if service_token else None)


@mcp.tool
async def query_order(order_id: str) -> dict:
    if order_id in {"SLOW", "ORD-CN-SLOW"}:
        await asyncio.sleep(2)
    if order_id == "INVALID":
        return {"isError": True, "code": "ORDER_NOT_FOUND", "message": "订单不存在"}
    return dict(MOCK_ORDERS["ORD-CN-2001"])


@mcp.tool
async def query_device(serial_number: str) -> dict:
    return dict(MOCK_ORDERS["ORD-CN-2001"])


@mcp.tool
async def query_repair_status(order_id: str | None = None, serial_number: str | None = None) -> dict:
    return {**MOCK_ORDERS["ORD-CN-2001"], "status": "IN_REPAIR", "status_label": "维修处理中"}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=int(sys.argv[1]))
