-- ============================================================
-- Migration: add unique constraint to assets table
-- ============================================================
-- Run this in Supabase SQL Editor if you already ran schema.sql
-- and the assets table exists WITHOUT the unique constraint.
--
-- Dashboard: https://supabase.com/dashboard/project/cllqahmtyvdcbyyrouxx/sql/new
--
-- This constraint is REQUIRED for the upsert in composite/localize nodes.
-- Without it, upsert falls back to INSERT and creates duplicate rows.

ALTER TABLE assets
  ADD CONSTRAINT IF NOT EXISTS uq_asset_per_run
  UNIQUE (run_id, product_id, market, aspect_ratio);
