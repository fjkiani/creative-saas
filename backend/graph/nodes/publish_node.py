"""
Node 9: publish — CreativeOS v4.

Final pipeline node. Publishes composited assets and/or videos to
Instagram and TikTok.

Publishing strategy:
  - Each market gets its own post (localized copy as caption)
  - Aspect ratio selection: 1:1 for Instagram feed, 9:16 for Reels/TikTok
  - Videos published as Reels (Instagram) and TikTok videos
  - Images published as feed posts (Instagram) or carousels

State input:  composited_assets, video_outputs, publish_platforms,
              scheduled_publish_time, localized_copy
State output: publish_results (list of PublishResult dicts)

Workspace credentials are loaded from the DB (workspace table).
If no credentials are configured, publish is skipped with a warning.
"""
import asyncio
import structlog
from backend.graph.state import (
    PipelineState, CampaignBrief, CompositedAsset, VideoOutput,
    LocalizedCopy, PublishResult,
)
from backend.graph.nodes._broadcast import broadcast

log = structlog.get_logger(__name__)


async def publish_node(state: PipelineState) -> PipelineState:
    """
    Publish assets to configured platforms.
    Skips gracefully if no platforms configured or no credentials.
    """
    run_id = state["run_id"]
    platforms = state.get("publish_platforms", [])
    scheduled_time = state.get("scheduled_publish_time")
    
    if not platforms:
        log.info("publish.skipped", run_id=run_id, reason="no platforms configured")
        return {**state, "publish_results": [], "current_node": "publish"}
    
    await broadcast(run_id, "publish", "STARTED", {
        "message": f"Publishing to {', '.join(platforms)}...",
        "platforms": platforms,
        "scheduled": scheduled_time is not None,
    })
    
    brief = CampaignBrief.model_validate(state["brief"])
    composited_assets = [
        CompositedAsset.model_validate(a)
        for a in state.get("composited_assets", [])
    ]
    video_outputs = [
        VideoOutput.model_validate(v)
        for v in state.get("video_outputs", [])
    ]
    localized_copies = {
        (c["product_id"], c["market"]): LocalizedCopy.model_validate(c)
        for c in state.get("localized_copy", [])
    }
    
    # Load workspace credentials from DB
    workspace_creds = await _load_workspace_credentials(run_id)
    
    publish_results: list[dict] = []
    tasks = []
    
    for platform in platforms:
        if platform == "instagram":
            ig_token = workspace_creds.get("instagram_access_token")
            ig_user_id = workspace_creds.get("instagram_user_id")
            if not ig_token or not ig_user_id:
                log.warning("publish.instagram.no_credentials", run_id=run_id)
                publish_results.append(PublishResult(
                    platform="instagram",
                    market="all",
                    status="failed",
                    error="Instagram credentials not configured. Connect via workspace settings.",
                ).model_dump())
                continue
            
            from backend.providers.publish import InstagramPublishProvider
            ig_provider = InstagramPublishProvider(ig_token, ig_user_id)
            
            # Publish videos as Reels (9:16 ratio)
            for video in video_outputs:
                if video.ratio == "9:16":
                    tasks.append(_publish_video_to_instagram(
                        ig_provider, video, brief, localized_copies, scheduled_time
                    ))
            
            # Publish images as feed posts (1:1 ratio, one per market)
            market_assets: dict[str, list[CompositedAsset]] = {}
            for asset in composited_assets:
                if asset.aspect_ratio == "1:1":
                    if asset.market not in market_assets:
                        market_assets[asset.market] = []
                    market_assets[asset.market].append(asset)
            
            for market, assets in market_assets.items():
                if len(assets) == 1:
                    tasks.append(_publish_image_to_instagram(
                        ig_provider, assets[0], brief, localized_copies, scheduled_time
                    ))
                elif len(assets) > 1:
                    tasks.append(_publish_carousel_to_instagram(
                        ig_provider, assets, brief, localized_copies, market, scheduled_time
                    ))
        
        elif platform == "tiktok":
            tt_token = workspace_creds.get("tiktok_access_token")
            if not tt_token:
                log.warning("publish.tiktok.no_credentials", run_id=run_id)
                publish_results.append(PublishResult(
                    platform="tiktok",
                    market="all",
                    status="failed",
                    error="TikTok credentials not configured. Connect via workspace settings.",
                ).model_dump())
                continue
            
            from backend.providers.publish import TikTokPublishProvider
            tt_provider = TikTokPublishProvider(tt_token)
            
            # Publish 9:16 videos to TikTok
            for video in video_outputs:
                if video.ratio == "9:16":
                    tasks.append(_publish_video_to_tiktok(
                        tt_provider, video, brief, localized_copies, scheduled_time
                    ))
    
    # Execute all publish tasks concurrently
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                log.error("publish.task_failed", error=str(result))
                publish_results.append(PublishResult(
                    platform="unknown", market="unknown", status="failed",
                    error=str(result),
                ).model_dump())
            elif isinstance(result, list):
                publish_results.extend([r.model_dump() for r in result])
            elif isinstance(result, PublishResult):
                publish_results.append(result.model_dump())
    
    # Save publish results to DB
    await _save_publish_results(run_id, publish_results)
    
    published_count = sum(1 for r in publish_results if r.get("status") in ("published", "scheduled"))
    failed_count = sum(1 for r in publish_results if r.get("status") == "failed")
    
    log.info("publish.complete",
             run_id=run_id,
             published=published_count,
             failed=failed_count)
    
    await broadcast(run_id, "publish", "COMPLETED", {
        "published_count": published_count,
        "failed_count": failed_count,
        "results": publish_results,
    })
    
    return {
        **state,
        "publish_results": publish_results,
        "current_node": "publish",
    }


