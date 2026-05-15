"""
OpenRouter LLM Provider — CreativeOS v4.

Routes to free OpenRouter models with automatic fallback:
  1. nvidia/nemotron-nano-9b-v2        (128K ctx, reasoning)
  2. deepseek/deepseek-v4-flash        (1M ctx, fast)
  3. google/gemma-4-26b-a4b            (256K ctx, multimodal)
  4. qwen/qwen3-coder-480b-a35b        (262K ctx, strong reasoning)
  5. meta-llama/llama-3.3-70b-instruct (66K ctx, reliable)

Override default model via OPENROUTER_MODEL env var.
Override fallback chain via OPENROUTER_FALLBACK_MODELS (comma-separated).

All models use JSON-mode structured output (same pattern as GeminiLLMProvider).
"""
from __future__ import annotations

import json
import os
import asyncio
import structlog
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from backend.providers.base import LLMProvider

log = structlog.get_logger(__name__)

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"

# Free model roster — ordered by capability/reliability
FREE_MODELS = [
    "nvidia/nemotron-nano-9b-v2",           # reasoning, 128K
    "deepseek/deepseek-v4-flash",           # 1M ctx, fast MoE
    "google/gemma-4-26b-a4b",               # 256K, multimodal
    "qwen/qwen3-coder-480b-a35b",           # 262K, strong
    "meta-llama/llama-3.3-70b-instruct",    # 66K, reliable
    "qwen/qwen3-next-80b-a3b-instruct",     # 262K, stable
    "nousresearch/hermes-3-405b-instruct",  # 131K, agentic
]


class OpenRouterLLMProvider(LLMProvider):
    """
    OpenRouter LLM provider with free model fallback chain.
    Uses JSON-mode structured output — returns validated Pydantic models.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        fallback_models: list[str] | None = None,
    ):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.model = model or os.getenv("OPENROUTER_MODEL", FREE_MODELS[0])

        # Build fallback chain: configured model first, then rest of FREE_MODELS
        env_fallbacks = os.getenv("OPENROUTER_FALLBACK_MODELS", "")
        if fallback_models:
            self._fallback_chain = [self.model] + fallback_models
        elif env_fallbacks:
            self._fallback_chain = [self.model] + [m.strip() for m in env_fallbacks.split(",")]
        else:
            # Default: configured model + rest of free roster
            self._fallback_chain = [self.model] + [m for m in FREE_MODELS if m != self.model]

        self._active_model = self.model

    def name(self) -> str:
        return f"openrouter/{self._active_model}"

    async def complete(
        self,
        system: str,
        user: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        """
        Structured completion with automatic model fallback.
        Tries each model in the fallback chain until one succeeds.
        """
        last_error: Exception | None = None

        for model in self._fallback_chain:
            try:
                result = await self._complete_with_model(model, system, user, response_model)
                self._active_model = model
                return result
            except RateLimitError as e:
                log.warning("openrouter.rate_limit", model=model, error=str(e))
                last_error = e
                continue
            except ModelUnavailableError as e:
                log.warning("openrouter.model_unavailable", model=model, error=str(e))
                last_error = e
                continue
            except Exception as e:
                log.error("openrouter.unexpected_error", model=model, error=str(e))
                last_error = e
                # Don't fallback on parse errors — likely a prompt issue
                if "json" in str(e).lower() or "parse" in str(e).lower():
                    raise
                continue

        raise RuntimeError(
            f"All OpenRouter models exhausted. Last error: {last_error}"
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def _complete_with_model(
        self,
        model: str,
        system: str,
        user: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        """Single model completion attempt with retry."""
        import httpx

        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")

        schema = response_model.model_json_schema()
        prompt = (
            f"{system}\n\n"
            f"Respond ONLY with valid JSON matching this exact schema:\n"
            f"{json.dumps(schema, indent=2)}\n\n"
            f"Request:\n{user}"
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://creativeos.app",
            "X-Title": "CreativeOS",
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a JSON-only responder. Output valid JSON only, no markdown, no explanation."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
            "max_tokens": 4096,
        }

        log.info("openrouter.request", model=model, response_model=response_model.__name__)

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OPENROUTER_API_BASE}/chat/completions",
                headers=headers,
                json=payload,
            )

            if resp.status_code == 429:
                raise RateLimitError(f"Rate limited on {model}: {resp.text[:200]}")
            if resp.status_code in (503, 502, 500):
                raise ModelUnavailableError(f"Model {model} unavailable: HTTP {resp.status_code}")
            if resp.status_code == 402:
                raise ModelUnavailableError(f"Model {model} requires payment: HTTP 402")

            resp.raise_for_status()
            data = resp.json()

        # Extract content
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"No choices in response from {model}: {data}")

        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise RuntimeError(f"Empty content from {model}")

        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"JSON parse error from {model}: {e}\nContent: {content[:300]}")

        result = response_model.model_validate(parsed)
        log.info("openrouter.response.ok", model=model, response_model=response_model.__name__)
        return result


class RateLimitError(Exception):
    pass


class ModelUnavailableError(Exception):
    pass


# ── Model catalog for frontend/API exposure ───────────────────────────────────

FREE_MODEL_CATALOG = [
    {
        "id": "nvidia/nemotron-nano-9b-v2",
        "name": "NVIDIA Nemotron Nano 9B v2",
        "context": 128_000,
        "provider": "nvidia",
        "tags": ["reasoning", "free"],
    },
    {
        "id": "deepseek/deepseek-v4-flash",
        "name": "DeepSeek V4 Flash",
        "context": 1_048_576,
        "provider": "deepseek",
        "tags": ["fast", "long-context", "free"],
    },
    {
        "id": "google/gemma-4-26b-a4b",
        "name": "Google Gemma 4 26B",
        "context": 262_144,
        "provider": "google",
        "tags": ["multimodal", "free"],
    },
    {
        "id": "qwen/qwen3-coder-480b-a35b",
        "name": "Qwen3 Coder 480B",
        "context": 262_144,
        "provider": "qwen",
        "tags": ["coding", "reasoning", "free"],
    },
    {
        "id": "meta-llama/llama-3.3-70b-instruct",
        "name": "Llama 3.3 70B Instruct",
        "context": 65_536,
        "provider": "meta-llama",
        "tags": ["reliable", "free"],
    },
    {
        "id": "qwen/qwen3-next-80b-a3b-instruct",
        "name": "Qwen3 Next 80B",
        "context": 262_144,
        "provider": "qwen",
        "tags": ["stable", "free"],
    },
    {
        "id": "nousresearch/hermes-3-405b-instruct",
        "name": "Hermes 3 405B",
        "context": 131_072,
        "provider": "nousresearch",
        "tags": ["agentic", "free"],
    },
]
