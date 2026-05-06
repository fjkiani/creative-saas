"""
Node 5.5: review_gate  (inserted between composite and localize)

Implements the "threshold valve" pattern described in the Adobe meeting notes.
Computes a confidence score from the pre-compliance report and routes:

  score >= HITL_AUTO_APPROVE (default 0.85) → auto-approve → localize
  score <  HITL_AUTO_REJECT  (default 0.10) → auto-reject  → END (REJECTED)
  otherwise                                 → PENDING_REVIEW (LangGraph interrupt)

When interrupted, the pipeline is paused at this node. The frontend shows a
ReviewCard with sample assets + compliance score. A human calls:

  POST /api/runs/{run_id}/review  {"decision": "approve"|"reject", "reviewer_notes": "..."}

which resumes the graph from its MemorySaver checkpoint.

Confidence score formula (heuristic, configurable):
  score = 1.0 - (0.10 × warning_count) - (0.25 × error_count)
  clamped to [0.0, 1.0]

  Rationale: compliance_pre is an LLM heuristic with frequent false positives.
  Errors should send the run to human review (PENDING_REVIEW), not auto-reject.
  The human reviewer sees the actual generated images + compliance report and
  makes the final call. Auto-reject only triggers on 4+ errors (catastrophic).

Thresholds are configurable via env vars:
  HITL_AUTO_APPROVE  (default: 0.85)
  HITL_AUTO_REJECT   (default: 0.10)
"""
import os
import structlog
from langgraph.types import interrupt
from backend.graph.state import PipelineState, ComplianceReport
from backend.graph.nodes._broadcast import broadcast

log = structlog.get_logger(__name__)

HITL_AUTO_APPROVE = float(os.getenv("HITL_AUTO_APPROVE", "0.85"))
# Auto-reject threshold set very low — pre-compliance is an LLM heuristic.
# Only auto-reject on truly catastrophic combinations (3+ errors).
# Everything else goes to human review so the reviewer sees the actual images.
HITL_AUTO_REJECT = float(os.getenv("HITL_AUTO_REJECT", "0.10"))

# Score deductions per issue type
# Warnings = advisory (language softening) → small deduction
# Errors   = flagged by LLM heuristic, may be false positive → medium deduction
# Scoring table:
#   0 issues        → 1.00 → auto-approve
#   1 warning       → 0.90 → auto-approve
#   2 warnings      → 0.80 → human review
#   1 error         → 0.75 → human review
#   2 errors        → 0.50 → human review   ← was auto-reject before
#   3 errors        → 0.25 → human review
#   1 error+2 warns → 0.65 → human review
#   4+ errors       → 0.00 → auto-reject (catastrophic)
DEDUCTION_WARNING = 0.10
DEDUCTION_ERROR = 0.25


def compute_confidence_score(pre_compliance: dict | None) -> float:
    """
    Compute a 0–1 confidence score from the pre-compliance report.
    Higher = more confident the content is compliant.
    """
    if not pre_compliance:
        return 1.0  # No compliance data → assume clean

    report = ComplianceReport.model_validate(pre_compliance)
    warning_count = len(report.warnings)
    error_count = len(report.errors)

    score = 1.0 - (DEDUCTION_WARNING * warning_count) - (DEDUCTION_ERROR * error_count)
    return max(0.0, min(1.0, score))


