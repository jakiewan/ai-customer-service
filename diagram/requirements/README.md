# 需求图集历史资产与当前实现对照

> 说明核对日期：2026-10-15。本目录七张 SVG/PNG、manifest 和检查记录仍对应旧受理流程；历史源码快照为 b764afe（2026-10-10），不是当前工作区快照。
> 当前说明以[技术方案](../../技术方案.md)和[业务流程](../../当前项目业务逻辑-从用户输入到回复.md)为准。以下图片不能直接作为当前答辩现状图。

## 历史资产索引

| 图 | 历史维度 | 阅读入口 | 可编辑源图 |
| --- | --- | --- | --- |
| 01 | 旧项目价值、受理闭环与草稿边界 | [PNG](01-project-value.png) | [SVG](01-project-value.svg) |
| 02 | 旧组件、知识后端与业务读写 | [PNG](02-runtime-architecture.png) | [SVG](02-runtime-architecture.svg) |
| 03 | 旧意图、追问与草稿创建/复用 | [PNG](03-intent-routing.png) | [SVG](03-intent-routing.svg) |
| 04 | 旧维修受理、工具、账本与回执时序 | [PNG](04-request-sequence.png) | [SVG](04-request-sequence.svg) |
| 05 | 旧政策检索与数据保存 | [PNG](05-policy-and-data-flow.png) | [SVG](05-policy-and-data-flow.svg) |
| 06 | 旧身份、消息幂等与草稿写入保护 | [PNG](06-security-and-idempotency.png) | [SVG](06-security-and-idempotency.svg) |
| 07 | 旧状态和运行模式 | [PNG](07-state-and-runtime-modes.png) | [SVG](07-state-and-runtime-modes.svg) |

## 当前源码与重制要求

| 图 | 当前源码依据 | 重制时应采用的语义 |
| --- | --- | --- |
| 01 | [规则](../../agent_service/app/intent.py)、[图](../../agent_service/app/graph.py) | 政策/排查/入口/UNKNOWN 分流；终点为咨询答案或官方入口 |
| 02 | [装配](../../agent_service/app/main.py)、[知识端口](../../agent_service/app/knowledge_base.py) | Milvus + Redis；PostgreSQL 只保留会话/审计/checkpoint；MCP 独立 |
| 03 | [四层路由与六节点](../../agent_service/app/graph.py) | L0/L1/L2/L3、补条件续接、FAQ 候选澄清、固定入口和无证据提示 |
| 04 | [API](../../agent_service/app/main.py)、[SSE 服务](../../agent_service/app/service.py) | 会话归属、锁/指纹、图、保存结果、正文/done；不含业务写入 |
| 05 | [目录](../../agent_service/app/faq_catalog.py)、[检索](../../agent_service/app/knowledge_base.py) | FAQ 三元组、条件/版本答案变体、单条款证据与适用性复核 |
| 06 | [鉴权](../../agent_service/app/auth.py)、[Store](../../agent_service/app/store.py)、[PostgresStore](../../agent_service/app/postgres_store.py) | 消息 ID 绑定请求、同会话锁、结果重放、未知结果阻止重跑；无草稿账本 |
| 07 | [枚举](../../agent_service/app/constants.py)、[配置](../../agent_service/app/config.py)、[checkpoint](../../agent_service/app/checkpointer.py) | phase/status/result_type 分开；追问也可 COMPLETED；Store 与 checkpoint 分别启用 |

最终回复是政策条款原文和固定话术；模型只在 L3 分类。API 仍可追问品类/渠道，可信订单自动关联是规划。预发/生产配置门禁不代表生产能力已验收。

## 显示与维护

SVG 是源资产，PNG 是历史展示资产；manifest.json 和 render-check.json 记录当时尺寸、主题、来源和布局检查。Markdown 相对链接保留，不表示图片已经随源码变化更新。

后续若重制，应更新 SVG、源码依据与 manifest，重新渲染 PNG，检查箭头、文字边界及文档引用。本次不修改图形资产。

## 本轮验证范围

本轮只核对三份图示 Markdown 的当前说明、历史标识和链接。未重渲染七张图，也未重新执行其历史视觉/业务检查。

历史 69 项图集相关测试及源码一致性声明不作为本轮结论。本轮实际运行 实际 Python 和真实 HTTP 场景，外部依赖边界见[测试说明与报告](../../测试说明与报告.md)。当前工作区没有历史 Java 服务目录，已移除失效 Java 源码链接。

当前新架构见 [运行架构](../current-runtime-20261015/architecture.html)，历史图片不代表新图。
