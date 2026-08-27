-- Operations table: the persisted record of every scan/batch job.
-- This IS the work queue (status = 'queued') — no in-memory queue exists.
CREATE TABLE IF NOT EXISTS operations (
    operation_id  TEXT        PRIMARY KEY,
    status        TEXT        NOT NULL,
    batch_type    TEXT        NOT NULL,
    metadata      JSONB       NOT NULL DEFAULT '{}',
    result        JSONB,
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    log_path      TEXT
);
