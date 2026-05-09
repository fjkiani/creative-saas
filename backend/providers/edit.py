"""
Canvas edit providers — CreativeOS v4.

Three editing modes, one unified interface:
  Mode 1 — text_edit: instruction-based edit (GPT-5 Image or Gemini)
  Mode 2 — mask_edit: inpainting on a painted mask region
  Mode 3 — layer swap: handled directly in composite.py (no AI needed)

Usage:
    from backend.providers.edit import get_edit_provider
    provider = get_edit_provider()
    edited_bytes = await provider.text_edit(image_bytes, "make the sky more dramatic")
    edited_bytes = await provider.mask_edit(image_bytes, mask_bytes, "replace with bold logo")
"""
from __future__ import annotations
import base64
import os
import io
import structlog
from abc import ABC, abstractmethod

log = structlog.get_logger(__name__)

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"


# ── Abstract base ─────────────────────────────────────────────────────────────

class EditProvider(ABC):
    """Abstract image editing provider."""

    @abstractmethod
    async def text_edit(self, image: bytes, instruction: str) -> bytes:
        """
        Edit an image based on a text instruction.
        Returns edited image bytes (PNG).
        """
        ...

    @abstractmethod
    async def mask_edit(self, image: bytes, mask: bytes, instruction: str) -> bytes:
        """
        Edit only the masked region of an image based on a text instruction.
        mask: PNG with white = edit region, black = preserve.
        Returns edited image bytes (PNG).
        """
        ...


# ── GPT-5 Image (via OpenRouter) ──────────────────────────────────────────────

class GPT5ImageEditProvider(EditProvider):
    """
    openai/gpt-5-image via OpenRouter.
    Supports both text-only edits and mask-based inpainting.
    Cost: ~$0.04 per edit.
    """

    MODEL = "openai/gpt-5-image"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")

    async def text_edit(self, image: bytes, instruction: str) -> bytes:
        """Send image + instruction to GPT-5 Image, return edited image."""
        import httpx

        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")

        img_b64 = base64.b64encode(image).decode()

        payload = {
            "model": self.MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                        {
                            "type": "text",
                            "text": (
                                f"Edit this image: {instruction}\n\n"
                                "Return ONLY the edited image. Preserve all elements not mentioned in the instruction. "
                                "Maintain the same dimensions and composition."
                            ),
                        },
                    ],
                }
            ],
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://creativeos.app",
            "X-Title": "CreativeOS Canvas Editor",
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OPENROUTER_API_BASE}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        # Extract image from response
        return self._extract_image_from_response(data)

    async def mask_edit(self, image: bytes, mask: bytes, instruction: str) -> bytes:
        """
        Inpainting: edit only the masked region.
        Sends image + mask + instruction to GPT-5 Image.
        """
        import httpx

        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")

        img_b64 = base64.b64encode(image).decode()
        mask_b64 = base64.b64encode(mask).decode()

        payload = {
            "model": self.MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{mask_b64}"},
                        },
                        {
                            "type": "text",
                            "text": (
                                f"The second image is a mask (white = region to edit, black = preserve). "
                                f"Edit ONLY the white masked region: {instruction}\n\n"
                                "Everything outside the white mask must remain pixel-identical. "
                                "Return the complete edited image."
                            ),
                        },
                    ],
                }
            ],
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://creativeos.app",
            "X-Title": "CreativeOS Canvas Editor",
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OPENROUTER_API_BASE}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        return self._extract_image_from_response(data)

    def _extract_image_from_response(self, data: dict) -> bytes:
        """Extract image bytes from OpenRouter response."""
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"No choices in response: {data}")

        content = choices[0].get("message", {}).get("content", "")

        # Response may be a list of content blocks or a string
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "image_url":
                    url = block["image_url"]["url"]
                    if url.startswith("data:"):
                        # data URI: data:image/png;base64,<b64>
                        b64_data = url.split(",", 1)[1]
                        return base64.b64decode(b64_data)
        elif isinstance(content, str) and content.startswith("data:"):
            b64_data = content.split(",", 1)[1]
            return base64.b64decode(b64_data)

        raise RuntimeError(f"Could not extract image from response: {str(data)[:200]}")


# ── Gemini Edit Provider (cheaper fallback) ───────────────────────────────────

class GeminiEditProvider(EditProvider):
    """
    google/gemini-2.5-flash-image via OpenRouter.
    Cheaper than GPT-5 Image, slightly less precise on complex edits.
    """

    MODEL = "google/gemini-2.5-flash-preview-05-20"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")

    async def text_edit(self, image: bytes, instruction: str) -> bytes:
        """Edit image using Gemini's vision capabilities."""
        try:
            import google.generativeai as genai
            from google.generativeai.types import Part
        except ImportError:
            raise RuntimeError("google-generativeai not installed")

        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        img_part = Part.from_bytes(data=image, mime_type="image/png")
        prompt = (
            f"Edit this image: {instruction}\n\n"
            "Return the edited image. Preserve all elements not mentioned. "
            "Maintain the same dimensions and composition."
        )

        response = await model.generate_content_async([img_part, prompt])

        # Extract image from response
        for part in response.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                return part.inline_data.data

        raise RuntimeError("Gemini did not return an image in the response")

    async def mask_edit(self, image: bytes, mask: bytes, instruction: str) -> bytes:
        """Mask-based edit using Gemini."""
        try:
            import google.generativeai as genai
            from google.generativeai.types import Part
        except ImportError:
            raise RuntimeError("google-generativeai not installed")

        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        img_part = Part.from_bytes(data=image, mime_type="image/png")
        mask_part = Part.from_bytes(data=mask, mime_type="image/png")
        prompt = (
            f"The second image is a mask (white = region to edit, black = preserve). "
            f"Edit ONLY the white masked region: {instruction}\n\n"
            "Everything outside the white mask must remain unchanged."
        )

        response = await model.generate_content_async([img_part, mask_part, prompt])

        for part in response.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                return part.inline_data.data

        raise RuntimeError("Gemini did not return an image in the response")


# ── Factory ───────────────────────────────────────────────────────────────────

def get_edit_provider(provider: str | None = None) -> EditProvider:
    """Return the configured edit provider."""
    name = provider or os.getenv("EDIT_PROVIDER", "gpt5")
    match name:
        case "gpt5" | "openai":
            return GPT5ImageEditProvider()
        case "gemini":
            return GeminiEditProvider()
        case _:
            return GPT5ImageEditProvider()
