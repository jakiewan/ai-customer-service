# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 全局语言规则：用户偏好中文，所有 thinking 与对外回复统一使用中文（技术术语、代码标识符、第三方工具名保留英文）。见 `~/.claude/CLAUDE.md`。

## 当前业务契约（2026-10-14，优先于历史章节）

GlobalCare 商品与售后咨询 Agent。政策按 `market + goodsid + sales_channel` 唯一匹配；goodsid 是三级品类 ID，SKU/型号/地区/日期不参与政策过滤。当前客服不调用个人业务工具；购买/申请通过固定 URL 引导，用户在官方页面办理。入口 URL 来自环境配置，不由模型生成。草稿业务代码和接口已移除。

本轮不新增 PG 知识表，保留既有会话/消息/审计/锁和 checkpoint。消息链接落既有消息表，先执行迁移。修改请遵守最小修改原则，不因企业级表述增加 Redis、多层 FAQ、费用账本或其他新平台。

下列章节为历史运行说明，旧草稿路径不再生效；当前命令与范围见 README.md 和技术方案.md。

## 常用命令

> 默认 Python 环境: `/opt/anaconda3/envs/ai-base/bin/python`（`ai-base` conda env）。

### 安装与基础设施

```bash
/opt/anaconda3/envs/ai-base/bin/python -m pip install -r requirements.txt
docker compose up -d                                          # 独立 pgvector 容器,端口 55432
export GLOBALCARE_DATABASE_URL='postgresql+psycopg://postgres:postgres@127.0.0.1:55432/globalcare_agent'
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/init_postgres.py
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/seed_policy_pgvector.py
```

### 启动三个服务（按顺序）

```bash
# 1) Spring Boot 模拟业务（端口 8081）
cd domain_service && mvn -Dmaven.repo.local=.mvn/repository spring-boot:run

# 2) FastMCP 工具服务（端口 8001,streamable-http）
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python -m agent_service.mcp_server

# 3) FastAPI Agent（端口 8000）
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

# 4) Vue 静态页面（端口 4173）
cd frontend && python3 -m http.server 4173 --bind 127.0.0.1
```

### 测试

```bash
# 离线单元与 API 契约测试（不需要外部服务）
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python -m pytest -q agent_service/tests

# 单文件 / 单用例
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python -m pytest -q agent_service/tests/test_intent_and_policy.py
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python -m pytest -q agent_service/tests/test_pgvector_rag.py::test_xxx

# Spring Boot 单元测试
cd domain_service && mvn -Dmaven.repo.local=.mvn/repository clean test

# 三服务启动后端到端 smoke（FastAPI → LangGraph → MCP → Spring Boot）
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/http_e2e_smoke.py

# 27 个真实服务边界用例循环 3 轮
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/run_boundary_loop.py

# 全量回归（48 个离线用例 × 3 轮）
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/run_regression_loop.py
```

### Lint / 格式化

仓库未集成 ruff/black;`.ruff_cache/` 仅用于 IDE 缓存。Python 风格遵循 PEP 8 + 中英混合注释（中文 `#` 注释,代码标识符英文）。

## 架构总览

```
浏览器 Vue 3 (SSE 消费)
        │
        ▼
FastAPI (agent_service/app/main.py)
   ├─ ConversationService (service.py)   ← SSE 事件编排 + 审计
   ├─ LangGraph GraphService (graph.py)  ← 状态图 + 步骤/Token 预算
   │     ├─ classify_intent → collect_info → retrieve_policy
   │     ├─ → query_business → build_case_draft → validate_draft
   │     └─ → create_case_draft | reuse_draft → render_response → END
   ├─ PolicyStore (policy_store.py)     ← 内存向量 + 可选 pgvector
   ├─ BusinessGateway (business.py)      ← Mock / Http / Mcp 三种实现
   └─ InMemoryStore (store.py)           ← 会话/草稿/审计(可替换为 Postgres)
        │
        ▼
FastMCP (mcp_server.py)                 ← 仅暴露 query_order/query_device/query_repair_status
        │
        ▼
Spring Boot (domain_service)             ← /internal/* 模拟业务接口 + Case Draft 幂等
```

### 关键边界（务必遵守）

