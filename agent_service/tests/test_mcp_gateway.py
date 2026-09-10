import pytest

from agent_service.app.business import BusinessGatewayError, McpBusinessGateway


class FakeMcpTool:
    def __init__(self, result):
        self.result = result

    async def ainvoke(self, _payload):
        return self.result


@pytest.mark.asyncio
async def test_mcp_gateway_normalizes_order_not_found_result() -> None:
    gateway = McpBusinessGateway("http://unused/mcp")
    gateway._client = object()
    gateway._tools = {
        "query_order": FakeMcpTool(
            [{"type": "text", "text": "Error calling tool 'query_order': 订单不存在"}]
        )
    }

    with pytest.raises(BusinessGatewayError) as error:
        await gateway.query_order("ORD-NO-9999")

    assert error.value.code == "ORDER_NOT_FOUND"
    assert error.value.retryable is False
    assert str(error.value) == "订单不存在"
