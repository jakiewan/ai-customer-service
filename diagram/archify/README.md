# GlobalCare Archify 图集

本目录使用 Archify 2.16 为当前 `ai-customer-service` MVP 生成 5 类可交互 HTML 图。每张图的 JSON 是可复用规格，HTML 是经过 showcase 校验和桌面尺寸检查的交付物。

| 图类型 | 说明 | JSON 规格 | HTML 交付物 |
|---|---|---|---|
| Architecture | Vue、FastAPI、LangGraph、LangChain、pgvector、MCP、Spring Boot 的运行架构与写入边界 | [globalcare-architecture.json](globalcare-architecture.json) | [globalcare-architecture.html](globalcare-architecture.html) |
| Workflow | 意图分层、政策/业务证据、Case Draft 校验与 `human_required` 分支 | [globalcare-workflow.json](globalcare-workflow.json) | [globalcare-workflow.html](globalcare-workflow.html) |
| Sequence | 网页消息到政策检索、MCP 查询、Case Draft 写入和 SSE 返回的时序 | [globalcare-sequence.json](globalcare-sequence.json) | [globalcare-sequence.html](globalcare-sequence.html) |
| Data flow | 用户消息、Agent 状态、政策证据、业务事实、草稿和审计事件的数据流 | [globalcare-dataflow.json](globalcare-dataflow.json) | [globalcare-dataflow.html](globalcare-dataflow.html) |
| Lifecycle | Agent 会话从接收、路由、检索到回复、草稿终点和安全终点的生命周期 | [globalcare-lifecycle.json](globalcare-lifecycle.json) | [globalcare-lifecycle.html](globalcare-lifecycle.html) |

## 验证摘要

- 5/5 图通过 `quality_profile=showcase` 校验，每张均为 9/9 artifact checks、0 errors、0 warnings。
- 架构图额外校验了当前 Git 仓库 `22e24b4da0f738c2339f4b4c119485f81fdee5ae` 的源码路径证据。
- 5/5 图通过 1440×900、1600×1000、1920×1080、2048×1320 的浅色/深色自动视觉检查，无溢出。
- 已抽查 5 张 1440×900 浅色截图；深色截图已自动捕获并通过尺寸、可读性和 Viewer Chrome 检查。

自动检查产生的截图、contact sheet 和 JSON receipt 也保存在本目录，文件名以 `.visual-check.*` 开头。