# ── Helper publish functions ──────────────────────────────────────────────────

def _build_caption(
    brief: CampaignBrief,
    localized_copies: dict,
    market: str,
    product_id: str | None = None,
) -> str:
    """Build a caption from localized copy."""
    # Try to find localized copy for this market
    for (pid, mkt), copy in localized_copies.items():
        if mkt == market and (product_id is None or pid == product_id):
            parts = [copy.headline]
            if copy.tagline:
                parts.append(copy.tagline)
            if copy.cta:
                parts.append(f"\n{copy.cta}")
            return "\n".join(parts)
    
    # Fallback to brand name
    return f"{brief.brand} — {brief.objective or 'New Campaign'}"


async def _publish_image_to_instagram(
    provider,
    asset: CompositedAsset,
    brief: CampaignBrief,
    localized_copies: dict,
    scheduled_time: str | None,
) -> PublishResult:
    caption = _build_caption(brief, localized_copies, asset.market, asset.product_id)
    return await provider.publish_image(
        image_url=asset.storage_url,
        caption=caption,
        market=asset.market,
        scheduled_time=scheduled_time,
    )


async def _publish_carousel_to_instagram(
    provider,
    assets: list[CompositedAsset],
    brief: CampaignBrief,
    localized_copies: dict,
    market: str,
    scheduled_time: str | None,
) -> PublishResult:
    caption = _build_caption(brief, localized_copies, market)
    image_urls = [a.storage_url for a in assets]
    return await provider.publish_carousel(
        image_urls=image_urls,
        caption=caption,
        market=market,
        scheduled_time=scheduled_time,
    )


async def _publish_video_to_instagram(
    provider,
    video: VideoOutput,
    brief: CampaignBrief,
    localized_copies: dict,
    scheduled_time: str | None,
) -> PublishResult:
    # Use first available market's copy for video caption
    caption = _build_caption(brief, localized_copies, "us") or brief.brand
    return await provider.publish_video(
        video_url=video.storage_url,
        caption=caption,
        market="all",
        scheduled_time=scheduled_time,
    )


async def _publish_video_to_tiktok(
    provider,
    video: VideoOutput,
    brief: CampaignBrief,
    localized_copies: dict,
    scheduled_time: str | None,
) -> PublishResult:
    caption = _build_caption(brief, localized_copies, "us") or brief.brand
    return await provider.publish_video(
        video_url=video.storage_url,
        caption=caption,
        market="all",
        scheduled_time=scheduled_time,
    )


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _load_workspace_credentials(run_id: str) -> dict:
    """Load workspace credentials from DB for this run."""
    try:
        from backend.db.client import get_supabase_admin
        db = get_supabase_admin()
        
        # Get workspace_id from run
        run_result = db.table("runs").select("workspace_id").eq("id", run_id).single().execute()
        workspace_id = run_result.data.get("workspace_id") if run_result.data else None
        
        if not workspace_id:
            return {}
        
        ws_result = db.table("workspaces").select(
            "instagram_access_token, instagram_user_id, tiktok_access_token"
        ).eq("id", workspace_id).single().execute()
        
        return ws_result.data or {}
    except Exception as e:
        log.warning("publish.credentials_load_failed", error=str(e))
        return {}


async def _save_publish_results(run_id: str, results: list[dict]) -> None:
    """Save publish results to DB."""
    try:
        from backend.db.client import get_supabase_admin
        from datetime import datetime, timezone
        
        db = get_supabase_admin()
        rows = [
            {
                "run_id": run_id,
                "platform": r.get("platform"),
                "market": r.get("market"),
                "post_url": r.get("post_url"),
                "post_id": r.get("post_id"),
                "published_at": r.get("published_at"),
                "status": r.get("status"),
            }
            for r in results
        ]
        if rows:
            db.table("publish_results").insert(rows).execute()
    except Exception as e:
        log.warning("publish.save_results_failed", error=str(e))
