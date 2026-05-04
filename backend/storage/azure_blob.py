"""
Azure Blob Storage backend.
Activate via: STORAGE_BACKEND=azure
"""
import structlog
from azure.storage.blob import BlobServiceClient, ContentSettings
from backend.storage.base import StorageBackend
from backend.config import settings

log = structlog.get_logger(__name__)


class AzureBlobStorageBackend(StorageBackend):

    def __init__(self):
        self._service = BlobServiceClient.from_connection_string(
            settings.azure_storage_connection_string
        )
        self._container = settings.azure_container_name

    def name(self) -> str:
        return f"azure/{self._container}"

    async def save(self, path: str, data: bytes, content_type: str = "image/png") -> str:
        log.info("azure.upload", container=self._container, path=path)
        blob_client = self._service.get_blob_client(container=self._container, blob=path)
        blob_client.upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        return self.public_url(path)

    async def load(self, path: str) -> bytes:
        blob_client = self._service.get_blob_client(container=self._container, blob=path)
        return blob_client.download_blob().readall()

    async def exists(self, path: str) -> bool:
        blob_client = self._service.get_blob_client(container=self._container, blob=path)
        return blob_client.exists()

    def public_url(self, path: str) -> str:
        account = self._service.account_name
        return f"https://{account}.blob.core.windows.net/{self._container}/{path}"
