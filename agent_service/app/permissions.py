from .constants import ALLOWED_MODEL_TOOLS, ToolName


class ToolPermissionError(PermissionError):
    pass


def assert_model_tool_allowed(tool_name: str) -> None:
    if tool_name not in ALLOWED_MODEL_TOOLS:
        raise ToolPermissionError(f"模型工具权限拒绝: {tool_name}")


def assert_application_write_allowed(tool_name: str) -> None:
    if tool_name != ToolName.CREATE_CASE_DRAFT.value:
        raise ToolPermissionError(f"应用写操作不在允许范围: {tool_name}")