- **模型工具白名单**：`ALLOWED_MODEL_TOOLS = {query_order, query_device, query_repair_status}`。`create_case_draft` **不属于**模型工具,通过 `assert_application_write_allowed` 在 `graph.create_case_draft_node` 中显式调用。任何把写入工具暴露给 LLM 的改动都会破坏安全底座。
- **离线路由优先**：意图识别 `intent.classify_intent` 是确定性规则,LLM(`GLOBALCARE_LLM_MODE=minimax`)只在 `model_factory.AgentModel.classify` 中作为增强层,失败必须回退离线,否则 CI 会挂。
- **Case Draft 幂等**：`business.draft_fingerprint` 基于 (conversation_id, market, issue_category, issue_summary, order_id, serial_number, suggested_resolution) 做 SHA256;`MockBusinessGateway` / `HttpBusinessGateway` / Spring Boot `DomainService.createCaseDraft` 三端必须保持相同去重语义。
- **市场隔离**：`PolicyStore.retrieve` 按 `metadata.market` 过滤且硬编码 `UNSUPPORTED_PRODUCT_TERMS`(智能手表/耳机/笔记本等);若查询命中退换/赔偿/支付等 `UNSUPPORTED_POLICY_TERMS` 直接返回空证据 → 进入 `human_required`。
- **步骤预算**：`max_graph_steps=12`、`token_budget=4096`、`max_tool_calls=3`;`GraphService.stream` 内会硬性抛 `AgentRunError` / `AgentBudgetError`,SSE 翻译为 `error` 事件。
- **幂等消息**：`MessageRequest.client_message_id` 同值时 `InMemoryStore.append_user_message` 返回上次结果并走 `_replay_result`,不再重跑图。
- **审计口径**：`TOOL_CALL` / `POLICY_EVIDENCE` / `CASE_DRAFT_CREATED` / `GRAPH_NODE_COMPLETED` / `AGENT_RUN_COMPLETED` 等事件类型在 `service.py` 中固定;`GET /api/v1/conversations/{id}/audit` 返回全部事件。

## 目录地图（仅说明关注点）

- `agent_service/app/` — Python 主代码,文件名即模块职责（`graph`/`intent`/`policy_store`/`business`/`permissions`/`checkpointer`/`state`/`schemas`/`model_factory`/`service`/`store`/`main`/`config`/`constants`）。
- `agent_service/mcp_server.py` — FastMCP 入口;`python -m agent_service.mcp_server` 启动 streamable-http。
- `agent_service/data/policies/policy_{cn,my}.md` — 含 YAML frontmatter (`policy_id`/`title`/`market`/`language`/`version`),PolicyStore 读取时解析为 `Document`。
- `agent_service/migrations/001_init.sql` — `conversations` / `conversation_messages` / `policy_documents(embedding VECTOR(256))` / `case_drafts` / `agent_audit_events`;LangGraph checkpoint 表由 `AsyncPostgresSaver.setup()` 在 `checkpointer.open_checkpointer` 中创建。
- `agent_service/tests/` — 7 个测试文件,分别覆盖 intent/policy、graph flow、pgvector RAG、API 契约、模型工厂、权限、回归矩阵;`test_pgvector_rag.py` 需要真实 pgvector,默认会被跳过。
- `domain_service/src/main/java/com/globalcare/domain/` — Spring Boot 包结构 `api/DomainController` + `service/DomainService` + `model/*`(record 类)。
- `frontend/index.html` — Vue 3 CDN 单文件;真实 SSE 消费,右侧 trace/policy/draft 三张卡片。
- `scripts/` — `init_postgres.py` / `seed_policy_pgvector.py` / `http_e2e_smoke.py` / `live_boundary_e2e.py` / `run_boundary_loop.py` / `run_regression_loop.py`。
- `diagram/*.svg` — 项目已生成资产:思维导图、后端架构、Case Draft 时序。
- 顶层 `.md` 资料:`技术方案.md`(架构权威来源) / `需求分析报告.md` / `开发执行状态.md` / `测试说明与报告.md` / `测试用例清单.md` / `SDD缺口清单.md` / `面试话术.md`。**架构改动前先翻 `技术方案.md`,再翻 `SDD缺口清单.md` 是否会触发新缺口**。

