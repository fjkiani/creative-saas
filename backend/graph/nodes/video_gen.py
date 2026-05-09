"""
Node 8: video_gen — CreativeOS v4.

Generates video trailers from the composited assets.

Two modes:
  Mode A — slideshow (default, $0): moviepy Ken Burns + cross-dissolve
  Mode B — ai: per-image AI motion via WanVideoProvider (OpenRouter)

Runs after compliance_post. Groups assets by aspect ratio and generates
one video per ratio (1:1, 9:16, 16:9).

State input:  composited_assets, video_mode
State output: video_outputs (list of VideoOutput dicts)
"""
import asyncio
import os
import tempfile
import structlog
from pathlib import Path
from backend.graph.state import PipelineState, CampaignBrief, CompositedAsset, VideoOutput
from backend.providers.video import get_video_provider
from backend.storage.base import get_storage_backend
from backend.graph.nodes._broadcast import broadcast

log = structlog.get_logger(__name__)


async def video_gen_node(state: PipelineState) -> PipelineState:
    """
    Generate video trailers from composited assets.
    Groups by aspect ratio, generates one video per ratio.
    """
    run_id = state["run_id"]
    video_mode = state.get("video_mode", "slideshow")
    
    if video_mode == "none":
        log.info("video_gen.skipped", run_id=run_id, reason="video_mode=none")
        return {**state, "video_outputs": [], "current_node": "video_gen"}
    
    composited_assets = [
        CompositedAsset.model_validate(a)
        for a in state.get("composited_assets", [])
    ]
    
    if not composited_assets:
        log.warning("video_gen.no_assets", run_id=run_id)
        return {**state, "video_outputs": [], "current_node": "video_gen"}
    
    await broadcast(run_id, "video_gen", "STARTED", {
        "message": f"Generating {video_mode} videos...",
        "asset_count": len(composited_assets),
        "mode": video_mode,
    })
    
    storage = get_storage_backend()
    provider = get_video_provider(video_mode)
    
    # Group assets by aspect ratio
    ratio_groups: dict[str, list[CompositedAsset]] = {}
    for asset in composited_assets:
        ratio = asset.aspect_ratio
        if ratio not in ratio_groups:
            ratio_groups[ratio] = []
        ratio_groups[ratio].append(asset)
    
    video_outputs: list[dict] = []
    
    for ratio, assets in ratio_groups.items():
        try:
            log.info("video_gen.ratio_start", ratio=ratio, asset_count=len(assets))
            
            # Download images to temp files
            temp_paths = []
            for asset in assets:
                try:
                    img_bytes = await storage.load(asset.storage_path)
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        tmp.write(img_bytes)
                        temp_paths.append(tmp.name)
                except Exception as e:
                    log.warning("video_gen.asset_load_failed",
                                path=asset.storage_path, error=str(e))
            
            if not temp_paths:
                log.warning("video_gen.no_images_for_ratio", ratio=ratio)
                continue
            
            # Generate video
            video_bytes, duration_s = await provider.generate_slideshow(
                image_paths=temp_paths,
                ratio=ratio,
                run_id=run_id,
            )
            
            # Upload to storage
            ratio_key = ratio.replace(":", "x")
            storage_path = f"{run_id}/videos/{ratio_key}_{video_mode}.mp4"
            video_url = await storage.save(
                storage_path,
                video_bytes,
                content_type="video/mp4",
            )
            
            video_output = VideoOutput(
                ratio=ratio,
                mode=video_mode,
                storage_url=video_url,
                storage_path=storage_path,
                duration_s=duration_s,
            )
            video_outputs.append(video_output.model_dump())
            
            log.info("video_gen.ratio_done",
                     ratio=ratio, duration_s=duration_s, url=video_url)
            
            await broadcast(run_id, "video_gen", "PROGRESS", {
                "ratio": ratio,
                "duration_s": duration_s,
                "video_url": video_url,
            })
            
        except Exception as e:
            log.error("video_gen.ratio_failed", ratio=ratio, error=str(e))
            state["errors"] = state.get("errors", []) + [f"video_gen {ratio}: {e}"]
        finally:
            # Clean up temp files
            for p in temp_paths:
                Path(p).unlink(missing_ok=True)
    
    log.info("video_gen.complete", run_id=run_id, videos=len(video_outputs))
    await broadcast(run_id, "video_gen", "COMPLETED", {
        "video_count": len(video_outputs),
        "mode": video_mode,
        "videos": video_outputs,
    })
    
    return {
        **state,
        "video_outputs": video_outputs,
        "current_node": "video_gen",
    }
