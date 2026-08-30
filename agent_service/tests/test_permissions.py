import pytest

from agent_service.app.permissions import ToolPermissionError, assert_model_tool_allowed


def test_model_cannot_call_case_draft_write_tool() -> None:
    with pytest.raises(ToolPermissionError):
        assert_model_tool_allowed("create_case_draft")
