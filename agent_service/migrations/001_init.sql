CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id UUID PRIMARY KEY,
    demo_subject_hash VARCHAR(128) NOT NULL,
    channel VARCHAR(32) NOT NULL,
    market VARCHAR(8),
    language VARCHAR(16),
    status VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(conversation_id),
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- policy_documents 已迁移到 Milvus，新库不再创建；存量库由 005_drop_policy_documents.sql 清理

CREATE TABLE IF NOT EXISTS agent_audit_events (
    audit_id UUID PRIMARY KEY,
    conversation_id UUID,
    trace_id VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    event_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
