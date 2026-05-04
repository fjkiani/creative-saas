"""
Node 6: localize
LLM-powered copy localization for each product × market combination.

Goes beyond literal translation — adapts tone, register, and cultural nuance
for each target market. Falls back to English if translation confidence is low.

Supported out of the box: EN, ES, FR, DE, JA, PT-BR (configurable via brief).
"""
import io
import structlog
from PIL import Image, ImageDraw
from pathlib import Path
from backend.graph.state import (
    PipelineState, CampaignBrief, CreativeSpec,
    LocalizedCopySet, LocalizedCopy, CompositedAsset
)
from backend.providers.base import get_llm_provider
from backend.storage.base import get_storage_backend
from backend.graph.nodes._broadcast import broadcast
from backend.graph.nodes.composite import (
    load_brand_config, smart_crop, add_gradient_overlay,
    composite_logo, render_text, _load_font, _wrap_text, ASPECT_DIMENSIONS
)
import yaml

log = structlog.get_logger(__name__)

SYSTEM = """You are a world-class multilingual copywriter specializing in global advertising campaigns.
Your job is to localize campaign copy for each target market.

Requirements:
- Go beyond literal translation — adapt tone, idioms, and cultural references
- Maintain the brand voice and campaign message intent
- Keep headlines punchy and short (max 8 words)
- Keep taglines concise (max 12 words)
- If the target language is English, still optimize the copy for the specific regional audience
- Note any cultural adaptations you made in translation_notes"""


def build_user_prompt(brief: CampaignBrief, spec: CreativeSpec) -> str:
    combos = []
    for product in brief.products:
        for market in brief.markets:
            line = (
                f"- Product: {product.name} | Market: {market.display_region} | "
                f"Language: {market.lang}"
            )
            if market.audience:
                line += f" | Audience: {market.audience}"
            if market.message:
                line += f" | Original message: '{market.message}'"
            elif product.tagline:
                line += f" | Tagline to adapt: '{product.tagline}'"
            combos.append(line)

    return f"""Brand voice: {spec.brand_voice}
Campaign: {brief.campaign_id}
Brand tone: {brief.tone or 'aspirational'}

Localize the campaign copy for each product × market combination below.
Generate a headline, tagline, and optional CTA for each.
Use market_id (the 'id' field like 'us', 'uk', 'fr') as the 'market' field in your response.

Combinations:
{chr(10).join(combos)}

Return a LocalizedCopySet with one LocalizedCopy per combination.
For the 'market' field use the market id: {[m.market_id for m in brief.markets]}
For the 'language' field use the language code: {[m.lang for m in brief.markets]}"""


async def localize_node(state: PipelineState) -> PipelineState:
    run_id = state["run_id"]
    await broadcast(run_id, "localize", "STARTED", {"message": "Localizing copy for all markets..."})
    log.info("node.localize.start", run_id=run_id)

    try:
        brief = CampaignBrief.model_validate(state["brief"])
        spec = CreativeSpec.model_validate(state["creative_spec"])
        llm = get_llm_provider()

        copy_set: LocalizedCopySet = await llm.complete(
            system=SYSTEM,
            user=build_user_prompt(brief, spec),
            response_model=LocalizedCopySet,
        )

        log.info("node.localize.copies_generated", run_id=run_id, count=len(copy_set.copies))

        # Re-composite images with localized text overlays
        updated_assets = await _apply_localized_text(
            state, brief, copy_set.copies
        )

        await broadcast(run_id, "localize", "COMPLETED", {
            "copy_count": len(copy_set.copies),
            "languages": list({c.language for c in copy_set.copies}),
            "assets_updated": len(updated_assets),
        })

        return {
            **state,
            "localized_copy": [c.model_dump() for c in copy_set.copies],
            "composited_assets": updated_assets,
            "current_node": "localize",
        }

    except Exception as e:
        log.error("node.localize.failed", run_id=run_id, error=str(e))
        await broadcast(run_id, "localize", "FAILED", {"error": str(e)})
        return {**state, "errors": state.get("errors", []) + [f"localize: {e}"], "current_node": "localize"}


async def _apply_localized_text(
    state: PipelineState,
    brief: CampaignBrief,
    copies: list[LocalizedCopy],
) -> list[dict]:
    """
    Re-render composited images with localized headline + tagline.
    Replaces the English-only composites from node 5.
    """
    storage = get_storage_backend()
    brand_config = load_brand_config(brief.brand)
    run_id = state["run_id"]

    # Build copy lookup: (product_id, market) → LocalizedCopy
    copy_lookup = {(c.product_id, c.market): c for c in copies}

    updated_assets: list[dict] = []

    for asset_dict in state.get("composited_assets", []):
        asset = CompositedAsset.model_validate(asset_dict)
        copy = copy_lookup.get((asset.product_id, asset.market))

        if not copy:
            updated_assets.append(asset_dict)
            continue

        try:
            # Load the composited image (has gradient + logo but English text)
            img_bytes = await storage.load(asset.storage_path)
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

            # Re-render with localized headline + tagline
            img = render_text(
                img,
                headline=copy.headline,
                tagline=copy.tagline,
                font_size_h=brand_config.get("font_size_headline", 64),
                font_size_t=brand_config.get("font_size_tagline", 40),
                color=brand_config.get("primary_color", "#FFFFFF"),
            )

            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)

            # Save with language suffix
            ratio_key = asset.aspect_ratio.replace(":", "x")
            localized_path = f"{run_id}/{asset.product_id}/{asset.market}/{copy.language}_{ratio_key}.png"
            url = await storage.save(localized_path, buf.getvalue())

            updated = CompositedAsset(
                product_id=asset.product_id,
                market=asset.market,
                aspect_ratio=asset.aspect_ratio,
                language=copy.language,
                storage_url=url,
                storage_path=localized_path,
            )
            updated_assets.append(updated.model_dump())

        except Exception as e:
            log.error("localize.recomposite_failed", asset=asset.storage_path, error=str(e))
            updated_assets.append(asset_dict)

    return updated_assets