## 配置矩阵（`agent_service/app/config.py`）

| 环境变量 | 默认 | 用途 |
|---|---|---|
| `GLOBALCARE_LLM_MODE` | `mock` | `mock` = 离线路由;`minimax` = 真实 `MiniMax-M3`(需 `MINIMAX_*`) |
| `GLOBALCARE_EMBEDDING_MODE` | `hash` | `hash` 确定性离线;`huggingface` = 本地多语言(下载权重) |
| `GLOBALCARE_EMBEDDING_DIMENSIONS` | `256` | HuggingFace 模型需改为 `384` 并重建 pgvector collection |
| `GLOBALCARE_POLICY_BACKEND` | `memory` | `pgvector` 时启用独立 PGVector(失败自动降级为 memory) |
| `GLOBALCARE_CHECKPOINTER_MODE` | `memory` | `postgres` = LangGraph `AsyncPostgresSaver` |
| `GLOBALCARE_BUSINESS_MODE` | `mock` | `http` = Spring Boot / `mcp` = FastMCP |
| `GLOBALCARE_MCP_MODE` | `mock` | `http` / `streamable_http` 触发 `MultiServerMCPClient` |
| `GLOBALCARE_DATABASE_URL` | `postgresql+psycopg://...:5432/globalcare_agent` | 实际默认端口已被 docker-compose 改为 55432 |
| `GLOBALCARE_DEMO_TOKEN` | `globalcare-demo-token` | 通过 `X-Demo-Token` Header 鉴权,缺失/错误返回 401 |
| `GLOBALCARE_CORS_ORIGINS` | 包含 `null` | 本地 file:// 兼容;生产环境需覆盖为明确 HTTPS |

## 排错速查

- **`/readyz` 显示 `pgvector: degraded`** → `scripts/seed_policy_pgvector.py` 未跑或维度不一致;PolicyStore 初始化时会**静默降级**到内存,不会报错。
- **`mcp: down`** → FastMCP 进程未起或端口被占;`MultiServerMCPClient` 不会重试。
- **Case Draft 重复创建** → 检查 `draft_fingerprint` 字段是否在请求体;Spring Boot `draftsByFingerprint` 是同进程 map,重启即丢失。
- **草稿市场与对话不一致** → 业务事实的 `market` 字段优先级高于用户声明,`graph.query_business_node` 会触发 `MARKET_MISMATCH` 拒绝路径。
- **离线意图路由命中 FAQ 但期望 TROUBLESHOOTING** → 检查文本是否包含 `UNSUPPORTED_POLICY_TERMS`(退款/退货/换机/赔偿等),`intent.classify_intent` 会**强制降级**为 FAQ。
- **pytest 跑通但服务启动失败** → 多半是 `pydantic_settings` 在 `.env` 中读取了旧值;启动前显式 `export` 环境变量覆盖。

## 知识资产与已知风险

- `.learnings/LEARNINGS.md` — 工作区级自我改进日志,按 `[LRN-YYYYMMDD-NNN]` 编号;架构、幂等或一致性改动前先扫一遍 `Status: pending` 条目,避免重复踩坑。
- `.learnings/ERRORS.md` — 已记录的命令/集成错误与解决方案。

**关键 pending 风险**(不要据此声称已生产级):

- `LRN-20260901-001` — **消息级幂等存在并发缺口**。`InMemoryStore.append_user_message` 在处理完成才写结果,同会话并发 5 次同 `client_message_id` 会重复执行完整图并发出重复 `case_draft_created`(下游 `draft_fingerprint` 仍去重,数据层正确但事件/Token 浪费)。本文件"幂等消息"段仅描述单线程视角,生产化前需补 single-flight 或原子 PROCESSING/SUCCEEDED 状态。
- `LRN-20260830-006` — **RAG 拒答门禁依赖负例覆盖**。新增市场或产品类型时,必须补正例 + 跨市场负例 + 高风险负例(退款/退货/换机/赔偿等 `UNSUPPORTED_POLICY_TERMS`)+ 非支持产品负例(`UNSUPPORTED_PRODUCT_TERMS`),并用真实 pgvector 跑一遍,不能只测内存 VectorStore。
