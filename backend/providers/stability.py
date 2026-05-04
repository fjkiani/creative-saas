"""
Stability AI provider — Stable Diffusion 3.5 Large.

Activate via: IMAGE_PROVIDER=stability
"""
import structlog
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from backend.providers.base import ImageProvider
from backend.config import settings

log = structlog.get_logger(__name__)

STABILITY_API_BASE = "https://api.stability.ai/v2beta"


class StabilityImageProvider(ImageProvider):
    """Stable Diffusion 3.5 Large — maximum customization, open-source lineage."""

    def name(self) -> str:
        return "stability/sd3.5-large"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
    async def generate(self, prompt: str, width: int, height: int) -> bytes:
        aspect = _dims_to_stability_ratio(width, height)
        log.info("stability.image.request", aspect=aspect, prompt=prompt[:80])

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{STABILITY_API_BASE}/stable-image/generate/sd3",
                headers={
                    "Authorization": f"Bearer {settings.stability_api_key}",
                    "Accept": "image/*",
                },
                data={
                    "prompt": prompt,
                    "aspect_ratio": aspect,
                    "model": "sd3.5-large",
                    "output_format": "png",
                },
                timeout=120,
            )
            resp.raise_for_status()

        log.info("stability.image.success", bytes=len(resp.content))
        return resp.content


def _dims_to_stability_ratio(width: int, height: int) -> str:
    ratio = width / height
    if abs(ratio - 1.0) < 0.05:
        return "1:1"
    elif ratio > 1:
        return "16:9"
    else:
        return "9:16"
