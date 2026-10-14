# GlobalCare 商品与售后咨询 Agent

基于 FastAPI、LangGraph 和 LangChain 的网页客服。当前仅服务中国大陆，内部 `market=CN`；提供保修政策、故障排查和已配置的官网商城/售后入口。用户在官方页面自行办理。

> 核对日期：2026-10-15。以当前工作区源码为准，包含尚未提交的四层路由改动；测试范围见[测试说明与报告](测试说明与报告.md)。

## 当前能力

- 六节点图：`classify_intent`、`collect_info`、`retrieve_policy`、`resolve_link`、`unknown`、`render_response`。
- 四层路由：L0 确定性规则 → L1 FAQ 精确匹配 → L2 意图与 FAQ 检索 → L3 模型分类/UNKNOWN 固定话术。
- `KnowledgeStore`：Milvus 检索意图语料和 FAQ 问法，Redis 保存精确匹配、版本指针和答案变体；memory 模式使用同源目录在进程内模拟。
- 消息级引用、`action_links`、SSE 输出；JWT/本地凭证、会话归属、消息幂等、会话锁和审计。
- 内存或 PostgreSQL 会话存储、内存或 PostgreSQL checkpoint，分别配置。
- 独立只读 MCP/业务网关保留；当前聊天图不查询个人订单、设备或实时维修状态。维修进度问题提供售后页面入口。

当前没有草稿创建或查询接口、业务写入、生成式答案、混合召回、独立重排或父块回查。政策资料仍是模拟样例，正式政策和官方入口需业务提供。

## 政策适用条件

国家固定 CN；政策条件是 `goodsid`（三级品类）和 `sales_channel`。型号、SKU、地区和日期不参与政策过滤。

| goodsid | 三级品类 |
| --- | --- |
| 65539 | 手机 |
| 65540 | 电脑 |
| 65541 | 空调 |
| 65542 | 冰箱 |
| 65543 | 洗衣机 |

渠道：`MALL` 商城、`DIRECT` 自营店、`AUTHORIZED` 授权店。当前只有手机三渠道样例；编码存在不代表已有对应政策。

网页默认提交手机和商城；API 缺少品类、渠道时仍会追问。系统自动获取可信订单上下文是后续设计。接口显式传入非 CN 国家会被拒绝；历史非大陆会话需新建，历史记录不会被重标为 CN。

## 离线启动

从仓库根目录执行，显式覆盖可能遗留的配置：

```bash
export GLOBALCARE_ENVIRONMENT=local
export GLOBALCARE_AUTH_MODE=demo
export GLOBALCARE_LLM_MODE=mock
export GLOBALCARE_EMBEDDING_MODE=hash
export GLOBALCARE_EMBEDDING_MODEL=BAAI/bge-m3
export GLOBALCARE_EMBEDDING_DIMENSIONS=1024
export GLOBALCARE_POLICY_BACKEND=memory
export GLOBALCARE_POLICY_ALLOW_FALLBACK=true
export GLOBALCARE_PERSISTENCE_MODE=memory
export GLOBALCARE_CHECKPOINTER_MODE=memory
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python -m uvicorn agent_service.app.main:app --host 127.0.0.1 --port 8000
```

在另一个终端启动网页：

```bash
(cd frontend && python3 -m http.server 4173 --bind 127.0.0.1)
```

配置 `GLOBALCARE_PURCHASE_URL`、`GLOBALCARE_AFTERSALES_URL` 为业务确认的 HTTPS 地址；为空时说明入口不可用，不猜测 URL。

`.env.example` 仍含旧 Embedding 模型、256 维和旧向量库说明，不能直接作为当前 Milvus 配置使用。真实密钥只放环境变量，不提交 `.env`。

## Milvus、Redis 与 PostgreSQL

项目 compose 管理 PostgreSQL（宿主机 55432）和 Redis（56379）；Milvus 需单独运行，默认地址 19530，database 为 `ai_customer_service`。

在停用相关演示写入、确认目标实例后执行：

```bash
docker compose up -d
export GLOBALCARE_DATABASE_URL='postgresql+psycopg://postgres:postgres@127.0.0.1:55432/globalcare_agent'
export GLOBALCARE_REDIS_URL='redis://127.0.0.1:56379/0'
export GLOBALCARE_MILVUS_URI='http://127.0.0.1:19530'
export GLOBALCARE_MILVUS_DB=ai_customer_service
export GLOBALCARE_EMBEDDING_MODE=huggingface
export GLOBALCARE_EMBEDDING_MODEL=BAAI/bge-m3
export GLOBALCARE_EMBEDDING_DIMENSIONS=1024
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/init_postgres.py
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/seed_milvus_redis.py
export GLOBALCARE_POLICY_BACKEND=milvus
export GLOBALCARE_POLICY_ALLOW_FALLBACK=false
export GLOBALCARE_PERSISTENCE_MODE=postgres
export GLOBALCARE_CHECKPOINTER_MODE=postgres
```

之后按前述命令重启 FastAPI。语义 Embedding 初始化固定使用 CPU；需具备相关依赖和模型文件。建库与运行时的模型、模式和维度必须一致。

`init_postgres.py` 执行全部 SQL 迁移，其中 `005_drop_policy_documents.sql` 会删除旧模拟政策表。seed 会重建 `intent_corpus`、`faq_question`，并清空重写 Redis 的 `gc:faq:*`。这些是有写入的初始化操作，本次文档同步未执行。

PostgreSQL 镜像仍带 vector 扩展，初始化 SQL 也仍创建该扩展；当前应用不使用它检索知识。这不表示已替换容器镜像或移除全部历史兼容字段。

## 验证

无需数据库的测试：

```bash
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python -m pytest -q agent_service/tests --ignore=agent_service/tests/test_postgres_conversations.py --ignore=agent_service/tests/test_fault_recovery.py
```

全量 Python 测试中的会话恢复/故障恢复模块需要独立 PostgreSQL 测试库；详细命令与本轮结果见[测试说明与报告](测试说明与报告.md)。

运行中的 FastAPI 可用 `scripts/http_e2e_smoke.py` 验证政策、入口、SSE、幂等和审计；当前客服 E2E 不需要启动 MCP 或 Java 服务。本轮未执行该 HTTP E2E。当前工作区没有 `domain_service/`，不能直接执行历史 Maven 启动和测试命令。

## 文档

- [当前业务流程](当前项目业务逻辑-从用户输入到回复.md)
- [当前技术方案](技术方案.md)
- [测试说明与报告](测试说明与报告.md)
- [后续完整 RAG 设计](docs/design/ecommerce-rag-20261013/技术方案-详细实现版.md)
- [图示说明与历史资产边界](diagram/current-agent-flow说明.md)
