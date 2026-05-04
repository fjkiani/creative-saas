"""
Local filesystem storage backend — for development and demo runs.
No cloud credentials required.

Files are saved under outputs/<path> relative to the project root.
Public URLs are file:// paths (or served by a static file server in dev).
"""
import asyncio
import structlog
from pathlib import Path

from backend.storage.base import StorageBackend

log = structlog.get_logger(__name__)

# Root output directory — relative to project root
OUTPUT_ROOT = Path("outputs")


class LocalStorageBackend(StorageBackend):

    def __init__(self, root: Path = OUTPUT_ROOT):
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def name(self) -> str:
        return f"local:{self._root}"

    async def save(self, path: str, data: bytes, content_type: str = "image/png") -> str:
        full_path = self._root / path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # Run blocking I/O in thread pool
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, full_path.write_bytes, data)

        log.info("local_storage.save", path=path, bytes=len(data))
        return str(full_path.resolve())

    async def load(self, path: str) -> bytes:
        full_path = self._root / path
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, full_path.read_bytes)

    async def exists(self, path: str) -> bool:
        return (self._root / path).exists()

    def public_url(self, path: str) -> str:
        return str((self._root / path).resolve())
