"""
CreativeOS — FastAPI Backend v4

New endpoints in v4:
  POST /api/competitor/analyze          — upload screenshot or submit URL
  GET  /api/competitor/{analysis_id}    — get analysis result + counter-brief

  GET  /api/assets/{asset_id}/layers    — get layer URLs for canvas editor
  POST /api/assets/{asset_id}/edit      — text or mask edit (canvas editor)
  GET  /api/assets/{asset_id}/edits     — edit history

  GET  /api/runs/{run_id}/videos        — list generated videos
  POST /api/runs/{run_id}/videos/generate — trigger video generation

  POST /api/runs/{run_id}/publish       — publish to platforms
  GET  /api/runs/{run_id}/publish       — get publish results

  POST /api/workspaces                  — create workspace
  GET  /api/workspaces/{id}             — get workspace + credits
  POST /api/workspaces/{id}/connect/instagram  — OAuth flow
  POST /api/workspaces/{id}/connect/tiktok     — OAuth flow

  POST /api/billing/checkout            — Stripe checkout session
  POST /api/billing/webhook             — Stripe webhook
  GET  /api/billing/usage               — current period usage

Existing endpoints (unchanged from v3):
  POST /api/runs                    — Submit a campaign brief
  GET  /api/runs/{run_id}           — Get run status + report
  GET  /api/runs                    — List all runs
  POST /api/runs/{run_id}/review    — Approve or reject PENDING_REVIEW run
  POST /api/campaigns               — Create a named campaign
  GET  /api/campaigns               — List campaigns
  GET  /api/health                  — Health check (always public)
  GET  /api/briefs/examples         — Example briefs
"""
import asyncio
import uuid
import os
import structlog
import yaml
import json
import base64
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi.staticfiles import StaticFiles

from fastapi import FastAPI, HTTPException, BackgroundTasks, Header, Depends, APIRouter, UploadFile, File, Form
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
    if _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup.pipeline_compile")
    from backend.graph.pipeline import pipeline
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
    title="CreativeOS",
    description="AI-powered creative campaign platform — brief to published in minutes",
    version="4.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

public_router = APIRouter()
api_router = APIRouter(dependencies=[Depends(verify_api_key)])


# ── Request/Response models ───────────────────────────────────────────────────

class RunRequest(BaseModel):
    brief: dict
    image_provider: str | None = None
    llm_provider: str | None = None
    video_mode: str = "slideshow"          # v4: slideshow | ai | none
    publish_platforms: list[str] = []      # v4: ["instagram", "tiktok"]
    scheduled_publish_time: str | None = None  # v4: ISO datetime


class ReviewRequest(BaseModel):
    decision: str
    reviewer_notes: str | None = None


class CampaignCreateRequest(BaseModel):
    name: str
    brand: str
    brand_config: dict = {}
    workspace_id: str | None = None


class AssetEditRequest(BaseModel):
    mode: str                    # "text" | "mask" | "layer"
    instruction: str
    mask_base64: str | None = None   # PNG mask for mask mode
    layer: str | None = None         # "base" | "gradient" | "logo" | "text" for layer mode
    apply_to_all_ratios: bool = False


class VideoGenerateRequest(BaseModel):
    mode: str = "slideshow"      # "slideshow" | "ai"


class PublishRequest(BaseModel):
    platforms: list[str]         # ["instagram", "tiktok"]
    scheduled_time: str | None = None


class WorkspaceCreateRequest(BaseModel):
    name: str
    owner_user_id: str
    plan: str = "free"


class CompetitorAnalyzeRequest(BaseModel):
    screenshots_base64: list[str] = []   # base64-encoded PNG screenshots
    competitor_url: str | None = None    # social handle URL
    brand_context: str = ""
    workspace_id: str | None = None


# ── Background pipeline runner (unchanged from v3) ────────────────────────────

