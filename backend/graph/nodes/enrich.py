"""
Node 1: enrich
Transforms the raw campaign brief into a rich CreativeSpec using the LLM.
The CreativeSpec drives all downstream image prompt generation.
"""
import structlog
from backend.graph.state import PipelineState, CampaignBrief, CreativeSpec
from backend.providers.base import get_llm_provider
from backend.graph.nodes._broadcast import broadcast

log = structlog.get_logger(__name__)

SYSTEM = """You are a senior creative director at a global advertising agency.
Your job is to analyze a campaign brief and produce a detailed creative specification
that will guide AI image generation and copywriting.
Be specific, visual, and actionable. Think in terms of what a photographer and art director
would need to produce stunning, on-brand social ad creatives."""

def build_user_prompt(brief: CampaignBrief) -> str:
    products_lines = []
    for p in brief.products:
        line = f"- {p.name}: {p.description}"
        if p.tagline:
            line += f' | Tagline: "{p.tagline}"'
        if p.key_claims:
            line += f" | Claims: {', '.join(p.key_claims[:3])}"
        products_lines.append(line)

    markets_lines = []
    for m in brief.markets:
        markets_lines.append(
            f"- {m.display_region} (lang={m.lang}, platform={m.platform or 'instagram'})"
        )

    style = brief.style_hints or {}
    style_block = ""
    if style:
        style_block = f"\nStyle hints: mood={style.get('mood','')}, palette={style.get('palette','')}, lighting={style.get('lighting','')}"

    return f"""Campaign: {brief.campaign_id}
Brand: {brief.brand}
Objective: {brief.objective or 'Drive brand awareness and product trial'}
Tone: {brief.tone or 'aspirational'}

Products:
{chr(10).join(products_lines)}

Target Markets:
{chr(10).join(markets_lines)}
{style_block}

Produce a CreativeSpec that will guide image generation and copy for this campaign.
The spec should work across all products and markets while allowing for localization."""


async def enrich_node(state: PipelineState) -> PipelineState:
    run_id = state["run_id"]
    await broadcast(run_id, "enrich", "STARTED", {"message": "Analyzing campaign brief..."})
    log.info("node.enrich.start", run_id=run_id)

    try:
        brief = CampaignBrief.model_validate(state["brief"])
        llm = get_llm_provider()

        spec: CreativeSpec = await llm.complete(
            system=SYSTEM,
            user=build_user_prompt(brief),
            response_model=CreativeSpec,
        )

        log.info("node.enrich.complete", run_id=run_id, style=spec.visual_style, mood=spec.mood)
        await broadcast(run_id, "enrich", "COMPLETED", {
            "visual_style": spec.visual_style,
            "mood": spec.mood,
            "brand_voice": spec.brand_voice,
        })

        return {
            **state,
            "creative_spec": spec.model_dump(),
            "current_node": "enrich",
            "provider_llm": llm.name(),
        }

    except Exception as e:
        log.error("node.enrich.failed", run_id=run_id, error=str(e))
        await broadcast(run_id, "enrich", "FAILED", {"error": str(e)})
        return {**state, "errors": state.get("errors", []) + [f"enrich: {e}"], "current_node": "enrich"}
