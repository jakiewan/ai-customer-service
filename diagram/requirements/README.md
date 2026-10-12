# 需求文档当前实现图集

本图集说明 GlobalCare 的当前代码逻辑。每张图顶部包含“解决的问题”；正文区分已有代码机制、开发配置、可选集成和业务范围外的能力。图集不将需求目标或历史验证记录当作当前生产事实。

源码快照：[b764afe](https://github.com/jakiewan/ai-customer-service/tree/b764afeeaa7f62b57db2bc39ba78f359a081dec7)，核对日期 2026-10-10。该提交是当前业务源码最近一次变更；本地工作区与本次发布前远端 `master` 的 `agent_service/`、`domain_service/`、`frontend/`、`scripts/` 没有差异。

| 图 | 维度 | 阅读入口 | 可编辑源图 |
|---|---|---|---|
| 01 | 项目问题、能力、核心闭环与交付边界 | [高清 PNG](01-project-value.png) | [SVG](01-project-value.svg) |
| 02 | 服务组件、调用关系、读写边界及配置分支 | [高清 PNG](02-runtime-architecture.png) | [SVG](02-runtime-architecture.svg) |
| 03 | 意图路由、信息追问、新草稿与已有草稿路径 | [高清 PNG](03-intent-routing.png) | [SVG](03-intent-routing.svg) |
| 04 | 一次维修受理的 HTTP/MCP、账本、回执与 SSE 时序 | [高清 PNG](04-request-sequence.png) | [SVG](04-request-sequence.svg) |
| 05 | 政策准备、市场过滤、证据消费和业务数据保存 | [高清 PNG](05-policy-and-data-flow.png) | [SVG](05-policy-and-data-flow.svg) |
| 06 | 身份授权、消息幂等、草稿账本与未知写入保护 | [高清 PNG](06-security-and-idempotency.png) | [SVG](06-security-and-idempotency.svg) |
| 07 | 节点阶段、会话状态、失败出口及运行模式 | [高清 PNG](07-state-and-runtime-modes.png) | [SVG](07-state-and-runtime-modes.svg) |

## 源码证据

| 图 | 具体实现依据 | 图中需保持的关键语义 |
|---|---|---|
| 01 | [意图规则](../../agent_service/app/intent.py)、[状态图](../../agent_service/app/graph.py)、[会话服务](../../agent_service/app/service.py) | 政策、排查、进度、维修申请与一般引导分流；业务终点是 Case Draft |
| 02 | [应用装配](../../agent_service/app/main.py)、[模型工厂](../../agent_service/app/model_factory.py)、[业务网关](../../agent_service/app/business.py)、[MCP 工具](../../agent_service/mcp_server.py)、[域服务接口](../../domain_service/src/main/java/com/globalcare/domain/api/DomainController.java) | 模型只增强分类；查询由图节点调用；MCP 不暴露写入工具；网关、数据库与模型按配置启用 |
| 03 | [节点与条件路由](../../agent_service/app/graph.py)的 `classify_node`、`_missing_fields`、`after_classify`、`after_collect`、`after_policy`、`after_query`、`after_validate` | 超范围词路由优先；缺字段追问；进度查询跳过政策；同对象草稿复用；失败返回安全回复 |
| 04 | [API](../../agent_service/app/main.py)、[SSE 编排](../../agent_service/app/service.py)、[受控写入节点](../../agent_service/app/graph.py)、[HTTP/MCP 网关](../../agent_service/app/business.py) | 已有会话的成功场景；步骤可重复查询两种标识；确认账本后保存本轮结果；成功事件在保存结果后发送 |
| 05 | [政策加载与检索](../../agent_service/app/policy_store.py)、[状态与有界消息](../../agent_service/app/state.py)、[证据模型](../../agent_service/app/schemas.py)、[数据库迁移](../../agent_service/migrations/) | 当前处理短政策 Markdown；严格模式禁止静默降级；估算分数不代表模型置信度；业务 Store 与 checkpoint 职责不同 |
| 06 | [身份验证](../../agent_service/app/auth.py)、[权限](../../agent_service/app/permissions.py)、[内存存储](../../agent_service/app/store.py)、[PostgreSQL 存储](../../agent_service/app/postgres_store.py)、[指纹](../../agent_service/app/business.py)、[下游幂等](../../domain_service/src/main/java/com/globalcare/domain/service/DomainService.java) | 消息 ID 必须绑定相同有效请求；本轮事件据实重放；写入前留意图；未知结果阻止盲目重写；模拟 Map 不跨重启 |
| 07 | [枚举](../../agent_service/app/constants.py)、[图节点](../../agent_service/app/graph.py)、[会话结果](../../agent_service/app/service.py)、[配置门禁](../../agent_service/app/config.py)、[checkpoint](../../agent_service/app/checkpointer.py) | phase、conversation_status 和 done 不可混同；普通成功通常仍为 AI_ACTIVE；配置门禁不代表生产已验收 |

## 在线显示与维护

需求文档以 `![说明](diagram/requirements/文件名.png)` 嵌入同仓库图片。GitHub Markdown 可直接显示 PNG，不依赖 HTML、脚本、额外图床或 Cloudflare 账号。每张图另有高清 PNG 与 SVG 链接；下载仓库后相对路径仍可使用。后续移动文件时需同步修改引用路径。

SVG 是可编辑的源资产，PNG 是浏览器按 2 倍分辨率渲染的展示资产。每张 SVG 宽 1600，PNG 宽 3200；详细内容可打开高清链接放大。`manifest.json` 保存尺寸、主题及源码依据，`render-check.json` 保存浏览器测量的文字边界检查结果。

修改逻辑时先核对对应函数和调用者，再修改 SVG，重新用浏览器渲染 PNG，同时检查箭头、文字边界及 Markdown 引用。代码与图集是同仓库快照；业务逻辑变化后须同步更新图集，不能假设图片会自动更新。

## 本次验证范围

- 7 张 SVG 可被 XML 解析；7 张 PNG 已完成实际浏览器渲染。
- 使用 Chrome DevTools 测量 SVG 文字范围，检查画布和卡片内容是否越界；逐张查看渲染图，核对文字与关系。
- 检查 7 张嵌入图片及高清、SVG、源码链接均对应存在的文件。
- 相关离线测试：`test_graph_flow.py`、`test_permissions.py`、`test_intent_and_policy.py`、`test_model_write_safety.py`、`test_business_validation.py`，共 **69 passed**。
- `python -m compileall -q agent_service` 与 `git diff --check` 通过。

本次只新增图集和更新需求文档，未修改业务代码、接口、数据库、依赖或运行配置。未启动真实 MCP/HTTP 服务、PostgreSQL 或模型，未重新执行真实端到端或生产验收。自动文字检查不等于流程正确性证明，图中语义按上表列出的当前源码人工核对。
