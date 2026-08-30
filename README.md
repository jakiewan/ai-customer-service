# GlobalCare Web 售后 Case 智能受理 Agent MVP

一个面向消费电子售后网页聊天的 Agent 项目。它把“故障咨询、维修进度、申请售后”拆成不同的受理路径：Agent 可以给出带政策依据的回答、查询业务事实、创建或复用结构化 `Case Draft`，但不能创建正式 Case 或 Work Order。

这是求职作品级、可本地联调的 MVP：业务数据、政策与 Spring Boot 下游均为脱敏模拟实现；Agent 状态机、MCP 工具边界、pgvector 政策检索、SSE、草稿幂等和测试是真实代码。

## 已落地能力

- LangGraph：显式状态图、条件路由、单轮步骤预算、内存/ PostgreSQL Checkpointer。
- LangChain：MiniMax-M3 可选结构化意图模型、Prompt、`Document`、Embedding 和 pgvector Retriever。
- MCP：FastMCP 仅暴露 `query_order`、`query_device`、`query_repair_status` 三个只读工具；写入草稿不暴露给模型。
- FastAPI：会话、SSE、草稿查询、健康检查、CORS 和 OpenAPI。
- Spring Boot：模拟订单、设备、维修状态和 Case Draft 幂等接口。
- Vue 3：真实调用 FastAPI SSE，不再只用固定话术演示。
- PostgreSQL + pgvector：独立 Docker 容器、政策向量集合和 LangGraph checkpoint 表。

## 项目资料

- [技术方案](技术方案.md)
- [需求分析报告](需求分析报告.md)
- [开发执行状态](开发执行状态.md)
- [开工思维导图](diagram/globalcare-project-mindmap.svg)
- [后端架构图](diagram/globalcare-backend-architecture.svg)
- [Case Draft 时序图](diagram/globalcare-case-draft-sequence.svg)

## 代码结构

```text
ai-customer-service/
├── frontend/                 # Vue 3 CDN 单页，真实 SSE 联调
├── agent_service/
│   ├── app/                  # FastAPI、LangGraph、LangChain、MCP adapter
│   ├── data/policies/        # CN/MY 脱敏模拟政策
│   ├── migrations/           # PostgreSQL + pgvector DDL
│   └── mcp_server.py         # FastMCP 只读工具服务
├── domain_service/           # Spring Boot 模拟业务服务
├── scripts/                  # pgvector 初始化、种子与 HTTP E2E smoke
├── docker-compose.yml        # 独立 pgvector 容器（55432）
└── 技术方案.md
```

## 本地运行

默认 Python 环境为本机 `ai-base`：`/opt/anaconda3/envs/ai-base/bin/python`。

### 1. 安装 Python 依赖

```bash
/opt/anaconda3/envs/ai-base/bin/python -m pip install -r requirements.txt
```

### 2. 启动独立 pgvector

```bash
docker compose up -d
export GLOBALCARE_DATABASE_URL='postgresql+psycopg://postgres:postgres@127.0.0.1:55432/globalcare_agent'
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/init_postgres.py
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/seed_policy_pgvector.py
```

该容器仅使用端口 `55432` 和项目命名卷 `globalcare_pgdata`，不会触碰本机已有的 `5432` PostgreSQL。

### 3. 启动 Spring Boot 模拟业务服务

```bash
cd domain_service
mvn -Dmaven.repo.local=.mvn/repository spring-boot:run
```

### 4. 启动 FastMCP 工具服务

```bash
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python -m agent_service.mcp_server
```

### 5. 启动 FastAPI Agent

```bash
export GLOBALCARE_LLM_MODE=mock
export GLOBALCARE_BUSINESS_MODE=http
export GLOBALCARE_MCP_MODE=http
export GLOBALCARE_POLICY_BACKEND=pgvector
export GLOBALCARE_CHECKPOINTER_MODE=postgres
export GLOBALCARE_DATABASE_URL='postgresql+psycopg://postgres:postgres@127.0.0.1:55432/globalcare_agent'
export GLOBALCARE_BUSINESS_BASE_URL='http://127.0.0.1:8081'
export GLOBALCARE_MCP_URL='http://127.0.0.1:8001/mcp'
export GLOBALCARE_DEMO_TOKEN='globalcare-demo-token'
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python -m uvicorn agent_service.app.main:app --host 127.0.0.1 --port 8000
```

访问 `http://127.0.0.1:8000/docs` 查看运行时 OpenAPI 文档。

### 6. 启动 Vue 页面

```bash
cd frontend
python3 -m http.server 4173 --bind 127.0.0.1
```

浏览器打开 `http://127.0.0.1:4173/index.html`。

## 验证

```bash
# 离线单元与 API 契约测试
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python -m pytest -q agent_service/tests

# Spring Boot 单元测试
cd domain_service && mvn -Dmaven.repo.local=.mvn/repository clean test

# 三个服务启动后，验证 FastAPI → LangGraph → MCP → Spring Boot
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/http_e2e_smoke.py
```

当前 Golden Conversation 至少覆盖：

1. “手机无法开机怎么办” → 排查步骤 + 政策引用，**不创建草稿**。
2. “我要申请售后维修” → 请求订单号/SN；下一轮补 `ORD-MY-2001` 后创建草稿。
3. “查询维修进度” → 调 MCP 返回 `维修处理中`，**不创建第二份草稿**。
4. 重复提交相同 `client_message_id` → SSE 幂等重放。

## 模型与 Embedding 模式

- `GLOBALCARE_LLM_MODE=mock`：离线可测试的结构化路由；默认模式。
- `GLOBALCARE_LLM_MODE=minimax`：使用 `MINIMAX_API_KEY`、`MINIMAX_BASE_URL`、`MINIMAX_MODEL=MiniMax-M3`；真实调用会消耗配额。
- `GLOBALCARE_EMBEDDING_MODE=hash`：默认，确定性离线 embedding，便于 CI 与快速 MVP。
- `GLOBALCARE_EMBEDDING_MODE=huggingface`：本地多语言 embedding。若使用 `paraphrase-multilingual-MiniLM-L12-v2`，设置 `GLOBALCARE_EMBEDDING_DIMENSIONS=384`，并重新创建独立的 pgvector collection；首次会下载模型权重。

## 明确边界

- 没有真实登录、真实客户数据、真实订单、正式 Case、Work Order、人工坐席或通知自动化。
- Case Draft 是 Agent 唯一可触发的受控业务写入，且由应用节点调用 Spring Boot 接口；模型没有写工具权限。
- 当前 API 会话对象仍采用内存仓储；政策向量和 LangGraph Checkpoint 可以落 PostgreSQL。将会话、审计和真实鉴权全面持久化是下一阶段工作，不应声称已经生产上线。
