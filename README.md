# Creative Automation Pipeline v3

A production-grade GenAI creative automation pipeline that turns a campaign brief into hundreds of localized, multi-ratio, multi-platform ad creatives — with human-in-the-loop review, parallel image generation, and one-click Render deployment.

**Architecture**: FastAPI + LangGraph + React/Vite + Supabase Realtime  
**Default models**: Gemini 2.5 Pro (LLM) + `gemini-2.5-flash-image` (image generation)  
**Swappable to**: OpenAI GPT-4o/DALL-E 3, Anthropic Claude, Adobe Firefly Image5, Stability SD3.5

---

## Pipeline Topology

```
START → enrich → prompt_gen → compliance_pre ──(HARD FAIL)──→ END
                                             └──(PASS)──────→ image_gen (parallel)
                                                               → composite
                                                               → review_gate ──(auto-reject)──→ END
                                                                             └──(pending)────→ [HUMAN REVIEW]
                                                                             └──(approved)───→ localize
                                                                                              → compliance_post
                                                                                              → END
```

### Nodes

| Node | Description |
|------|-------------|
| `enrich` | LLM enriches brief → `CreativeSpec` (visual style, mood, palette, brand voice) |
| `prompt_gen` | LLM generates one optimized image prompt per product × market |
| `compliance_pre` | LLM scans prompts for prohibited words, health claims, superlatives, competitor mentions |
| `image_gen` | Parallel image generation via `asyncio.gather` + semaphore (configurable concurrency) |
| `composite` | Pillow compositor: smart crop → gradient overlay → logo → text per aspect ratio |
| `review_gate` | **HITL threshold valve**: auto-approve / auto-reject / pause for human review |
| `localize` | LLM localizes copy (headline, tagline, CTA) per market; re-composites images |
| `compliance_post` | Pixel-level checks: logo detection (OpenCV), brand color adherence (k-means), text scan |

---

## Human-in-the-Loop (HITL) Review Gate

The `review_gate` node implements the **threshold valve** pattern — a configurable confidence score determines whether a run needs human review.

### Confidence Score

Computed from the pre-compliance report:

```
score = 1.0 - (0.15 × warning_count) - (0.50 × error_count)
score = clamp(score, 0.0, 1.0)
```

### Routing

| Score | Action |
|-------|--------|
| `score >= HITL_AUTO_APPROVE` (default: 0.85) | Auto-approve → pipeline continues |
| `score < HITL_AUTO_REJECT` (default: 0.60) | Auto-reject → run status: `REJECTED` |
| Between thresholds | Pause → run status: `PENDING_REVIEW` → human decision required |

### Thresholds

Configure via env vars:

```bash
HITL_AUTO_APPROVE=0.85   # raise to require more human review
HITL_AUTO_REJECT=0.60    # lower to be more permissive
```

### Resuming a Paused Run

When a run enters `PENDING_REVIEW`, the frontend shows a **ReviewCard** with:
- Compliance score and issue breakdown
- Sample composited asset previews
- Approve / Reject buttons with optional reviewer notes

Or call the API directly:

```bash
curl -X POST https://your-backend.onrender.com/api/runs/{run_id}/review \
  -H "X-Api-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"decision": "approve", "reviewer_notes": "Looks good for FR market"}'
```

### LangGraph Checkpointing

The pipeline uses `MemorySaver` for in-process checkpointing — sufficient for a single Render instance. For multi-instance deployments:

```python
# pipeline.py — swap MemorySaver for AsyncPostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

checkpointer = AsyncPostgresSaver.from_conn_string(os.getenv("DATABASE_URL"))
```

Install: `pip install langgraph-checkpoint-postgres`

---

## Hero Asset Input (1 Hero → Many Variants)

The pipeline supports the **"1 hero asset → hundreds of variants"** pattern. Provide an existing product image in the brief and the pipeline uses it as a visual reference for all market/ratio variants.

### In your brief YAML

```yaml
products:
  - id: radiance-serum
    name: Lumina Radiance Serum
    description: ...
    existing_asset: /path/to/hero_product_shot.png   # local path
    # OR
    existing_asset: https://cdn.example.com/hero.jpg  # HTTP URL
```

When `existing_asset` is set:
- The image is loaded (local path or fetched from URL)
- Passed to `generate_with_reference()` in the Gemini provider as an inline reference
- Gemini uses it as a visual anchor for product appearance and style
- Each market gets a culturally adapted variant maintaining product identity

When `existing_asset` is `null` or omitted, the pipeline generates from scratch using the text prompt.

> **Note**: Reference-based generation is best-effort — the model adapts the reference rather than pixel-perfectly preserving it. For strict inpainting or mask-based editing, use Adobe Firefly Image5 (`IMAGE_PROVIDER=firefly`) which supports explicit mask regions.

---

## Parallel Image Generation

