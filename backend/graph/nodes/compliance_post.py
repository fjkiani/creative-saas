"""
Node 7: compliance_post
Post-generation compliance check on the final composited images.

Three checks:
1. Logo presence — pixel-based template matching via OpenCV
2. Brand color adherence — dominant color extraction (k-means, k=5) vs. brand palette
3. Overlay text scan — prohibited word check on rendered text strings

v3 fix: compliance_passed is now written back to each CompositedAsset individually,
so the run report and frontend can show per-asset compliance status.
"""
import io
import structlog
import numpy as np
from PIL import Image
from pathlib import Path
from backend.graph.state import PipelineState, CampaignBrief, ComplianceReport, ComplianceIssue, CompositedAsset
from backend.storage.base import get_storage_backend
from backend.graph.nodes._broadcast import broadcast
from backend.graph.nodes.compliance_pre import load_prohibited_words
import yaml

log = structlog.get_logger(__name__)


def load_brand_palette(brand: str) -> list[tuple[int, int, int]]:
    """Load brand color palette as list of RGB tuples."""
    config_path = Path(f"assets/brand/brand_configs/{brand}.yaml")
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f)
            colors = config.get("brand_colors", ["#FFFFFF"])
            return [_hex_to_rgb(c) for c in colors]
    return [(255, 255, 255)]


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _color_distance(c1: tuple, c2: tuple) -> float:
    return sum((a - b) ** 2 for a, b in zip(c1, c2)) ** 0.5