async def review_gate_node(state: PipelineState) -> PipelineState:
    """
    Threshold valve node. Routes based on confidence score.
    If score is in the review band, calls interrupt() to pause the graph.
    """
    run_id = state["run_id"]
    await broadcast(run_id, "review_gate", "STARTED", {
        "message": "Evaluating compliance confidence score..."
    })
    log.info("node.review_gate.start", run_id=run_id)

    score = compute_confidence_score(state.get("pre_compliance"))
    composited_count = len(state.get("composited_assets", []))

    log.info("node.review_gate.score", run_id=run_id, score=score,
             auto_approve=HITL_AUTO_APPROVE, auto_reject=HITL_AUTO_REJECT)

    # ── Auto-approve ──────────────────────────────────────────────────────────
    if score >= HITL_AUTO_APPROVE:
        log.info("node.review_gate.auto_approve", run_id=run_id, score=score)
        await broadcast(run_id, "review_gate", "COMPLETED", {
            "decision": "auto_approved",
            "score": score,
            "message": f"Auto-approved (score={score:.2f} ≥ {HITL_AUTO_APPROVE})",
        })
        return {
            **state,
            "review_decision": "approved",
            "review_score": score,
            "reviewer_notes": f"Auto-approved (score={score:.2f})",
            "current_node": "review_gate",
        }

    # ── Auto-reject ───────────────────────────────────────────────────────────
    if score < HITL_AUTO_REJECT:
        log.warning("node.review_gate.auto_reject", run_id=run_id, score=score)
        await broadcast(run_id, "review_gate", "COMPLETED", {
            "decision": "auto_rejected",
            "score": score,
            "message": f"Auto-rejected (score={score:.2f} < {HITL_AUTO_REJECT}). Too many compliance issues.",
        })

        # Update DB status
        try:
            from backend.db.client import get_supabase_admin
            db = get_supabase_admin()
            db.table("runs").update({
                "status": "REJECTED",
                "completed_at": "now()",
            }).eq("id", run_id).execute()
        except Exception as e:
            log.warning("review_gate.db_update_failed", error=str(e))

        return {
            **state,
            "review_decision": "rejected",
            "review_score": score,
            "reviewer_notes": f"Auto-rejected: compliance score {score:.2f} below threshold {HITL_AUTO_REJECT}",
            "current_node": "review_gate",
        }

    # ── Human review required ─────────────────────────────────────────────────
    log.info("node.review_gate.pending_review", run_id=run_id, score=score,
             composited_assets=composited_count)

    # Update DB to PENDING_REVIEW so frontend can surface the ReviewCard
    try:
        from backend.db.client import get_supabase_admin
        db = get_supabase_admin()
        db.table("runs").update({
            "status": "PENDING_REVIEW",
            "review_score": score,
        }).eq("id", run_id).execute()
    except Exception as e:
        log.warning("review_gate.db_pending_update_failed", error=str(e))

    await broadcast(run_id, "review_gate", "PENDING_REVIEW", {
        "score": score,
        "composited_count": composited_count,
        "message": (
            f"Human review required (score={score:.2f}, "
            f"band={HITL_AUTO_REJECT}–{HITL_AUTO_APPROVE}). "
            f"Approve or reject via the Review panel."
        ),
        "pre_compliance": state.get("pre_compliance"),
    })

    # LangGraph interrupt — pauses graph execution here.
    # The graph will resume from this point when POST /api/runs/{id}/review is called.
    # The interrupt value is passed back as the resume input.
    human_decision = interrupt({
        "run_id": run_id,
        "score": score,
        "message": "Awaiting human review decision (approve/reject).",
        "composited_assets": state.get("composited_assets", [])[:3],  # sample for UI
    })

    # Resume: human_decision is the dict passed to graph.ainvoke(Command(resume=...))
    decision = human_decision.get("decision", "approved") if isinstance(human_decision, dict) else "approved"
    notes = human_decision.get("reviewer_notes", "") if isinstance(human_decision, dict) else ""

    log.info("node.review_gate.resumed", run_id=run_id, decision=decision)
    await broadcast(run_id, "review_gate", "COMPLETED", {
        "decision": decision,
        "score": score,
        "reviewer_notes": notes,
    })

    return {
        **state,
        "review_decision": decision,
        "review_score": score,
        "reviewer_notes": notes,
        "current_node": "review_gate",
    }


def review_gate_router(state: PipelineState) -> str:
    """
    Conditional edge after review_gate.
    Routes to 'localize' on approval, 'end_rejected' on rejection.
    """
    decision = state.get("review_decision", "approved")
    if decision == "rejected":
        return "end_rejected"
    return "localize"
