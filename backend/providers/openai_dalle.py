"""
OpenAI providers.

LLM:   GPT-4o with structured outputs via instructor
Image: DALL-E 3 with native size support

Activate via: LLM_PROVIDER=openai / IMAGE_PROVIDER=openai
"""
import structlog
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from backend.providers.base import LLMProvider, ImageProvider
from backend.config import settings

log = structlog.get_logger(__name__)


class OpenAILLMProvider(LLMProvider):
    """GPT-4o with instructor for guaranteed structured outputs."""

    def __init__(self):
        import instructor
        from openai import AsyncOpenAI
        self._raw_client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._client = instructor.from_openai(self._raw_client)
        self._model = "gpt-4o"

    def name(self) -> str:
        return f"openai/{self._model}"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def complete(self, system: str, user: str, response_model: type[BaseModel]) -> BaseModel:
        log.info("openai.llm.request", model=self._model, response_model=response_model.__name__)
        result = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_model=response_model,
        )
        log.info("openai.llm.response", model=self._model)
        return result


class OpenAIImageProvider(ImageProvider):
    """DALL-E 3 — highest prompt adherence for ad copy."""

    # DALL-E 3 supported sizes
    _SIZES = {
        (1024, 1024): "1024x1024",
        (1024, 1792): "1024x1792",
        (1792, 1024): "1792x1024",
    }

    def __init__(self):
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    def name(self) -> str:
        return "openai/dall-e-3"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
    async def generate(self, prompt: str, width: int, height: int) -> bytes:
        import httpx

        size = self._SIZES.get((width, height), "1024x1024")
        log.info("dalle.image.request", size=size, prompt=prompt[:80])

        response = await self._client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size=size,
            quality="hd",
            response_format="url",
            n=1,
        )

        image_url = response.data[0].url
        async with httpx.AsyncClient() as client:
            img_response = await client.get(image_url, timeout=60)
            img_response.raise_for_status()

        log.info("dalle.image.success", bytes=len(img_response.content))
        return img_response.content
