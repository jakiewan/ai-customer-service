# Agent 生产关键能力改造：PR 验收报告

分支：feature/agent-production-hardening；目标：master。只改 Agent 及必要的联调页面，不创建正式 Case/Work Order，不增加坐席系统或新的 Agent 平台。

## 1. 当前实现与可核验依据

| 关键能力 | 实现 | 验证证据 |
|---|---|---|
| 身份与隔离 | 现有登录系统 RS256 JWT，固定算法、issuer/audience、时效及身份字段；所有者由 issuer/tenant/sub/customer 稳定散列确定 | test_production_auth.py：刷新保留身份、错误 claims、跨用户/租户、业务越权拒绝 |
| 业务事实校验 | Pydantic DTO、查询标识、所有市场与订单/SN 关联、客户/租户；草稿回执 UUID/日期/状态/布尔值/指纹校验 | test_business_validation.py：真实 HTTP 模拟错误事实和回执，均不报告草稿成功 |
| 会话持久化 | PostgreSQL 保存会话、消息、草稿缓存、请求结果与审计；支持增量迁移 | test_postgres_conversations.py：独立 App 重建后读历史/草稿/审计并重放 |
| 并发与幂等 | 同会话全轮串行、跨实例 session advisory lock、请求 ID 绑定正文/市场/语言、完成后重放 | 两实例并发五次只有一组消息/一次完成审计；更改请求内容拒绝 |
| 写入恢复 | 外部写入前持久化草稿意图，确认后保存回执；未知结果不自动重试；指纹使用规范 JSON 而非分隔符拼接 | test_run_limits.py：写入后步骤失败/写入超时、换消息 ID、重启均不第二次写入 |
| 模型不等于授权 | 模型只能建议意图；订单/SN 必须来自原话或已确认上下文；购买市场不从订单前缀或模型猜测；新设备追问、取消/流程咨询不触发写入 | test_model_write_safety.py、test_graph_flow.py：模拟错误模型结构化输出及跨轮正/负例 |
| 运行限制 | 每节点执行前检查步数，业务调用计数，单轮截止时间、锁等待上限、每进程并发槽、checkpoint 最近 20 条消息；模型输出请求上限 512 | test_run_limits.py、test_fault_recovery.py、test_model_factory.py |
| 检索与回答 | LangChain/pgvector；严格模式不降级；元数据、市场二次校验；只摘录实际政策；生产要求 approved 且非 simulated | test_strict_policy.py；20 项真实 pgvector 正/负例及原有 RAG 矩阵 |
| 工具层 | 三种只读 MCP；发现失败可恢复，发现/调用均限时，服务凭证；HTTP 业务也可携带服务凭证；不自动重试写入 | test_mcp_protocol.py：真实 MCP 进程、HTTP 协议、超时、错误结果、鉴权 |
| 错误闭环 | 成功回执和完成审计与结果同事务提交；提交后才发回复/草稿/done；图失败可重放，超时未知有审计 | test_postgres_conversations.py、test_fault_recovery.py |
| Readiness/生产门禁 | 实际查询存储和依赖，失败返回 503；禁止生产 Demo/Mock/Hash/内存/非 HTTPS/示例 DB 凭证/未审核模拟政策 | test_production_gates.py：逐项负例、MCP/pgvector 故障与隐私错误信息检查 |
| 网页与 CI | 网页消费 error/done 并显示追踪号，不伪称检索后端；PR 自动启动独立 pgvector 后回归 | 本地浏览器实际操作截图；.github/workflows/agent-ci.yml，实际云端结果以 PR Checks 为准 |

### 与旧矩阵不同的行为

旧用例曾从 ORD-CN 前缀猜购买国并在 MY 会话直接生成 CN 草稿。已改为依据业务事实核验：请求市场与业务市场冲突拒绝，明确选择 CN 后正常受理。该用例改为负例，不是跳过失败；新增了购买国补充后继续受理的正例。

取消是停止本次 Agent 新写入，不表示已取消下游正式维修；草稿存在也不表示正式维修已受理。进度来自既有业务记录。人工入口仍为独立传统通道，没有实际转接实现。

## 2. 验证范围与复现

使用独立 globalcare_hardening_test 数据库，不删除现有业务数据；测试服务用临时端口并自动结束。执行：

~~~bash
GLOBALCARE_TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@127.0.0.1:55432/globalcare_hardening_test' python scripts/run_regression_loop.py
python -m compileall -q agent_service/app agent_service/mcp_server.py
git diff --check
~~~

每条参数化用例独立计数；三轮结果在最终提交/PR 中报告。模型使用 Mock 或真实 HTTP 协议的模拟提供方；JWT 使用测试生成 RSA 签发端；业务使用 Mock/HTTP 模拟服务；PostgreSQL/pgvector、MCP 协议和网页操作是真实执行。

当前项目没有配置 MiniMax API Key，本轮不宣称真实 MiniMax 连接已通过。HuggingFace 语义模型实际加载/质量、真实公司登录与业务接口、生产容量/HA/安全审计均未在此仓库环境验收。

![网页错误原因和追踪号可见](frontend-budget-error.jpg)

## 3. 联调契约补充

原 REST 路径与聊天入参未替换。开发模式保留 X-Demo-Token；JWT 模式只接受 Authorization: Bearer，不接受客户端自报用户/租户身份。

| JWT claim | 类型 | 必需 | 说明 |
|---|---|---|---|
| iss / aud | string / string 或标准 audience 数组 | 是 | 与配置严格匹配 |
| sub / tenant_id / customer_id | string | 是 | 非空且不超过 128 字符，来自现有身份系统签名 |
| iat / exp | JWT NumericDate | 是 | 验证签发时间和过期时间；nbf 若存在也验证 |

