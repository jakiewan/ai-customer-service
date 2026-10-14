-- 增量迁移：保留旧数据；会话状态与请求结果独立于 LangGraph checkpoint。
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS state JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS agent_message_requests (
    conversation_id UUID NOT NULL REFERENCES conversations(conversation_id),
    client_message_id VARCHAR(64) NOT NULL,
    request_fingerprint VARCHAR(64) NOT NULL,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, client_message_id)
);
CREATE INDEX IF NOT EXISTS messages_conversation_time_idx ON conversation_messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS audits_conversation_time_idx ON agent_audit_events(conversation_id, created_at);
