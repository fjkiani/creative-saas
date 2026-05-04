"""
Anthropic Claude provider — LLM only (no image generation).

Model: claude-3-5-sonnet-20241022
Activate via: LLM_PROVIDER=anthropic
"""
import json
import structlog
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from backend.providers.base import LLMProvider
from backend.config import settings

log = structlog.get_logger(__name__)


class AnthropicLLMProvider(LLMProvider):
    """Claude 3.5 Sonnet with structured JSON outputs."""

    def __init__(self):
        import instructor
        import anthropic
        self._raw_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._client = instructor.from_anthropic(self._raw_client)
        self._model = "claude-3-5-sonnet-20241022"

    def name(self) -> str:
        return f"anthropic/{self._model}"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def complete(self, system: str, user: str, response_model: type[BaseModel]) -> BaseModel:
        log.info("anthropic.llm.request", model=self._model, response_model=response_model.__name__)
        result = await self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
            response_model=response_model,
        )
        log.info("anthropic.llm.response", model=self._model)
        return result
