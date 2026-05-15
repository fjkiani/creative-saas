"""
Abstract base interfaces for LLM and Image providers.

Design pattern: Strategy — swap providers via IMAGE_PROVIDER / LLM_PROVIDER env vars.
Zero code changes required to switch between providers.

Provider registry:
  LLM:   openrouter (default) | gemini | openai | anthropic
  Image: modal (default) | gemini | openai | firefly | stability
  Video: modal (default) | slideshow | ai | ai_hailuo
"""
from abc import ABC, abstractmethod
from pydantic import BaseModel


class LLMProvider(ABC):
    """Abstract LLM provider. All structured outputs return validated Pydantic models."""

    @abstractmethod
    async def complete(
        self,
        system: str,
        user: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        """
        Send a structured completion request.
        Returns a validated Pydantic model — never raw strings.
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name for logging/reporting."""
        ...


class ImageProvider(ABC):
    """Abstract image generation provider."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        width: int,
        height: int,
    ) -> bytes:
        """
        Generate an image from a text prompt.
        Returns raw PNG/JPEG bytes.
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name for logging/reporting."""
        ...


def get_llm_provider() -> LLMProvider:
    """Factory: return the configured LLM provider."""
    from backend.config import settings

    match settings.llm_provider.lower():
        case "openrouter":
            from backend.providers.openrouter_llm import OpenRouterLLMProvider
            return OpenRouterLLMProvider()
        case "openai":
            from backend.providers.openai_dalle import OpenAILLMProvider
            return OpenAILLMProvider()
        case "anthropic":
            from backend.providers.anthropic_claude import AnthropicLLMProvider
            return AnthropicLLMProvider()
        case _:  # default: gemini
            from backend.providers.gemini import GeminiLLMProvider
            return GeminiLLMProvider()


def get_image_provider() -> ImageProvider:
    """Factory: return the configured image generation provider."""
    from backend.config import settings

    match settings.image_provider.lower():
        case "modal":
            from backend.providers.modal_image import ModalImageProvider
            return ModalImageProvider()
        case "openai":
            from backend.providers.openai_dalle import OpenAIImageProvider
            return OpenAIImageProvider()
        case "firefly":
            from backend.providers.firefly import FireflyImageProvider
            return FireflyImageProvider()
        case "stability":
            from backend.providers.stability import StabilityImageProvider
            return StabilityImageProvider()
        case _:  # default: gemini
            from backend.providers.gemini import GeminiImageProvider
            return GeminiImageProvider()
