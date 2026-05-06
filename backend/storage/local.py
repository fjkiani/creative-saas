"""
Local filesystem storage backend — for development and demo runs.
No cloud credentials required.

Files are saved under /app/outputs/<path> (configurable via OUTPUTS_DIR env var).
Public URLs are returned as /outputs/<path> — a relative URL served by the
FastAPI StaticFiles mount at /outputs, proxied through nginx on the frontend.
"""
import asyncio
import os
import structlog
from pathlib import Path

from backend.storage.base import StorageBackend

log = structlog.get_logger(__name__)

# Root output directory — configurable, defaults to /app/outputs in production
OUTPUT_ROOT = Path(os.getenv("OUTPUTS_DIR", "/app/outputs"))


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
        # Return a browser-accessible relative URL served by the /outputs StaticFiles mount
        return f"/outputs/{path}"

    async def load(self, path: str) -> bytes:
        full_path = self._root / path
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, full_path.read_bytes)

    async def exists(self, path: str) -> bool:
        return (self._root / path).exists()

    def public_url(self, path: str) -> str:
        return f"/outputs/{path}"
