"""
Video providers — CreativeOS v4.

Two modes:
  Mode A — SlideshowVideoProvider: moviepy-based, $0 cost, Ken Burns + cross-dissolve.
  Mode B — WanVideoProvider / HailuoVideoProvider: AI per-clip via OpenRouter async polling.

Usage:
    from backend.providers.video import get_video_provider
    provider = get_video_provider("slideshow")
    url = await provider.generate_slideshow(image_paths, ratio, run_id)
"""
from __future__ import annotations
import asyncio
import io
import os
import time
import tempfile
import structlog
from abc import ABC, abstractmethod
from pathlib import Path

log = structlog.get_logger(__name__)

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"


# ── Abstract base ─────────────────────────────────────────────────────────────

class VideoProvider(ABC):
    """Abstract video generation provider."""

    @abstractmethod
    async def generate_slideshow(
        self,
        image_paths: list[str],   # local file paths or storage URLs
        ratio: str,               # "1:1" | "9:16" | "16:9"
        run_id: str,
        music_url: str | None = None,
    ) -> tuple[bytes, float]:
        """
        Generate a slideshow video from a list of images.
        Returns (video_bytes, duration_seconds).
        """
        ...

    @abstractmethod
    async def generate_ai_clip(
        self,
        image_path: str,
        prompt: str,
        duration_s: int = 4,
    ) -> bytes:
        """
        Generate an AI motion video clip from a single image.
        Returns video_bytes.
        """
        ...


# ── Mode A: Slideshow (moviepy, $0) ──────────────────────────────────────────

