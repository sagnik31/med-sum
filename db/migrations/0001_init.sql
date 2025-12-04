-- Enable extension for UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ==============
-- users
-- ==============

CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_number    TEXT NOT NULL UNIQUE,
    -- Optional extra fields for later
    full_name       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);

-- ==============
-- documents
-- ==============

CREATE TABLE IF NOT EXISTS documents (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    original_name    TEXT NOT NULL,          -- e.g. "report_2024_12_01.pdf"
    content_type     TEXT NOT NULL,          -- e.g. "application/pdf", "image/png"
    storage_path     TEXT NOT NULL,          -- where the file is stored (S3 key, local path, etc)

    status           TEXT NOT NULL DEFAULT 'uploaded',
    -- status: 'uploaded' | 'processing' | 'processed' | 'failed'

    uploaded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);

-- ==============
-- insights
-- ==============

CREATE TABLE IF NOT EXISTS insights (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

    status           TEXT NOT NULL DEFAULT 'pending',
    -- status: 'pending' | 'processing' | 'ready' | 'failed'

    summary_text     TEXT,               -- human-readable summary / main insight
    details_json     JSONB,              -- structured data from SLM/VLM
    error_message    TEXT,               -- if generation failed

    generated_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_insights_document_id ON insights(document_id);
CREATE INDEX IF NOT EXISTS idx_insights_status ON insights(status);