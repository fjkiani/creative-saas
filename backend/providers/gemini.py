"""
Google Gemini providers — default for both LLM and image generation.

LLM:   gemini-2.5-pro  (reasoning, structured outputs via JSON mode)
Image: gemini-2.5-flash-image  (Nano Banana — generate_content + ImageConfig aspect ratio)

API pattern for image generation (gemini-2.5-flash-image):
  - Uses client.aio.models.generate_content()  (NOT generate_images)
  - response_modalities=['IMAGE'] in GenerateContentConfig
  - Aspect ratio via ImageConfig(aspect_ratio="16:9")
  - Image bytes extracted from part.inline_data.data (base64) or part.as_image()
  - Supported aspect ratios: 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9

Hero asset editing (generate_with_reference):
  - Pass reference image as inline_data Part alongside the text prompt
  - Gemini uses the reference as a visual anchor for style/composition
  - Best-effort: model may not perfectly preserve all reference details
"""
import io
import json
import base64
import structlog
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from backend.providers.base import LLMProvider, ImageProvider
from backend.config import settings

log = structlog.get_logger(__name__)


class GeminiLLMProvider(LLMProvider):
    """Gemini 2.5 Pro for structured reasoning tasks via JSON mode."""

    def __init__(self):
        from google import genai
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = "gemini-2.5-pro"

    def name(self) -> str:
        return f"gemini/{self._model}"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def complete(
        self,
        system: str,
        user: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        """
        Structured completion using Gemini's JSON response mode.
        Returns a validated Pydantic model — no free-form string parsing.
        """
        from google.genai import types

        schema = response_model.model_json_schema()
        prompt = (
            f"{system}\n\n"
            f"Respond ONLY with valid JSON that matches this exact schema:\n"
            f"{json.dumps(schema, indent=2)}\n\n"
            f"Request:\n{user}"
        )

        log.info("gemini.llm.request", model=self._model, response_model=response_model.__name__)

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7,
            ),
        )

        raw = response.text
        data = json.loads(raw)
        result = response_model.model_validate(data)
        log.info("gemini.llm.response.ok", model=self._model, response_model=response_model.__name__)
        return result


class GeminiImageProvider(ImageProvider):
    """
    gemini-2.5-flash-image (Nano Banana) via the Gemini Developer API.

    API: generate_content() with response_modalities=['IMAGE'] and ImageConfig(aspect_ratio=...)
    Image bytes are in response parts as inline_data (base64-encoded PNG).

    Supported aspect ratios: 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9
    All generate at 1024px resolution (1290 tokens per image).

    Enterprise upgrade: swap to gemini-3-pro-image-preview for 4K output and
    thinking-mode composition refinement.
    """

    def __init__(self):
        from google import genai
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = "gemini-2.5-flash-image"

    def name(self) -> str:
        return f"gemini/{self._model}"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
    async def generate(self, prompt: str, width: int, height: int) -> bytes:
        """
        Generate an image from a text prompt using gemini-2.5-flash-image.

        Uses generate_content() with:
          - response_modalities=['IMAGE']
          - ImageConfig(aspect_ratio=...) for native aspect ratio output

        Returns raw PNG bytes extracted from part.inline_data.data (base64 decoded).
        """
        from google.genai import types

        aspect = _dims_to_aspect(width, height)
        log.info("gemini.image.request", model=self._model, aspect=aspect, prompt=prompt[:80])

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=aspect,
                ),
            ),
        )

        return _extract_image_bytes(response, self._model, aspect)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
    async def generate_with_reference(
        self,
        prompt: str,
        reference_image_bytes: bytes,
        width: int,
        height: int,
    ) -> bytes:
        """
        Generate an image using a reference hero asset as visual anchor.

        The reference image is passed as an inline_data Part alongside the text prompt.
        Gemini uses it for style, composition, and product continuity across variants.

        This implements the "1 hero → many variants" pattern from the Adobe use case:
        upload a hero product shot, get market-adapted variants at any aspect ratio.

        Note: This is best-effort — the model adapts the reference rather than
        pixel-perfectly preserving it. For strict inpainting, use a dedicated
        inpainting model (e.g., Firefly Image5 with mask).
        """
        from google.genai import types

        aspect = _dims_to_aspect(width, height)
        log.info("gemini.image.reference.request", model=self._model,
                 aspect=aspect, ref_bytes=len(reference_image_bytes), prompt=prompt[:80])

        # Detect MIME type from bytes magic bytes
        mime_type = _detect_mime_type(reference_image_bytes)

        # Build multipart content: [reference image, text prompt]
        contents = [
            types.Part.from_bytes(
                data=reference_image_bytes,
                mime_type=mime_type,
            ),
            types.Part.from_text(
                text=(
                    f"Using the provided reference image as a visual anchor for product "
                    f"appearance and style, create a new marketing creative:\n\n{prompt}\n\n"
                    f"Maintain the product's visual identity while adapting composition "
                    f"and context for the target market."
                )
            ),
        ]

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=aspect,
                ),
            ),
        )

        img_bytes = _extract_image_bytes(response, self._model, aspect)
        log.info("gemini.image.reference.success", model=self._model,
                 bytes=len(img_bytes), aspect=aspect)
        return img_bytes


def _extract_image_bytes(response, model: str, aspect: str) -> bytes:
    """
    Extract image bytes from a Gemini generate_content response.
    Tries inline_data first, then part.as_image() PIL fallback.
    """
    for part in response.parts:
        if part.inline_data is not None:
            img_bytes = part.inline_data.data
            if isinstance(img_bytes, str):
                img_bytes = base64.b64decode(img_bytes)
            log.info("gemini.image.success", model=model, bytes=len(img_bytes), aspect=aspect)
            return img_bytes

    # Fallback: try part.as_image() PIL path → convert to bytes
    for part in response.parts:
        try:
            pil_img = part.as_image()
            if pil_img is not None:
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                img_bytes = buf.getvalue()
                log.info("gemini.image.success.pil", model=model,
                         bytes=len(img_bytes), aspect=aspect)
                return img_bytes
        except Exception:
            continue

    raise RuntimeError(
        f"gemini-2.5-flash-image returned no image parts. "
        f"Response text: {getattr(response, 'text', 'N/A')[:200]}"
    )


def _detect_mime_type(image_bytes: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    elif image_bytes[:3] == b'\xff\xd8\xff':
        return "image/jpeg"
    elif image_bytes[:6] in (b'GIF87a', b'GIF89a'):
        return "image/gif"
    elif image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return "image/webp"
    return "image/png"  # default


def _dims_to_aspect(width: int, height: int) -> str:
    """
    Convert pixel dimensions to gemini-2.5-flash-image aspect ratio string.
    Supported: 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9
    """
    ratio = width / height
    if abs(ratio - 1.0) < 0.05:
        return "1:1"
    elif abs(ratio - 16/9) < 0.05:
        return "16:9"
    elif abs(ratio - 9/16) < 0.05:
        return "9:16"
    elif abs(ratio - 4/3) < 0.05:
        return "4:3"
    elif abs(ratio - 3/4) < 0.05:
        return "3:4"
    elif abs(ratio - 3/2) < 0.05:
        return "3:2"
    elif abs(ratio - 2/3) < 0.05:
        return "2:3"
    elif ratio > 1:
        return "16:9"
    else:
        return "9:16"
