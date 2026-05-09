-- ============================================================
-- CreativeOS — Migration: v3 → v4
-- ============================================================
-- Run this AFTER deploying v4 if you have an existing v3 database.
-- Safe to run multiple times (uses IF NOT EXISTS / IF EXISTS guards).
--
-- Run via: psql $DATABASE_URL -f migrate_v3_to_v4.sql

-- ── Add workspace_id to runs ──────────────────────────────────
ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL;

-- ── Add video_mode and publish fields to runs ─────────────────
ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS video_mode TEXT NOT NULL DEFAULT 'slideshow';

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS publish_platforms JSONB NOT NULL DEFAULT '[]';

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS scheduled_publish_time TEXT;

-- ── Add layer paths to assets ─────────────────────────────────
ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS layer_base_path TEXT;

ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS layer_gradient_path TEXT;

ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS layer_logo_path TEXT;

ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS layer_text_path TEXT;

-- ── Create new v4 tables (idempotent — schema.sql already has these) ──
-- workspaces, asset_edits, video_outputs, publish_results,
-- competitor_analyses, billing_events are created by schema.sql.
-- Run schema.sql first if starting fresh, or just this file for migration.

-- ── Enable Realtime on new tables ────────────────────────────
DO $$
BEGIN
    BEGIN
        ALTER PUBLICATION supabase_realtime ADD TABLE video_outputs;
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
    BEGIN
        ALTER PUBLICATION supabase_realtime ADD TABLE publish_results;
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
END $$;

-- ── Verify migration ──────────────────────────────────────────
SELECT
    'runs.workspace_id' AS check_name,
    COUNT(*) > 0 AS column_exists
FROM information_schema.columns
WHERE table_name = 'runs' AND column_name = 'workspace_id'
UNION ALL
SELECT
    'runs.video_mode',
    COUNT(*) > 0
FROM information_schema.columns
WHERE table_name = 'runs' AND column_name = 'video_mode'
UNION ALL
SELECT
    'assets.layer_base_path',
    COUNT(*) > 0
FROM information_schema.columns
WHERE table_name = 'assets' AND column_name = 'layer_base_path';
