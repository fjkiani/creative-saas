"""
Central configuration — CreativeOS v4.
Loaded from .env via pydantic-settings.
All provider/backend selection happens here via env vars.

v4 additions:
  - openrouter_api_key: for vision, edit, and AI video providers
  - stripe_*: billing and subscriptions
  - instagram_*: Meta Graph API OAuth
  - tiktok_*: TikTok Content Posting API OAuth
  - apify_api_token: competitor URL scraping
  - edit_provider: gpt5 | gemini
  - vision_provider: llama
  - frontend_url: for Stripe redirect URLs

IMPORTANT — supabase_url default is intentionally empty string "".
  Do NOT default to "http://localhost:54321" — that would make
  supabase_configured return False even when the URL looks set,
  and would silently fall back to LocalDB in production.
  Empty string → LocalDB stub (safe dev default).
  https://... URL + service key → real Supabase (production).
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM ─────────────────────────────────────────────────
    llm_provider: str = Field(default="gemini", description="gemini | openai | anthropic")
    gemini_api_key: str = Field(default="")
    openai_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")

    # ── Image Generation ─────────────────────────────────────
    image_provider: str = Field(default="gemini", description="gemini | openai | firefly | stability")
    firefly_client_id: str = Field(default="")
    firefly_client_secret: str = Field(default="")
    stability_api_key: str = Field(default="")

    # ── v4: OpenRouter (vision, edit, AI video) ───────────────
    openrouter_api_key: str = Field(default="", description="Required for canvas editor, competitor analysis, AI video")
    edit_provider: str = Field(default="gpt5", description="gpt5 | gemini")
    vision_provider: str = Field(default="llama", description="llama")

    # ── Supabase ─────────────────────────────────────────────
    # Default MUST be empty string — empty triggers LocalDB fallback in client.py.
    # Do NOT default to localhost; that would mask missing config in production.
    supabase_url: str = Field(default="")
    supabase_anon_key: str = Field(default="")
    supabase_service_role_key: str = Field(default="")
    # Legacy alias — accepted from env as SUPABASE_SERVICE_KEY
    supabase_service_key: str = Field(default="")

    @property
    def supabase_service_key_resolved(self) -> str:
        """Return service_role_key, falling back to legacy supabase_service_key alias."""
        return self.supabase_service_role_key or self.supabase_service_key

    @property
    def supabase_configured(self) -> bool:
        """
        True when a real Supabase project URL and service key are both present.
        Used by db/client.py and storage/base.py to decide LocalDB vs real Supabase.
        """
        url = self.supabase_url
        key = self.supabase_service_key_resolved
        return bool(url and key and url.startswith("https://"))

    # ── Storage ──────────────────────────────────────────────
    storage_backend: str = Field(default="supabase", description="supabase | s3 | azure | dropbox | local")
    aws_access_key_id: str = Field(default="")
    aws_secret_access_key: str = Field(default="")
    aws_region: str = Field(default="us-east-1")
    s3_bucket_name: str = Field(default="creative-assets")
    azure_storage_connection_string: str = Field(default="")
    azure_container_name: str = Field(default="creative-assets")
    dropbox_access_token: str = Field(default="")

    # ── v4: Stripe (billing) ──────────────────────────────────
    stripe_secret_key: str = Field(default="")
    stripe_webhook_secret: str = Field(default="")
    stripe_price_pro: str = Field(default="", description="Stripe Price ID for Pro plan ($49/mo)")
    stripe_price_agency: str = Field(default="", description="Stripe Price ID for Agency plan ($199/mo)")
    stripe_price_enterprise: str = Field(default="", description="Stripe Price ID for Enterprise plan")

    # ── v4: Instagram OAuth (Meta Graph API) ──────────────────
    instagram_client_id: str = Field(default="")
    instagram_client_secret: str = Field(default="")
    instagram_redirect_uri: str = Field(default="")

    # ── v4: TikTok OAuth (Content Posting API v2) ─────────────
    tiktok_client_key: str = Field(default="")
    tiktok_client_secret: str = Field(default="")
    tiktok_redirect_uri: str = Field(default="")

    # ── v4: Apify (competitor URL scraping) ───────────────────
    apify_api_token: str = Field(default="", description="Required for URL-based competitor analysis")

    # ── App ──────────────────────────────────────────────────
    pipeline_api_key: str = Field(default="", description="X-Api-Key header value; empty = auth disabled (dev mode)")
    backend_port: int = Field(default=8000)
    cors_origins: str = Field(default="http://localhost:5173")
    log_level: str = Field(default="INFO")
    frontend_url: str = Field(default="http://localhost:5173", description="Used for Stripe redirect URLs")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    @property
    def llm_api_key_configured(self) -> bool:
        """True if at least one LLM API key is set."""
        return bool(self.gemini_api_key or self.openai_api_key or self.anthropic_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
