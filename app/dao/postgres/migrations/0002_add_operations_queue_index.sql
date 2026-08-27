-- Supports the Pool Manager's queue query:
--   SELECT ... FROM operations WHERE status = 'queued' ORDER BY created_at ASC
CREATE INDEX IF NOT EXISTS idx_operations_status_created_at
    ON operations (status, created_at);
