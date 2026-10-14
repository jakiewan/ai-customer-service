from .constants import ALLOWED_MODEL_TOOLS


class ToolPermissionError(PermissionError):
    pass


def assert_model_tool_allowed(tool_name: str) -> None:
    if tool_name not in ALLOWED_MODEL_TOOLS:
        raise ToolPermissionError(f"模型工具权限拒绝: {tool_name}")

