"""
Supabase client singleton.
Provides both the anon client (for reads) and service-role client (for writes).
"""
from functools import lru_cache
from supabase import create_client, Client
from backend.config import settings
import structlog

log = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """Return the anon-key Supabase client (singleton)."""
    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    log.info("supabase.client.initialized", url=settings.supabase_url)
    return client


@lru_cache(maxsize=1)
def get_supabase_admin() -> Client:
    """Return the service-role Supabase client for server-side writes (singleton)."""
    client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    log.info("supabase.admin.initialized", url=settings.supabase_url)
    return client