Images are generated concurrently using `asyncio.gather` with a semaphore:

```python
IMAGE_GEN_CONCURRENCY=3   # default: 3 concurrent API calls
```

- **3–4× faster** than sequential generation on a 6-prompt batch
- Semaphore prevents rate-limit hammering
- `return_exceptions=True` — one failure doesn't cancel the batch
- Failed prompts are logged to `state["errors"]`; successful ones proceed

Tune via env var:
```bash
IMAGE_GEN_CONCURRENCY=5   # increase for larger batches / higher rate limits
IMAGE_GEN_CONCURRENCY=1   # set to 1 to restore sequential behavior
```

---

## API Authentication

All `/api/*` routes (except `/api/health`) require an `X-Api-Key` header when `PIPELINE_API_KEY` is set.

```bash
# Set in .env or Render dashboard
PIPELINE_API_KEY=your-strong-random-key

# Use in API calls
curl -H "X-Api-Key: your-strong-random-key" https://your-backend.onrender.com/api/runs
```

If `PIPELINE_API_KEY` is not set, auth is disabled (dev mode — a warning is logged at startup).

**Production upgrade path**: Replace header-based key auth with JWT (e.g., Supabase Auth or Auth0). Add a `verify_jwt` dependency alongside `verify_api_key`.

---

## Deployment

### Render (Recommended)

