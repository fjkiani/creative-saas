"""
Supabase Storage backend — default.

Bucket: creative-assets
Path convention: {run_id}/{product_id}/{market}/{ratio}.png
"""
import structlog
from backend.storage.base import StorageBackend
from backend.db.client import get_supabase_admin
from backend.config import settings

log = structlog.get_logger(__name__)

BUCKET = "creative-assets"


class SupabaseStorageBackend(StorageBackend):

    def name(self) -> str:
        return "supabase-storage"

    def _client(self):
        return get_supabase_admin().storage.from_(BUCKET)

    async def save(self, path: str, data: bytes, content_type: str = "image/png") -> str:
        log.info("supabase.storage.upload", path=path, bytes=len(data))
        self._client().upload(
            path=path,
            file=data,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        url = self.public_url(path)
        log.info("supabase.storage.uploaded", path=path, url=url)
        return url

    async def load(self, path: str) -> bytes:
        log.info("supabase.storage.download", path=path)
        data = self._client().download(path)
        return data

    async def exists(self, path: str) -> bool:
        try:
            files = self._client().list(path.rsplit("/", 1)[0])
            filename = path.rsplit("/", 1)[-1]
            return any(f["name"] == filename for f in files)
        except Exception:
            return False

    def public_url(self, path: str) -> str:
        result = get_supabase_admin().storage.from_(BUCKET).get_public_url(path)
        return result