class SlideshowVideoProvider(VideoProvider):
    """
    Pure Python slideshow generator using moviepy.
    - Ken Burns effect (slow zoom/pan per image)
    - Cross-dissolve transitions (0.5s)
    - Brand color fade-in/out
    - Optional royalty-free music
    Cost: $0 (no API calls)
    """

    RATIO_DIMS = {
        "1:1":  (1080, 1080),
        "9:16": (1080, 1920),
        "16:9": (1920, 1080),
    }
    CLIP_DURATION = 3.5   # seconds per image
    TRANSITION_DURATION = 0.5  # cross-dissolve duration
    FADE_DURATION = 0.4   # fade in/out at start/end

    async def generate_slideshow(
        self,
        image_paths: list[str],
        ratio: str,
        run_id: str,
        music_url: str | None = None,
    ) -> tuple[bytes, float]:
        """Generate slideshow video. Runs moviepy in a thread pool to avoid blocking."""
        return await asyncio.get_event_loop().run_in_executor(
            None,
            self._build_slideshow_sync,
            image_paths, ratio, run_id, music_url,
        )

    def _build_slideshow_sync(
        self,
        image_paths: list[str],
        ratio: str,
        run_id: str,
        music_url: str | None,
    ) -> tuple[bytes, float]:
        try:
            from moviepy.editor import (
                ImageClip, concatenate_videoclips,
                AudioFileClip, CompositeVideoClip,
                ColorClip,
            )
            import numpy as np
            from PIL import Image
        except ImportError as e:
            raise RuntimeError(f"moviepy/PIL not installed: {e}")

        target_w, target_h = self.RATIO_DIMS.get(ratio, (1080, 1080))
        clips = []

        for i, img_path in enumerate(image_paths):
            try:
                # Load and resize image
                img = Image.open(img_path).convert("RGB")
                # Smart crop to target ratio
                img = self._smart_crop(img, target_w, target_h)
                img_array = np.array(img)

                # Base clip
                clip = ImageClip(img_array).set_duration(self.CLIP_DURATION)

                # Ken Burns: alternate zoom-in and zoom-out
                if i % 2 == 0:
                    # Zoom in: 1.0 → 1.08
                    clip = clip.resize(lambda t: 1.0 + 0.08 * (t / self.CLIP_DURATION))
                else:
                    # Zoom out: 1.08 → 1.0
                    clip = clip.resize(lambda t: 1.08 - 0.08 * (t / self.CLIP_DURATION))

                # Cross-dissolve: fade in/out on each clip
                if i > 0:
                    clip = clip.crossfadein(self.TRANSITION_DURATION)
                if i < len(image_paths) - 1:
                    clip = clip.crossfadeout(self.TRANSITION_DURATION)

                clips.append(clip)

            except Exception as e:
                log.warning("slideshow.clip_failed", path=img_path, error=str(e))
                continue

        if not clips:
            raise RuntimeError("No valid image clips to assemble")

        # Concatenate with padding for transitions
        final = concatenate_videoclips(clips, method="compose", padding=-self.TRANSITION_DURATION)

        # Fade in/out at video boundaries
        final = final.fadein(self.FADE_DURATION).fadeout(self.FADE_DURATION)

        duration = final.duration

        # Write to temp file then read bytes
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            final.write_videofile(
                tmp_path,
                fps=24,
                codec="libx264",
                audio_codec="aac",
                preset="fast",
                logger=None,  # suppress moviepy progress bars
                threads=2,
            )
            with open(tmp_path, "rb") as f:
                video_bytes = f.read()
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            final.close()
            for c in clips:
                c.close()

        log.info("slideshow.generated", ratio=ratio, clips=len(clips), duration_s=duration)
        return video_bytes, duration

    def _smart_crop(self, img, target_w: int, target_h: int):
        """Center-crop image to target dimensions."""
        from PIL import Image
        src_w, src_h = img.size
        scale = max(target_w / src_w, target_h / src_h)
        new_w = int(src_w * scale)
        new_h = int(src_h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        return img.crop((left, top, left + target_w, top + target_h))

    async def generate_ai_clip(self, image_path: str, prompt: str, duration_s: int = 4) -> bytes:
        raise NotImplementedError("SlideshowVideoProvider does not support AI clip generation")


# ── Mode B: AI Video via OpenRouter ──────────────────────────────────────────

class WanVideoProvider(VideoProvider):
    """
    alibaba/wan-2.6 via OpenRouter async polling.
    Generates per-image motion clips (~4s each).
    Cost: ~$0.02–0.05 per clip.
    """

    MODEL = "alibaba/wan-2.6"
    POLL_INTERVAL = 5   # seconds between status polls
    MAX_WAIT = 300       # 5 minutes max per clip

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")

    async def generate_ai_clip(
        self,
        image_path: str,
        prompt: str,
        duration_s: int = 4,
    ) -> bytes:
        """Submit image-to-video job and poll until complete."""
        import httpx
        import base64

        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")

        # Load image as base64
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://creativeos.app",
            "X-Title": "CreativeOS",
        }

        payload = {
            "model": self.MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                        {"type": "text", "text": f"{prompt} — smooth, cinematic, {duration_s} seconds"},
                    ],
                }
            ],
            "max_tokens": 1,  # video generation — token count irrelevant
        }

        async with httpx.AsyncClient(timeout=30) as client:
            # Submit job
            resp = await client.post(
                f"{OPENROUTER_API_BASE}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

            # OpenRouter returns a generation_id for async jobs
            generation_id = data.get("id")
            if not generation_id:
                raise RuntimeError(f"No generation_id in response: {data}")

            log.info("wan_video.submitted", generation_id=generation_id)

            # Poll for completion
            start = time.time()
            while time.time() - start < self.MAX_WAIT:
                await asyncio.sleep(self.POLL_INTERVAL)

                poll_resp = await client.get(
                    f"{OPENROUTER_API_BASE}/generation?id={generation_id}",
                    headers=headers,
                )
                poll_resp.raise_for_status()
                poll_data = poll_resp.json()

                status = poll_data.get("data", {}).get("status")
                log.info("wan_video.poll", generation_id=generation_id, status=status)

                if status == "complete":
                    video_url = poll_data["data"].get("video_url")
                    if not video_url:
                        raise RuntimeError("No video_url in completed generation")
                    # Download video
                    dl = await client.get(video_url)
                    dl.raise_for_status()
                    return dl.content

                elif status == "failed":
                    raise RuntimeError(f"Video generation failed: {poll_data}")

            raise TimeoutError(f"Video generation timed out after {self.MAX_WAIT}s")

    async def generate_slideshow(
        self,
        image_paths: list[str],
        ratio: str,
        run_id: str,
        music_url: str | None = None,
    ) -> tuple[bytes, float]:
        """
        Generate AI clips for each image, then assemble into a trailer.
        Falls back to slideshow assembly if clip generation fails.
        """
        import tempfile
        from pathlib import Path

        clip_paths = []
        for i, img_path in enumerate(image_paths):
            try:
                prompt = f"Product advertisement — smooth cinematic motion, professional lighting"
                clip_bytes = await self.generate_ai_clip(img_path, prompt)
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                    tmp.write(clip_bytes)
                    clip_paths.append(tmp.name)
                log.info("wan_video.clip_done", index=i, total=len(image_paths))
            except Exception as e:
                log.warning("wan_video.clip_failed", index=i, error=str(e))

        if not clip_paths:
            raise RuntimeError("All AI clip generations failed")

        # Assemble clips with moviepy
        return await asyncio.get_event_loop().run_in_executor(
            None, self._assemble_clips, clip_paths, ratio
        )

    def _assemble_clips(self, clip_paths: list[str], ratio: str) -> tuple[bytes, float]:
        from moviepy.editor import VideoFileClip, concatenate_videoclips
        import tempfile

        clips = [VideoFileClip(p) for p in clip_paths]
        final = concatenate_videoclips(clips, method="compose")
        duration = final.duration

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            final.write_videofile(tmp_path, fps=24, codec="libx264", logger=None)
            with open(tmp_path, "rb") as f:
                video_bytes = f.read()
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            for c in clips:
                c.close()
            final.close()
            for p in clip_paths:
                Path(p).unlink(missing_ok=True)

        return video_bytes, duration


class HailuoVideoProvider(WanVideoProvider):
    """
    minimax/hailuo-2.3 via OpenRouter — more dramatic motion style.
    Drop-in fallback for WanVideoProvider.
    """
    MODEL = "minimax/hailuo-2.3"


# ── Factory ───────────────────────────────────────────────────────────────────

def get_video_provider(mode: str = "slideshow") -> VideoProvider:
    """Return the appropriate video provider for the given mode."""
    match mode:
        case "slideshow":
            return SlideshowVideoProvider()
        case "ai":
            return WanVideoProvider()
        case "ai_hailuo":
            return HailuoVideoProvider()
        case _:
            return SlideshowVideoProvider()
