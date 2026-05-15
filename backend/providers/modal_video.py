"""
Modal Video Provider — CreativeOS v4.

Calls the Modal-deployed Wan2.2-T2V-A14B endpoint for AI video generation.
Falls back to SlideshowVideoProvider if Modal endpoint is not configured or fails.

Endpoint contract (set by modal_apps/video_gen.py):
  POST <MODAL_VIDEO_ENDPOINT>/generate
  Headers: Modal-Key, Modal-Secret
  Body: {"prompt": str, "duration_s": int, "width": int, "height": int, "fps": int}
  Response: MP4 bytes (Content-Type: video/mp4)
             OR {"error": str} on failure
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import structlog
from pathlib import Path
from backend.providers.video import VideoProvider, SlideshowVideoProvider

log = structlog.get_logger(__name__)

# Aspect ratio → resolution mapping for Wan2.2
RATIO_DIMS = {
    "1:1":  (832, 832),
    "9:16": (624, 1104),
    "16:9": (1104, 624),
}

# Available HF video models deployed on Modal
MODAL_VIDEO_MODELS = [
    {
        "id": "wan2.2-t2v-a14b",
        "name": "Wan2.2 T2V A14B",
        "hf_repo": "QuantStack/Wan2.2-T2V-A14B-GGUF",
        "params": "14B (GGUF Q4_K_M)",
        "vram": "~8GB",
        "tags": ["text-to-video", "open-source", "free"],
    },
]


class ModalVideoProvider(VideoProvider):
    """
    AI video generation via Modal-deployed Wan2.2 endpoint.
    Generates per-image motion clips, assembles with moviepy.
    Falls back to SlideshowVideoProvider if Modal is unavailable.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        key_id: str | None = None,
        key_secret: str | None = None,
    ):
        self.endpoint = (endpoint or os.getenv("MODAL_VIDEO_ENDPOINT", "")).rstrip("/")
        self.key_id = key_id or os.getenv("MODAL_KEY_ID", "")
        self.key_secret = key_secret or os.getenv("MODAL_KEY_SECRET", "")
        self._fallback = SlideshowVideoProvider()

    async def generate_ai_clip(
        self,
        image_path: str,
        prompt: str,
        duration_s: int = 4,
    ) -> bytes:
        """Generate a single AI video clip from an image via Modal."""
        if not self.endpoint:
            raise RuntimeError("MODAL_VIDEO_ENDPOINT not configured")

        # Load image and build a rich prompt
        import base64
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        return await self._call_modal(
            prompt=f"{prompt}, cinematic motion, smooth camera movement, professional lighting",
            duration_s=duration_s,
            width=832,
            height=832,
            image_b64=img_b64,
        )

    async def generate_slideshow(
        self,
        image_paths: list[str],
        ratio: str,
        run_id: str,
        music_url: str | None = None,
    ) -> tuple[bytes, float]:
        """
        Generate AI video clips for each image, assemble into a trailer.
        Falls back to slideshow if Modal fails.
        """
        if not self.endpoint:
            log.warning("modal_video.no_endpoint", fallback="slideshow")
            return await self._fallback.generate_slideshow(image_paths, ratio, run_id, music_url)

        width, height = RATIO_DIMS.get(ratio, (832, 832))
        clip_paths: list[str] = []

        for i, img_path in enumerate(image_paths):
            try:
                log.info("modal_video.clip_start", index=i, total=len(image_paths))
                prompt = "product advertisement, smooth cinematic motion, professional lighting, 4K quality"
                clip_bytes = await self.generate_ai_clip(img_path, prompt, duration_s=4)

                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                    tmp.write(clip_bytes)
                    clip_paths.append(tmp.name)

                log.info("modal_video.clip_done", index=i, bytes=len(clip_bytes))

            except Exception as e:
                log.warning("modal_video.clip_failed", index=i, error=str(e))

        if not clip_paths:
            log.warning("modal_video.all_clips_failed", fallback="slideshow")
            return await self._fallback.generate_slideshow(image_paths, ratio, run_id, music_url)

        # Assemble clips with moviepy
        try:
            video_bytes, duration = await asyncio.get_event_loop().run_in_executor(
                None, self._assemble_clips, clip_paths
            )
            return video_bytes, duration
        except Exception as e:
            log.error("modal_video.assemble_failed", error=str(e), fallback="slideshow")
            return await self._fallback.generate_slideshow(image_paths, ratio, run_id, music_url)
        finally:
            for p in clip_paths:
                Path(p).unlink(missing_ok=True)

    def _assemble_clips(self, clip_paths: list[str]) -> tuple[bytes, float]:
        from moviepy.editor import VideoFileClip, concatenate_videoclips

        clips = [VideoFileClip(p) for p in clip_paths]
        final = concatenate_videoclips(clips, method="compose")
        duration = final.duration

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            final.write_videofile(tmp_path, fps=24, codec="libx264", logger=None)
            with open(tmp_path, "rb") as f:
                return f.read(), duration
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            for c in clips:
                c.close()
            final.close()

    async def _call_modal(
        self,
        prompt: str,
        duration_s: int,
        width: int,
        height: int,
        image_b64: str | None = None,
    ) -> bytes:
        import httpx

        headers = {
            "Modal-Key": self.key_id,
            "Modal-Secret": self.key_secret,
            "Content-Type": "application/json",
        }

        payload: dict = {
            "prompt": prompt,
            "duration_s": duration_s,
            "width": width,
            "height": height,
            "fps": 16,
        }
        if image_b64:
            payload["image_b64"] = image_b64

        log.info("modal_video.request", endpoint=self.endpoint, duration_s=duration_s)

        async with httpx.AsyncClient(timeout=360) as client:
            resp = await client.post(
                f"{self.endpoint}/generate",
                headers=headers,
                json=payload,
            )

            if resp.status_code != 200:
                raise RuntimeError(
                    f"Modal video endpoint returned HTTP {resp.status_code}: {resp.text[:300]}"
                )

            content_type = resp.headers.get("content-type", "")
            if "video" in content_type or resp.content[:4] == b"\x00\x00\x00":
                log.info("modal_video.success", bytes=len(resp.content))
                return resp.content

            try:
                err = resp.json()
                raise RuntimeError(f"Modal video error: {err}")
            except Exception:
                raise RuntimeError(f"Unexpected Modal response: {resp.text[:200]}")