def check_logo_presence(img_array: np.ndarray, logo_path: str) -> bool:
    """
    Template matching to verify logo is present in the composited image.
    Returns True if logo is detected above threshold.
    """
    try:
        import cv2
        logo_file = Path(logo_path)
        if not logo_file.exists():
            return True  # Can't check if logo file missing — pass by default

        logo = cv2.imread(str(logo_file), cv2.IMREAD_GRAYSCALE)
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)

        # Multi-scale template matching
        for scale in [1.0, 0.8, 0.6, 0.4]:
            h, w = logo.shape
            scaled_logo = cv2.resize(logo, (int(w * scale), int(h * scale)))
            if scaled_logo.shape[0] > gray.shape[0] or scaled_logo.shape[1] > gray.shape[1]:
                continue
            result = cv2.matchTemplate(gray, scaled_logo, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            if max_val > 0.6:
                return True
        return False
    except Exception as e:
        log.warning("compliance_post.logo_check_error", error=str(e))
        return True  # Fail open


def check_brand_colors(img_array: np.ndarray, palette: list[tuple]) -> bool:
    """
    Extract dominant colors via k-means and check if any brand color is in top 5.
    Returns True if brand color adherence passes.
    """
    try:
        from sklearn.cluster import KMeans
        pixels = img_array.reshape(-1, 3).astype(np.float32)
        # Sample for speed
        if len(pixels) > 10000:
            idx = np.random.choice(len(pixels), 10000, replace=False)
            pixels = pixels[idx]

        k = min(5, len(pixels))
        kmeans = KMeans(n_clusters=k, n_init=3, random_state=42)
        kmeans.fit(pixels)
        dominant = [tuple(int(c) for c in center) for center in kmeans.cluster_centers_]

        for dom_color in dominant:
            for brand_color in palette:
                if _color_distance(dom_color, brand_color) < 80:
                    return True
        return False
    except Exception as e:
        log.warning("compliance_post.color_check_error", error=str(e))
        return True  # Fail open


def check_text_prohibited(localized_copies: list[dict], prohibited_words: list[str]) -> list[tuple[str, str, str]]:
    """
    Scan all rendered text strings for prohibited words.
    Returns list of (product_id, market, description) tuples for attribution.
    """
    flagged = []
    for copy in localized_copies:
        for field in ["headline", "tagline", "cta"]:
            text = (copy.get(field) or "").lower()
            for word in prohibited_words:
                if word.lower() in text:
                    flagged.append((
                        copy.get("product_id", "unknown"),
                        copy.get("market", "unknown"),
                        f"'{word}' found in {field}: '{copy.get(field)}'",
                    ))
    return flagged


async def compliance_post_node(state: PipelineState) -> PipelineState:
    run_id = state["run_id"]
    await broadcast(run_id, "compliance_post", "STARTED", {
        "message": "Running post-generation compliance checks..."
    })
    log.info("node.compliance_post.start", run_id=run_id)

    brief = CampaignBrief.model_validate(state["brief"])
    storage = get_storage_backend()
    prohibited = load_prohibited_words(brief.brand)
    palette = load_brand_palette(brief.brand)
    logo_path = f"assets/brand/{brief.brand.lower()}_logo.png"
    localized_copies = state.get("localized_copy", [])

    issues: list[ComplianceIssue] = []
    warnings: list[str] = []
    errors: list[str] = []

    # Track per-asset error keys: set of (product_id, market) with ERROR severity
    error_asset_keys: set[tuple[str, str]] = set()

    # ── Check 1: Text prohibited words ───────────────────────────────────────
    flagged_text = check_text_prohibited(localized_copies, prohibited)
    for product_id, market, description in flagged_text:
        issues.append(ComplianceIssue(
            severity="WARNING",
            category="PROHIBITED_WORD",
            description=description,
            flagged_text=description,
            product_id=product_id,
            market=market,
        ))
        warnings.append(description)

    # ── Check 2: Logo presence + brand colors on sample images ───────────────
    composited_assets = state.get("composited_assets", [])
    # Sample: check first image per product × market (not all ratios — expensive)
    checked_keys: set[str] = set()

    for asset_dict in composited_assets:
        asset = CompositedAsset.model_validate(asset_dict)
        key = f"{asset.product_id}_{asset.market}"
        if key in checked_keys:
            continue
        checked_keys.add(key)

        try:
            img_bytes = await storage.load(asset.storage_path)
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            img_array = np.array(img)

            # Logo check
            logo_found = check_logo_presence(img_array, logo_path)
            if not logo_found:
                issues.append(ComplianceIssue(
                    severity="WARNING",
                    category="BRAND",
                    description=f"Logo not detected in {asset.product_id} × {asset.market}",
                    product_id=asset.product_id,
                    market=asset.market,
                ))
                warnings.append(f"Logo not detected: {asset.product_id} × {asset.market}")

            # Brand color check
            colors_ok = check_brand_colors(img_array, palette)
            if not colors_ok:
                issues.append(ComplianceIssue(
                    severity="WARNING",
                    category="BRAND",
                    description=f"No brand colors detected in dominant palette for {asset.product_id} × {asset.market}",
                    product_id=asset.product_id,
                    market=asset.market,
                ))
                warnings.append(f"Brand color mismatch: {asset.product_id} × {asset.market}")

        except Exception as e:
            log.warning("compliance_post.image_check_error",
                        asset=asset.storage_path, error=str(e))

    passed = len(errors) == 0
    report = ComplianceReport(passed=passed, issues=issues, warnings=warnings, errors=errors)

    # ── v3 fix: Write compliance_passed back to each CompositedAsset ─────────
    # An asset fails if its (product_id, market) has any ERROR-severity issue.
    # Warnings do not fail individual assets.
    updated_assets: list[dict] = []
    for asset_dict in composited_assets:
        asset = CompositedAsset.model_validate(asset_dict)
        asset_key = (asset.product_id, asset.market)
        asset.compliance_passed = asset_key not in error_asset_keys
        updated_assets.append(asset.model_dump())

    log.info("node.compliance_post.complete", run_id=run_id, passed=passed,
             warnings=len(warnings), errors=len(errors),
             assets_updated=len(updated_assets))

    await broadcast(run_id, "compliance_post", "COMPLETED", {
        "passed": passed,
        "warning_count": len(warnings),
        "error_count": len(errors),
        "issues": [i.model_dump() for i in issues],
        "assets_compliance_written": len(updated_assets),
    })

    # Update run status in DB
    try:
        from backend.db.client import get_supabase_admin
        db = get_supabase_admin()
        db.table("runs").update({
            "status": "COMPLETE",
            "completed_at": "now()",
        }).eq("id", run_id).execute()
    except Exception as e:
        log.warning("compliance_post.db_update_failed", error=str(e))

    return {
        **state,
        "post_compliance": report.model_dump(),
        "composited_assets": updated_assets,
        "current_node": "compliance_post",
    }
