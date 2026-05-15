"""
Modal Image Provider — CreativeOS v4.

Calls the Modal-deployed HunyuanImage-3.0-Instruct-Distil-NF4-v2 endpoint.
Falls back to GeminiImageProvider if Modal endpoint is not configured or fails.

Endpoint contract (set by modal_apps/image_gen.py):
  POST <MODAL_IMAGE_ENDPOINT>/generate
  Headers: Modal-Key, Modal-Secret
  Body: {"prompt": str, "width": int, "height": int, "steps": int, "guidance_scale": float}
  Response: PNG bytes (Content-Type: image/png)
             OR {"error": str} on failure
"""
from __future__ import annotations

import os
import structlog
from backend.providers.base import ImageProvider

log = structlog.get_logger(__name__)

# Available HF image models deployed on Modal
MODAL_IMAGE_MODELS = [
    {
        "id": "hunyuan-image-3-distil-nf4",
        "name": "HunyuanImage 3.0 Instruct Distil NF4",
        "hf_repo": "EricRollei/HunyuanImage-3.0-Instruct-Distil-NF4-v2",
        "params": "83B (NF4 quant)",
        "vram": "~20GB",
        "tags": ["text-to-image", "open-source", "free"],
    },
]


class ModalImageProvider(ImageProvider):
    """
    Image generation via Modal-deployed HunyuanImage endpoint.
    Automatically falls back to Gemini if Modal is unavailable.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        key_id: str | None = None,
        key_secret: str | None = None,
    ):
        self.endpoint = (endpoint or os.getenv("MODAL_IMAGE_ENDPOINT", "")).rstrip("/")
        self.key_id = key_id or os.getenv("MODAL_KEY_ID", "")
        self.key_secret = key_secret or os.getenv("MODAL_KEY_SECRET", "")
        self._fallback: ImageProvider | None = None

    def name(self) -> str:
        if self.endpoint:
            return "modal/hunyuan-image-3-distil-nf4"
        return "gemini/fallback"

    def _get_fallback(self) -> ImageProvider:
        if self._fallback is None:
            from backend.providers.gemini import GeminiImageProvider
            self._fallback = GeminiImageProvider()
        return self._fallback

    async def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate image via Modal endpoint, fall back to Gemini on failure."""
        if not self.endpoint:
            log.warning("modal_image.no_endpoint", fallback="gemini")
            return await self._get_fallback().generate(prompt, width, height)

        try:
            return await self._call_modal(prompt, width, height)
        except Exception as e:
            log.error("modal_image.failed", error=str(e), fallback="gemini")
            return await self._get_fallback().generate(prompt, width, height)

    async def generate_with_reference(
        self, prompt: str, reference_bytes: bytes, width: int, height: int
    ) -> bytes:
        """Reference-guided generation — delegates to Gemini (HunyuanImage is T2I only)."""
        log.info("modal_image.reference_to_gemini", reason="HunyuanImage is T2I only")
        return await self._get_fallback().generate_with_reference(
            prompt, reference_bytes, width, height
        )

    async def _call_modal(self, prompt: str, width: int, height: int) -> bytes:
        import httpx

        headers = {
            "Modal-Key": self.key_id,
            "Modal-Secret": self.key_secret,
            "Content-Type": "application/json",
        }

        payload = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "steps": 20,
            "guidance_scale": 5.0,
        }

        log.info("modal_image.request", endpoint=self.endpoint, width=width, height=height)

        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                f"{self.endpoint}/generate",
                headers=headers,
                json=payload,
            )

            if resp.status_code != 200:
                raise RuntimeError(
                    f"Modal image endpoint returned HTTP {resp.status_code}: {resp.text[:300]}"
                )

            content_type = resp.headers.get("content-type", "")
            if "image" in content_type:
                log.info("modal_image.success", bytes=len(resp.content))
                return resp.content

            # JSON error response
            try:
                err = resp.json()
                raise RuntimeError(f"Modal image error: {err}")
            except Exception:
                raise RuntimeError(f"Unexpected Modal response: {resp.text[:200]}")
