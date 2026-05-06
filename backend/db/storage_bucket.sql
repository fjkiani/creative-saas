-- ============================================================
-- Creative Automation Pipeline — Supabase Storage Setup
-- ============================================================
-- Run via SQL Editor: https://supabase.com/dashboard/project/cllqahmtyvdcbyyrouxx/sql/new
-- Creates the `creative-assets` bucket and a public read policy.

-- Create bucket (idempotent)
INSERT INTO storage.buckets (id, name, public)
VALUES ('creative-assets', 'creative-assets', true)
ON CONFLICT (id) DO NOTHING;

-- Allow public read access to all objects in the bucket
CREATE POLICY "Public read access"
  ON storage.objects FOR SELECT
  USING (bucket_id = 'creative-assets');

-- Allow service role to insert/update/delete (backend uses service_role key)
CREATE POLICY "Service role full access"
  ON storage.objects
  USING (bucket_id = 'creative-assets')
  WITH CHECK (bucket_id = 'creative-assets');
