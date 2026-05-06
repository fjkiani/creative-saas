"""
Run report writer.
Produces a structured JSON report for each pipeline run.

Saves to:
  1. Supabase `runs` table (run_report JSONB column)
  2. Local outputs/ directory (fallback / debug)

The asset_summary section is enriched from the Supabase `assets` table
(authoritative source) so the run_report always reflects what's actually
in storage, not just what's in the pipeline state dict.
"""
import json
import structlog
from datetime import datetime, timezone
from pathlib import Path
from backend.graph.state import PipelineState

log = structlog.get_logger(__name__)


def _get_storage_name() -> str:
    """Return the effective storage backend name for the run report."""
    try:
        from backend.storage.base import get_storage_backend
        return get_storage_backend().name()
    except Exception:
        return "unknown"


def _fetch_assets_from_db(run_id: str) -> list[dict]:
    """
    Pull the final asset rows from Supabase for this run.
    Returns empty list if Supabase is not configured or query fails.
    """
    try:
        from backend.db.client import get_supabase_admin, using_local_db
        if using_local_db():
            return []
        db = get_supabase_admin()
        result = db.table("assets").select(
            "id,product_id,market,aspect_ratio,language,"
            "storage_url,storage_path,prompt_hash,reused,compliance_passed,created_at"
        ).eq("run_id", run_id).order("created_at").execute()
        return result.data or []
    except Exception as e:
        log.warning("reporter.fetch_assets_failed", run_id=run_id, error=str(e))
        return []


def build_run_report(state: PipelineState, db_assets: list[dict] | None = None) -> dict:
    """
    Build a structured run report from the final pipeline state.

    db_assets: rows from Supabase assets table (preferred).
               Falls back to state composited_assets if None or empty.
    """
    brief = state.get("brief", {})
    compliance_pre = state.get("pre_compliance", {})
    compliance_post = state.get("post_compliance", {})
    composited = state.get("composited_assets", [])
    generated = state.get("generated_assets", [])

    # Use DB assets as authoritative source; fall back to state
    asset_rows = db_assets if db_assets else composited

    return {
        "run_id": state["run_id"],
        "campaign_id": state.get("campaign_id"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "providers": {
            "llm": state.get("provider_llm", "unknown"),
            "image": state.get("provider_image", "unknown"),
            "storage": state.get("storage_backend", _get_storage_name()),
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
            "total_composited": len(asset_rows),
            "total_generated": len(generated),
            "reused": sum(1 for a in generated if a.get("reused")),
            # Full asset list with storage URLs — used by frontend fallback path
            "assets": asset_rows,
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
    """
    Save run report to Supabase DB and local outputs/ directory.

    Asset summary is pulled from the Supabase assets table (authoritative)
    so the report reflects the final persisted state, not just in-memory state.
    """
    run_id = state["run_id"]

    # Pull authoritative asset rows from DB
    db_assets = _fetch_assets_from_db(run_id)
    log.info("reporter.assets_from_db", run_id=run_id, count=len(db_assets))

    report = build_run_report(state, db_assets=db_assets if db_assets else None)

    # ── Save to Supabase runs table ───────────────────────────────────────────
    try:
        from backend.db.client import get_supabase_admin
        db = get_supabase_admin()
        db.table("runs").update({"run_report": report}).eq("id", run_id).execute()
        log.info("reporter.saved_to_supabase", run_id=run_id,
                 asset_count=len(report["asset_summary"]["assets"]))
    except Exception as e:
        log.warning("reporter.supabase_save_failed", run_id=run_id, error=str(e))

    # ── Save to local outputs/ directory (debug / offline fallback) ───────────
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
