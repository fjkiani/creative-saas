"""
Node 5: composite
For each generated asset × aspect ratio:
  1. Smart center-crop to target canvas dimensions
  2. Apply semi-transparent gradient overlay (bottom 40%) for text legibility
  3. Composite brand logo at configured anchor position
  4. Render campaign message headline with brand font/color
  5. Upload composited image to Supabase Storage
  6. INSERT row into Supabase `assets` table (fires Realtime → frontend updates live)

Compositing is done with Pillow — no external dependencies.
"""
import io
import structlog
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np
from pathlib import Path
from backend.graph.state import PipelineState, CampaignBrief, GeneratedAsset, CompositedAsset
from backend.storage.base import get_storage_backend
from backend.graph.nodes._broadcast import broadcast
import yaml

log = structlog.get_logger(__name__)

ASPECT_DIMENSIONS = {
    "1:1":  (1024, 1024),
    "9:16": (1024, 1792),
    "16:9": (1792, 1024),
}


def load_brand_config(brand: str) -> dict:
    config_path = Path(f"assets/brand/brand_configs/{brand}.yaml")
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {
        "primary_color": "#FFFFFF",
        "overlay_color": "#000000",
        "overlay_opacity": 0.55,
        "logo_position": "bottom_right",
        "font_size_headline": 64,
        "font_size_tagline": 40,
    }


