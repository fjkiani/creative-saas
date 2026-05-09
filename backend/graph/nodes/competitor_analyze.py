"""
Node 0: competitor_analyze (optional entry point) — CreativeOS v4.

Takes a competitor ad screenshot (or list of screenshots from URL scraping)
and produces a counter-brief that feeds into the enrich node as style_hints.

Flow:
  screenshot bytes → OCR (extract text) → vision analysis → counter-brief
  counter-brief → CampaignBrief.style_hints → enrich node

This node is OPTIONAL — if no competitor data is provided, the pipeline
runs normally from enrich without any style_hints override.

Standalone usage (outside pipeline):
  POST /api/competitor/analyze  →  analysis_id
  GET  /api/competitor/{id}     →  CompetitorAnalysis + counter-brief
"""
import structlog
from backend.graph.state import PipelineState, CampaignBrief, CompetitorAnalysis
from backend.providers.vision import get_vision_provider
from backend.graph.nodes._broadcast import broadcast

log = structlog.get_logger(__name__)


async def competitor_analyze_node(state: PipelineState) -> PipelineState:
    """
    Analyze competitor ads and inject counter-brief into style_hints.
    
    Expects state["brief"]["competitor_screenshots"] to be a list of
    base64-encoded PNG strings, or state["brief"]["competitor_url"] to be
    a social handle URL for scraping.
    
    If neither is present, this node is a no-op.
    """
    run_id = state["run_id"]
    brief_dict = state["brief"]
    
    # Check if competitor data was provided
    competitor_screenshots = brief_dict.get("competitor_screenshots", [])
    competitor_url = brief_dict.get("competitor_url")
    
    if not competitor_screenshots and not competitor_url:
        log.info("competitor_analyze.skipped", run_id=run_id, reason="no competitor data provided")
        return {**state, "competitor_brief": None, "current_node": "competitor_analyze"}
    
    await broadcast(run_id, "competitor_analyze", "STARTED", {
        "message": "Analyzing competitor ads...",
        "screenshot_count": len(competitor_screenshots),
        "url": competitor_url,
    })
    
    provider = get_vision_provider()
    brief = CampaignBrief.model_validate(brief_dict)
    brand_context = f"Brand: {brief.brand}. Objective: {brief.objective or 'brand awareness'}."
    
    analyses = []
    
    # Process screenshots
    for i, screenshot_b64 in enumerate(competitor_screenshots[:5]):  # max 5 screenshots
        try:
            import base64
            img_bytes = base64.b64decode(screenshot_b64)
            
            # Step 1: OCR
            extracted_text = await provider.extract_text(img_bytes)
            log.info("competitor_analyze.ocr_done", index=i, text_length=len(extracted_text))
            
            # Step 2: Vision analysis
            analysis = await provider.analyze_ad(img_bytes, extracted_text, brand_context)
            analyses.append(analysis)
            log.info("competitor_analyze.analysis_done", index=i, tone=analysis.emotional_tone)
            
        except Exception as e:
            log.error("competitor_analyze.screenshot_failed", index=i, error=str(e))
            state["errors"] = state.get("errors", []) + [f"competitor_analyze screenshot {i}: {e}"]
    
    # URL scraping (Apify or direct API)
    if competitor_url and not analyses:
        try:
            scraped_analyses = await _scrape_url(competitor_url, provider, brand_context)
            analyses.extend(scraped_analyses)
        except Exception as e:
            log.warning("competitor_analyze.url_scrape_failed", url=competitor_url, error=str(e))
    
    if not analyses:
        log.warning("competitor_analyze.no_analyses", run_id=run_id)
        return {**state, "competitor_brief": None, "current_node": "competitor_analyze"}
    
    # Aggregate: if multiple analyses, synthesize a unified counter-brief
    if len(analyses) == 1:
        final_analysis = analyses[0]
    else:
        final_analysis = await _aggregate_analyses(analyses, brand_context)
    
    # Inject style_hints into brief
    updated_brief = {**brief_dict, "style_hints": final_analysis.style_hints}
    
    log.info("competitor_analyze.complete",
             run_id=run_id,
             analyses_count=len(analyses),
             counter_strategy=final_analysis.counter_strategy[:100])
    
    await broadcast(run_id, "competitor_analyze", "COMPLETED", {
        "analyses_count": len(analyses),
        "emotional_tone": final_analysis.emotional_tone,
        "counter_strategy": final_analysis.counter_strategy,
        "style_hints": final_analysis.style_hints,
    })
    
    return {
        **state,
        "brief": updated_brief,
        "competitor_brief": final_analysis.model_dump(),
        "current_node": "competitor_analyze",
    }