async def run_pipeline_background(run_id: str, state: PipelineState):
    from backend.db.client import get_supabase_admin
    from backend.graph.pipeline import pipeline

    db = get_supabase_admin()
    log.info("pipeline.start", run_id=run_id)

    try:
        db.table("runs").update({"status": "RUNNING"}).eq("id", run_id).execute()

        config = {"configurable": {"thread_id": run_id}}
        final_state = await pipeline.ainvoke(state, config=config)

        review_decision = final_state.get("review_decision")
        if review_decision is None and final_state.get("current_node") == "review_gate":
            log.info("pipeline.interrupted_for_review", run_id=run_id)
            return

        await save_run_report(final_state)

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
                 assets=len(final_state.get("composited_assets", [])),
                 videos=len(final_state.get("video_outputs", [])),
                 published=len(final_state.get("publish_results", [])))

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
    from backend.db.client import get_supabase_admin
    from backend.graph.pipeline import pipeline
    from langgraph.types import Command

    db = get_supabase_admin()
    log.info("pipeline.resume", run_id=run_id, decision=decision)

    try:
        db.table("runs").update({"status": "RUNNING"}).eq("id", run_id).execute()

        config = {"configurable": {"thread_id": run_id}}
        resume_value = {"decision": decision, "reviewer_notes": reviewer_notes or ""}

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
    from backend.db.client import using_local_db
    db_mode = "sqlite" if using_local_db() else "supabase"
    return {
        "status": "ok",
        "version": "4.0.0",
        "auth_enabled": bool(_API_KEY),
        "checks": {
            "db_configured": True,
            "db_mode": db_mode,
            "supabase_configured": settings.supabase_configured,
            "modal_configured": settings.modal_configured,
            "llm_configured": settings.llm_api_key_configured,
        },
        "providers": {
            "llm": settings.llm_provider,
            "llm_model": settings.openrouter_model if settings.llm_provider == "openrouter" else None,
            "image": settings.image_provider,
            "video": settings.video_provider,
            "storage": settings.storage_backend,
            "modal_image_endpoint": bool(settings.modal_image_endpoint),
            "modal_video_endpoint": bool(settings.modal_video_endpoint),
        },
    }


@public_router.get("/api/models")
async def list_models():
    """List all available LLM and image/video models."""
    from backend.providers.openrouter_llm import FREE_MODEL_CATALOG
    from backend.providers.modal_image import MODAL_IMAGE_MODELS
    from backend.providers.modal_video import MODAL_VIDEO_MODELS

    return {
        "llm": {
            "active_provider": settings.llm_provider,
            "active_model": settings.openrouter_model,
            "free_models": FREE_MODEL_CATALOG,
        },
        "image": {
            "active_provider": settings.image_provider,
            "modal_models": MODAL_IMAGE_MODELS,
            "modal_configured": bool(settings.modal_image_endpoint),
        },
        "video": {
            "active_provider": settings.video_provider,
            "modal_models": MODAL_VIDEO_MODELS,
            "modal_configured": bool(settings.modal_video_endpoint),
        },
    }


# ── Existing protected routes (v3, unchanged) ─────────────────────────────────

@api_router.post("/api/campaigns")
async def create_campaign(req: CampaignCreateRequest):
    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()
    result = db.table("campaigns").insert({
        "name": req.name,
        "brand": req.brand,
        "brand_config": req.brand_config,
        "workspace_id": req.workspace_id,
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
    from backend.db.client import get_supabase_admin

    try:
        brief = CampaignBrief.model_validate(req.brief)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid brief: {e}")

    run_id = str(uuid.uuid4())
    db = get_supabase_admin()

    image_provider = req.image_provider or settings.image_provider
    llm_provider = req.llm_provider or settings.llm_provider

    db.table("runs").insert({
        "id": run_id,
        "status": "PENDING",
        "provider_image": image_provider,
        "provider_llm": llm_provider,
        "brief": req.brief,
        "video_mode": req.video_mode,
        "publish_platforms": req.publish_platforms,
        "scheduled_publish_time": req.scheduled_publish_time,
    }).execute()

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
        "competitor_brief": None,
        "video_outputs": [],
        "video_mode": req.video_mode,
        "publish_results": [],
        "publish_platforms": req.publish_platforms,
        "scheduled_publish_time": req.scheduled_publish_time,
        "current_node": "start",
        "errors": [],
        "provider_llm": llm_provider,
        "provider_image": image_provider,
        "storage_backend": settings.storage_backend,
    }

    if req.image_provider:
        os.environ["IMAGE_PROVIDER"] = req.image_provider
    if req.llm_provider:
        os.environ["LLM_PROVIDER"] = req.llm_provider

    background_tasks.add_task(run_pipeline_background, run_id, initial_state)

    log.info("run.created", run_id=run_id, campaign=brief.campaign_id,
             video_mode=req.video_mode, platforms=req.publish_platforms)

    return {
        "run_id": run_id,
        "status": "PENDING",
        "message": "Pipeline started.",
        "providers": {"llm": llm_provider, "image": image_provider},
        "video_mode": req.video_mode,
        "publish_platforms": req.publish_platforms,
    }


@api_router.post("/api/runs/{run_id}/review", status_code=202)
async def review_run(run_id: str, req: ReviewRequest, background_tasks: BackgroundTasks):
    from backend.db.client import get_supabase_admin

    if req.decision not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="decision must be 'approve' or 'reject'")

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

    background_tasks.add_task(
        resume_pipeline_background, run_id, req.decision, req.reviewer_notes or ""
    )

    return {"run_id": run_id, "decision": req.decision, "message": f"Pipeline resuming: {req.decision}"}


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
    result = db.table("runs").select("*").eq("id", run_id).single().execute()
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
    briefs = {}
    for brief_file in Path("briefs").glob("*.yaml"):
        with open(brief_file) as f:
            briefs[brief_file.stem] = yaml.safe_load(f)
    return briefs


