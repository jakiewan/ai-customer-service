"""真实 streamable-http 协议测试，不注入 gateway 私有缓存。"""

import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent_service.app.business import BusinessGatewayError, McpBusinessGateway


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def server(port, token=""):
    root = Path(__file__).resolve().parents[2]
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("mcp_fixture_server.py")), str(port)],
        cwd=root, env={**os.environ, "PYTHONPATH": str(root), "GLOBALCARE_MCP_TOKEN": token},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("MCP 测试服务启动失败")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("MCP 测试服务启动超时")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.asyncio
async def test_mcp_discovery_failure_is_typed_and_can_recover_when_service_returns():
    port = free_port()
    gateway = McpBusinessGateway(f"http://127.0.0.1:{port}/mcp")
    with pytest.raises(BusinessGatewayError):
        await gateway.query_order("ORD-CN-2001")
    with server(port):
        result = await gateway.query_order("ORD-CN-2001")
        assert result["order_id"] == "ORD-CN-2001"


@pytest.mark.asyncio
async def test_real_mcp_call_timeout_and_json_error_are_not_business_facts():
    with server(free_port()) as url:
        gateway = McpBusinessGateway(url, timeout_seconds=0.2)
        assert (await gateway.query_order("ORD-CN-2001"))["market"] == "CN"
        with pytest.raises(BusinessGatewayError) as slow:
            await gateway.query_order("SLOW")
        assert slow.value.code == "TOOL_TIMEOUT"
        with pytest.raises(BusinessGatewayError) as invalid:
            await gateway.query_order("INVALID")
        assert invalid.value.code == "ORDER_NOT_FOUND"


@pytest.mark.asyncio
async def test_mcp_business_tools_require_service_credential_when_configured():
    with server(free_port(), token="fixture-service-credential") as url:
        with pytest.raises(BusinessGatewayError):
            await McpBusinessGateway(url).query_order("ORD-CN-2001")
        gateway = McpBusinessGateway(url, service_token="fixture-service-credential")
        assert (await gateway.query_order("ORD-CN-2001"))["market"] == "CN"
