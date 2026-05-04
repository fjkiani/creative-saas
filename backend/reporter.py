"""
Run report writer.
Produces a structured JSON report for each pipeline run, saved to Supabase
and optionally to the local outputs/ directory.
"""
import json
import structlog
from datetime import datetime, timezone
from pathlib import Path
from backend.graph.state import PipelineState

log = structlog.get_logger(__name__)


def build_run_report(state: PipelineState) -> dict:
    """Build a structured run report from the final pipeline state."""
    brief = state.get("brief", {})
    compliance_pre = state.get("pre_compliance", {})
    compliance_post = state.get("post_compliance", {})
    composited = state.get("composited_assets", [])
    generated = state.get("generated_assets", [])

    return {
        "run_id": state["run_id"],
        "campaign_id": state.get("campaign_id"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "providers": {
            "llm": state.get("provider_llm", "unknown"),
            "image": state.get("provider_image", "unknown"),
            "storage": state.get("storage_backend", "unknown"),
        },
        "brief_summary": {
            "campaign_id": brief.get("campaign_id"),
            "brand": brief.get("brand"),
            "product_count": len(brief.get("products", [])),
            "market_count": len(brief.get("markets", [])),
            "aspect_ratios": brief.get("aspect_ratios", []),
        },
        "creative_spec": state.get("creative_spec"),
        "asset_summary": {
            "total_composited": len(composited),
            "total_generated": len(generated),
            "reused": sum(1 for a in generated if a.get("reused")),
            "assets": composited,
        },
        "compliance": {
            "pre_generation": compliance_pre,
            "post_generation": compliance_post,
            "overall_passed": (
                compliance_pre.get("passed", True) and
                compliance_post.get("passed", True)
            ),
        },
        "localized_copy": state.get("localized_copy", []),
        "errors": state.get("errors", []),
        "status": "FAILED" if state.get("errors") else "COMPLETE",
    }


async def save_run_report(state: PipelineState) -> dict:
    """Save run report to Supabase DB and local outputs/ directory."""
    report = build_run_report(state)
    run_id = state["run_id"]

    # Save to Supabase
    try:
        from backend.db.client import get_supabase_admin
        db = get_supabase_admin()
        db.table("runs").update({"run_report": report}).eq("id", run_id).execute()
        log.info("reporter.saved_to_supabase", run_id=run_id)
    except Exception as e:
        log.warning("reporter.supabase_save_failed", run_id=run_id, error=str(e))

    # Save to local outputs/ directory
    try:
        output_dir = Path(f"outputs/{run_id}")
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "run_report.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        log.info("reporter.saved_locally", path=str(report_path))
    except Exception as e:
        log.warning("reporter.local_save_failed", run_id=run_id, error=str(e))

    return report
