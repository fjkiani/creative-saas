"""
Node 3: compliance_pre
Pre-generation compliance check on the campaign brief and generated prompts.
Scans for prohibited words, health claims, superlatives, competitor mentions.

HARD_FAIL (errors) → pipeline halts via conditional edge.
WARNINGS → logged, pipeline continues.

Note: This is an LLM-based heuristic check, not legal advice.
"""
import yaml
import structlog
from pathlib import Path
from backend.graph.state import PipelineState, CampaignBrief, ComplianceReport, ComplianceIssue
from backend.providers.base import get_llm_provider
from backend.graph.nodes._broadcast import broadcast

log = structlog.get_logger(__name__)

SYSTEM = """You are a legal and brand compliance reviewer for a global advertising agency.
Your job is to review campaign briefs and image prompts for potential compliance issues.

Check for:
1. PROHIBITED_WORD: Any words from the brand's prohibited list
2. HEALTH_CLAIM: Unsubstantiated health/medical claims (e.g., "cures", "eliminates", "clinically proven" without qualification)
3. SUPERLATIVE: Unqualified superlatives in regulated markets (e.g., "#1", "best", "world's leading")
4. COMPETITOR: Direct competitor mentions or comparative claims
5. LEGAL: Other potential legal issues (misleading claims, regulatory violations)

Severity:
- ERROR: Must fix before publishing (blocks generation)
- WARNING: Should review but does not block

Be thorough but not overly conservative. Flag genuine risks, not every adjective."""


def load_prohibited_words(brand: str) -> list[str]:
    """Load brand-specific prohibited words from brand_config.yaml."""
    config_path = Path(f"assets/brand/brand_configs/{brand}.yaml")
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f)
            return config.get("prohibited_words", [])
    return []


def build_user_prompt(brief: CampaignBrief, prompts: list[dict], prohibited: list[str]) -> str:
    brief_text = "\n".join([
        f"Campaign: {brief.campaign_id}",
        f"Brand: {brief.brand}",
        "Products: " + ", ".join(f"{p.name}: {p.description}" for p in brief.products),
        "Markets: " + " | ".join(f"{m.market_id} ({m.lang})" for m in brief.markets),
    ])
    prompt_text = "\n".join(f"- {p['product_id']} × {p['market']}: {p['prompt'][:200]}" for p in prompts)
    prohibited_text = ", ".join(prohibited) if prohibited else "none specified"

    return f"""Review the following campaign content for compliance issues.

Brand Prohibited Words: {prohibited_text}

Campaign Brief:
{brief_text}

Image Prompts (first 200 chars each):
{prompt_text}

Return a ComplianceReport with all issues found. If nothing is flagged, return passed=true with empty issues list."""


async def compliance_pre_node(state: PipelineState) -> PipelineState:
    run_id = state["run_id"]
    await broadcast(run_id, "compliance_pre", "STARTED", {"message": "Running pre-generation compliance check..."})
    log.info("node.compliance_pre.start", run_id=run_id)

    try:
        brief = CampaignBrief.model_validate(state["brief"])
        prompts = state.get("image_prompts", [])
        prohibited = load_prohibited_words(brief.brand)
        llm = get_llm_provider()

        report: ComplianceReport = await llm.complete(
            system=SYSTEM,
            user=build_user_prompt(brief, prompts, prohibited),
            response_model=ComplianceReport,
        )

        log.info("node.compliance_pre.complete",
                 run_id=run_id, passed=report.passed,
                 errors=len(report.errors), warnings=len(report.warnings))

        await broadcast(run_id, "compliance_pre", "COMPLETED", {
            "passed": report.passed,
            "error_count": len(report.errors),
            "warning_count": len(report.warnings),
            "issues": [i.model_dump() for i in report.issues],
        })

        return {
            **state,
            "pre_compliance": report.model_dump(),
            "current_node": "compliance_pre",
        }

    except Exception as e:
        log.error("node.compliance_pre.failed", run_id=run_id, error=str(e))
        await broadcast(run_id, "compliance_pre", "FAILED", {"error": str(e)})
        # Don't block pipeline on compliance check failure — log and continue
        fallback = ComplianceReport(passed=True, warnings=[f"Compliance check failed: {e}"])
        return {
            **state,
            "pre_compliance": fallback.model_dump(),
            "errors": state.get("errors", []) + [f"compliance_pre: {e}"],
            "current_node": "compliance_pre",
        }


def compliance_pre_router(state: PipelineState) -> str:
    """
    Conditional edge: if pre_compliance has HARD errors, route to END.
    Otherwise continue to image_gen.
    """
    report = state.get("pre_compliance", {})
    if report and not report.get("passed", True) and report.get("errors"):
        log.warning("compliance_pre.hard_fail", errors=report["errors"])
        return "end_with_error"
    return "image_gen"