1. Fork this repo to your GitHub account
2. Go to [Render Dashboard](https://dashboard.render.com) → **New** → **Blueprint**
3. Connect your repo — Render detects `render.yaml` automatically
4. Set secret env vars in the Render dashboard:
   - `GEMINI_API_KEY` — your Google AI Studio API key
   - `PIPELINE_API_KEY` — a strong random string for API auth
   - `VITE_API_URL` — the backend service URL (set after backend deploys)
   - `VITE_API_KEY` — same as `PIPELINE_API_KEY`
5. Click **Apply** — both services deploy automatically

The backend uses a **10GB persistent disk** at `/workspace/outputs` for asset storage. For production at scale, switch to Supabase Storage:

```bash
STORAGE_BACKEND=supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-key
```

### Local Development (Docker Compose)

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env — set GEMINI_API_KEY at minimum

# 2. Start all services
docker compose up --build

# 3. Access
# Frontend: http://localhost:5173
# Backend API: http://localhost:8000
# API docs: http://localhost:8000/docs

# 4. Run without Supabase (local storage only)
docker compose up backend frontend
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | — | **Required.** Google AI Studio API key |
| `PIPELINE_API_KEY` | _(empty)_ | API auth key. Empty = auth disabled |
| `IMAGE_PROVIDER` | `gemini` | Image gen provider: `gemini`, `dalle`, `firefly`, `stability` |
| `LLM_PROVIDER` | `gemini` | LLM provider: `gemini`, `openai`, `anthropic` |
| `STORAGE_BACKEND` | `local` | Storage: `local`, `supabase`, `s3`, `azure`, `dropbox` |
| `IMAGE_GEN_CONCURRENCY` | `3` | Max concurrent image API calls |
| `HITL_AUTO_APPROVE` | `0.85` | Confidence score threshold for auto-approval |
| `HITL_AUTO_REJECT` | `0.60` | Confidence score threshold for auto-rejection |
| `CORS_ORIGINS` | `*` | Allowed CORS origins (comma-separated) |

---

## Scaling to Production

### Horizontal Scaling (Celery + Redis)

The current implementation uses FastAPI background tasks (`asyncio.gather`) — sufficient for a single Render instance handling ~10 concurrent runs.

For high-throughput production (100+ concurrent runs), migrate to Celery:

**1. Install**
```bash
pip install celery[redis] redis
```

**2. Create `backend/worker.py`**
```python
from celery import Celery
import asyncio

celery_app = Celery("pipeline", broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"))

@celery_app.task
def run_pipeline_task(run_id: str, state: dict):
    asyncio.run(run_pipeline_background(run_id, state))
```

**3. Swap in `main.py`**
```python
# Before (FastAPI background task):
background_tasks.add_task(run_pipeline_background, run_id, initial_state)

# After (Celery task):
from backend.worker import run_pipeline_task
run_pipeline_task.delay(run_id, initial_state)
```

**4. Add to `render.yaml`**
```yaml
- type: worker
  name: creative-pipeline-worker
  runtime: docker
  dockerfilePath: ./backend/Dockerfile
  startCommand: celery -A backend.worker worker --loglevel=info --concurrency=4
  envVars:
    - key: REDIS_URL
      fromService:
        name: creative-pipeline-redis
        type: redis
        property: connectionString
```

**5. Add Redis service**
```yaml
- type: redis
  name: creative-pipeline-redis
  plan: starter
```

---

## Project Structure

```
creative-pipeline/
├── backend/
│   ├── graph/
│   │   ├── nodes/
│   │   │   ├── enrich.py          # Node 1: brief enrichment
│   │   │   ├── prompt_gen.py      # Node 2: image prompt generation
│   │   │   ├── compliance_pre.py  # Node 3: pre-generation compliance
│   │   │   ├── image_gen.py       # Node 4: parallel image generation
│   │   │   ├── composite.py       # Node 5: aspect ratio compositor
│   │   │   ├── review_gate.py     # Node 5.5: HITL threshold valve ← NEW v3
│   │   │   ├── localize.py        # Node 6: copy localization + re-composite
│   │   │   └── compliance_post.py # Node 7: pixel-level compliance checks
│   │   ├── pipeline.py            # LangGraph graph + MemorySaver
│   │   └── state.py               # PipelineState TypedDict + Pydantic models
│   ├── providers/
│   │   ├── base.py                # LLMProvider + ImageProvider ABCs
│   │   ├── gemini.py              # Gemini 2.5 Pro + gemini-2.5-flash-image
│   │   ├── openai_dalle.py        # GPT-4o + DALL-E 3
│   │   ├── anthropic_claude.py    # Claude 3.5 Sonnet
│   │   ├── firefly.py             # Adobe Firefly Image5
│   │   └── stability.py           # Stability AI SD3.5
│   ├── storage/
│   │   ├── base.py                # StorageBackend ABC + factory
│   │   ├── local.py               # Local filesystem (default)
│   │   ├── supabase_storage.py    # Supabase Storage
│   │   ├── s3.py                  # AWS S3
│   │   ├── azure_blob.py          # Azure Blob Storage
│   │   └── dropbox_storage.py     # Dropbox
│   ├── main.py                    # FastAPI app + auth + HITL review endpoint
│   ├── config.py                  # Settings (pydantic-settings)
│   ├── reporter.py                # Run report generation
│   └── Dockerfile
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── ReviewCard.tsx     # HITL approve/reject UI ← NEW v3
│       │   ├── PipelineTracker.tsx
│       │   ├── AssetGrid.tsx
│       │   ├── CompliancePanel.tsx
│       │   └── BriefEditor.tsx
│       └── pages/
│           ├── NewCampaign.tsx
│           └── RunDetail.tsx      # Updated: shows ReviewCard when PENDING_REVIEW
├── assets/brand/
│   ├── brand_configs/             # Per-brand YAML configs
│   ├── lumina_logo.png
│   └── apex_logo.png
├── briefs/
│   ├── lumina_skincare.yaml       # Example brief (Lumina skincare)
│   └── apex_sportswear.yaml       # Example brief (Apex sportswear)
├── render.yaml                    # Render deployment blueprint ← NEW v3
├── docker-compose.yml             # Local dev stack
├── .env.example                   # Environment variable template
└── requirements.txt
```

---

## API Reference

### `POST /api/runs`
Submit a campaign brief and start the pipeline.

```bash
curl -X POST http://localhost:8000/api/runs \
  -H "X-Api-Key: your-key" \
  -H "Content-Type: application/json" \
  -d @briefs/lumina_skincare.json
```

Response: `{ "run_id": "...", "status": "PENDING" }`

### `GET /api/runs/{run_id}`
Get run status, events, and assets.

### `POST /api/runs/{run_id}/review`
Approve or reject a `PENDING_REVIEW` run.

```bash
curl -X POST http://localhost:8000/api/runs/{run_id}/review \
  -H "X-Api-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"decision": "approve", "reviewer_notes": "Approved for all markets"}'
```

### `GET /api/health`
Health check (always public, no auth required).

```bash
curl http://localhost:8000/api/health
# {"status":"ok","version":"3.0.0","auth_enabled":true,"providers":{...}}
```

---

## Swapping Providers

All providers implement the same `LLMProvider` / `ImageProvider` abstract base class. Swap at runtime via env var — no code changes needed.

```bash
# Use OpenAI for LLM + DALL-E 3 for images
LLM_PROVIDER=openai
IMAGE_PROVIDER=dalle
OPENAI_API_KEY=sk-...

# Use Adobe Firefly for images (supports mask-based inpainting)
IMAGE_PROVIDER=firefly
FIREFLY_CLIENT_ID=...
FIREFLY_CLIENT_SECRET=...
```

---

## Live Run Results (v2 baseline)

A full pipeline run on the Lumina skincare brief produced:
- **2 products × 3 markets × 3 aspect ratios = 18 composited creatives**
- All 7 nodes completed with 0 errors
- Pre-compliance: PASSED (2 legal warnings: substantiation for "14 days" claim, vegan/cruelty-free certs)
- Post-compliance: PASSED
- Genuine FR localization: "Révélez Votre Éclat Naturel"
- Image generation: ~1.2–1.4 MB per PNG via `gemini-2.5-flash-image`