async def _scrape_url(url: str, provider, brand_context: str) -> list[CompetitorAnalysis]:
    """
    Scrape competitor social profile and analyze recent posts.
    Uses Apify Instagram scraper if APIFY_API_TOKEN is set,
    otherwise returns empty list (graceful degradation).
    """
    import os
    import httpx
    import base64
    
    apify_token = os.getenv("APIFY_API_TOKEN", "")
    if not apify_token:
        log.warning("competitor_analyze.apify_not_configured",
                    message="Set APIFY_API_TOKEN to enable URL scraping")
        return []
    
    # Detect platform from URL
    if "instagram.com" in url or url.startswith("@"):
        handle = url.split("/")[-1].lstrip("@")
        actor_id = "apify/instagram-profile-scraper"
        run_input = {
            "usernames": [handle],
            "resultsLimit": 12,
        }
    elif "tiktok.com" in url:
        handle = url.split("@")[-1].split("/")[0]
        actor_id = "clockworks/tiktok-scraper"
        run_input = {
            "profiles": [handle],
            "resultsPerPage": 12,
        }
    else:
        log.warning("competitor_analyze.unsupported_url", url=url)
        return []
    
    async with httpx.AsyncClient(timeout=120) as client:
        # Start Apify run
        run_resp = await client.post(
            f"https://api.apify.com/v2/acts/{actor_id}/runs",
            params={"token": apify_token},
            json=run_input,
        )
        run_resp.raise_for_status()
        run_id_apify = run_resp.json()["data"]["id"]
        
        # Poll for completion
        import asyncio
        for _ in range(24):
            await asyncio.sleep(5)
            status_resp = await client.get(
                f"https://api.apify.com/v2/actor-runs/{run_id_apify}",
                params={"token": apify_token},
            )
            status = status_resp.json()["data"]["status"]
            if status == "SUCCEEDED":
                break
            elif status in ("FAILED", "ABORTED"):
                log.error("competitor_analyze.apify_failed", status=status)
                return []
        
        # Get results
        dataset_id = status_resp.json()["data"]["defaultDatasetId"]
        items_resp = await client.get(
            f"https://api.apify.com/v2/datasets/{dataset_id}/items",
            params={"token": apify_token, "limit": 12},
        )
        items = items_resp.json()
    
    # Analyze each post image
    analyses = []
    for item in items[:6]:  # analyze first 6 posts
        img_url = item.get("displayUrl") or item.get("thumbnailUrl") or item.get("coverImageUrl")
        if not img_url:
            continue
        try:
            async with httpx.AsyncClient(timeout=15) as dl:
                img_resp = await dl.get(img_url)
                img_bytes = img_resp.content
            
            text = item.get("caption", "") or item.get("text", "")
            analysis = await provider.analyze_ad(img_bytes, text, brand_context)
            analyses.append(analysis)
        except Exception as e:
            log.warning("competitor_analyze.post_failed", url=img_url, error=str(e))
    
    return analyses


async def _aggregate_analyses(analyses: list[CompetitorAnalysis], brand_context: str) -> CompetitorAnalysis:
    """
    Aggregate multiple competitor analyses into a single unified counter-brief.
    Uses LLM to synthesize patterns across multiple posts.
    """
    import os
    import json
    import httpx
    
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        # Simple aggregation without LLM
        return analyses[0]
    
    summaries = [
        {
            "tone": a.emotional_tone,
            "claims": a.claims_made,
            "strengths": a.strengths,
            "weaknesses": a.weaknesses,
        }
        for a in analyses
    ]
    
    prompt = f"""You analyzed {len(analyses)} competitor ads. Here are the individual analyses:

{json.dumps(summaries, indent=2)}

Brand context: {brand_context}

Synthesize these into ONE unified counter-brief. Identify:
1. The consistent patterns across their ads (what they always do)
2. The consistent weaknesses (what they always miss)
3. The single most powerful counter-strategy

Return JSON with: layout_description, color_palette (array), emotional_tone, 
claims_made (array), strengths (array), weaknesses (array), counter_strategy, 
style_hints (dict with: visual_style, mood, color_direction, tone_direction, cta_direction)"""
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json={
                "model": "meta-llama/llama-3.3-70b-instruct:free",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    
    import json as json_mod
    try:
        agg_dict = json_mod.loads(content)
        return CompetitorAnalysis.model_validate(agg_dict)
    except Exception:
        return analyses[0]  # fallback to first analysis
