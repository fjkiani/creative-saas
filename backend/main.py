"""
Creative Automation Pipeline — FastAPI Backend v3

Endpoints:
  POST /api/runs                    — Submit a campaign brief, kick off pipeline async
  GET  /api/runs/{run_id}           — Get run status + report
  GET  /api/runs                    — List all runs
  POST /api/runs/{run_id}/review    — Approve or reject a PENDING_REVIEW run (HITL)
  POST /api/campaigns               — Create a named campaign
  GET  /api/campaigns               — List campaigns
  GET  /api/health                  — Health check (always public)

Authentication:
  All /api/* routes except /api/health require X-Api-Key header when
  PIPELINE_API_KEY env var is set. If not set, auth is disabled (dev mode).

Human-in-the-loop:
  When a run enters PENDING_REVIEW state (confidence score in review band),
  POST /api/runs/{run_id}/review resumes the LangGraph graph from its
  MemorySaver checkpoint with the human decision.
"""
import asyncio
import uuid
import os
import structlog
import yaml
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, BackgroundTasks, Header, Depends, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.config import settings
from backend.graph.state import PipelineState, CampaignBrief
from backend.reporter import save_run_report

log = structlog.get_logger(__name__)

# ── API Key Auth ──────────────────────────────────────────────────────────────

_API_KEY = os.getenv("PIPELINE_API_KEY", "")


