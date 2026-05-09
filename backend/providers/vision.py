"""
Vision providers — CreativeOS v4.

Used by the competitor_analyze node to analyze competitor ad screenshots.
Extracts layout, color palette, emotional tone, claims, strengths/weaknesses,
and synthesizes a counter-brief.

Usage:
    from backend.providers.vision import get_vision_provider
    provider = get_vision_provider()
    analysis = await provider.analyze_ad(image_bytes, extracted_text)
"""
from __future__ import annotations
import base64
import os
import json
import structlog
from abc import ABC, abstractmethod
from backend.graph.state import CompetitorAnalysis

log = structlog.get_logger(__name__)

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"


# ── Abstract base ─────────────────────────────────────────────────────────────

class VisionProvider(ABC):
    """Abstract vision/analysis provider for competitor ad analysis."""

    @abstractmethod
    async def analyze_ad(
        self,
        image: bytes,
        extracted_text: str,
        brand_context: str = "",
    ) -> CompetitorAnalysis:
        """
        Analyze a competitor ad image and extracted text.
        Returns a CompetitorAnalysis with counter-brief.
        """
        ...

    @abstractmethod
    async def extract_text(self, image: bytes) -> str:
        """
        OCR: extract all text from an image.
        Returns plain text string.
        """
        ...


# ── Llama 3.2 Vision (via OpenRouter) ────────────────────────────────────────

class LlamaVisionProvider(VisionProvider):
    """
    meta-llama/llama-3.2-11b-vision-instruct via OpenRouter.
    Free tier available. Good at layout/composition analysis.
    """

    VISION_MODEL = "meta-llama/llama-3.2-11b-vision-instruct:free"
    TEXT_MODEL = "meta-llama/llama-3.3-70b-instruct:free"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")

    async def extract_text(self, image: bytes) -> str:
        """OCR via Llama Vision — extract all visible text from the ad."""
        import httpx

        img_b64 = base64.b64encode(image).decode()

        payload = {
            "model": self.VISION_MODEL,
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
                                "Extract ALL text visible in this advertisement image. "
                                "Include headlines, taglines, body copy, CTAs, disclaimers, "
                                "prices, and any other text. Return only the extracted text, "
                                "one element per line."
                            ),
                        },
                    ],
                }
            ],
            "max_tokens": 500,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://creativeos.app",
            "X-Title": "CreativeOS Competitor Analysis",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{OPENROUTER_API_BASE}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        return data["choices"][0]["message"]["content"]

    async def analyze_ad(
        self,
        image: bytes,
        extracted_text: str,
        brand_context: str = "",
    ) -> CompetitorAnalysis:
        """
        Full competitor ad analysis:
        1. Vision model analyzes layout, colors, composition
        2. LLM synthesizes counter-brief
        """
        import httpx

        img_b64 = base64.b64encode(image).decode()

        # Step 1: Visual analysis
        visual_prompt = f"""Analyze this competitor advertisement in detail.

Extracted text from the ad:
{extracted_text}

{f"Our brand context: {brand_context}" if brand_context else ""}

Provide a detailed analysis covering:
1. LAYOUT: Describe the visual layout, hierarchy, and composition
2. COLOR_PALETTE: List the dominant colors (as hex codes if possible)
3. EMOTIONAL_TONE: What emotion/feeling does this ad evoke?
4. CLAIMS_MADE: List all explicit and implicit claims made
5. STRENGTHS: What is working well in this ad?
6. WEAKNESSES: What is weak or missing?
7. COUNTER_STRATEGY: How should a competitor respond? Be specific about visual style, tone, claims, and CTA.

Return as JSON with these exact keys: layout_description, color_palette (array of strings), 
emotional_tone, claims_made (array), strengths (array), weaknesses (array), counter_strategy.
Also include style_hints as a dict with keys: visual_style, mood, color_direction, tone_direction, cta_direction."""

        payload = {
            "model": self.VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                        {"type": "text", "text": visual_prompt},
                    ],
                }
            ],
            "max_tokens": 1000,
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://creativeos.app",
            "X-Title": "CreativeOS Competitor Analysis",
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OPENROUTER_API_BASE}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]

        # Parse JSON response
        try:
            analysis_dict = json.loads(content)
        except json.JSONDecodeError:
            # Fallback: extract JSON from markdown code block
            import re
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
            if match:
                analysis_dict = json.loads(match.group(1))
            else:
                # Last resort: build minimal analysis from text
                analysis_dict = {
                    "layout_description": content[:500],
                    "color_palette": [],
                    "emotional_tone": "unknown",
                    "claims_made": [],
                    "strengths": [],
                    "weaknesses": [],
                    "counter_strategy": "Differentiate on quality and authenticity.",
                    "style_hints": {},
                }

        # Ensure style_hints exists
        if "style_hints" not in analysis_dict:
            analysis_dict["style_hints"] = {
                "visual_style": "differentiated",
                "mood": "contrasting",
                "color_direction": "opposite palette",
                "tone_direction": "contrasting tone",
                "cta_direction": "stronger, more direct",
            }

        log.info("vision.analysis_complete",
                 tone=analysis_dict.get("emotional_tone"),
                 claims=len(analysis_dict.get("claims_made", [])))

        return CompetitorAnalysis.model_validate(analysis_dict)


# ── Factory ───────────────────────────────────────────────────────────────────

def get_vision_provider(provider: str | None = None) -> VisionProvider:
    """Return the configured vision provider."""
    name = provider or os.getenv("VISION_PROVIDER", "llama")
    match name:
        case "llama" | "openrouter":
            return LlamaVisionProvider()
        case _:
            return LlamaVisionProvider()
