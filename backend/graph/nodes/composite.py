"""
Node 5: composite — CreativeOS v4.

For each generated asset × aspect ratio:
  1. Smart center-crop to target canvas dimensions
  2. Apply semi-transparent gradient overlay (bottom 40%) for text legibility
  3. Composite brand logo at configured anchor position
  4. Render campaign message headline with brand font/color
  5. Upload composited image to storage

v4 addition: Save each layer separately for the Canvas Editor.
  Layer 0: base.png  — cropped base image (no overlays)
  Layer 1: gradient.png — gradient overlay only (RGBA)
  Layer 2: logo.png  — logo only (RGBA, transparent background)
  Layer 3: text.png  — text only (RGBA, transparent background)

Layer paths are stored in CompositedAsset for the canvas editor to use.
Most edits (text, logo swap) are instant layer swaps — no AI needed.
Only background/style changes require AI editing.

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


def make_gradient_layer(size: tuple[int, int], color_hex: str, opacity: float) -> Image.Image:
    """Create a standalone gradient layer (RGBA) for the canvas editor."""
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = size
    gradient_start = int(h * 0.55)

    r = int(color_hex.lstrip("#")[0:2], 16)
    g = int(color_hex.lstrip("#")[2:4], 16)
    b = int(color_hex.lstrip("#")[4:6], 16)

    for y in range(gradient_start, h):
        alpha = int(opacity * 255 * (y - gradient_start) / (h - gradient_start))
        draw.line([(0, y), (w, y)], fill=(r, g, b, alpha))

    return overlay


def composite_logo(img: Image.Image, logo_path: str, position: str, padding: int = 40) -> Image.Image:
    """Composite brand logo onto the image at the specified anchor position."""
    logo_file = Path(logo_path)
    if not logo_file.exists():
        log.warning("compositor.logo.not_found", path=logo_path)
        return img

    logo = Image.open(logo_file).convert("RGBA")
    # Scale logo to ~12% of image width
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


def make_logo_layer(
    size: tuple[int, int],
    logo_path: str,
    position: str,
    padding: int = 40,
) -> Image.Image:
    """Create a standalone logo layer (RGBA, transparent background) for the canvas editor."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    logo_file = Path(logo_path)
    if not logo_file.exists():
        return layer

    logo = Image.open(logo_file).convert("RGBA")
    max_logo_w = int(size[0] * 0.12)
    ratio = max_logo_w / logo.width
    logo = logo.resize((max_logo_w, int(logo.height * ratio)), Image.LANCZOS)

    lw, lh = logo.size
    iw, ih = size
    positions = {
        "top_left":     (padding, padding),
        "top_right":    (iw - lw - padding, padding),
        "bottom_left":  (padding, ih - lh - padding),
        "bottom_right": (iw - lw - padding, ih - lh - padding),
        "top_center":   ((iw - lw) // 2, padding),
    }
    pos = positions.get(position, positions["bottom_right"])
    layer.paste(logo, pos, logo)
    return layer


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


def make_text_layer(
    size: tuple[int, int],
    headline: str,
    tagline: str | None,
    font_size_h: int,
    font_size_t: int,
    color: str,
) -> Image.Image:
    """Create a standalone text layer (RGBA, transparent background) for the canvas editor."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    w, h = size

    font_headline = _load_font(font_size_h)
    font_tagline = _load_font(font_size_t)

    r = int(color.lstrip("#")[0:2], 16)
    g = int(color.lstrip("#")[2:4], 16)
    b = int(color.lstrip("#")[4:6], 16)
    text_color = (r, g, b, 255)

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

    return layer


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


def _img_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, optimize=True)
    return buf.getvalue()


async def _upsert_asset_row(
    run_id: str,
    product_id: str,
    market: str,
    aspect_ratio: str,
    language: str,
    storage_url: str,
    storage_path: str,
    prompt_hash: str | None = None,
    reused: bool = False,
    compliance_passed: bool | None = None,
    layer_base_path: str | None = None,
    layer_gradient_path: str | None = None,
    layer_logo_path: str | None = None,
    layer_text_path: str | None = None,
) -> None:
    """
    Upsert an asset row in Supabase.

    Called by:
      - composite_node: initial INSERT (language='en')
      - localize_node: UPDATE with final language + localized storage_url

    The UNIQUE constraint on (run_id, product_id, market, aspect_ratio) means
    the second call (localize) updates the existing row rather than inserting a new one.

    No-ops gracefully if Supabase is not configured (LocalDB stub handles it).
    """
    try:
        from backend.db.client import get_supabase_admin
        db = get_supabase_admin()
        db.table("assets").insert({
            "run_id": run_id,
            "product_id": product_id,
            "market": market,
            "aspect_ratio": aspect_ratio,
            "language": language,
            "storage_url": storage_url,
            "storage_path": storage_path,
            "prompt_hash": prompt_hash,
            "reused": reused,
            "compliance_passed": compliance_passed,
            "layer_base_path": layer_base_path,
            "layer_gradient_path": layer_gradient_path,
            "layer_logo_path": layer_logo_path,
            "layer_text_path": layer_text_path,
        }).execute()
    except Exception as e:
        # Log but don't crash — asset is already in storage, DB row is non-critical
        log.warning("composite.upsert_asset_row.failed",
                    run_id=run_id, product_id=product_id, market=market,
                    aspect_ratio=aspect_ratio, error=str(e))


async def composite_node(state: PipelineState) -> PipelineState:
    run_id = state["run_id"]
    await broadcast(run_id, "composite", "STARTED", {"message": "Compositing creatives for all aspect ratios..."})
    log.info("node.composite.start", run_id=run_id)

    brief = CampaignBrief.model_validate(state["brief"])
    brand_config = load_brand_config(brief.brand)
    storage = get_storage_backend()
    aspect_ratios = brief.aspect_ratios

    generated_assets = [GeneratedAsset.model_validate(a) for a in state.get("generated_assets", [])]
    asset_lookup = {(a.product_id, a.market): a for a in generated_assets}

    message_lookup = {}
    for m in brief.markets:
        msg = m.message or ""
        message_lookup[m.market_id] = msg

    composited_assets: list[dict] = []
    total = len(generated_assets) * len(aspect_ratios)
    done = 0

    for asset in generated_assets:
        try:
            img_bytes = await storage.load(asset.storage_path)
            base_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except Exception as e:
            log.error("composite.load_failed", path=asset.storage_path, error=str(e))
            continue

        headline = message_lookup.get(asset.market, "")
        logo_path = f"assets/brand/{brief.brand.lower()}_logo.png"

        for ratio in aspect_ratios:
            target_w, target_h = ASPECT_DIMENSIONS.get(ratio, (1024, 1024))
            ratio_key = ratio.replace(":", "x")

            try:
                # ── Layer 0: Base (cropped) ───────────────────────────────────
                base_cropped = smart_crop(base_img.copy(), target_w, target_h)

                # ── Layer 1: Gradient overlay ─────────────────────────────────
                gradient_layer = make_gradient_layer(
                    (target_w, target_h),
                    brand_config.get("overlay_color", "#000000"),
                    brand_config.get("overlay_opacity", 0.55),
                )

                # ── Layer 2: Logo ─────────────────────────────────────────────
                logo_layer = make_logo_layer(
                    (target_w, target_h),
                    logo_path,
                    brand_config.get("logo_position", "bottom_right"),
                )

                # ── Layer 3: Text ─────────────────────────────────────────────
                text_layer = make_text_layer(
                    (target_w, target_h),
                    headline=headline,
                    tagline=None,
                    font_size_h=brand_config.get("font_size_headline", 64),
                    font_size_t=brand_config.get("font_size_tagline", 40),
                    color=brand_config.get("primary_color", "#FFFFFF"),
                )

                # ── Composite: flatten all layers ─────────────────────────────
                composite = base_cropped.convert("RGBA")
                composite = Image.alpha_composite(composite, gradient_layer)
                composite = Image.alpha_composite(composite, logo_layer)
                composite = Image.alpha_composite(composite, text_layer)
                composite = composite.convert("RGB")

                # ── Save composited image ─────────────────────────────────────
                storage_path = f"{run_id}/{asset.product_id}/{asset.market}/{ratio_key}.png"
                url = await storage.save(storage_path, _img_to_bytes(composite))

                # ── Save individual layers (for canvas editor) ────────────────
                layer_base_path = f"{run_id}/{asset.product_id}/{asset.market}/layers/{ratio_key}_base.png"
                layer_gradient_path = f"{run_id}/{asset.product_id}/{asset.market}/layers/{ratio_key}_gradient.png"
                layer_logo_path = f"{run_id}/{asset.product_id}/{asset.market}/layers/{ratio_key}_logo.png"
                layer_text_path = f"{run_id}/{asset.product_id}/{asset.market}/layers/{ratio_key}_text.png"

                await storage.save(layer_base_path, _img_to_bytes(base_cropped))
                await storage.save(layer_gradient_path, _img_to_bytes(gradient_layer))
                await storage.save(layer_logo_path, _img_to_bytes(logo_layer))
                await storage.save(layer_text_path, _img_to_bytes(text_layer))

                # Persist asset row to Supabase (localize node will upsert with final language)
                await _upsert_asset_row(
                    run_id=run_id,
                    product_id=asset.product_id,
                    market=asset.market,
                    aspect_ratio=ratio,
                    language="en",
                    storage_url=url,
                    storage_path=storage_path,
                    prompt_hash=asset.prompt_hash if hasattr(asset, "prompt_hash") else None,
                    reused=asset.reused if hasattr(asset, "reused") else False,
                    layer_base_path=layer_base_path,
                    layer_gradient_path=layer_gradient_path,
                    layer_logo_path=layer_logo_path,
                    layer_text_path=layer_text_path,
                )

                composited_assets.append(CompositedAsset(
                    product_id=asset.product_id,
                    market=asset.market,
                    aspect_ratio=ratio,
                    language="en",
                    storage_url=url,
                    storage_path=storage_path,
                    layer_base_path=layer_base_path,
                    layer_gradient_path=layer_gradient_path,
                    layer_logo_path=layer_logo_path,
                    layer_text_path=layer_text_path,
                ).model_dump())

                done += 1
                log.info("composite.done", product=asset.product_id, market=asset.market, ratio=ratio)

            except Exception as e:
                log.error("composite.ratio_failed",
                          product=asset.product_id, market=asset.market, ratio=ratio, error=str(e))
                state["errors"] = state.get("errors", []) + [
                    f"composite {asset.product_id}×{asset.market}×{ratio}: {e}"
                ]

    log.info("node.composite.complete", run_id=run_id, count=len(composited_assets))
    await broadcast(run_id, "composite", "COMPLETED", {
        "composited_count": len(composited_assets),
        "aspect_ratios": aspect_ratios,
        "layers_saved": True,
    })

    return {
        **state,
        "composited_assets": composited_assets,
        "current_node": "composite",
    }
