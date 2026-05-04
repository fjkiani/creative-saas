"""
Dropbox storage backend.
Activate via: STORAGE_BACKEND=dropbox
"""
import structlog
import dropbox
from dropbox.files import WriteMode
from backend.storage.base import StorageBackend
from backend.config import settings

log = structlog.get_logger(__name__)


class DropboxStorageBackend(StorageBackend):

    def __init__(self):
        self._dbx = dropbox.Dropbox(settings.dropbox_access_token)

    def name(self) -> str:
        return "dropbox"

    def _dropbox_path(self, path: str) -> str:
        return f"/creative-assets/{path}"

    async def save(self, path: str, data: bytes, content_type: str = "image/png") -> str:
        dbx_path = self._dropbox_path(path)
        log.info("dropbox.upload", path=dbx_path)
        self._dbx.files_upload(data, dbx_path, mode=WriteMode.overwrite)
        # Create a shared link for public access
        try:
            link = self._dbx.sharing_create_shared_link(dbx_path)
            return link.url.replace("?dl=0", "?raw=1")
        except dropbox.exceptions.ApiError:
            links = self._dbx.sharing_list_shared_links(path=dbx_path)
            return links.links[0].url.replace("?dl=0", "?raw=1")

    async def load(self, path: str) -> bytes:
        _, response = self._dbx.files_download(self._dropbox_path(path))
        return response.content

    async def exists(self, path: str) -> bool:
        try:
            self._dbx.files_get_metadata(self._dropbox_path(path))
            return True
        except dropbox.exceptions.ApiError:
            return False

    def public_url(self, path: str) -> str:
        # Dropbox URLs are generated at upload time; return placeholder
        return f"dropbox://creative-assets/{path}"
