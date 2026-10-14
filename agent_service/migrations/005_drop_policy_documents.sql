-- 政策检索已迁移到 Milvus（database ai_customer_service），PostgreSQL 不再承担向量检索。
-- 表内仅为 seed 生成的模拟政策，DROP 不可逆，按 2026-10-14 决策执行。
DROP INDEX IF EXISTS policy_documents_market_idx;
DROP TABLE IF EXISTS policy_documents;
