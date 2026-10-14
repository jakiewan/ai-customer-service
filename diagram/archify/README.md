# GlobalCare Archify 历史图集

> 说明核对日期：2026-10-15。本目录的 5 类 JSON/HTML 和截图仍是旧 MVP 快照，含旧知识后端和受理草稿流程；不是当前工作区的运行架构。
> 当前说明见[技术方案](../../技术方案.md)、[业务流程](../../当前项目业务逻辑-从用户输入到回复.md)及[当前流程说明](../current-agent-flow说明.md)。

## 资产索引

| 图类型 | 历史内容 | JSON 规格 | HTML 交付物 |
| --- | --- | --- | --- |
| Architecture | 旧服务架构、知识向量库和业务写入边界 | [globalcare-architecture.json](globalcare-architecture.json) | [globalcare-architecture.html](globalcare-architecture.html) |
| Workflow | 旧政策/业务查询、草稿校验和人工提示 | [globalcare-workflow.json](globalcare-workflow.json) | [globalcare-workflow.html](globalcare-workflow.html) |
| Sequence | 旧网页到业务查询、草稿写入及 SSE 时序 | [globalcare-sequence.json](globalcare-sequence.json) | [globalcare-sequence.html](globalcare-sequence.html) |
| Data flow | 旧消息、政策、业务事实、草稿与审计流转 | [globalcare-dataflow.json](globalcare-dataflow.json) | [globalcare-dataflow.html](globalcare-dataflow.html) |
| Lifecycle | 旧会话状态、草稿和安全终点 | [globalcare-lifecycle.json](globalcare-lifecycle.json) | [globalcare-lifecycle.html](globalcare-lifecycle.html) |

## 与当前代码的差异

| 维度 | 当前实现 |
| --- | --- |
| 知识端口 | KnowledgeStore，Milvus intent_corpus/faq_question + Redis；离线 memory 同源模拟 |
| 路由 | L0 规则 → L1 精确匹配 → L2 意图/FAQ → L3 模型分类/UNKNOWN |
| 图节点 | classify_intent、collect_info、retrieve_policy、resolve_link、unknown、render_response |
| 业务终点 | 原政策条款或配置官方入口；用户自行办理 |
| 业务工具 | 保留独立 MCP；聊天图不查个人订单、设备、实时维修状态 |
| 持久化 | InMemoryStore/PostgresStore 与 MemorySaver/AsyncPostgresSaver 分别配置 |
| 结果传输 | 保存后发送分段正文和 done；没有草稿事件/查询接口 |
| 最终答案 | 应用摘录条款和固定话术；模型只做 L3 分类 |

当前源码入口：[main.py](../../agent_service/app/main.py)、[graph.py](../../agent_service/app/graph.py)、[knowledge_base.py](../../agent_service/app/knowledge_base.py)、[faq_catalog.py](../../agent_service/app/faq_catalog.py)、[service.py](../../agent_service/app/service.py)。

## 验证与维护

原交付使用 Archify 2.16，保留了 showcase 和多尺寸视觉检查 receipt。这些记录只证明当时资产的生成/布局检查，不能证明当前代码与图一致。本轮未重跑这些检查。

修改图片前需同时更新 JSON、HTML、源码证据与导出图，按实际工具流程重新验证。本次仅更新说明，不重制图，不将旧图标为当前已验证架构。

本轮代码测试和未验证项见[测试说明与报告](../../测试说明与报告.md)。
