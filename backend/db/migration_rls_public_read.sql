-- Migration: Add public read RLS policies for pipeline tables
-- 
-- Problem: The frontend uses the anon key (VITE_SUPABASE_ANON_KEY) to query
-- runs, run_events, and assets tables. Without these policies, RLS blocks all
-- anon reads and the frontend sees 0 rows — assets never appear in the UI,
-- run status never updates, and events never show.
--
-- These tables contain non-sensitive creative pipeline data (no PII).
-- Public read is safe and intentional.
--
-- Run this in Supabase SQL Editor:
-- https://supabase.com/dashboard/project/cllqahmtyvdcbyyrouxx/sql/new

-- runs table
CREATE POLICY IF NOT EXISTS "public_read_runs"
  ON public.runs FOR SELECT
  TO anon, authenticated
  USING (true);

-- run_events table
CREATE POLICY IF NOT EXISTS "public_read_run_events"
  ON public.run_events FOR SELECT
  TO anon, authenticated
  USING (true);

-- assets table
CREATE POLICY IF NOT EXISTS "public_read_assets"
  ON public.assets FOR SELECT
  TO anon, authenticated
  USING (true);

-- Also add the unique constraint for fast-path upsert (if not already applied)
-- ALTER TABLE assets
--   ADD CONSTRAINT uq_asset_per_run
--   UNIQUE (run_id, product_id, market, aspect_ratio);
