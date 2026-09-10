"""把测试清单的实际结果按用例 ID 批量回填,生成 最新实测版本。"""
import re
from pathlib import Path

RESULTS = {
    # ============ TC-SYS ============
    "TC-SYS-001": (
        "✅ HTTP 200,响应:`{\"code\":\"OK\",\"data\":{\"status\":\"ok\",\"service\":\"globalcare-agent\",\"version\":\"0.1.0\"},\"trace_id\":\"...\"}`\n"
        "- 期望命中。"
    ),
    "TC-SYS-002": (
        "✅ HTTP 200,响应:`postgres=up, pgvector=up, mcp=up, model_config=configured`,全绿。\n"
        "- 期望命中。"
    ),
    "TC-SYS-003": (
        "⛔ 未执行(需要主动停掉 Spring Boot/MCP/pgvector 才能达到\"仅 FastAPI\"起点,且 busy 状态下这条用例不影响后续验证)。\n"
        "- 已通过 TC-SYS-002 看到 memory 模式可以在线,且 readyz 字段会如实显示 down/degraded(代码层 [main.py:_status_for_error](agent_service/app/main.py#L47) 与 readyz handler 已实现降级)。"
    ),
    "TC-SYS-004": (
        "✅ HTTP 200,7 个 path 全部是 GlobalCare:`/healthz`、`/readyz`、`/api/v1/conversations`、`/api/v1/conversations/{conversation_id}/messages/stream`、`/api/v1/conversations/{conversation_id}`、`/api/v1/conversations/{conversation_id}/audit`、`/api/v1/case-drafts/{case_draft_id}`。"
    ),
    "TC-SYS-005": (
        "✅ HTTP 200,响应头 `Access-Control-Allow-Origin: http://127.0.0.1:4173`、`Access-Control-Allow-Methods: GET, POST, OPTIONS`、`Access-Control-Allow-Headers` 含 `X-Demo-Token`。"
    ),
    # ============ TC-AUTH ============
    "TC-AUTH-001": (
        "✅ HTTP 401,`{\"code\":\"UNAUTHORIZED\",\"message\":\"UNAUTHORIZED\",\"retryable\":false,\"trace_id\":\"f1d1084b-...\"}`。\n"
        "- 期望命中。"
    ),
    "TC-AUTH-002": "✅ HTTP 401,`code=UNAUTHORIZED`。期望命中。",
    "TC-AUTH-003": (
        "✅ HTTP 200,`conversation_id=3aea0785-e2fe-4a01-9927-e94766111d69`(本次实测 UUID),`market=MY`,`status=AI_ACTIVE`。\n"
        "- 期望命中。"
    ),
    "TC-AUTH-004": "✅ HTTP 401,`code=UNAUTHORIZED`。期望命中。",
    # ============ TC-CONV ============
    "TC-CONV-001": (
        "✅ HTTP 200,不传 market/language 时 `data.market == null`、`data.language == null`、`status=AI_ACTIVE`。"
    ),
    "TC-CONV-002": (
        "✅ HTTP 200,`market=CN`、`language=zh-CN`、`status=AI_ACTIVE`。"
    ),
    "TC-CONV-003": (
        "✅ HTTP 400,`{\"code\":\"VALIDATION_ERROR\",\"message\":\"请求字段校验失败\",...}`。期望命中。"
    ),
    "TC-CONV-004": "✅ HTTP 400,`code=VALIDATION_ERROR`。期望命中。",
    "TC-CONV-005": "✅ HTTP 400,`code=VALIDATION_ERROR`。期望命中。",
    "TC-CONV-006": (
        "✅ HTTP 404,`{\"code\":\"CONVERSATION_NOT_FOUND\",\"message\":\"会话不存在\",...}`。期望命中。"
    ),
    # ============ TC-MSG ============
    "TC-MSG-001": (
        "✅ SSE 事件流:`AI_ACTIVE → RETRIEVING_POLICY → AI_ACTIVE`,`policy_evidence(MY-MOBILE-AFTERSALES-2026)`,**无 tool_call**,**无 draft_created**。\n"
        "- 拼接 reply 包含\"充电 30 分钟\"、\"长按电源键 10 秒\"。`done.status=AI_ACTIVE`。"
    ),
    "TC-MSG-002": (
        "✅ SSE 事件流:`policy_evidence(CN-MOBILE-AFTERSALES-2026)`,reply 包含\"12 个月\"、\"中国大陆手机售后保修政策(模拟)\"。`done.status=AI_ACTIVE`。"
    ),
    "TC-MSG-003": (
        "✅ SSE 事件流:`tool_call(query_repair_status, success)`,**无 draft_created**。\n"
        "- reply:\"我查询到订单 ORD******01 的当前维修状态:维修处理中。该状态是系统当前记录,不代表承诺完成时间。\"\n"
        "- 订单号正确脱敏。"
    ),
    "TC-MSG-004": (
        "❌ **Bug-007【多轮状态延续】** — 期望反问\"请提供订单号或设备序列号\",实际**直接创建草稿**(draft_id=`c65c28c7-...`)。\n"
        "- 根因:同一会话 CID_MY 上一轮(MSG-003)已提取 `order_id=ORD-MY-2001`,LangGraph checkpointer 在 state 中保留 `order_id`,`graph.classify_node` 用 `decision.order_id or state.get(\"order_id\")` 自动复用 → 缺字段校验放行 → 走完整 `build_case_draft → validate_draft → create_case_draft` 路径。\n"
        "- 复现:在 CID_MY 上按顺序 MSG-003(\"查询维修进度,订单号 ORD-MY-2001\") → MSG-004(\"我要申请售后维修\"),后者会直接建草稿而非反问。\n"
        "- 备注:从产品视角可解释为\"延续用户订单\",但与测试用例\"独立缺字段应反问\"的设计矛盾。"
    ),
    "TC-MSG-005": (
        "❌ **Bug-009【多轮意图分类覆盖】** — 期望继续 CASE_REQUEST 路径并创建草稿,实际走 TROUBLESHOOTING 路径,未创建草稿。\n"
        "- 现象:`message=\"订单号 ORD-MY-2001,手机无法开机\"` 包含\"无法开机\"关键词,`intent.classify_intent` 命中 TROUBLESHOOTING_TERMS,**先于** CASE 延续检查。\n"
        "- 期望 reply 包含\"已根据订单和政策信息保存 Case Draft\",实际 reply 是排查步骤(充电 30 分钟 / 长按电源键 10 秒)。\n"
        "- 复现:在已生成草稿的 CID_MY 上发\"ORD-MY-2001 无法开机\" — 不会续接 CASE_REQUEST。\n"
        "- 根因:[intent.py:classify_intent](agent_service/app/intent.py#L97) 的优先级是 HIGH_RISK → STATUS → CASE → TROUBLESHOOTING,延续逻辑在 [graph.py:101](agent_service/app/graph.py#L101) 仅在决策后生效,无法覆盖先验分类结果。"
    ),
    "TC-MSG-006": (
        "✅ SSE 事件流:`reuse_draft` 节点命中,reply = \"当前会话已经存在一份 Case Draft,我会复用它,不重复创建新的草稿。\",**无** `case_draft_created` 事件(已沿用 c65c28c7)。`done.status=CASE_DRAFT_CREATED`。"
    ),
    "TC-MSG-007": (
        "✅ SSE 事件流:`human_required(reason=\"没有匹配的政策证据\")`,reply 包含\"这次请求暂时无法基于可靠的政策或业务事实给出确定结论\"。`done.status=AI_CANNOT_RESOLVE`。"
    ),
    "TC-MSG-008": (
        "❌ **Bug-006【意图路由放过非手机产品】** — 期望 human_required,实际**复用已有草稿**(reuse_draft)。\n"
        "- 现象:`message=\"智能手表维修\"` 包含\"维修\"关键词,被 `intent.classify_intent` 命中 `CASE_TERMS`(优先级:TROUBLESHOOTING_TERMS 是\"故障\"\"坏了\",而 CASE_TERMS 含\"维修\"\"报修\")。\n"
        "- 然后 `graph.after_classify` 检测到 `state.case_draft_id` 已存在 → 直接返回 `reuse_draft`,**不**触发 `PolicyStore.retrieve` 中的 `UNSUPPORTED_PRODUCT_TERMS` 过滤。\n"
        "- 复现:在已有草稿的 CID 上发\"智能手表维修\",reply = \"当前会话已经存在一份 Case Draft,我会复用它\"。\n"
        "- 根因:LangGraph `after_classify` 在 [graph.py:341](agent_service/app/graph.py#L341) 优先匹配 `case_draft_id` 而非按意图分流;`PolicyStore.UNSUPPORTED_PRODUCT_TERMS` 仅在 RAG 阶段生效,无法反向阻断整条 CASE_REQUEST 路径。"
    ),
    "TC-MSG-009": (
        "❌ **Bug-006 同源** — 期望 GENERAL 回复引导(排查/政策/进度/草稿),实际**复用已有草稿**。\n"
        "- `message=\"你好\"` 应被分类为 `Intent.GENERAL`(无任何关键词命中),走 `render_response` 分支;但 `after_classify` 因 `case_draft_id` 已存在直接走 `reuse_draft`。"
    ),
    "TC-MSG-010": (
        "❌ **测试用例设计 bug**(不是系统 bug) — 期望市场不一致拒绝,实际创建草稿。\n"
        "- 根因:`message=\"我要申请售后维修,订单号 ORD-MY-2001\"` 文本中 `\bMY\b` 被 [intent.py:extract_context](agent_service/app/intent.py#L143) 匹配 → `decision.market=MY` → `state.market` 被覆盖为 MY → 与业务事实 market=MY 一致 → `graph.query_business_node` 的 MARKET_MISMATCH 检查未触发。\n"
        "- 实际 SSE 事件:`RETRIEVING_POLICY → QUERYING_BUSINESS_DATA(query_order) → BUILDING_CASE_DRAFT → VALIDATING_DRAFT → CASE_DRAFT_CREATED`,draft_id=`2de343c4-...`,market=MY。\n"
        "- 复现:在 CID_CN 上发\"我要申请售后维修,订单号 ORD-MY-2001\",会正常创建草稿。\n"
        "- 修正用例:若要触发 MARKET_MISMATCH,message 必须**不含** `MY`/`CN`/`马来西亚`/`中国`/`malaysia`/`china` 关键词,仅留订单号 — 此时 state.market 会保留 CN(从会话创建),但 query_business 拿到 ORD-MY-2001 → market=MY → 触发 mismatch。"
    ),
    # ============ TC-DRAFT ============
    "TC-DRAFT-001": (
        "✅ HTTP 200,响应 `data.case_draft_id=c65c28c7-bf5b-4f30-8746-871684d921dc`、`status=DRAFT_CREATED`、`market=MY`、`suggested_resolution=repair`、`review_required=true`、`policy_evidence` 非空(`MY-MOBILE-AFTERSALES-2026`)。"
    ),
    "TC-DRAFT-002": (
        "✅ HTTP 404,`{\"code\":\"CASE_DRAFT_NOT_FOUND\",\"message\":\"Case Draft 不存在\",...}`。期望命中。"
    ),
    "TC-DRAFT-003": "✅ HTTP 401,`code=UNAUTHORIZED`(实际响应中 `message=\"UNAUTHORIZED\"`)。期望命中。",
    "TC-DRAFT-004": (
        "✅ 间接验证 — Spring Boot `/internal/case-drafts` 首次返回 `idempotent_replay=false` + UUID,相同 fingerprint 二次返回 `idempotent_replay=true` + 同 UUID(见 TC-DOWN-005)。"
    ),
    "TC-DRAFT-005": (
        "⛔ 阻塞 — 用例要求修改 `business.py` 中的 payload,违反用户\"不修改代码\"约束;且即便能改,也需要 FastAPI 启动 + 重启,本轮不执行。"
    ),
    # ============ TC-AUDIT ============
    "TC-AUDIT-001": (
        "✅ HTTP 200,`data` 含 48 条事件,事件类型齐全:`MESSAGE_RECEIVED`、`GRAPH_NODE_COMPLETED`、`POLICY_EVIDENCE`、`TOOL_CALL`、`CASE_DRAFT_CREATED`、`AGENT_RUN_COMPLETED`。"
    ),
    "TC-AUDIT-002": "✅ HTTP 404(用全零 UUID 触发)。期望命中。",
    "TC-AUDIT-003": "✅ CID_MY 上跨多轮累计 48 条审计事件,证实跨消息累积有效。",
    # ============ TC-IDEMP ============
    "TC-IDEMP-001": (
        "✅ 第一次:首条 status `phase=AI_ACTIVE, message=正在理解你的售后问题`,正常返回排查步骤。\n"
        "   第二次(同 `client_message_id=replay-001`、同 message):首条 status `phase=AI_ACTIVE, message=幂等重放:返回上次处理结果`,reply 拼接后与第一次**完全一致**(包括\"充电 30 分钟\"等 token)。"
    ),
    "TC-IDEMP-002": (
        "✅ 同一 `client_message_id=replay-001`、message 改为\"查询维修进度\",**仍** 返回首次结果(\"充电 30 分钟\"),证明幂等键绑定的是固化结果,不重新评估 message。"
    ),
    # ============ TC-ERR ============
    "TC-ERR-001": (
        "✅ HTTP 400,`{\"code\":\"VALIDATION_ERROR\",\"message\":\"请求字段校验失败\",...}`(`message=\" \"` 纯空格被 Pydantic `strip_message` validator 拦截)。"
    ),
    "TC-ERR-002": (
        "✅ HTTP 400,`code=VALIDATION_ERROR`(缺 `client_message_id`,FastAPI Pydantic 拦截)。"
    ),
    "TC-ERR-003": (
        "❌ **Bug-010【错误码语义错位】** — 期望工具返回 `error_code=ORDER_NOT_FOUND` + `human_required(reason=\"订单不存在\")`,实际 `tool_call(query_order, success)` + `human_required(reason=\"订单所属市场与请求市场不一致\")`。\n"
        "- 现象:从 CID_ERR(创建时 market=MY)上发\"我要申请售后维修,订单号 ORD-NO-9999\",Spring Boot `/internal/orders/ORD-NO-9999` 真实返回 404 NOT_FOUND,但 SSE 流显示 `query_order` 是 `success`,最终走 MARKET_MISMATCH 路径。\n"
        "- 可能根因:**待排查** — 怀疑 FastAPI 当前 `BUSINESS_MODE` 是 mock 而非 http(实际 mock 的 MOCK_ORDERS 字典也没有 ORD-NO-9999);若是 mock,query_order 应该抛 BusinessGatewayError,但 log 显示 success,矛盾。**这是一个值得继续复现与修复的 bug**。\n"
        "- 复现:`curl -s -X POST \"http://127.0.0.1:8000/api/v1/conversations/$CID/messages/stream\" -H 'X-Demo-Token: globalcare-demo-token' -H 'Content-Type: application/json' -d '{\"client_message_id\":\"e003\",\"message\":\"我要申请售后维修,订单号 ORD-NO-9999\"}'`,观察 SSE 流中 `tool_call` 与 `human_required.reason`。\n"
        "- 备注:下次 cron 轮询时优先复现这条,定位 gateway 模式。"
    ),
    "TC-ERR-004": (
        "❌ **Bug-010 同源** — `message=\"查询维修进度 SN-XX-9999\"` 期望 `tool_call(query_repair_status, error_code=DEVICE_NOT_FOUND)` + reason \"设备不存在\",实际 `tool_call(... success)` + reason \"订单所属市场与请求市场不一致\"。"
    ),
    "TC-ERR-005": (
        "✅ HTTP 200(`/healthz` 忽略 `X-Request-Id`),响应头中未直接 echo `X-Request-Id`(无 trace_id 泄露),`trace_id` 仅在响应 JSON body 的 `trace_id` 字段中。\n"
        "- 备注:本条仅验证 /healthz 路径不主动透传用户输入,生产环境的 /api/v1 路径在 main.py 中由 `_trace_id` 控制,默认取 `X-Request-Id[:64] or uuid4()`。"
    ),
    "TC-ERR-006": (
        "✅ HTTP 400,`{\"code\":\"VALIDATION_ERROR\",\"message\":\"请求字段校验失败\",...}`(路径参数 `not-a-uuid` 被 Pydantic UUID 校验拦截)。"
    ),
    # ============ TC-DOWN ============
    "TC-DOWN-001": (
        "✅ HTTP 200,完整响应:`{\"order_id\":\"ORD-MY-2001\",\"customer_id\":\"CUS-MY-001\",\"market\":\"MY\",\"product_model\":\"Xiaomi 15\",\"serial_number\":\"SN-MY-2001\",\"purchase_date\":\"2026-03-12\",\"repair_status\":\"IN_REPAIR\"}`。"
    ),
    "TC-DOWN-002": (
        "✅ HTTP 200,完整响应:`{\"serial_number\":\"SN-CN-1001\",\"order_id\":\"ORD-CN-1001\",...,\"market\":\"CN\",\"product_model\":\"Xiaomi 15\",\"purchase_date\":\"2026-02-10\",\"repair_status\":\"RECEIVED\"}`。"
    ),
    "TC-DOWN-003": (
        "✅ HTTP 200,`status=RECEIVED`,`status_label=\"已收件,等待检测\"`。"
    ),
    "TC-DOWN-004": (
        "✅ HTTP 200,`status=IN_REPAIR`,`status_label=\"维修处理中\"`。"
    ),
    "TC-DOWN-005": (
        "⚠️ **用例本身设计 bug(Bug-002)+ Spring Boot SNAKE_CASE 命名约定** — 用例 payload 用 camelCase,Spring `application.yml` 配置 `property-naming-strategy: SNAKE_CASE`,期望 snake_case。改 snake_case 后:\n"
        "  - 首次:HTTP 200,`case_draft_id=\"94480ae1-...\"`,`idempotent_replay=false`,`status=DRAFT_CREATED`\n"
        "  - 二次(同 fingerprint):HTTP 200,**相同** UUID,`idempotent_replay=true`\n"
        "- 备注:**Agent 端不受影响** — `graph.build_case_draft_node` 构造的 draft 字典本身是 snake_case,与 Spring Boot 约定一致。"
    ),
    "TC-DOWN-006": (
        "⚠️ 状态码符合(404),但 **code 名不符**(Bug-003)。\n"
        "- 实际:`{\"code\":\"NOT_FOUND\",\"message\":\"Case Draft 不存在\"}`,期望 `CASE_DRAFT_NOT_FOUND`。\n"
        "- 根因:`GlobalExceptionHandler` 把所有 `DomainNotFoundException` 统一映射成 `NOT_FOUND`。"
    ),
    "TC-DOWN-007": (
        "✅ FastMCP 完整协议走通:initialize → tools/list → tools/call。\n"
        "- `serverInfo.name=globalcare-business-tools`,`version=2.14.7`,instructions 含\"GlobalCare 售后只读业务查询工具\"。\n"
        "- `exposed_tools=['query_order','query_device','query_repair_status']`,`has_create_case_draft=False`(权限边界硬隔离 ✅)。\n"
        "- `tools/call query_order(ORD-MY-2001)` 返回 `structuredContent` 与 mock 数据完全一致。"
    ),
    "TC-DOWN-008": (
        "⛔ 阻塞 — 用例要求修改 `ALLOWED_MODEL_TOOLS` 加入 `create_case_draft`,违反\"不修改代码\"约束。\n"
        "- 静态代码已确认权限边界 — 见 TC-DOWN-007 的 `exposed_tools` 不含 `create_case_draft`,且 `McpBusinessGateway.create_case_draft` 显式抛 `TOOL_FORBIDDEN`。"
    ),
    "TC-DOWN-009": (
        "✅ pgvector 直接检索通过:\n"
        "- `\"中国手机保修期多久\"/CN → 1 条,policy_id=CN-MOBILE-AFTERSALES-2026,market=CN,score=0.809`\n"
        "- `\"马来西亚手机无法开机\"/MY → 1 条,policy_id=MY-MOBILE-AFTERSALES-2026`\n"
        "- `\"退款政策\"/CN → 0 条(UNSUPPORTED_POLICY_TERMS 过滤生效)`"
    ),
    # ============ TC-UI ============
    "TC-UI-001": (
        "⛔ **能力阻塞** — 我无浏览器自动化工具(未配置 Playwright/Puppeteer MCP)。\n"
        "- 静态确认:`index.html` `title=GlobalCare Web · 售后智能客服`,`apiBaseUrl=http://127.0.0.1:8000`,`demoToken=globalcare-demo-token`。FastAPI 现已正常监听 8000(见 TC-SYS-001/002),Vue → Agent 链路理论上已恢复。"
    ),
    "TC-UI-002": "⛔ 同 TC-UI-001(能力阻塞,后端已恢复)。",
    "TC-UI-003": "⛔ 同 TC-UI-001。",
    "TC-UI-004": "⛔ 同 TC-UI-001。",
    "TC-UI-005": "⛔ 同 TC-UI-001。",
    "TC-UI-006": "⛔ 同 TC-UI-001。",
}


def main() -> None:
    src = Path("测试清单.md").read_text(encoding="utf-8")
    missing = []
    for case_id, result in RESULTS.items():
        pattern = rf"(### {re.escape(case_id)} .*?\n(?:.*?\n)*?- \*\*实际结果\*\*:\n)"
        m = re.search(pattern, src)
        if not m:
            missing.append(case_id)
            continue
        body = "\n".join(("  " + line) if line else "" for line in result.split("\n")) + "\n"
        src = src[:m.start()] + m.group(1) + body + src[m.end():]
    Path("测试清单.md").write_text(src, encoding="utf-8")
    print(f"已更新 {len(RESULTS) - len(missing)} 条;缺失 {len(missing)} 条")
    if missing:
        print("未匹配:", missing)


if __name__ == "__main__":
    main()
