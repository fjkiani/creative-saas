# CreativeOS v4 — AI Creative Automation SaaS

End-to-end AI pipeline that turns a campaign brief into production-ready social assets, videos, and published posts — with competitor intelligence, canvas editing, and SaaS billing.

## What It Does

1. **Competitor Analysis** — Upload competitor ads; vision AI extracts style, color, and messaging patterns to inform your brief
2. **Brief Enrichment** — LLM expands a sparse brief into structured prompts per product × market × ratio
3. **Compliance Pre-flight** — Checks claims against prohibited list before spending on image generation
4. **Image Generation** — Gemini Imagen 3, DALL-E 3, Adobe Firefly, or Stable Diffusion
5. **Compositing** — Pillow-based compositor: crop, overlay, text, logo, brand colors — all layers saved to Supabase
6. **Canvas Editor** — Browser-based layer editor for post-generation tweaks (text, logo position, color)
7. **Localization** — LLM rewrites copy per market locale; re-composites with translated text
8. **Compliance Post-check** — OpenCV brand compliance: color accuracy, logo presence, text readability
9. **Video Generation** — Slideshow (free, moviepy) or AI video (Wan-2.6 / Hailuo via OpenRouter)
10. **Social Publishing** — Instagram (image / carousel / Reels) + TikTok (video / photo carousel)
11. **Human-in-the-Loop** — Review gate with approve/reject; resumes pipeline from checkpoint
12. **SaaS Billing** — Stripe Checkout + webhook; workspace isolation per customer

## Architecture

```
START → competitor_analyze → enrich → prompt_gen → compliance_pre
                                                     ↓ (pass)
                                                image_gen → composite → review_gate
                                                                         ↓ (approved)
                                                                    localize → compliance_post
                                                                                ↓
                                                                          video_gen → publish → END
```

**Stack**: FastAPI + LangGraph (backend) · React + Vite + Supabase Realtime (frontend) · Supabase (Postgres + Storage) · Render (hosting)

## Quick Start

### Prerequisites
- Python 3.11+
- Node 20+
- Supabase project (free tier works)

### 1. Clone & configure
```bash
git clone https://github.com/fjkiani/creative-saas.git
cd creative-saas
cp .env.example .env
# Edit .env — at minimum set GEMINI_API_KEY + SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
```

### 2. Set up Supabase
```sql
-- In Supabase SQL Editor, run in order:
-- 1. backend/db/schema.sql          (all tables)
-- 2. backend/db/storage_bucket.sql  (creative-assets bucket)
-- 3. backend/db/migrate_v3_to_v4.sql  (only if upgrading from v3)
```

Or use the init script:
```bash
pip install supabase
python scripts/init_supabase_storage.py
```

### 3. Run locally
```bash
# Backend
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000

# Frontend (new terminal)
cd frontend
npm install
cp .env.example .env.local
# Edit .env.local with VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY
npm run dev
```

Open http://localhost:5173

## Deploy to Render

1. Fork this repo
2. In Render dashboard: **New → Blueprint** → connect your fork
3. Render reads `render.yaml` and creates both services automatically
4. Set secret env vars in Render dashboard (marked `sync: false` in render.yaml):
   - `SUPABASE_SERVICE_ROLE_KEY` (required)
   - `GEMINI_API_KEY` (required)
   - `PIPELINE_API_KEY` (optional — enables API auth)
   - `OPENROUTER_API_KEY` (for video gen + vision)
   - `INSTAGRAM_ACCESS_TOKEN` + `INSTAGRAM_BUSINESS_ACCOUNT_ID` (for publishing)
   - `TIKTOK_ACCESS_TOKEN` + `TIKTOK_OPEN_ID` (for publishing)
   - `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` (for billing)

## Environment Variables

See `.env.example` for the full list with descriptions.

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Yes | Gemini LLM + Imagen 3 image gen |
| `SUPABASE_URL` | Yes | `https://your-project.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Service role key (never expose to browser) |
| `SUPABASE_ANON_KEY` | Yes | Anon key (safe for browser) |
| `STORAGE_BACKEND` | Yes | `supabase` (or `local`, `s3`, `azure`) |
| `OPENROUTER_API_KEY` | For video | Wan-2.6 / Hailuo video gen + Llama vision |
| `INSTAGRAM_ACCESS_TOKEN` | For publishing | Meta Graph API long-lived token |
| `TIKTOK_ACCESS_TOKEN` | For publishing | TikTok Content Posting API v2 |
| `APIFY_API_TOKEN` | For competitor | Instagram/TikTok scraping |
| `STRIPE_SECRET_KEY` | For billing | Stripe secret key |

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/runs` | Submit campaign brief, start pipeline |
| `GET` | `/api/runs/{id}` | Get run status + report |
| `GET` | `/api/runs` | List all runs |
| `POST` | `/api/runs/{id}/review` | Approve or reject PENDING_REVIEW run |
| `POST` | `/api/competitor` | Upload competitor images for analysis |
| `GET` | `/api/assets/{id}/layers` | Get composited layer paths |
| `POST` | `/api/assets/{id}/edit` | Apply canvas edit (text/logo/color) |
| `GET` | `/api/runs/{id}/videos` | Get generated video outputs |
| `POST` | `/api/runs/{id}/publish` | Trigger social publishing |
| `POST` | `/api/workspaces` | Create workspace (SaaS tenant) |
| `POST` | `/api/billing/checkout` | Create Stripe Checkout session |
| `POST` | `/api/billing/webhook` | Stripe webhook receiver |
| `GET` | `/api/health` | Health check (always public) |

## Database Schema

**v3 tables** (existing): `pipeline_runs`, `run_events`, `assets`, `campaigns`

**v4 tables** (new): `workspaces`, `asset_edits`, `video_outputs`, `publish_results`, `competitor_analyses`, `billing_events`

## Providers

### LLM
- `gemini` — Gemini 2.5 Pro (default)
- `openai` — GPT-4o
- `anthropic` — Claude 3.5 Sonnet

### Image Generation
- `gemini` — Imagen 3 (default)
- `openai` — DALL-E 3
- `firefly` — Adobe Firefly Image 5
- `stability` — Stable Diffusion 3.5

### Video Generation
- `slideshow` — moviepy Ken Burns + cross-dissolve (free, no API key)
- `wan` — Wan-2.6 via OpenRouter
- `hailuo` — Hailuo via OpenRouter

### Vision (Competitor Analysis)
- `llama` — Llama 3.2 Vision via OpenRouter (free tier available)

## Known Limitations

1. **MemorySaver** — HITL review state is in-process only. Restart loses PENDING_REVIEW runs. Production fix: swap to `AsyncPostgresSaver` (see `backend/graph/pipeline.py` comments)
2. **WanVideoProvider** — API shape is approximate; wan-2.6 endpoint may differ from OpenRouter docs at time of deployment
3. **Instagram/TikTok OAuth** — Requires platform app approval (not instant). Use test accounts during development
4. **Stripe webhook** — Must be registered in Stripe dashboard pointing to `https://your-backend.onrender.com/api/billing/webhook`
5. **Competitor scraping** — Apify actor IDs may change; verify `apify/instagram-scraper` and `apify/tiktok-scraper` are current

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for v3 → v4 changes.

## License

MIT
