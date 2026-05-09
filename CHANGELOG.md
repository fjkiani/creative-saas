# Changelog

## v4.0.0 — CreativeOS (2026-05-06)

### New Features

#### Pipeline Nodes
- **`competitor_analyze`** — New first node. Upload competitor ad images; Llama 3.2 Vision extracts style, color palette, messaging patterns, and layout. Outputs `style_hints` injected into the brief to inform counter-positioning.
- **`video_gen`** — New node after `compliance_post`. Groups assets by aspect ratio, generates slideshow videos (Ken Burns + cross-dissolve, free) or AI videos (Wan-2.6 / Hailuo via OpenRouter).
- **`publish`** — New final node. Posts to Instagram (image, carousel, Reels) and TikTok (video, photo carousel) with per-market captions. Graceful degradation — individual market failures don't abort the run.

#### Providers
- **`VideoProvider`** — `SlideshowVideoProvider` (moviepy, free), `WanVideoProvider`, `HailuoVideoProvider`
- **`EditProvider`** — `GPT5ImageEditProvider`, `GeminiEditProvider` (text_edit + mask_edit for canvas changes)
- **`PublishProvider`** — `InstagramPublishProvider` (Meta Graph API v21.0), `TikTokPublishProvider` (Content Posting API v2)
- **`VisionProvider`** — `LlamaVisionProvider` (meta-llama/llama-3.2-11b-vision-instruct:free via OpenRouter)

#### Frontend Components
- **`CanvasEditor`** — Browser-based layer editor. Drag text, reposition logo, adjust colors. Calls `/api/assets/{id}/edit` to re-composite server-side.
- **`VideoPlayer`** — Inline video playback with download button. Shows all ratio variants in tabs.
- **`PublishPanel`** — Per-market publish controls. Shows publish status per platform with error details.
- **`CompetitorUpload`** — Drag-and-drop competitor image upload with preview grid.

#### API Endpoints (8 new groups)
- `POST /api/competitor` — Upload + analyze competitor images
- `GET /api/assets/{id}/layers` — Get layer paths for canvas editor
- `POST /api/assets/{id}/edit` — Apply canvas edit
- `GET /api/runs/{id}/videos` — Get video outputs
- `POST /api/runs/{id}/publish` — Trigger social publishing
- `POST /api/workspaces` — Create SaaS workspace
- `POST /api/billing/checkout` — Stripe Checkout session
- `POST /api/billing/webhook` — Stripe webhook

#### Database (6 new tables)
- `workspaces` — SaaS tenant isolation
- `asset_edits` — Canvas edit history
- `video_outputs` — Generated video metadata + storage paths
- `publish_results` — Per-market publish status + platform post IDs
- `competitor_analyses` — Extracted competitor style data
- `billing_events` — Stripe event log

### Bug Fixes

- **`config.py`**: `supabase_url` defaulted to `http://localhost:54321` — silently broke `supabase_configured` check in production. Fixed to default `""`.
- **`config.py`**: Missing `supabase_configured`, `supabase_service_key_resolved`, `supabase_service_key` properties — `db/client.py` called these at every DB operation. All added.
- **`config.py`**: Missing `pipeline_api_key` field — `main.py` reads `PIPELINE_API_KEY` env var. Added.
- **`composite.py`**: Missing `_upsert_asset_row()` function — `localize.py` imports this to persist asset rows. Added full async implementation with graceful error handling.
- **`composite.py`**: `_upsert_asset_row` not called in `composite_node` save loop — assets were generated but never persisted to Supabase. Fixed.
- **`publish.py`**: Zero `try/except` blocks — any API error would crash the entire publish node. Full rewrite with `_failed()` helper; every method wrapped; never raises.
- **`publish.py`**: Instagram carousel item failures aborted the whole carousel. Fixed — individual item failures are logged, carousel continues with remaining items.
- **`publish.py`**: TikTok `scheduled_time` parsing could crash on malformed ISO strings. Added `try/except` around `fromisoformat()`.

### Infrastructure
- `render.yaml` updated: service names `creative-saas-backend` / `creative-saas-frontend`; all v4 env vars added
- `requirements.txt` updated: added `moviepy`, `stripe`
- `backend/db/migrate_v3_to_v4.sql` — safe `ALTER TABLE` migration for existing v3 databases
- `.env.example` updated with all v4 variables

---

## v3.0.0 — Production Hardening (2026-05-05)

- Multi-provider image generation (Gemini, OpenAI, Firefly, Stability)
- Supabase Storage integration with `creative-assets` bucket
- Human-in-the-loop review gate with LangGraph `interrupt()`
- Brand compliance checking (OpenCV template matching + color analysis)
- Localization node (LLM copy rewrite per market)
- Realtime pipeline progress via Supabase Realtime
- Docker + Render deployment blueprint
- API key authentication

---

## v2.0.0 — LangGraph Pipeline

- LangGraph state machine replacing sequential script
- Structured Pydantic models for all LLM outputs
- MemorySaver checkpointing
- Supabase Postgres for run persistence

---

## v1.0.0 — Initial Release

- Basic image generation pipeline
- Local file storage
- CLI runner (`run_pipeline.py`)
