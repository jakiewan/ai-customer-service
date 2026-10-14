# CLAUDE.md

本文件说明当前工作区的开发与核对规则。用户偏好中文；技术术语和代码标识符保留英文。

> 核对日期：2026-10-15。当前行为以源码为准；完整说明见[技术方案](技术方案.md)和[当前业务流程](当前项目业务逻辑-从用户输入到回复.md)。当前工作区包含未提交代码，不能只按 HEAD 判断实现。

## 当前业务契约

- 商品与售后咨询 Agent，仅服务中国大陆，`market` 固定 CN，不识别或追问国家。
- `goodsid` 表示三级品类；政策按固定 CN + `goodsid` + `sales_channel` 匹配。型号、SKU、地区、日期不参与政策过滤。
- 网页/API、文本抽取和已有状态提供品类、渠道；缺失时当前代码仍追问。可信订单上下文自动关联属于后续设计。
- 保修/排查输出已匹配 FAQ 的政策原文；购买、申请售后和维修进度通过已配置 URL 导航，用户在官方页面办理。
- 当前聊天图不查询个人订单、设备或实时维修状态，不创建受理草稿，不执行业务写入。
- 真实模型只在 L3 辅助结构化意图分类，不生成最终答案或 URL。

## 当前组件与数据职责

当前六节点：`classify_intent`、`collect_info`、`retrieve_policy`、`resolve_link`、`unknown`、`render_response`。

```text
Vue → FastAPI → ConversationService → LangGraph
                                   ├─ KnowledgeStore → Redis / Milvus
                                   ├─ AgentModel（L3 分类）
                                   └─ 固定入口配置
会话、消息、审计、幂等结果 → InMemoryStore / PostgresStore
图 checkpoint → MemorySaver / AsyncPostgresSaver
独立 MCP → Mock / HTTP 只读业务网关（当前聊天图不调用）
```

- L0：规则 SOP 路由及不支持范围判断。
- L1：标准问/相似问归一化 hash 精确匹配，得到 `intent_id / faq_group_id / faq_item_id`。
- L2：意图语料检索、按意图聚合、FAQ 组内检索、分差判断、答案变体适用性复核。
- L3：前三层未确定时由模型分类；无法确定则 `UNKNOWN` 固定话术。FAQ 关键词还可在 L2 未命中后提供规则方向。
- Milvus：`intent_corpus` 和 `faq_question`，默认 database `ai_customer_service`。
- Redis：`gc:faq:*` 精确索引、答案版本指针和答案变体。
- PostgreSQL：会话、消息、审计、请求指纹/结果与 checkpoint，不承担当前知识检索。

## 目录地图

| 路径 | 当前职责 |
| --- | --- |
| `agent_service/app/main.py` | FastAPI 接口、装配、readiness |
| `agent_service/app/graph.py` | 六节点图、四层路由、跨轮补条件、回复 |
| `agent_service/app/knowledge_base.py` | `KnowledgeStore`、Embedding、检索、答案审查、探测 |
| `agent_service/app/faq_catalog.py` | FAQ 目录与政策条款合成、hash/Redis key |
| `agent_service/data/faq/faq_catalog.yaml` | 意图语料、标准问、相似问、条款映射 |
| `agent_service/data/policies/*.md` | 中国大陆手机三渠道模拟政策源 |
| `agent_service/app/service.py` | 会话锁、幂等、SSE、审计、保存结果 |
| `agent_service/app/store.py`、`postgres_store.py` | 会话和消息级结果存储 |
| `agent_service/app/checkpointer.py` | 独立配置图 checkpoint |
| `agent_service/app/model_factory.py`、`intent.py` | 结构化意图与确定性分类/抽取 |
| `agent_service/mcp_server.py`、`app/business.py` | 独立只读工具及网关 |
| `agent_service/migrations/` | 会话/审计迁移；005 删除旧模拟政策表 |
| `scripts/seed_milvus_redis.py` | Milvus 两个 collection 与 Redis 数据初始化 |
| `frontend/index.html` | Vue 3 CDN 单页，POST + fetch 消费 SSE |
| `diagram/` | 图示资产；部分为历史快照，使用前查看对应说明 |

旧政策检索模块和旧 seed 脚本已经退出当前工作区。`domain_service/` 当前不存在；不得把历史 Java 路径写成当前可运行服务。

## 配置

配置权威来源是 `agent_service/app/config.py`。`.env.example` 仍有旧 Embedding 配置，使用时显式覆盖。

