-- 外部写入前保留意图；未知结果不自动重试，已确认结果可跨消息/重启复用。
CREATE TABLE IF NOT EXISTS agent_draft_writes (
    draft_fingerprint VARCHAR(128) PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(conversation_id),
    payload JSONB NOT NULL,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
