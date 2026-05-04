"""
Pipeline state and all shared Pydantic models.

PipelineState is the single source of truth passed between every LangGraph node.
All structured LLM outputs are Pydantic models — no raw string parsing anywhere.

Note on naming: LangGraph prohibits node names that match state keys.
State keys that store node outputs use the _result suffix to avoid collision.
"""
from __future__ import annotations
from typing import TypedDict, Any
from pydantic import BaseModel, Field


# ── Input models ─────────────────────────────────────────────────────────────

class ProductBrief(BaseModel):
    id: str
    name: str
    description: str
    tagline: str | None = None
    key_claims: list[str] = Field(default_factory=list)
    prohibited_claims: list[str] = Field(default_factory=list)
    price_usd: float | None = None
    hero_color: str | None = None
    existing_asset: str | None = None  # local path or http(s) URL; None = generate from scratch


class MarketBrief(BaseModel):
    """
    Flexible market model — accepts both the simple schema (region/language/audience/message)
    and the richer YAML schema (id/locale/currency/platform/legal_footer).
    All fields are optional with sensible defaults so either format works.
    """
    # Rich YAML format
    id: str | None = None
    locale: str | None = None
    currency: str | None = None
    platform: str | None = None
    legal_footer: str | None = None
    # Simple format
    region: str | None = None
    language: str | None = None
    audience: str | None = None
    message: str | None = None

    @property
    def market_id(self) -> str:
        """Canonical market identifier."""
        return self.id or self.region or "unknown"

    @property
    def lang(self) -> str:
        """Canonical language code."""
        if self.language:
            return self.language
        if self.locale:
            return self.locale.split("-")[0]
        return "en"

    @property
    def display_region(self) -> str:
        return self.region or self.id or self.locale or "unknown"


class CampaignBrief(BaseModel):
    campaign_id: str
    brand: str
    brand_config: str | None = None
    objective: str | None = None
    tone: str | None = None
    products: list[ProductBrief]
    markets: list[MarketBrief]
    aspect_ratios: list[str] = Field(default=["1:1", "9:16", "16:9"])
    style_hints: dict | None = None


# ── LLM output models (structured outputs) ───────────────────────────────────

class CreativeSpec(BaseModel):
    """Output of the enrich node — enriched creative direction for the campaign."""
    visual_style: str = Field(description="Overall visual style: e.g. 'clean minimalist', 'vibrant lifestyle'")
    mood: str = Field(description="Emotional tone: e.g. 'energetic', 'serene', 'aspirational'")
    color_palette_description: str = Field(description="Describe the color palette to use in image generation")
    lighting: str = Field(description="Lighting style: e.g. 'soft natural light', 'studio lighting'")
    composition_notes: str = Field(description="Composition guidance for the hero image")
    negative_prompt: str = Field(description="What to avoid in image generation")
    brand_voice: str = Field(description="Tone of copy: e.g. 'authoritative', 'warm', 'playful'")


class ImagePrompt(BaseModel):
    """A single optimized image generation prompt for one product × market combination."""
    product_id: str
    market: str
    prompt: str = Field(description="Full, detailed image generation prompt optimized for the target audience and region")
    negative_prompt: str = Field(description="What to avoid")


class ImagePromptSet(BaseModel):
    """Output of the prompt_gen node — one prompt per product × market."""
    prompts: list[ImagePrompt]


class ComplianceIssue(BaseModel):
    severity: str  # WARNING | ERROR
    category: str  # LEGAL | BRAND | PROHIBITED_WORD | HEALTH_CLAIM | COMPETITOR
    description: str
    flagged_text: str | None = None
    # Optional per-asset attribution (used by compliance_post writeback)
    product_id: str | None = None
    market: str | None = None


class ComplianceReport(BaseModel):
    """Output of compliance check nodes."""
    passed: bool
    issues: list[ComplianceIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class LocalizedCopy(BaseModel):
    """Localized campaign copy for one product × market."""
    product_id: str
    market: str
    language: str
    headline: str
    tagline: str
    cta: str | None = None
    translation_notes: str | None = None


class LocalizedCopySet(BaseModel):
    """Output of the localize node."""
    copies: list[LocalizedCopy]


# ── Asset tracking ────────────────────────────────────────────────────────────

class GeneratedAsset(BaseModel):
    """Tracks one generated/reused raw image (before compositing)."""
    product_id: str
    market: str
    storage_url: str
    storage_path: str
    prompt_hash: str
    reused: bool = False
    provider: str


class CompositedAsset(BaseModel):
    """Tracks one final composited creative (after text overlay, logo, crop)."""
    product_id: str
    market: str
    aspect_ratio: str
    language: str
    storage_url: str
    storage_path: str
    compliance_passed: bool | None = None  # set by compliance_post node


# ── LangGraph state ───────────────────────────────────────────────────────────

class PipelineState(TypedDict):
    """
    Shared state passed between every LangGraph node.

    IMPORTANT: State key names must NOT match node names (LangGraph constraint).
    Node output keys use descriptive names distinct from node names.
    """
    # Identity
    run_id: str
    campaign_id: str

    # Input
    brief: dict  # CampaignBrief serialized to dict

    # Node outputs — named to avoid collision with node names
    creative_spec: dict | None           # output of 'enrich' node
    image_prompts: list[dict]            # output of 'prompt_gen' node
    pre_compliance: dict | None          # output of 'compliance_pre' node
    generated_assets: list[dict]         # output of 'image_gen' node
    composited_assets: list[dict]        # output of 'composite' node
    localized_copy: list[dict]           # output of 'localize' node
    post_compliance: dict | None         # output of 'compliance_post' node

    # Human-in-the-loop review (review_gate node)
    review_decision: str | None          # "approved" | "rejected" | None
    review_score: float | None           # confidence score 0–1
    reviewer_notes: str | None           # human reviewer notes

    # Execution metadata
    current_node: str
    errors: list[str]
    provider_llm: str
    provider_image: str
    storage_backend: str