| 环境变量 | 代码默认值 | 用途 |
| --- | --- | --- |
| `GLOBALCARE_LLM_MODE` | mock | mock / minimax 分类 |
| `GLOBALCARE_EMBEDDING_MODE` | hash | 离线 hash / HuggingFace |
| `GLOBALCARE_EMBEDDING_MODEL` | BAAI/bge-m3 | 语义模型，代码固定 CPU |
| `GLOBALCARE_EMBEDDING_DIMENSIONS` | 1024 | 建库与查询维度一致 |
| `GLOBALCARE_POLICY_BACKEND` | memory | memory / milvus |
| `GLOBALCARE_POLICY_ALLOW_FALLBACK` | true | 是否允许知识后端降级 |
| `GLOBALCARE_MILVUS_URI` | http://127.0.0.1:19530 | 独立 Milvus |
| `GLOBALCARE_MILVUS_DB` | ai_customer_service | Milvus database |
| `GLOBALCARE_REDIS_URL` | redis://127.0.0.1:56379/0 | compose 的 Redis |
| `GLOBALCARE_PERSISTENCE_MODE` | memory | 会话 Store |
| `GLOBALCARE_CHECKPOINTER_MODE` | memory | 图 checkpoint，需另行设置 |
| `GLOBALCARE_DATABASE_URL` | 本机 5432/globalcare_agent | compose 实际暴露 55432，需覆盖 |
| `GLOBALCARE_PURCHASE_URL` / `GLOBALCARE_AFTERSALES_URL` | 空 | 业务确认的 HTTPS 入口 |
| `GLOBALCARE_AUTH_MODE` | demo | 本地共享凭证 / JWT |
| `GLOBALCARE_MAX_GRAPH_STEPS` | 12 | 节点执行上限 |
| `GLOBALCARE_TOKEN_BUDGET` | 4096 | 图输入消息字符数粗估 |
| `GLOBALCARE_AGENT_RUN_TIMEOUT_SECONDS` | 60 | 单轮总超时 |
| `GLOBALCARE_MESSAGE_LOCK_TIMEOUT_SECONDS` | 5 | 会话锁等待 |
| `GLOBALCARE_MAX_ACTIVE_RUNS` | 8 | 服务实例内活跃运行上限 |

`policy_top_k=3` 仍保留在配置类，但当前 FAQ 检索未使用；Milvus search 固定 limit=10，最终取一条答案或两个澄清候选。`max_tool_calls` 也不是当前六节点图的执行门禁，因为图不调用业务工具。

## 运行与验证

离线启动、真实知识后端初始化命令见[README](README.md)，验证记录见[测试说明与报告](测试说明与报告.md)。

```bash
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python -m uvicorn agent_service.app.main:app --host 127.0.0.1 --port 8000
(cd frontend && python3 -m http.server 4173 --bind 127.0.0.1)
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python -m pytest -q agent_service/tests --ignore=agent_service/tests/test_postgres_conversations.py --ignore=agent_service/tests/test_fault_recovery.py
/opt/anaconda3/envs/ai-base/bin/python -m compileall -q agent_service
git diff --check
```

全量 pytest 中两个数据库相关模块需要独立测试库，会执行 SQL 迁移，不能指向业务库。无需为文档同步执行数据库初始化或 seed。

## 安全与可靠性边界

- MCP 白名单只有 `query_order`、`query_device`、`query_repair_status`；`permissions.py` 只定义模型只读工具校验。当前图没有应用写入节点。
- 非空入口 URL 必须为有主机名、无内嵌凭证的 HTTPS 地址；官方域名由业务配置确认。
- 会话身份校验与消息指纹绑定必须保留。相同会话的同 ID/同请求完成后重放，不同请求冲突，已登记且无确定结果不盲目重跑。
- 内存会话锁只在进程内有效；PostgreSQL Store 使用 advisory lock。活跃运行上限不是集群限流。
- 资料加载检查 CN、品类、渠道、版本、标识、语言和内容，以及品类×渠道唯一性。预发/生产要求源资料 approved 且非 simulated；返回答案再次复核适用条件。
- 预发/生产要求 JWT、PostgreSQL Store/checkpoint、Milvus、HuggingFace、禁止 fallback、真实模型及相关 HTTPS/凭证条件。配置通过不代表真实服务或生产效果已验收。
- `/readyz` 返回 store/postgres/milvus/redis/knowledge_backend/model_config；memory 知识后端如实标 degraded，MCP 不是当前客服 readiness 依赖。
- 状态、证据事件可能在保存前发出；最终正文分块和成功 done 在保存结果后发出。SSE HTTP 200 不代表业务成功。
- `human_required` 只是人工服务提示，没有坐席转接或审批恢复。

## 已知边界与维护规则

- `RESUME_BLOCK_TERMS` 是补条件时阻止沿用旧路由的关键词；不是通用上下文理解。词表遗漏可能把新购买诉求续接为旧政策问题。
- 文本品类/渠道和页面选择可能不同步；下一轮页面再次提交的值可能覆盖此前文本条件。
- 当前只保留最近 20 条 checkpoint 消息；完整消息由 Store 保存。
- 图预算为输入字符粗估，不是实际模型和检索 token 账本。
- `PolicyEvidence` 仍接受历史 `pgvector` / `token_overlap_heuristic` 引用以读取旧消息；不表示当前后端仍提供该检索路径。compose 的旧镜像及迁移仍带 vector 扩展。
- 历史图、旧测试次数和旧验证截图不能当作当前运行证据。
- 按最小修改原则工作；架构调整前核对 `技术方案.md` 与 `SDD缺口清单.md`。本次文档同步不改变上述实现或配置。
