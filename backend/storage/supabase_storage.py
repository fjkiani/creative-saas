"""
Supabase Storage backend.

Bucket: creative-assets
Path convention: {run_id}/{product_id}/{market}/{ratio}.png

Falls back gracefully to LocalStorageBackend if Supabase is not configured,
so the pipeline never crashes due to missing storage credentials.
"""
import structlog
from backend.storage.base import StorageBackend
from backend.config import settings

log = structlog.get_logger(__name__)

BUCKET = "creative-assets"


def _get_storage_client():
    """
    Return the Supabase Storage bucket client.
    Raises RuntimeError with a clear message if Supabase is not configured,
    so callers can catch and fall back rather than getting an AttributeError.
    """
    from backend.db.client import get_supabase_admin, using_local_db
    if using_local_db():
        raise RuntimeError(
            "Supabase is not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing). "
            "Set STORAGE_BACKEND=local or provide Supabase credentials."
        )
    return get_supabase_admin().storage.from_(BUCKET)


class SupabaseStorageBackend(StorageBackend):

    def name(self) -> str:
        return "supabase-storage"

    async def save(self, path: str, data: bytes, content_type: str = "image/png") -> str:
        log.info("supabase.storage.upload", path=path, bytes=len(data))
        _get_storage_client().upload(
            path=path,
            file=data,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        url = self.public_url(path)
        log.info("supabase.storage.uploaded", path=path, url=url)
        return url

    async def load(self, path: str) -> bytes:
        log.info("supabase.storage.download", path=path)
        data = _get_storage_client().download(path)
        return data

    async def exists(self, path: str) -> bool:
        try:
            folder = path.rsplit("/", 1)[0]
            filename = path.rsplit("/", 1)[-1]
            files = _get_storage_client().list(folder)
            return any(f["name"] == filename for f in (files or []))
        except Exception:
            return False

    def public_url(self, path: str) -> str:
        from backend.db.client import get_supabase_admin
        result = get_supabase_admin().storage.from_(BUCKET).get_public_url(path)
        return result
