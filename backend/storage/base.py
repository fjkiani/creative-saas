"""
Abstract storage backend interface.

Design pattern: Adapter — swap storage backends via STORAGE_BACKEND env var.
All backends expose the same interface: save / load / exists / public_url.
"""
from abc import ABC, abstractmethod


class StorageBackend(ABC):

    @abstractmethod
    async def save(self, path: str, data: bytes, content_type: str = "image/png") -> str:
        """
        Save bytes to the given path.
        Returns the public URL of the saved file.
        """
        ...

    @abstractmethod
    async def load(self, path: str) -> bytes:
        """Load bytes from the given path."""
        ...

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Check if a file exists at the given path."""
        ...

    @abstractmethod
    def public_url(self, path: str) -> str:
        """Return the public URL for a given storage path."""
        ...

    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name."""
        ...


def get_storage_backend() -> StorageBackend:
    """Factory: return the configured storage backend."""
    from backend.config import settings

    match settings.storage_backend.lower():
        case "local":
            from backend.storage.local import LocalStorageBackend
            return LocalStorageBackend()
        case "s3":
            from backend.storage.s3 import S3StorageBackend
            return S3StorageBackend()
        case "azure":
            from backend.storage.azure_blob import AzureBlobStorageBackend
            return AzureBlobStorageBackend()
        case "dropbox":
            from backend.storage.dropbox_storage import DropboxStorageBackend
            return DropboxStorageBackend()
        case _:  # default: supabase
            from backend.storage.supabase_storage import SupabaseStorageBackend
            return SupabaseStorageBackend()