def smart_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Center-crop image to target dimensions, scaling up if needed."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def add_gradient_overlay(img: Image.Image, color_hex: str, opacity: float) -> Image.Image:
    """Add a vertical gradient overlay on the bottom portion for text legibility."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = img.size
    gradient_start = int(h * 0.55)

    r = int(color_hex.lstrip("#")[0:2], 16)
    g = int(color_hex.lstrip("#")[2:4], 16)
    b = int(color_hex.lstrip("#")[4:6], 16)

    for y in range(gradient_start, h):
        alpha = int(opacity * 255 * (y - gradient_start) / (h - gradient_start))
        draw.line([(0, y), (w, y)], fill=(r, g, b, alpha))

    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def composite_logo(img: Image.Image, logo_path: str, position: str, padding: int = 40) -> Image.Image:
    """Composite brand logo onto the image at the specified anchor position."""
    logo_file = Path(logo_path)
    if not logo_file.exists():
        log.warning("compositor.logo.not_found", path=logo_path)
        return img

    logo = Image.open(logo_file).convert("RGBA")
    max_logo_w = int(img.width * 0.12)
    ratio = max_logo_w / logo.width
    logo = logo.resize((max_logo_w, int(logo.height * ratio)), Image.LANCZOS)

    canvas = img.convert("RGBA")
    lw, lh = logo.size
    iw, ih = canvas.size

    positions = {
        "top_left":     (padding, padding),
        "top_right":    (iw - lw - padding, padding),
        "bottom_left":  (padding, ih - lh - padding),
        "bottom_right": (iw - lw - padding, ih - lh - padding),
        "top_center":   ((iw - lw) // 2, padding),
    }
    pos = positions.get(position, positions["bottom_right"])
    canvas.paste(logo, pos, logo)
    return canvas.convert("RGB")


def render_text(img: Image.Image, headline: str, tagline: str | None,
                font_size_h: int, font_size_t: int, color: str) -> Image.Image:
    """Render headline and optional tagline text onto the image."""
    draw = ImageDraw.Draw(img)
    w, h = img.size

    font_headline = _load_font(font_size_h)
    font_tagline = _load_font(font_size_t)

    r = int(color.lstrip("#")[0:2], 16)
    g = int(color.lstrip("#")[2:4], 16)
    b = int(color.lstrip("#")[4:6], 16)
    text_color = (r, g, b)

    text_area_top = int(h * 0.65)
    padding = int(w * 0.06)

    headline_lines = _wrap_text(headline, font_headline, w - 2 * padding)
    y = text_area_top + int(h * 0.05)

    for line in headline_lines:
        draw.text((padding, y), line, font=font_headline, fill=text_color)
        y += font_size_h + 8

    if tagline:
        y += 12
        tagline_lines = _wrap_text(tagline, font_tagline, w - 2 * padding)
        for line in tagline_lines:
            draw.text((padding, y), line, font=font_tagline, fill=text_color)
            y += font_size_t + 6

    return img


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load Liberation Sans or fall back to Pillow default."""
    font_paths = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in font_paths:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    """Simple word-wrap for text rendering."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        try:
            bbox = font.getbbox(test)
            w = bbox[2] - bbox[0]
        except Exception:
            w = len(test) * 10
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def _upsert_asset_row(
    run_id: str,
    product_id: str,
    market: str,
    aspect_ratio: str,
    language: str,
    storage_url: str,
    storage_path: str,
    prompt_hash: str,
    reused: bool,
) -> None:
    """
    INSERT or UPDATE a row in the Supabase `assets` table.

    Strategy:
    1. Try upsert with on_conflict (requires uq_asset_per_run constraint).
    2. If that fails with 42P10 (constraint missing), fall back to:
       - UPDATE existing row if it exists
       - INSERT new row if it doesn't
    This makes the code safe whether or not the migration has been applied.

    Non-blocking: any DB error is logged and swallowed so it never kills the pipeline.
    """
    try:
        from backend.db.client import get_supabase_admin, using_local_db
        if using_local_db():
            return  # No-op in local mode — assets table doesn't exist locally

        db = get_supabase_admin()
        row = {
            "run_id": run_id,
            "product_id": product_id,
            "market": market,
            "aspect_ratio": aspect_ratio,
            "language": language,
            "storage_url": storage_url,
            "storage_path": storage_path,
            "prompt_hash": prompt_hash,
            "reused": reused,
            "compliance_passed": None,  # set later by compliance_post
        }

        try:
            # Fast path: upsert (requires uq_asset_per_run constraint)
            db.table("assets").upsert(
                row,
                on_conflict="run_id,product_id,market,aspect_ratio",
            ).execute()
            log.debug("assets.upserted", product=product_id, market=market, ratio=aspect_ratio)

        except Exception as upsert_err:
            err_str = str(upsert_err)
            if "42P10" in err_str or "no unique or exclusion constraint" in err_str:
                # Constraint not yet applied — fall back to UPDATE-or-INSERT
                log.warning(
                    "assets.upsert_constraint_missing",
                    hint="Run migration_add_asset_unique.sql in Supabase SQL Editor",
                    product=product_id, market=market, ratio=aspect_ratio,
                )
                existing = db.table("assets").select("id").eq(
                    "run_id", run_id
                ).eq("product_id", product_id).eq(
                    "market", market
                ).eq("aspect_ratio", aspect_ratio).execute()

                if existing.data:
                    db.table("assets").update({
                        "language": language,
                        "storage_url": storage_url,
                        "storage_path": storage_path,
                    }).eq("id", existing.data[0]["id"]).execute()
                    log.debug("assets.updated_fallback", product=product_id, market=market)
                else:
                    db.table("assets").insert(row).execute()
                    log.debug("assets.inserted_fallback", product=product_id, market=market)
            else:
                raise  # Re-raise unexpected errors to outer handler

    except Exception as e:
        log.warning("assets.upsert_failed", product=product_id, market=market,
                    ratio=aspect_ratio, error=str(e))


async def composite_node(state: PipelineState) -> PipelineState:
    run_id = state["run_id"]
    await broadcast(run_id, "composite", "STARTED", {"message": "Compositing creatives for all aspect ratios..."})
    log.info("node.composite.start", run_id=run_id)

    brief = CampaignBrief.model_validate(state["brief"])
    brand_config = load_brand_config(brief.brand)
    storage = get_storage_backend()
    aspect_ratios = brief.aspect_ratios

    generated_assets = [GeneratedAsset.model_validate(a) for a in state.get("generated_assets", [])]

    # Build message lookup: market_id → headline message
    message_lookup = {m.market_id: (m.message or "") for m in brief.markets}

    # Build prompt_hash lookup: (product_id, market) → prompt_hash
    hash_lookup = {(a.product_id, a.market): a.prompt_hash for a in generated_assets}
    reused_lookup = {(a.product_id, a.market): a.reused for a in generated_assets}

    composited_assets: list[dict] = []
    errors: list[str] = list(state.get("errors", []))

    for asset in generated_assets:
        try:
            img_bytes = await storage.load(asset.storage_path)
            base_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except Exception as e:
            log.error("composite.load_failed", path=asset.storage_path, error=str(e))
            errors.append(f"composite load {asset.product_id}×{asset.market}: {e}")
            continue

        headline = message_lookup.get(asset.market, "")
        logo_path = f"assets/brand/{brief.brand.lower()}_logo.png"
        prompt_hash = hash_lookup.get((asset.product_id, asset.market), "")
        reused = reused_lookup.get((asset.product_id, asset.market), False)

        for ratio in aspect_ratios:
            target_w, target_h = ASPECT_DIMENSIONS.get(ratio, (1024, 1024))
            ratio_key = ratio.replace(":", "x")

            try:
                # 1. Smart crop → 2. Gradient overlay → 3. Logo → 4. Text
                img = smart_crop(base_img.copy(), target_w, target_h)
                img = add_gradient_overlay(
                    img,
                    brand_config.get("overlay_color", "#000000"),
                    brand_config.get("overlay_opacity", 0.55),
                )
                img = composite_logo(img, logo_path, brand_config.get("logo_position", "bottom_right"))
                img = render_text(
                    img,
                    headline=headline,
                    tagline=None,  # localized tagline added in node 6
                    font_size_h=brand_config.get("font_size_headline", 64),
                    font_size_t=brand_config.get("font_size_tagline", 40),
                    color=brand_config.get("primary_color", "#FFFFFF"),
                )

                # 5. Save to Supabase Storage
                buf = io.BytesIO()
                img.save(buf, format="PNG", optimize=True)
                img_bytes_out = buf.getvalue()

                storage_path = f"{run_id}/{asset.product_id}/{asset.market}/{ratio_key}.png"
                url = await storage.save(storage_path, img_bytes_out)

                composited_asset = CompositedAsset(
                    product_id=asset.product_id,
                    market=asset.market,
                    aspect_ratio=ratio,
                    language="en",
                    storage_url=url,
                    storage_path=storage_path,
                )
                composited_assets.append(composited_asset.model_dump())

                # 6. INSERT into Supabase assets table → fires Realtime to frontend
                _upsert_asset_row(
                    run_id=run_id,
                    product_id=asset.product_id,
                    market=asset.market,
                    aspect_ratio=ratio,
                    language="en",
                    storage_url=url,
                    storage_path=storage_path,
                    prompt_hash=prompt_hash,
                    reused=reused,
                )

                log.info("composite.done", product=asset.product_id,
                         market=asset.market, ratio=ratio, url=url)

            except Exception as e:
                log.error("composite.ratio_failed", product=asset.product_id,
                          market=asset.market, ratio=ratio, error=str(e))
                errors.append(f"composite {asset.product_id}×{asset.market}×{ratio}: {e}")

    log.info("node.composite.complete", run_id=run_id, count=len(composited_assets))
    await broadcast(run_id, "composite", "COMPLETED", {
        "composited_count": len(composited_assets),
        "aspect_ratios": aspect_ratios,
        "db_rows_written": len(composited_assets),
    })

    return {
        **state,
        "composited_assets": composited_assets,
        "errors": errors,
        "current_node": "composite",
    }
