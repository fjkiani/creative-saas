"""
Node 2: prompt_gen
Generates optimized image generation prompts for every product × market combination.
Uses the CreativeSpec from node 1 to ensure visual consistency across all prompts.
"""
import structlog
from backend.graph.state import PipelineState, CampaignBrief, CreativeSpec, ImagePromptSet
from backend.providers.base import get_llm_provider
from backend.graph.nodes._broadcast import broadcast

log = structlog.get_logger(__name__)

SYSTEM = """You are an expert at writing image generation prompts for commercial advertising.
You understand how to translate brand briefs into precise, detailed prompts that produce
stunning, photorealistic product photography and lifestyle imagery for social media ads.

Your prompts must:
- Be specific about lighting, composition, color, and mood
- Reference the target audience and regional aesthetic sensibilities
- Be optimized for the image generation model (avoid ambiguity)
- NOT include any text, words, or typography in the image (text is added in post-processing)
- Produce images that work well at 1:1, 9:16, and 16:9 aspect ratios"""


def build_user_prompt(brief: CampaignBrief, spec: CreativeSpec) -> str:
    combos = []
    for product in brief.products:
        for market in brief.markets:
            combos.append(
                f"- product_id={product.id} | market_id={market.market_id} | "
                f"Product: {product.name} ({product.description}) "
                f"| Region: {market.display_region} (lang={market.lang})"
                + (f" | Audience: {market.audience}" if market.audience else "")
            )

    return f"""Creative Spec:
- Visual Style: {spec.visual_style}
- Mood: {spec.mood}
- Color Palette: {spec.color_palette_description}
- Lighting: {spec.lighting}
- Composition: {spec.composition_notes}
- Avoid: {spec.negative_prompt}

Generate one detailed image prompt for each product × market combination below.
Each prompt should be tailored to the specific product and regional aesthetic.

Combinations:
{chr(10).join(combos)}

Return an ImagePromptSet with one ImagePrompt per combination.
IMPORTANT: Use the exact product_id and market_id values shown above (e.g. 'radiance-serum', 'us') in your response."""


async def prompt_gen_node(state: PipelineState) -> PipelineState:
    run_id = state["run_id"]
    await broadcast(run_id, "prompt_gen", "STARTED", {"message": "Generating image prompts..."})
    log.info("node.prompt_gen.start", run_id=run_id)

    try:
        brief = CampaignBrief.model_validate(state["brief"])
        spec = CreativeSpec.model_validate(state["creative_spec"])
        llm = get_llm_provider()

        prompt_set: ImagePromptSet = await llm.complete(
            system=SYSTEM,
            user=build_user_prompt(brief, spec),
            response_model=ImagePromptSet,
        )

        log.info("node.prompt_gen.complete", run_id=run_id, count=len(prompt_set.prompts))
        await broadcast(run_id, "prompt_gen", "COMPLETED", {
            "prompt_count": len(prompt_set.prompts),
            "combinations": [f"{p.product_id}×{p.market}" for p in prompt_set.prompts],
        })

        return {
            **state,
            "image_prompts": [p.model_dump() for p in prompt_set.prompts],
            "current_node": "prompt_gen",
        }

    except Exception as e:
        log.error("node.prompt_gen.failed", run_id=run_id, error=str(e))
        await broadcast(run_id, "prompt_gen", "FAILED", {"error": str(e)})
        return {**state, "errors": state.get("errors", []) + [f"prompt_gen: {e}"], "current_node": "prompt_gen"}
