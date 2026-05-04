"""
Central configuration — loaded from .env via pydantic-settings.
All provider/backend selection happens here via env vars.
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

    # ── Supabase ─────────────────────────────────────────────
    supabase_url: str = Field(default="http://localhost:54321")
    supabase_anon_key: str = Field(default="")
    supabase_service_role_key: str = Field(default="")

    # ── Storage ──────────────────────────────────────────────
    storage_backend: str = Field(default="supabase", description="supabase | s3 | azure | dropbox")
    aws_access_key_id: str = Field(default="")
    aws_secret_access_key: str = Field(default="")
    aws_region: str = Field(default="us-east-1")
    s3_bucket_name: str = Field(default="creative-assets")
    azure_storage_connection_string: str = Field(default="")
    azure_container_name: str = Field(default="creative-assets")
    dropbox_access_token: str = Field(default="")

    # ── App ──────────────────────────────────────────────────
    backend_port: int = Field(default=8000)
    cors_origins: str = Field(default="http://localhost:5173")
    log_level: str = Field(default="INFO")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