# ── v4: Video endpoints ───────────────────────────────────────────────────────

@api_router.get("/api/runs/{run_id}/videos")
async def get_run_videos(run_id: str):
    """List all generated videos for a run."""
    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()
    result = db.table("video_outputs").select("*").eq("run_id", run_id).execute()
    return result.data or []


@api_router.post("/api/runs/{run_id}/videos/generate", status_code=202)
async def generate_videos(run_id: str, req: VideoGenerateRequest, background_tasks: BackgroundTasks):
    """
    Trigger video generation for a completed run.
    Can be called after the pipeline completes to generate/regenerate videos.
    """
    from backend.db.client import get_supabase_admin
    from backend.graph.pipeline import pipeline

    db = get_supabase_admin()
    result = db.table("runs").select("*").eq("id", run_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Run not found")

    run = result.data
    if run["status"] not in ("COMPLETE", "FAILED"):
        raise HTTPException(status_code=409, detail="Run must be COMPLETE to generate videos")

    async def _generate():
        from backend.graph.nodes.video_gen import video_gen_node
        from backend.graph.state import PipelineState

        # Reconstruct minimal state for video_gen
        state: PipelineState = {
            **run.get("run_report", {}),
            "run_id": run_id,
            "campaign_id": run.get("campaign_id", ""),
            "brief": run["brief"],
            "composited_assets": run.get("run_report", {}).get("assets", []),
            "video_mode": req.mode,
            "video_outputs": [],
            "errors": [],
            "current_node": "video_gen",
        }

        result_state = await video_gen_node(state)

        # Save video outputs to DB
        for video in result_state.get("video_outputs", []):
            db.table("video_outputs").insert({
                "run_id": run_id,
                "ratio": video["ratio"],
                "mode": video["mode"],
                "storage_url": video["storage_url"],
                "storage_path": video["storage_path"],
                "duration_s": video["duration_s"],
            }).execute()

    background_tasks.add_task(_generate)
    return {"run_id": run_id, "mode": req.mode, "message": "Video generation started"}


# ── v4: Canvas editor endpoints ───────────────────────────────────────────────

@api_router.get("/api/assets/{asset_id}/layers")
async def get_asset_layers(asset_id: str):
    """
    Return layer URLs for the canvas editor.
    Layers: base, gradient, logo, text.
    """
    from backend.db.client import get_supabase_admin
    from backend.storage.base import get_storage_backend

    db = get_supabase_admin()
    result = db.table("assets").select("*").eq("id", asset_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Asset not found")

    asset = result.data
    storage = get_storage_backend()

    layers = {}
    for layer_name in ("base", "gradient", "logo", "text"):
        path_key = f"layer_{layer_name}_path"
        path = asset.get(path_key)
        if path:
            layers[layer_name] = storage.public_url(path)
        else:
            layers[layer_name] = None

    return {
        "asset_id": asset_id,
        "storage_url": asset.get("storage_url"),
        "layers": layers,
    }


@api_router.post("/api/assets/{asset_id}/edit")
async def edit_asset(asset_id: str, req: AssetEditRequest):
    """
    Edit an asset via the canvas editor.

    Modes:
      text  — instruction-based edit (GPT-5 Image)
      mask  — inpainting on painted mask region
      layer — instant layer swap (no AI needed for text/logo changes)
    """
    from backend.db.client import get_supabase_admin
    from backend.storage.base import get_storage_backend
    from backend.providers.edit import get_edit_provider

    db = get_supabase_admin()
    result = db.table("assets").select("*").eq("id", asset_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Asset not found")

    asset = result.data
    storage = get_storage_backend()

    before_url = asset["storage_url"]
    before_path = asset["storage_path"]

    if req.mode == "layer":
        # Layer swap — re-composite with updated layer content.
        # For text layer: re-render with instruction as new headline.
        # For logo/gradient/base: swap the layer file and re-composite.
        layer_name = req.layer or "text"
        try:
            from backend.graph.nodes.composite import (
                make_text_layer, make_gradient_layer, make_logo_layer,
                _img_to_bytes, _load_font,
            )
            from PIL import Image
            import io as _io

            # Load current composited image to get dimensions
            img_bytes = await storage.load(before_path)
            img = Image.open(_io.BytesIO(img_bytes)).convert("RGBA")
            size = img.size

            if layer_name == "text":
                # Re-render text layer with instruction as new headline
                new_layer = make_text_layer(
                    size=size,
                    headline=req.instruction,
                    tagline=None,
                    font_size_h=64,
                    font_size_t=40,
                    color="#FFFFFF",
                )
                # Load base + gradient + logo layers and re-composite
                base_path = asset.get("layer_base_path")
                gradient_path = asset.get("layer_gradient_path")
                logo_path = asset.get("layer_logo_path")

                base_bytes = await storage.load(base_path) if base_path else None
                gradient_bytes = await storage.load(gradient_path) if gradient_path else None
                logo_bytes = await storage.load(logo_path) if logo_path else None

                composite = Image.open(_io.BytesIO(base_bytes)).convert("RGBA") if base_bytes else Image.new("RGBA", size, (0,0,0,255))
                if gradient_bytes:
                    composite = Image.alpha_composite(composite, Image.open(_io.BytesIO(gradient_bytes)).convert("RGBA"))
                if logo_bytes:
                    composite = Image.alpha_composite(composite, Image.open(_io.BytesIO(logo_bytes)).convert("RGBA"))
                composite = Image.alpha_composite(composite, new_layer)
                edited_bytes = _img_to_bytes(composite.convert("RGB"))

                # Save updated text layer
                text_layer_path = asset.get("layer_text_path", before_path.replace(".png", "_text.png"))
                await storage.save(text_layer_path, _img_to_bytes(new_layer))

            else:
                # For base/gradient/logo: use text_edit as fallback
                provider = get_edit_provider()
                edited_bytes = await provider.text_edit(img_bytes, req.instruction)

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Layer edit failed: {e}")

        after_path = before_path.replace(".png", f"_layer_{req.layer}_{str(uuid.uuid4())[:8]}.png")
        after_url = await storage.save(after_path, edited_bytes)

        db.table("assets").update({
            "storage_url": after_url,
            "storage_path": after_path,
        }).eq("id", asset_id).execute()

        db.table("asset_edits").insert({
            "asset_id": asset_id,
            "run_id": asset["run_id"],
            "edit_type": "layer",
            "instruction": req.instruction,
            "before_url": before_url,
            "after_url": after_url,
            "layer_name": layer_name,
        }).execute()

        return {
            "asset_id": asset_id,
            "mode": "layer",
            "layer": layer_name,
            "before_url": before_url,
            "after_url": after_url,
        }

    # Load current image
    try:
        img_bytes = await storage.load(before_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load asset: {e}")

    provider = get_edit_provider()

    try:
        if req.mode == "text":
            edited_bytes = await provider.text_edit(img_bytes, req.instruction)
        elif req.mode == "mask":
            if not req.mask_base64:
                raise HTTPException(status_code=422, detail="mask_base64 required for mask mode")
            mask_bytes = base64.b64decode(req.mask_base64)
            edited_bytes = await provider.mask_edit(img_bytes, mask_bytes, req.instruction)
        else:
            raise HTTPException(status_code=422, detail=f"Unknown edit mode: {req.mode}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Edit failed: {e}")

    # Save edited image
    edit_id = str(uuid.uuid4())
    after_path = before_path.replace(".png", f"_edit_{edit_id[:8]}.png")
    after_url = await storage.save(after_path, edited_bytes)

    # Update asset storage_url to point to edited version
    db.table("assets").update({
        "storage_url": after_url,
        "storage_path": after_path,
    }).eq("id", asset_id).execute()

    # Save edit history
    db.table("asset_edits").insert({
        "asset_id": asset_id,
        "run_id": asset["run_id"],
        "edit_type": req.mode,
        "instruction": req.instruction,
        "before_url": before_url,
        "after_url": after_url,
    }).execute()

    # Apply to all ratios if requested
    if req.apply_to_all_ratios:
        await _apply_edit_to_all_ratios(
            db, storage, provider, asset, req, edited_bytes, edit_id
        )

    return {
        "asset_id": asset_id,
        "mode": req.mode,
        "before_url": before_url,
        "after_url": after_url,
        "edit_id": edit_id,
    }


@api_router.get("/api/assets/{asset_id}/edits")
async def get_asset_edits(asset_id: str):
    """Return edit history for an asset."""
    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()
    result = db.table("asset_edits").select("*").eq("asset_id", asset_id).order("created_at").execute()
    return result.data or []


async def _apply_edit_to_all_ratios(db, storage, provider, source_asset, req, edited_bytes, edit_id):
    """Apply the same edit to all aspect ratio variants of this asset."""
    # Find sibling assets (same product_id + market, different ratio)
    siblings = db.table("assets").select("*").eq(
        "run_id", source_asset["run_id"]
    ).eq("product_id", source_asset["product_id"]).eq(
        "market", source_asset["market"]
    ).neq("id", source_asset["id"]).execute()

    for sibling in (siblings.data or []):
        try:
            sibling_bytes = await storage.load(sibling["storage_path"])
            if req.mode == "text":
                sibling_edited = await provider.text_edit(sibling_bytes, req.instruction)
            elif req.mode == "mask" and req.mask_base64:
                mask_bytes = base64.b64decode(req.mask_base64)
                sibling_edited = await provider.mask_edit(sibling_bytes, mask_bytes, req.instruction)
            else:
                continue

            after_path = sibling["storage_path"].replace(".png", f"_edit_{edit_id[:8]}.png")
            after_url = await storage.save(after_path, sibling_edited)

            db.table("assets").update({
                "storage_url": after_url,
                "storage_path": after_path,
            }).eq("id", sibling["id"]).execute()

        except Exception as e:
            log.warning("edit.sibling_failed", sibling_id=sibling["id"], error=str(e))


# ── v4: Publish endpoints ─────────────────────────────────────────────────────

@api_router.post("/api/runs/{run_id}/publish", status_code=202)
async def publish_run(run_id: str, req: PublishRequest, background_tasks: BackgroundTasks):
    """Publish a completed run's assets to social platforms."""
    from backend.db.client import get_supabase_admin

    db = get_supabase_admin()
    result = db.table("runs").select("*").eq("id", run_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Run not found")

    if result.data["status"] not in ("COMPLETE",):
        raise HTTPException(status_code=409, detail="Run must be COMPLETE to publish")

    async def _publish():
        from backend.graph.nodes.publish_node import publish_node
        run = result.data
        state = {
            "run_id": run_id,
            "campaign_id": run.get("campaign_id", ""),
            "brief": run["brief"],
            "composited_assets": run.get("run_report", {}).get("assets", []),
            "video_outputs": [],
            "localized_copy": run.get("run_report", {}).get("localized_copy", []),
            "publish_platforms": req.platforms,
            "scheduled_publish_time": req.scheduled_time,
            "publish_results": [],
            "errors": [],
            "current_node": "publish",
        }
        # Load video outputs from DB
        videos = db.table("video_outputs").select("*").eq("run_id", run_id).execute()
        state["video_outputs"] = videos.data or []

        await publish_node(state)

    background_tasks.add_task(_publish)
    return {"run_id": run_id, "platforms": req.platforms, "message": "Publishing started"}


@api_router.get("/api/runs/{run_id}/publish")
async def get_publish_results(run_id: str):
    """Get publish results for a run."""
    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()
    result = db.table("publish_results").select("*").eq("run_id", run_id).execute()
    return result.data or []


# ── v4: Competitor analysis endpoints ─────────────────────────────────────────

@api_router.post("/api/competitor/analyze", status_code=202)
async def analyze_competitor(req: CompetitorAnalyzeRequest, background_tasks: BackgroundTasks):
    """
    Analyze competitor ads from screenshots or a social URL.
    Returns analysis_id immediately; result available via GET.
    """
    from backend.db.client import get_supabase_admin

    analysis_id = str(uuid.uuid4())
    db = get_supabase_admin()

    # Create placeholder record
    db.table("competitor_analyses").insert({
        "id": analysis_id,
        "workspace_id": req.workspace_id,
        "source_type": "url" if req.competitor_url else "screenshot",
        "source_url": req.competitor_url,
        "screenshot_count": len(req.screenshots_base64),
        "counter_strategy": "Analyzing...",
    }).execute()

    async def _analyze():
        from backend.providers.vision import get_vision_provider

        provider = get_vision_provider()
        analyses = []

        for screenshot_b64 in req.screenshots_base64[:5]:
            try:
                img_bytes = base64.b64decode(screenshot_b64)
                extracted_text = await provider.extract_text(img_bytes)
                analysis = await provider.analyze_ad(img_bytes, extracted_text, req.brand_context)
                analyses.append(analysis)
            except Exception as e:
                log.error("competitor.analyze_failed", error=str(e))

        if not analyses:
            db.table("competitor_analyses").update({
                "counter_strategy": "Analysis failed — no valid screenshots processed",
            }).eq("id", analysis_id).execute()
            return

        # Use first analysis (or aggregate if multiple)
        final = analyses[0]
        if len(analyses) > 1:
            from backend.graph.nodes.competitor_analyze import _aggregate_analyses
            final = await _aggregate_analyses(analyses, req.brand_context)

        db.table("competitor_analyses").update({
            "layout_description": final.layout_description,
            "color_palette": final.color_palette,
            "emotional_tone": final.emotional_tone,
            "claims_made": final.claims_made,
            "strengths": final.strengths,
            "weaknesses": final.weaknesses,
            "counter_strategy": final.counter_strategy,
            "style_hints": final.style_hints,
        }).eq("id", analysis_id).execute()

    background_tasks.add_task(_analyze)
    return {"analysis_id": analysis_id, "message": "Analysis started"}


@api_router.get("/api/competitor/{analysis_id}")
async def get_competitor_analysis(analysis_id: str):
    """Get competitor analysis result."""
    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()
    result = db.table("competitor_analyses").select("*").eq("id", analysis_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return result.data


# ── v4: Workspace endpoints ───────────────────────────────────────────────────

@api_router.post("/api/workspaces", status_code=201)
async def create_workspace(req: WorkspaceCreateRequest):
    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()

    # Set initial credits by plan
    plan_credits = {"free": 0, "pro": 500, "agency": 3000, "enterprise": 99999}
    credits = plan_credits.get(req.plan, 0)

    result = db.table("workspaces").insert({
        "name": req.name,
        "owner_user_id": req.owner_user_id,
        "plan": req.plan,
        "credits": credits,
    }).execute()
    return result.data[0]


@api_router.get("/api/workspaces/{workspace_id}")
async def get_workspace(workspace_id: str):
    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()
    result = db.table("workspaces").select(
        "id, name, plan, credits, created_at, "
        "instagram_user_id, tiktok_client_key"
        # Note: access tokens intentionally excluded from GET response
    ).eq("id", workspace_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return result.data


@api_router.post("/api/workspaces/{workspace_id}/connect/instagram")
async def connect_instagram(workspace_id: str, code: str = Form(...)):
    """
    Instagram OAuth callback. Exchange authorization code for access token.
    Frontend redirects to Instagram OAuth, which redirects back here with ?code=...
    """
    import httpx

    client_id = os.getenv("INSTAGRAM_CLIENT_ID", "")
    client_secret = os.getenv("INSTAGRAM_CLIENT_SECRET", "")
    redirect_uri = os.getenv("INSTAGRAM_REDIRECT_URI", "")

    if not all([client_id, client_secret, redirect_uri]):
        raise HTTPException(status_code=500, detail="Instagram OAuth not configured")

    async with httpx.AsyncClient() as client:
        # Exchange code for short-lived token
        resp = await client.post(
            "https://api.instagram.com/oauth/access_token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        resp.raise_for_status()
        token_data = resp.json()

        # Exchange for long-lived token (60 days)
        ll_resp = await client.get(
            "https://graph.instagram.com/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": client_secret,
                "access_token": token_data["access_token"],
            },
        )
        ll_resp.raise_for_status()
        ll_data = ll_resp.json()

    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()
    db.table("workspaces").update({
        "instagram_access_token": ll_data["access_token"],
        "instagram_user_id": str(token_data.get("user_id", "")),
    }).eq("id", workspace_id).execute()

    return {"status": "connected", "platform": "instagram"}


@api_router.post("/api/workspaces/{workspace_id}/connect/tiktok")
async def connect_tiktok(workspace_id: str, code: str = Form(...)):
    """TikTok OAuth callback. Exchange authorization code for access token."""
    import httpx

    client_key = os.getenv("TIKTOK_CLIENT_KEY", "")
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET", "")
    redirect_uri = os.getenv("TIKTOK_REDIRECT_URI", "")

    if not all([client_key, client_secret, redirect_uri]):
        raise HTTPException(status_code=500, detail="TikTok OAuth not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        resp.raise_for_status()
        token_data = resp.json().get("data", {})

    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()
    db.table("workspaces").update({
        "tiktok_access_token": token_data.get("access_token"),
        "tiktok_client_key": client_key,
    }).eq("id", workspace_id).execute()

    return {"status": "connected", "platform": "tiktok"}


# ── v4: Billing endpoints ─────────────────────────────────────────────────────

@api_router.post("/api/billing/checkout")
async def create_checkout(workspace_id: str, plan: str):
    """Create a Stripe checkout session for plan upgrade."""
    import stripe

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    PRICE_IDS = {
        "pro":      os.getenv("STRIPE_PRICE_PRO", ""),
        "agency":   os.getenv("STRIPE_PRICE_AGENCY", ""),
        "enterprise": os.getenv("STRIPE_PRICE_ENTERPRISE", ""),
    }

    price_id = PRICE_IDS.get(plan)
    if not price_id:
        raise HTTPException(status_code=422, detail=f"Unknown plan: {plan}")

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/billing/success",
        cancel_url=f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/billing/cancel",
        metadata={"workspace_id": workspace_id, "plan": plan},
    )

    return {"checkout_url": session.url, "session_id": session.id}


@api_router.post("/api/billing/webhook")
async def stripe_webhook(request):
    """Stripe webhook — update workspace plan and credits on subscription events."""
    import stripe

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    body = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(body, sig, webhook_secret)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    from backend.db.client import get_supabase_admin
    db = get_supabase_admin()

    PLAN_CREDITS = {"pro": 500, "agency": 3000, "enterprise": 99999}

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        workspace_id = session["metadata"].get("workspace_id")
        plan = session["metadata"].get("plan")
        if workspace_id and plan:
            db.table("workspaces").update({
                "plan": plan,
                "credits": PLAN_CREDITS.get(plan, 0),
                "stripe_subscription_id": session.get("subscription"),
            }).eq("id", workspace_id).execute()

    elif event["type"] == "invoice.payment_succeeded":
        # Monthly renewal — reset credits
        subscription_id = event["data"]["object"].get("subscription")
        if subscription_id:
            ws = db.table("workspaces").select("id, plan").eq(
                "stripe_subscription_id", subscription_id
            ).single().execute()
            if ws.data:
                db.table("workspaces").update({
                    "credits": PLAN_CREDITS.get(ws.data["plan"], 0),
                }).eq("id", ws.data["id"]).execute()

    elif event["type"] in ("customer.subscription.deleted", "customer.subscription.paused"):
        subscription_id = event["data"]["object"]["id"]
        db.table("workspaces").update({
            "plan": "free",
            "credits": 0,
        }).eq("stripe_subscription_id", subscription_id).execute()

    return {"received": True}


@api_router.get("/api/billing/usage")
async def get_billing_usage(workspace_id: str):
    """Get current period credit usage for a workspace."""
    from backend.db.client import get_supabase_admin
    from datetime import datetime, timezone

    db = get_supabase_admin()

    # Get workspace
    ws = db.table("workspaces").select("plan, credits").eq("id", workspace_id).single().execute()
    if not ws.data:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Get this month's billing events
    start_of_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0).isoformat()
    events = db.table("billing_events").select("event_type, credits_used").eq(
        "workspace_id", workspace_id
    ).gte("created_at", start_of_month).execute()

    total_used = sum(e["credits_used"] for e in (events.data or []))

    return {
        "workspace_id": workspace_id,
        "plan": ws.data["plan"],
        "credits_remaining": ws.data["credits"],
        "credits_used_this_month": total_used,
        "events": events.data or [],
    }




# ── Migration endpoint ────────────────────────────────────────────────────────

@api_router.post("/api/migrate")
async def run_migrations():
    """
    Run Supabase schema migrations.
    Tries multiple approaches:
    1. Direct psycopg2 via Supabase pooler (needs DB password in SUPABASE_DB_PASSWORD env)
    2. Supabase Management API (needs PAT in SUPABASE_ACCESS_TOKEN env)
    3. Returns SQL for manual execution if both fail
    """
    import httpx
    from pathlib import Path
    from backend.config import settings
    import os

    if not settings.supabase_configured:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    supabase_url = settings.supabase_url.rstrip("/")
    service_key = settings.supabase_service_key_resolved
    project_ref = supabase_url.replace("https://", "").replace(".supabase.co", "")

    db_password = os.getenv("SUPABASE_DB_PASSWORD", "")
    access_token = os.getenv("SUPABASE_ACCESS_TOKEN", "")

    sql_files = [
        ("schema", Path("/app/backend/db/schema.sql")),
        ("storage_bucket", Path("/app/backend/db/storage_bucket.sql")),
    ]

    results = {}

    # Approach 1: psycopg2 via pooler (needs DB password)
    if db_password:
        try:
            import psycopg2
            conn = psycopg2.connect(
                host="aws-0-us-east-1.pooler.supabase.com",
                port=6543,
                dbname="postgres",
                user=f"postgres.{project_ref}",
                password=db_password,
                connect_timeout=15,
                sslmode="require"
            )
            conn.autocommit = True
            cur = conn.cursor()
            for name, sql_path in sql_files:
                if not sql_path.exists():
                    results[name] = {"status": "skipped", "reason": "file not found"}
                    continue
                sql = sql_path.read_text()
                try:
                    cur.execute(sql)
                    results[name] = {"status": "ok", "method": "psycopg2"}
                except Exception as e:
                    results[name] = {"status": "error", "method": "psycopg2", "error": str(e)[:300]}
            conn.close()
            results["method"] = "psycopg2_pooler"
            return {"migration_results": results}
        except Exception as e:
            results["psycopg2_error"] = str(e)[:200]

    # Approach 2: Supabase Management API (needs PAT)
    if access_token:
        async with httpx.AsyncClient(timeout=60) as client:
            for name, sql_path in sql_files:
                if not sql_path.exists():
                    results[name] = {"status": "skipped", "reason": "file not found"}
                    continue
                sql = sql_path.read_text()
                try:
                    resp = await client.post(
                        f"https://api.supabase.com/v1/projects/{project_ref}/database/query",
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                        },
                        json={"query": sql},
                    )
                    results[name] = {
                        "status": "ok" if resp.status_code < 400 else "error",
                        "http_status": resp.status_code,
                        "method": "management_api",
                    }
                except Exception as e:
                    results[name] = {"status": "error", "method": "management_api", "error": str(e)[:200]}
        results["method"] = "management_api"
        return {"migration_results": results}

    # Neither credential available — return instructions
    schema_sql = sql_files[0][1].read_text() if sql_files[0][1].exists() else ""
    storage_sql = sql_files[1][1].read_text() if sql_files[1][1].exists() else ""

    return {
        "migration_results": {
            "status": "credentials_needed",
            "message": "Set SUPABASE_DB_PASSWORD or SUPABASE_ACCESS_TOKEN env var on Render, then POST /api/migrate again",
            "options": {
                "option_a": "Set SUPABASE_DB_PASSWORD = your postgres DB password (from Supabase dashboard → Settings → Database)",
                "option_b": "Set SUPABASE_ACCESS_TOKEN = your Supabase PAT (from supabase.com/dashboard/account/tokens)",
                "option_c": "Run SQL manually at https://supabase.com/dashboard/project/cllqahmtyvdcbyyrouxx/sql/new",
            },
            "sql_dashboard_url": f"https://supabase.com/dashboard/project/{project_ref}/sql/new",
            "schema_sql_preview": schema_sql[:500] + "..." if len(schema_sql) > 500 else schema_sql,
        }
    }

# ── Static file serving ───────────────────────────────────────────────────────

_OUTPUTS_DIR = Path(os.getenv("OUTPUTS_DIR", "/app/outputs"))
_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(_OUTPUTS_DIR)), name="outputs")

# ── Mount routers ─────────────────────────────────────────────────────────────

app.include_router(public_router)
app.include_router(api_router)
