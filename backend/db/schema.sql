-- ============================================================
-- Creative Automation Pipeline — Supabase Schema
-- ============================================================
-- Run via: supabase db push  OR  psql -f schema.sql

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── campaigns ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaigns (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    brand        TEXT NOT NULL,
    brand_config JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── runs ─────────────────────────────────────────────────────
-- One row per pipeline execution (one per brief submission)
CREATE TABLE IF NOT EXISTS runs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id      UUID REFERENCES campaigns(id) ON DELETE CASCADE,
    status           TEXT NOT NULL DEFAULT 'PENDING',
    -- PENDING | RUNNING | PENDING_REVIEW | COMPLETE | FAILED | REJECTED
    review_score     FLOAT,           -- HITL confidence score (0–1), set by review_gate node
    reviewer_notes   TEXT,            -- human reviewer notes from POST /api/runs/{id}/review
    provider_image   TEXT NOT NULL DEFAULT 'gemini',
    provider_llm     TEXT NOT NULL DEFAULT 'gemini',
    brief            JSONB NOT NULL DEFAULT '{}',
    run_report       JSONB,
    error_message    TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ
);

-- ── run_events ───────────────────────────────────────────────
-- One row per node execution event — drives Supabase Realtime UI updates
CREATE TABLE IF NOT EXISTS run_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_name   TEXT NOT NULL,
    status      TEXT NOT NULL,
    -- STARTED | COMPLETED | FAILED | SKIPPED
    payload     JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for fast Realtime filter queries
CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON run_events(run_id);

-- ── assets ───────────────────────────────────────────────────
-- Metadata for every generated/composited image.
-- Actual files live in Supabase Storage: creative-assets/{run_id}/{product_id}/{market}/{lang}_{ratio}.png
--
-- The unique constraint on (run_id, product_id, market, aspect_ratio) enables
-- upsert semantics: composite node INSERTs with language='en', then localize
-- node UPSERTs to update language + storage_url with the final localized asset.
CREATE TABLE IF NOT EXISTS assets (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id            UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    product_id        TEXT NOT NULL,
    market            TEXT NOT NULL,
    aspect_ratio      TEXT NOT NULL,   -- 1:1 | 9:16 | 16:9
    language          TEXT NOT NULL DEFAULT 'en',
    storage_url       TEXT,            -- public URL from Supabase Storage
    storage_path      TEXT,            -- internal path in bucket
    prompt_hash       TEXT,            -- SHA256 of (product_id + market + prompt) for cache lookup
    reused            BOOLEAN NOT NULL DEFAULT FALSE,
    compliance_passed BOOLEAN,         -- set by compliance_post node
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Unique constraint required for upsert on_conflict clause
    CONSTRAINT uq_asset_per_run UNIQUE (run_id, product_id, market, aspect_ratio)
);

CREATE INDEX IF NOT EXISTS idx_assets_run_id ON assets(run_id);
CREATE INDEX IF NOT EXISTS idx_assets_prompt_hash ON assets(prompt_hash);

-- ── Enable Realtime ───────────────────────────────────────────
-- run_events: drives live PipelineTracker in the frontend
-- assets: drives live AssetGrid updates as each image is composited
-- runs: drives status badge updates
ALTER PUBLICATION supabase_realtime ADD TABLE run_events;
ALTER PUBLICATION supabase_realtime ADD TABLE runs;
ALTER PUBLICATION supabase_realtime ADD TABLE assets;

-- ── Row Level Security (enterprise-ready, disabled for POC) ──
-- Uncomment and configure for multi-tenant production deployment
-- ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE run_events ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE assets ENABLE ROW LEVEL SECURITY;
