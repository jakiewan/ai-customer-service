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

CREATE TABLE IF NOT EXISTS policy_documents (
    policy_id VARCHAR(128) PRIMARY KEY,
    title VARCHAR(256) NOT NULL,
    market VARCHAR(8) NOT NULL,
    language VARCHAR(16) NOT NULL,
    version VARCHAR(64) NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(256),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS policy_documents_market_idx ON policy_documents(market);

CREATE TABLE IF NOT EXISTS case_drafts (
    case_draft_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(conversation_id),
    draft_fingerprint VARCHAR(128) NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_audit_events (
    audit_id UUID PRIMARY KEY,
    conversation_id UUID,
    trace_id VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    event_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
