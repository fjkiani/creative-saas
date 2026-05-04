
import asyncio
import uuid
import json
import os
import sys

os.chdir("/workspace/creative-pipeline")
sys.path.insert(0, "/workspace/creative-pipeline")

# Load env
from dotenv import load_dotenv
load_dotenv(".env")

import structlog
structlog.configure(
    processors=[structlog.dev.ConsoleRenderer()],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

async def main():
    import yaml
    from backend.graph.pipeline import pipeline
    from backend.graph.state import PipelineState

    # Load Lumina brief
    with open("briefs/lumina_skincare.yaml") as f:
        brief = yaml.safe_load(f)

    run_id = str(uuid.uuid4())
    print(f"\n{'='*60}")
    print(f"RUN ID: {run_id}")
    print(f"CAMPAIGN: {brief['campaign_id']}")
    print(f"PRODUCTS: {[p['name'] for p in brief['products']]}")
    print(f"MARKETS: {[m['id'] for m in brief['markets']]}")
    print(f"ASPECT RATIOS: {brief['aspect_ratios']}")
    print(f"{'='*60}\n")

    initial_state: PipelineState = {
        "run_id": run_id,
        "campaign_id": brief["campaign_id"],
        "brief": brief,
        "creative_spec": None,
        "image_prompts": [],
        "pre_compliance": None,
        "generated_assets": [],
        "composited_assets": [],
        "localized_copy": [],
        "post_compliance": None,
        "current_node": "start",
        "errors": [],
        "provider_llm": "gemini",
        "provider_image": "gemini",
        "storage_backend": "local",
    }

    print("Starting LangGraph pipeline...")
    final_state = await pipeline.ainvoke(initial_state)

    print(f"\n{'='*60}")
    print("PIPELINE COMPLETE")
    print(f"Errors: {final_state.get('errors', [])}")
    print(f"Generated assets: {len(final_state.get('generated_assets', []))}")
    print(f"Composited assets: {len(final_state.get('composited_assets', []))}")
    print(f"Localized copy entries: {len(final_state.get('localized_copy', []))}")
    print(f"{'='*60}\n")

    # Print composited asset paths
    for asset in final_state.get("composited_assets", []):
        print(f"  ASSET: {asset.get('product_id')} x {asset.get('market')} x {asset.get('aspect_ratio')} -> {asset.get('storage_url', asset.get('storage_path', 'N/A'))}")

    # Save final state for inspection
    import json
    from pathlib import Path
    out_dir = Path(f"outputs/{run_id}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Serialize state (remove non-serializable)
    state_copy = {k: v for k, v in final_state.items() if k != "brief"}
    state_copy["brief_campaign_id"] = brief.get("campaign_id")
    with open(out_dir / "final_state.json", "w") as f:
        json.dump(state_copy, f, indent=2, default=str)
    print(f"\nState saved to outputs/{run_id}/final_state.json")

    return run_id, final_state

run_id, state = asyncio.run(main())
print(f"\nRUN_ID={run_id}")
