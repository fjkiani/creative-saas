"""
Shared broadcast helper used by every pipeline node.

Writes a row to run_events in Supabase, which triggers a Postgres Changes
event that the React frontend subscribes to via Supabase Realtime.

This is the mechanism that drives the live PipelineTracker UI — no polling.
"""
import structlog
from backend.db.client import get_supabase_admin

log = structlog.get_logger(__name__)


async def broadcast(
    run_id: str,
    node_name: str,
    status: str,  # STARTED | COMPLETED | FAILED | SKIPPED
    payload: dict,
) -> None:
    """
    Insert a run_event row. Supabase Realtime propagates this to all
    subscribed frontend clients within ~100ms.
    """
    try:
        db = get_supabase_admin()
        db.table("run_events").insert({
            "run_id": run_id,
            "node_name": node_name,
            "status": status,
            "payload": payload,
        }).execute()
        log.info("broadcast.sent", run_id=run_id, node=node_name, status=status)
    except Exception as e:
        # Never let a broadcast failure kill the pipeline
        log.warning("broadcast.failed", run_id=run_id, node=node_name, error=str(e))
