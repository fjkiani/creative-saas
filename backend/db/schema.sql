-- ============================================================
-- CreativeOS — Supabase Schema v4
-- ============================================================
-- Run via: supabase db push  OR  psql -f schema.sql

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── workspaces ───────────────────────────────────────────────
-- Multi-tenant workspace — one per team/brand
CREATE TABLE IF NOT EXISTS workspaces (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    TEXT NOT NULL,
    owner_user_id           TEXT NOT NULL,
    plan                    TEXT NOT NULL DEFAULT 'free',
    -- free | pro | agency | enterprise
    credits                 INTEGER NOT NULL DEFAULT 0,
    instagram_access_token  TEXT,
    instagram_user_id       TEXT,
    tiktok_access_token     TEXT,
    tiktok_client_key       TEXT,
    stripe_customer_id      TEXT,
    stripe_subscription_id  TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workspaces_owner ON workspaces(owner_user_id);

-- ── campaigns ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaigns (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    brand        TEXT NOT NULL,
    brand_config JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── runs ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS runs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id      UUID REFERENCES campaigns(id) ON DELETE CASCADE,
    workspace_id     UUID REFERENCES workspaces(id) ON DELETE SET NULL,
    status           TEXT NOT NULL DEFAULT 'PENDING',
    -- PENDING | RUNNING | PENDING_REVIEW | COMPLETE | FAILED | REJECTED
    review_score     FLOAT,
    reviewer_notes   TEXT,
    provider_image   TEXT NOT NULL DEFAULT 'gemini',
    provider_llm     TEXT NOT NULL DEFAULT 'gemini',
    brief            JSONB NOT NULL DEFAULT '{}',
    run_report       JSONB,
    error_message    TEXT,
    -- v4 fields
    video_mode       TEXT NOT NULL DEFAULT 'slideshow',
    -- slideshow | ai | none
    publish_platforms JSONB NOT NULL DEFAULT '[]',
    -- ["instagram", "tiktok"]
    scheduled_publish_time TEXT,
    -- ISO datetime or null
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ
);

-- ── run_events ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS run_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_name   TEXT NOT NULL,
    status      TEXT NOT NULL,
    -- STARTED | COMPLETED | FAILED | SKIPPED | PROGRESS
    payload     JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON run_events(run_id);

-- ── assets ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS assets (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id            UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    product_id        TEXT NOT NULL,
    market            TEXT NOT NULL,
    aspect_ratio      TEXT NOT NULL,
    language          TEXT NOT NULL DEFAULT 'en',
    storage_url       TEXT,
    storage_path      TEXT,
    prompt_hash       TEXT,
    reused            BOOLEAN NOT NULL DEFAULT FALSE,
    compliance_passed BOOLEAN,
    -- v4: layer paths for canvas editor
    layer_base_path     TEXT,
    layer_gradient_path TEXT,
    layer_logo_path     TEXT,
    layer_text_path     TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assets_run_id ON assets(run_id);
CREATE INDEX IF NOT EXISTS idx_assets_prompt_hash ON assets(prompt_hash);

-- ── asset_edits ──────────────────────────────────────────────
-- Canvas editor edit history — one row per edit operation
CREATE TABLE IF NOT EXISTS asset_edits (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id     UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    run_id       UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    edit_type    TEXT NOT NULL,
    -- text | mask | layer
    instruction  TEXT NOT NULL,
    before_url   TEXT NOT NULL,
    after_url    TEXT NOT NULL,
    mask_url     TEXT,
    -- only for mask edits
    layer_name   TEXT,
    -- only for layer edits: base | gradient | logo | text
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asset_edits_asset_id ON asset_edits(asset_id);

-- ── video_outputs ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS video_outputs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ratio        TEXT NOT NULL,
    mode         TEXT NOT NULL,
    storage_url  TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    duration_s   FLOAT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_video_outputs_run_id ON video_outputs(run_id);

-- ── publish_results ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS publish_results (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    platform     TEXT NOT NULL,
    market       TEXT NOT NULL,
    post_url     TEXT,
    post_id      TEXT,
    published_at TIMESTAMPTZ,
    status       TEXT NOT NULL,
    -- published | scheduled | failed
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_publish_results_run_id ON publish_results(run_id);

-- ── competitor_analyses ──────────────────────────────────────
-- Standalone competitor analysis results (from POST /api/competitor/analyze)
CREATE TABLE IF NOT EXISTS competitor_analyses (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        UUID REFERENCES workspaces(id) ON DELETE CASCADE,
    source_type         TEXT NOT NULL DEFAULT 'screenshot',
    -- screenshot | url
    source_url          TEXT,
    screenshot_count    INTEGER NOT NULL DEFAULT 1,
    layout_description  TEXT,
    color_palette       JSONB NOT NULL DEFAULT '[]',
    emotional_tone      TEXT,
    claims_made         JSONB NOT NULL DEFAULT '[]',
    strengths           JSONB NOT NULL DEFAULT '[]',
    weaknesses          JSONB NOT NULL DEFAULT '[]',
    counter_strategy    TEXT,
    style_hints         JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_competitor_analyses_workspace ON competitor_analyses(workspace_id);

-- ── billing_events ───────────────────────────────────────────
-- Credit usage log for billing/metering
CREATE TABLE IF NOT EXISTS billing_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    event_type   TEXT NOT NULL,
    -- run | ai_video | publish | competitor_analysis | credit_purchase
    credits_used INTEGER NOT NULL DEFAULT 0,
    run_id       UUID REFERENCES runs(id) ON DELETE SET NULL,
    metadata     JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_billing_events_workspace ON billing_events(workspace_id);

-- ── Enable Realtime ───────────────────────────────────────────
ALTER PUBLICATION supabase_realtime ADD TABLE run_events;
ALTER PUBLICATION supabase_realtime ADD TABLE runs;
ALTER PUBLICATION supabase_realtime ADD TABLE assets;
ALTER PUBLICATION supabase_realtime ADD TABLE video_outputs;
ALTER PUBLICATION supabase_realtime ADD TABLE publish_results;

-- ── Row Level Security (enterprise-ready, disabled for local POC) ──
-- ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE run_events ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE assets ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE asset_edits ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE video_outputs ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE publish_results ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE competitor_analyses ENABLE ROW LEVEL SECURITY;
