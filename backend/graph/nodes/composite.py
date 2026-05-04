"""
Node 5: composite
For each generated asset × aspect ratio:
  1. Smart center-crop to target canvas dimensions
  2. Apply semi-transparent gradient overlay (bottom 40%) for text legibility
  3. Composite brand logo at configured anchor position
  4. Render campaign message headline with brand font/color
  5. Upload composited image to storage

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


def render_text(img: Image.Image, headline: str, tagline: str | None,
                font_size_h: int, font_size_t: int, color: str) -> Image.Image:
    """Render headline and optional tagline text onto the image."""
    draw = ImageDraw.Draw(img)
    w, h = img.size

    # Try to load a nice font, fall back to default
    font_headline = _load_font(font_size_h)
    font_tagline = _load_font(font_size_t)

    r = int(color.lstrip("#")[0:2], 16)
    g = int(color.lstrip("#")[2:4], 16)
    b = int(color.lstrip("#")[4:6], 16)
    text_color = (r, g, b)

    # Position text in bottom 35% of image
    text_area_top = int(h * 0.65)
    padding = int(w * 0.06)

    # Wrap headline if too long
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


async def composite_node(state: PipelineState) -> PipelineState:
    run_id = state["run_id"]
    await broadcast(run_id, "composite", "STARTED", {"message": "Compositing creatives for all aspect ratios..."})
    log.info("node.composite.start", run_id=run_id)

    brief = CampaignBrief.model_validate(state["brief"])
    brand_config = load_brand_config(brief.brand)
    storage = get_storage_backend()
    aspect_ratios = brief.aspect_ratios

    generated_assets = [GeneratedAsset.model_validate(a) for a in state.get("generated_assets", [])]
    # Build lookup: (product_id, market) → GeneratedAsset
    asset_lookup = {(a.product_id, a.market): a for a in generated_assets}

    # Build message lookup: market_id → tagline/message
    message_lookup = {}
    for m in brief.markets:
        msg = m.message or ""
        message_lookup[m.market_id] = msg

    composited_assets: list[dict] = []
    total = len(generated_assets) * len(aspect_ratios)
    done = 0

    for asset in generated_assets:
        # Load base image from storage
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
                # 1. Smart crop to target canvas
                img = smart_crop(base_img.copy(), target_w, target_h)

                # 2. Gradient overlay for text legibility
                img = add_gradient_overlay(
                    img,
                    brand_config.get("overlay_color", "#000000"),
                    brand_config.get("overlay_opacity", 0.55),
                )

                # 3. Logo composite
                img = composite_logo(img, logo_path, brand_config.get("logo_position", "bottom_right"))

                # 4. Text overlay (headline only at this stage; localized tagline added in localize node)
                img = render_text(
                    img,
                    headline=headline,
                    tagline=None,  # localized tagline added in node 6
                    font_size_h=brand_config.get("font_size_headline", 64),
                    font_size_t=brand_config.get("font_size_tagline", 40),
                    color=brand_config.get("primary_color", "#FFFFFF"),
                )

                # 5. Save to storage
                buf = io.BytesIO()
                img.save(buf, format="PNG", optimize=True)
                img_bytes_out = buf.getvalue()

                storage_path = f"{run_id}/{asset.product_id}/{asset.market}/{ratio_key}.png"
                url = await storage.save(storage_path, img_bytes_out)

                composited_assets.append(CompositedAsset(
                    product_id=asset.product_id,
                    market=asset.market,
                    aspect_ratio=ratio,
                    language="en",  # will be updated by localize node
                    storage_url=url,
                    storage_path=storage_path,
                ).model_dump())

                done += 1
                log.info("composite.done", product=asset.product_id, market=asset.market, ratio=ratio)

            except Exception as e:
                log.error("composite.ratio_failed", product=asset.product_id, market=asset.market, ratio=ratio, error=str(e))
                state["errors"] = state.get("errors", []) + [f"composite {asset.product_id}×{asset.market}×{ratio}: {e}"]

    log.info("node.composite.complete", run_id=run_id, count=len(composited_assets))
    await broadcast(run_id, "composite", "COMPLETED", {
        "composited_count": len(composited_assets),
        "aspect_ratios": aspect_ratios,
    })

    return {
        **state,
        "composited_assets": composited_assets,
        "current_node": "composite",
    }