async def verify_api_key(x_api_key: str = Header(default="")) -> None:
    """
    FastAPI dependency: validate X-Api-Key header.
    If PIPELINE_API_KEY is not set, auth is disabled (dev mode — logged at startup).
    """
    if _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: import and compile the LangGraph pipeline."""
    log.info("startup.pipeline_compile")
    from backend.graph.pipeline import pipeline  # triggers compilation
    app.state.pipeline = pipeline

    if not _API_KEY:
        log.warning("startup.auth_disabled",
                    message="PIPELINE_API_KEY not set — API authentication is disabled (dev mode)")
    else:
        log.info("startup.auth_enabled")

    log.info("startup.complete",
             llm=settings.llm_provider,
             image=settings.image_provider,
             storage=settings.storage_backend)
    yield
    log.info("shutdown")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Creative Automation Pipeline",
    description="GenAI-powered creative asset generation for social ad campaigns",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public router — no auth
public_router = APIRouter()

# Protected router — requires API key when PIPELINE_API_KEY is set
api_router = APIRouter(dependencies=[Depends(verify_api_key)])


# ── Request/Response models ───────────────────────────────────────────────────

class RunRequest(BaseModel):
    brief: dict                          # CampaignBrief as dict (from YAML/JSON editor)
    image_provider: str | None = None    # override IMAGE_PROVIDER env var
    llm_provider: str | None = None      # override LLM_PROVIDER env var


class ReviewRequest(BaseModel):
    decision: str                        # "approve" | "reject"
    reviewer_notes: str | None = None


class CampaignCreateRequest(BaseModel):
    name: str
    brand: str
    brand_config: dict = {}


# ── Background pipeline runner ────────────────────────────────────────────────

async def run_pipeline_background(run_id: str, state: PipelineState):
    """
    Execute the LangGraph pipeline in the background.
    Updates Supabase run status throughout. Saves run report on completion.

    Uses run_id as the LangGraph thread_id for MemorySaver checkpointing,
    enabling interrupt/resume for the HITL review_gate node.
    """
    from backend.db.client import get_supabase_admin
    from backend.graph.pipeline import pipeline

    db = get_supabase_admin()
    log.info("pipeline.start", run_id=run_id)

    try:
        # Mark run as RUNNING
        db.table("runs").update({"status": "RUNNING"}).eq("id", run_id).execute()

        # Execute the full LangGraph graph
        # thread_id = run_id enables MemorySaver to checkpoint per-run
        config = {"configurable": {"thread_id": run_id}}
        final_state = await pipeline.ainvoke(state, config=config)

        # If interrupted (PENDING_REVIEW), ainvoke returns early — don't mark complete yet
        review_decision = final_state.get("review_decision")
        if review_decision is None and final_state.get("current_node") == "review_gate":
            log.info("pipeline.interrupted_for_review", run_id=run_id)
            return  # DB already set to PENDING_REVIEW by review_gate node

        # Save run report
        await save_run_report(final_state)

        # Mark complete
        if final_state.get("review_decision") == "rejected":
            status = "REJECTED"
        elif final_state.get("errors"):
            status = "FAILED"
        else:
            status = "COMPLETE"

        db.table("runs").update({
            "status": status,
            "completed_at": "now()",
        }).eq("id", run_id).execute()

        log.info("pipeline.complete", run_id=run_id, status=status,
                 assets=len(final_state.get("composited_assets", [])))

    except Exception as e:
        log.error("pipeline.crashed", run_id=run_id, error=str(e))
        try:
            db.table("runs").update({
                "status": "FAILED",
                "error_message": str(e),
                "completed_at": "now()",
            }).eq("id", run_id).execute()
        except Exception:
            pass


async def resume_pipeline_background(run_id: str, decision: str, reviewer_notes: str):
    """
    Resume a PENDING_REVIEW pipeline from its MemorySaver checkpoint.
    Called after POST /api/runs/{run_id}/review.
    """
    from backend.db.client import get_supabase_admin
    from backend.graph.pipeline import pipeline
    from langgraph.types import Command

    db = get_supabase_admin()
    log.info("pipeline.resume", run_id=run_id, decision=decision)

    try:
        db.table("runs").update({"status": "RUNNING"}).eq("id", run_id).execute()

        config = {"configurable": {"thread_id": run_id}}
        resume_value = {"decision": decision, "reviewer_notes": reviewer_notes or ""}

        # Resume from checkpoint — LangGraph replays from the interrupt point
        final_state = await pipeline.ainvoke(
            Command(resume=resume_value),
            config=config,
        )

        await save_run_report(final_state)

        if decision == "reject":
            status = "REJECTED"
        elif final_state.get("errors"):
            status = "FAILED"
        else:
            status = "COMPLETE"

        db.table("runs").update({
            "status": status,
            "completed_at": "now()",
        }).eq("id", run_id).execute()

        log.info("pipeline.resume.complete", run_id=run_id, status=status)

    except Exception as e:
        log.error("pipeline.resume.crashed", run_id=run_id, error=str(e))
        try:
            db.table("runs").update({
                "status": "FAILED",
                "error_message": str(e),
                "completed_at": "now()",
            }).eq("id", run_id).execute()
        except Exception:
            pass


# ── Public routes ─────────────────────────────────────────────────────────────

@public_router.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "3.0.0",
        "auth_enabled": bool(_API_KEY),
        "providers": {
            "llm": settings.llm_provider,
            "image": settings.image_provider,
            "storage": settings.storage_backend,
        },
    }


# ── Protected routes ──────────────────────────────────────────────────────────

@api_router.post("/api/campaigns")
async def create_campaign(req: CampaignCreateRequest):
    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()
    result = db.table("campaigns").insert({
        "name": req.name,
        "brand": req.brand,
        "brand_config": req.brand_config,
    }).execute()
    return result.data[0]


@api_router.get("/api/campaigns")
async def list_campaigns():
    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()
    result = db.table("campaigns").select("*").order("created_at", desc=True).execute()
    return result.data


@api_router.post("/api/runs", status_code=202)
async def create_run(req: RunRequest, background_tasks: BackgroundTasks):
    """
    Submit a campaign brief and kick off the pipeline.
    Returns immediately with run_id — pipeline runs in background.
    Frontend subscribes to Supabase Realtime for live progress.
    """
    from backend.db.client import get_supabase_admin

    # Validate brief
    try:
        brief = CampaignBrief.model_validate(req.brief)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid brief: {e}")

    run_id = str(uuid.uuid4())
    db = get_supabase_admin()

    # Resolve providers (request overrides env var)
    image_provider = req.image_provider or settings.image_provider
    llm_provider = req.llm_provider or settings.llm_provider

    # Create run record in Supabase
    db.table("runs").insert({
        "id": run_id,
        "status": "PENDING",
        "provider_image": image_provider,
        "provider_llm": llm_provider,
        "brief": req.brief,
    }).execute()

    # Build initial pipeline state
    initial_state: PipelineState = {
        "run_id": run_id,
        "campaign_id": brief.campaign_id,
        "brief": req.brief,
        "creative_spec": None,
        "image_prompts": [],
        "pre_compliance": None,
        "generated_assets": [],
        "composited_assets": [],
        "localized_copy": [],
        "post_compliance": None,
        "review_decision": None,
        "review_score": None,
        "reviewer_notes": None,
        "current_node": "start",
        "errors": [],
        "provider_llm": llm_provider,
        "provider_image": image_provider,
        "storage_backend": settings.storage_backend,
    }

    # Override provider env vars if specified in request
    if req.image_provider:
        os.environ["IMAGE_PROVIDER"] = req.image_provider
    if req.llm_provider:
        os.environ["LLM_PROVIDER"] = req.llm_provider

    # Kick off pipeline as background task
    background_tasks.add_task(run_pipeline_background, run_id, initial_state)

    log.info("run.created", run_id=run_id, campaign=brief.campaign_id,
             image_provider=image_provider, llm_provider=llm_provider)

    return {
        "run_id": run_id,
        "status": "PENDING",
        "message": "Pipeline started. Subscribe to Supabase Realtime for live updates.",
        "providers": {"llm": llm_provider, "image": image_provider},
    }


@api_router.post("/api/runs/{run_id}/review", status_code=202)
async def review_run(run_id: str, req: ReviewRequest, background_tasks: BackgroundTasks):
    """
    Approve or reject a run that is in PENDING_REVIEW state.

    Resumes the LangGraph pipeline from its MemorySaver checkpoint.
    On approve: pipeline continues to localize → compliance_post → COMPLETE.
    On reject:  pipeline terminates with status REJECTED.

    Body:
      { "decision": "approve" | "reject", "reviewer_notes": "optional notes" }
    """
    from backend.db.client import get_supabase_admin

    if req.decision not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="decision must be 'approve' or 'reject'")

    # Verify run exists and is in PENDING_REVIEW
    try:
        db = get_supabase_admin()
        result = db.table("runs").select("id, status").eq("id", run_id).single().execute()
        run = result.data
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run["status"] != "PENDING_REVIEW":
            raise HTTPException(
                status_code=409,
                detail=f"Run is not in PENDING_REVIEW state (current: {run['status']})"
            )
    except HTTPException:
        raise
    except Exception as e:
        log.warning("review.db_check_failed", run_id=run_id, error=str(e))
        # Proceed anyway if DB check fails (graceful degradation)

    log.info("run.review_submitted", run_id=run_id, decision=req.decision)

    # Resume pipeline in background
    background_tasks.add_task(
        resume_pipeline_background,
        run_id,
        req.decision,
        req.reviewer_notes or "",
    )

    return {
        "run_id": run_id,
        "decision": req.decision,
        "message": f"Pipeline resuming with decision: {req.decision}",
    }


@api_router.get("/api/runs")
async def list_runs():
    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()
    result = db.table("runs").select("*").order("created_at", desc=True).limit(50).execute()
    return result.data


@api_router.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()
    result = db.table("runs").select("*, run_events(*), assets(*)").eq("id", run_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Run not found")
    return result.data


@api_router.get("/api/runs/{run_id}/events")
async def get_run_events(run_id: str):
    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()
    result = db.table("run_events").select("*").eq("run_id", run_id).order("created_at").execute()
    return result.data


@api_router.get("/api/briefs/examples")
async def get_example_briefs():
    """Return the bundled example briefs for the UI editor."""
    briefs = {}
    for brief_file in Path("briefs").glob("*.yaml"):
        with open(brief_file) as f:
            briefs[brief_file.stem] = yaml.safe_load(f)
    return briefs


# ── Mount routers ─────────────────────────────────────────────────────────────

app.include_router(public_router)
app.include_router(api_router)
