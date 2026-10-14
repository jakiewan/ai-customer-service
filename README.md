# GlobalCare 商品与售后咨询 Agent

基于 FastAPI、LangGraph 和 LangChain 的网页客服。政策按国家、三级品类 goodsid 和销售渠道唯一匹配；购买、申请售后或寻找办理入口时返回已配置的固定 URL。用户在官方页面自行办理。

## 当前能力

- 政策 Document 加载、元数据校验、向量检索与三字段适用性复核。
- 五节点图：意图识别、必要信息追问、政策检索、入口选择、回复。
- 固定官方入口与消息级引用、action_links、SSE 输出。
- JWT、会话归属、消息幂等、同会话锁、内存/PostgreSQL 会话存储和审计。
- 原个人业务查询模块和 MCP 服务保留为独立只读服务，当前客服不依赖或调用它们。

本轮不新增 PostgreSQL 知识表。知识发布、Milvus 混合检索、独立重排及父块回查是后续方案，完成状态以源码及验证为准。当前政策是回归样例，尚无正式业务资料与官方 URL。

## 政策匹配

唯一键为 `market + goodsid + sales_channel`，不使用型号、SKU、地区或日期过滤政策。

| goodsid | 三级品类 |
| --- | --- |
| 65539 | 手机 |
| 65540 | 电脑 |
| 65541 | 空调 |
| 65542 | 冰箱 |
| 65543 | 洗衣机 |

渠道：`MALL` 商城、`DIRECT` 自营店、`AUTHORIZED` 授权店。当前仅有手机的 CN/MY 六组样例；其他品类资料缺失时明确返回无依据。

SKU 表示具体商品规格，不与三级品类 goodsid 混用。保修起算日期仍可出现在政策正文里，但不参与本轮政策版本筛选。

## 启动

```bash
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python -m uvicorn agent_service.app.main:app --host 127.0.0.1 --port 8000
(cd frontend && python3 -m http.server 4173 --bind 127.0.0.1)
```

从 `.env.example` 配置 `GLOBALCARE_PURCHASE_URL`、`GLOBALCARE_AFTERSALES_URL`，填业务确认的 HTTPS 地址；未配置则说明入口不可用。

使用既有 PostgreSQL 会话存储时先执行迁移：

```bash
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/init_postgres.py
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/seed_policy_pgvector.py
```

本轮只增加消息表的 `action_links` 字段，不增加知识表，不对历史数据执行 DROP。重新入库使现有检索后端具有 goodsid、sales_channel 元数据。

## 验证

```bash
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python -m pytest -q agent_service/tests
(cd domain_service && mvn -Dmaven.repo.local=.mvn/repository clean test)
PYTHONPATH=. /opt/anaconda3/envs/ai-base/bin/python scripts/http_e2e_smoke.py
```

HTTP 脚本默认访问 8000，可用 `GLOBALCARE_BASE_URL` 指向其他本地验收实例。真实模型、正式政策与上线收益需要另行验证。

## 文档

- [当前技术方案](技术方案.md)
- [本轮实施范围](docs/design/ecommerce-rag-20261013/实施范围-20261014.md)
- [后续完整 RAG 设计](docs/design/ecommerce-rag-20261013/技术方案-详细实现版.md)