| SSE error.data 字段 | 类型 | 说明 |
|---|---|---|
| code | string | 可识别错误类型 |
| message | string | 可向用户展示的安全说明，屏蔽原始内部异常/SQL/凭证 |
| retryable | boolean | true 仅表示请求可以稍后再试，不表示写操作自动重试 |
| trace_id | string | 查询对应审计记录 |

| 新增错误 code | 行为 |
|---|---|
| AGENT_BUSY / CONVERSATION_BUSY | 未接受执行的容量或会话忙；后续可重试 |
| IDEMPOTENCY_CONFLICT | 同 ID 不同正文/市场/语言拒绝，不覆盖原结果 |
| REQUEST_INDETERMINATE | 遗留请求没有确定结果，不自动再次执行 |
| AGENT_RUN_TIMEOUT | 单轮超时；结果需核对，不能当下游写入失败 |
| DRAFT_WRITE_INDETERMINATE / DRAFT_CONFLICT | 草稿写账本或回执归属冲突，停止继续自动写入 |
| TOOL_INVALID_RESPONSE / BUSINESS_FORBIDDEN | 图内拒绝无效或越权事实，不创建草稿 |

后两行属于业务节点的 error_code/审计，可通过 human_required 与安全说明反馈，不保证每个业务拒绝都发送 SSE error。已经开始 SSE 的失败通过 error/done 表达，不能再改变此前的 HTTP 200。

政策证据新增 retrieval_backend、degraded、score_kind；score_kind 明确为 token_overlap_heuristic，分数不是答复正确概率，也不是 RAG 质量指标。

## 4. 部署与迁移

1. 备份目标数据库；先用 scripts/init_postgres.py 执行 001/002/003。新增 state/请求结果/写账本表，无自动删除或历史回填。
2. 配置 persistence_mode=postgres、checkpointer_mode=postgres，启动时验证表和连接。专用数据库账号按部署权限配置，不使用默认 postgres 示例账户作为生产业务账户。
3. 真实登录系统提供 RS256 公钥、issuer、audience，映射 sub/tenant/customer；公钥轮换由部署配置负责。Agent 不签发生产凭证。
4. HTTP 业务与 MCP 提供至少 32 字符服务凭证，使用明确 HTTPS 地址；业务接口须返回可信客户/租户、订单/SN、购买市场、型号和进度字段。缺字段拒绝，不伪造。
5. 发布非模拟已审核政策，包含 policy_id/title/market/language/version/approved 元数据。当前单位文档不超过 4000 字符；长政策必须先离线切分，安全警告与排查步骤不能分离。未提供通用文档上传/审批后台。
6. 切换真实 HuggingFace Embedding 和正确维度，使用独立生产数据库/collection 初始化并 seed；禁止把已有 Hash 向量当语义向量复用。不自动清空旧 collection。
7. 配置 environment=production（staging 也使用相同 Agent 门禁）、policy_allow_fallback=false、实际 MiniMax 配置、明确 HTTPS CORS 来源、真实健康检查路径；默认 business_health_path 是模拟 Java 的只读服务网点接口，生产应覆盖。
8. 完成公司接口/模型 smoke、负载和权限验收，再决定生产发布；/readyz 成功不代表模型实际在线或部署已上线。

回退：保留新增表和写账本，不删除。功能可切回开发模式用于模拟联调，但生产不能以关掉门禁作为恢复方案。旧版本忽略写账本，会失去未知写入保护；存在未确认写入时应先核对下游，不直接回退旧版本重复操作。

## 5. 明确边界与风险

- 内存模式只服务本地单进程演示；生产必须用 PostgreSQL。每进程并发槽不是跨集群全局限额，网关限流、资源与连接预算由部署方配置。
- 写账本阻止自动重复，但不凭空提供跨 Java 系统的分布式事务或 exactly-once。未知结果需人工/运维核对真实下游，尚未提供自动对账或审批恢复后台。
- 回复是证据原文摘录和业务事实模板，不是自由生成 RAG、模型实时 token 流、GraphRAG、多 Agent 或长期记忆。输入 token 预算是近似值，不代表账单计量；输出限制依赖提供方支持。
- 完整历史/审计尚无分页、清理与数据保留作业；大规模生产需补容量验收、保留/隐私制度和运维指标。未提供负载收益或真实线上准确率。
- 默认网页为开发联调 Demo，不是生产登录客户端；企业前端使用 JWT 接口，不把共享 Demo Token 部署出去。
- 默认政策、订单、设备和 Java 服务为模拟。生产启动门禁拒绝这些默认组合，但配置通过本身不等于真实依赖、数据或权限已验收。

## 6. 面试可讲的真实表述

“这是我基于售后 Case 受理流程独立实现的 Agent 项目。业务使用脱敏模拟数据，我重点把 Agent 侧的生产关键控制做实：模型建议与业务授权分离、市场和身份校验、受控草稿写入、跨实例幂等与故障恢复、严格检索、状态审计和持续回归。测试和代码、PR 都可以核验；真实公司登录、模型与业务接入以及线上容量并不是本仓库已经证明的结果。”

可展开的细节：为什么不能把写工具暴露给模型；为什么 checkpoint 不等于业务持久化；为什么超限必须在执行前检查；为什么同消息 ID 的幂等还需要草稿写账本；为什么未知结果不能自动重试；为什么必须测试 RAG 负例，而不是只看命中率。
