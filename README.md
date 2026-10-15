# GlobalCare 商品与售后咨询 Agent

网页客服通过四层路由处理保修、故障排查和官方办理入口。仅服务中国大陆，market 固定 CN；政策按三级品类 goodsid 和 sales_channel 匹配。

## 当前运行

打开 **http://127.0.0.1:18000/**。FastAPI 同源提供网页和 API，JWT HttpOnly Cookie 用于会话鉴权。8000 的既有 Attu 服务保留。

本轮实际启用：

- MiniMax-M3，temperature 0.1，thinking disabled，输出上限 512 token。
- HuggingFace BGE-M3 dense 1024 维，CPU。
- Milvus 两个 collection：intent_corpus、faq_question，Top 10、COSINE。
- Redis 精确匹配、版本指针、答案变体。
- PostgreSQL 会话、消息、审计、消息幂等结果及 LangGraph checkpoint。
- 模型和知识依赖禁止静默降级。

私密运行值存于被 Git 忽略的 .env，不提交密钥或 JWT。当前票据由本地运行入口提供，不代表已经接入公司登录系统。

从仓库根目录启动：

```bash
PYTHONPATH=. HF_HUB_OFFLINE=1 HF_HUB_CACHE='/Volumes/SSD-工作/models/caches/hf-cache/hub' /opt/anaconda3/envs/ai-base/bin/python -m uvicorn agent_service.app.main:app --host 127.0.0.1 --port 18000
```

需先提供 .env 中的模型密钥、公钥/issuer/audience 和有效网页 JWT。该命令复用已初始化的真实服务，不重建知识索引。当前网页使用同源接入，无需单独启动 4173 静态服务。

## 业务流程

六节点：classify_intent、collect_info、retrieve_policy、resolve_link、unknown、render_response。

- L0：明确 SOP、否定办理、问候与能力介绍，以及不支持范围判断。
- L1：归一化问句 SHA-256 精确匹配，得到 intent/group/item。
- L2：意图检索聚合、FAQ 组内检索、阈值和分差检查。
- L3：真实模型分类，继续知识、入口或固定能力回应。

FAQ 缺条件时追问，纯条件补充沿用原问题，完整新诉求重新路由；候选接近时给用户选择。页面同步后端最新品类和渠道。

当前目录覆盖 5 品类、3 渠道，10 FAQ、82 条意图语料、64 条 FAQ 问法、63 个精确索引和 102 个答案变体。资料仍保留项目来源与 simulated 标记，正式品牌审批未完成，不通过改标签声称生产政策。

| goodsid | 品类 |
| --- | --- |
| 65539 | 手机 |
| 65540 | 电脑 |
| 65541 | 空调 |
| 65542 | 冰箱 |
| 65543 | 洗衣机 |

渠道为 MALL、DIRECT、AUTHORIZED。当前不按 SKU、型号、地区或日期选政策，不查个人订单或实时维修状态，不执行业务写入。购买/售后/进度返回配置的官方页面，由用户自行办理。

## 验证与复现

```bash
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/live_e2e_loop.py --rounds 3 --output-dir docs/review/20261015-runtime/handoff
PYTHONPATH=. GLOBALCARE_BASE_URL=http://127.0.0.1:18000 /opt/anaconda3/envs/ai-base/bin/python scripts/http_e2e_smoke.py
```

本轮最终 HTTP 3×49=147 次通过；全量 Python 113 项通过；实际服务重启历史与幂等重放通过。命令、环境边界与场景记录见[运行记录](docs/review/20261015-runtime/README.md)和[测试说明](测试说明与报告.md)。本轮不把这些结果当作全部问法、正式知识或生产环境验收。

## 初始化边界

PostgreSQL 宿主机 55432，Redis 回环 56379，Milvus 独立 19530、database ai_customer_service。

`scripts/init_postgres.py` 执行全部迁移，005 会删除旧模拟政策表。`scripts/seed_milvus_redis.py` 重建两个 collection 并清空重写 gc:faq:*；建库与查询必须同模型、同维度。初始化会写入存储，应确认目标并先备份。本轮已备份后执行知识初始化，未重启其他 Docker 服务。

compose 仍使用带 vector 扩展的 PostgreSQL 镜像，但当前应用不走该知识检索路径。历史引用兼容字段保留。

## 文档与答辩

- [技术方案](技术方案.md)
- [当前业务流程](当前项目业务逻辑-从用户输入到回复.md)
- [当前架构图](diagram/current-runtime-20261015/architecture.html)
- [飞书方案](https://mia5mm2sp4l.feishu.cn/docx/ScqydKIMWoXVtFxwMQGcNHZPn2e)
- 答辩材料：答辩材料/2026-10-15/交付-真实链路，含 16 页 PPT 和逐页讲稿。

运营后台、独立知识 database、不可变发布快照与统一版本指针是后续配套设计，当前不宣称已实现。
