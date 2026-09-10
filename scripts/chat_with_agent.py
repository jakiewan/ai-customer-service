"""用脚本连续和 GlobalCare Agent 对话,等价于在 Vue 输入框打字。

支持两种用法:
  1. 交互式:直接运行,逐行输入,`:quit` 退出。
  2. 管道批跑:`echo "..." | python scripts/chat_with_agent.py`,便于回归/CI。

实现要点:
- 同一会话复用 conversation_id,跨轮由 LangGraph checkpointer 维护状态。
- client_message_id 用 uuid4,避免触发幂等重放。
- SSE 流按事件分流:status / tool_call / policy_evidence / token / case_draft_created /
  human_required / error / done。
"""

import json
import sys
import uuid

import httpx

BASE_URL = "http://127.0.0.1:8000"
TOKEN = "globalcare-demo-token"
HEADERS = {"X-Demo-Token": TOKEN, "Content-Type": "application/json"}


def create_conversation(client: httpx.Client, market: str) -> str:
    response = client.post(
        f"{BASE_URL}/api/v1/conversations",
        headers=HEADERS,
        json={"channel": "WEB_CHAT", "market": market, "language": "zh-CN"},
    )
    response.raise_for_status()
    return response.json()["data"]["conversation_id"]


def stream_message(client: httpx.Client, conversation_id: str, message: str) -> str:
    """发送一条用户消息,逐个事件打印,返回拼接后的助手回复。"""

    reply_chunks: list[str] = []
    with client.stream(
        "POST",
        f"{BASE_URL}/api/v1/conversations/{conversation_id}/messages/stream",
        headers=HEADERS,
        json={"client_message_id": str(uuid.uuid4()), "message": message},
        timeout=30,
    ) as response:
        response.raise_for_status()
        buffer = ""
        for chunk in response.iter_text():
            buffer += chunk
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                event_name, data_line = "message", ""
                for line in block.splitlines():
                    if line.startswith("event: "):
                        event_name = line.removeprefix("event: ")
                    elif line.startswith("data: "):
                        data_line = line.removeprefix("data: ")
                if not data_line:
                    continue
                payload = json.loads(data_line)
                _render_event(event_name, payload, reply_chunks)
    return "".join(reply_chunks)


def _render_event(event_name: str, payload: dict, reply_chunks: list[str]) -> None:
    if event_name == "status":
        node = payload.get("node") or payload.get("phase")
        print(f"  · [{node}] {payload.get('message')}", flush=True)
    elif event_name == "tool_call":
        print(f"  · 工具调用: {payload.get('tool_name')} -> {payload.get('status')}", flush=True)
    elif event_name == "policy_evidence":
        print(f"  · 政策引用: {payload.get('title')} (v{payload.get('version')})", flush=True)
    elif event_name == "token":
        delta = payload.get("delta", "")
        reply_chunks.append(delta)
        print(delta, end="", flush=True)
    elif event_name == "case_draft_created":
        print(f"\n  ✓ Case Draft 已生成: {payload.get('case_draft_id')}", flush=True)
    elif event_name == "human_required":
        print(f"\n  ⚠ 需人工介入: {payload.get('reason')}", flush=True)
    elif event_name == "error":
        print(f"\n  ✗ 错误: {payload.get('message')}", flush=True)
    elif event_name == "done":
        print(f"\n  · 会话结束: status={payload.get('status')}", flush=True)


def main() -> None:
    if not sys.stdin.isatty():
        market = sys.argv[1] if len(sys.argv) > 1 else "MY"
    else:
        market = input("选择购买市场 (CN/MY), 默认 MY: ").strip().upper() or "MY"

    with httpx.Client() as client:
        try:
            conversation_id = create_conversation(client, market)
        except httpx.HTTPError as exc:
            print(f"无法创建会话,请确认 FastAPI 服务已启动: {exc}", file=sys.stderr)
            return 2
        print(f"会话已创建: {conversation_id}, market={market}")
        print("输入消息并回车发送,`:quit` 退出。\n", flush=True)

        for raw_line in sys.stdin:
            user_text = raw_line.strip()
            if not user_text:
                continue
            if user_text in {":quit", ":exit", ":q"}:
                break
            print(f"[你] {user_text}", flush=True)
            try:
                stream_message(client, conversation_id, user_text)
            except httpx.HTTPError as exc:
                print(f"\n  ✗ 请求失败: {exc}", flush=True)
            print("\n", flush=True)
    print("对话结束")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
