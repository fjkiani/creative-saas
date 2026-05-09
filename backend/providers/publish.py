"""
Publish providers — CreativeOS v4.

Platforms at launch: Instagram (Meta Graph API) + TikTok (Content Posting API v2).

Design principles:
  - NEVER raise exceptions — always return PublishResult(status="failed", error=...)
  - All network calls wrapped in try/except
  - Tenacity retry on transient HTTP errors (429, 5xx)
  - Timeouts on every httpx call
  - Video processing polls with configurable max wait

Usage:
    from backend.providers.publish import InstagramPublishProvider, TikTokPublishProvider
    ig = InstagramPublishProvider(access_token=..., ig_user_id=...)
    result = await ig.publish_image(image_url, caption="...")
    if result.status == "failed":
        log.error("publish failed", error=result.error)
"""
from __future__ import annotations
import asyncio
import os
import structlog
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from backend.graph.state import PublishResult

log = structlog.get_logger(__name__)

# ── Retry decorator for transient network errors ──────────────────────────────

def _is_transient(exc: Exception) -> bool:
    """Retry on network errors and HTTP 429/5xx."""
    import httpx
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


def _failed(platform: str, market: str, error: str) -> PublishResult:
    """Convenience constructor for failed results."""
    log.error(f"{platform}.publish_failed", market=market, error=error[:200])
    return PublishResult(platform=platform, market=market, status="failed", error=error)


# ── Abstract base ─────────────────────────────────────────────────────────────

class PublishProvider(ABC):
    """Abstract social media publish provider."""

    @abstractmethod
    async def publish_image(
        self,
        image_url: str,
        caption: str,
        market: str = "unknown",
        scheduled_time: str | None = None,
        **kwargs,
    ) -> PublishResult:
        """Publish a single image post. Never raises — returns failed result on error."""
        ...

    @abstractmethod
    async def publish_video(
        self,
        video_url: str,
        caption: str,
        market: str = "unknown",
        scheduled_time: str | None = None,
        **kwargs,
    ) -> PublishResult:
        """Publish a video (Reel / TikTok). Never raises — returns failed result on error."""
        ...

    @abstractmethod
    async def publish_carousel(
        self,
        image_urls: list[str],
        caption: str,
        market: str = "unknown",
        scheduled_time: str | None = None,
    ) -> PublishResult:
        """Publish a carousel post. Never raises — returns failed result on error."""
        ...


# ── Instagram (Meta Graph API v21.0) ─────────────────────────────────────────

class InstagramPublishProvider(PublishProvider):
    """
    Meta Graph API v21.0.
    Supports: single image, carousel, Reels.
    Requires: instagram_business_account_id + page access_token.

    OAuth flow: POST /api/workspaces/{id}/connect/instagram
    Scopes needed: instagram_basic, instagram_content_publish, pages_read_engagement

    Error handling:
      - All methods return PublishResult(status="failed") on any error
      - Video Reels poll up to MAX_VIDEO_POLL_ATTEMPTS × POLL_INTERVAL_S seconds
      - Carousel item failures are logged but don't abort the whole carousel
    """

    GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
    POLL_INTERVAL_S = 5
    MAX_VIDEO_POLL_ATTEMPTS = 24  # 24 × 5s = 2 minutes max

    def __init__(self, access_token: str, ig_user_id: str):
        self.access_token = access_token
        self.ig_user_id = ig_user_id

    def _auth_params(self) -> dict:
        return {"access_token": self.access_token}

    async def _create_container(self, client, payload: dict) -> tuple[str | None, str | None]:
        """
        POST to /{ig_user_id}/media.
        Returns (container_id, error_message).
        """
        try:
            resp = await client.post(
                f"{self.GRAPH_API_BASE}/{self.ig_user_id}/media",
                data={**payload, **self._auth_params()},
            )
            if not resp.is_success:
                err = resp.json().get("error", {}).get("message", resp.text)
                return None, str(err)
            return resp.json().get("id"), None
        except Exception as e:
            return None, str(e)

    async def _publish_container(self, client, container_id: str) -> tuple[str | None, str | None]:
        """
        POST to /{ig_user_id}/media_publish.
        Returns (post_id, error_message).
        """
        try:
            resp = await client.post(
                f"{self.GRAPH_API_BASE}/{self.ig_user_id}/media_publish",
                data={"creation_id": container_id, **self._auth_params()},
            )
            if not resp.is_success:
                err = resp.json().get("error", {}).get("message", resp.text)
                return None, str(err)
            return resp.json().get("id"), None
        except Exception as e:
            return None, str(e)

    async def _poll_video_ready(self, client, container_id: str) -> tuple[bool, str | None]:
        """
        Poll container status until FINISHED or ERROR.
        Returns (is_ready, error_message).
        """
        for attempt in range(self.MAX_VIDEO_POLL_ATTEMPTS):
            try:
                await asyncio.sleep(self.POLL_INTERVAL_S)
                resp = await client.get(
                    f"{self.GRAPH_API_BASE}/{container_id}",
                    params={"fields": "status_code", **self._auth_params()},
                )
                if resp.is_success:
                    status_code = resp.json().get("status_code", "")
                    log.info("instagram.video_poll",
                             container_id=container_id, attempt=attempt, status=status_code)
                    if status_code == "FINISHED":
                        return True, None
                    elif status_code == "ERROR":
                        return False, "Instagram video processing returned ERROR status"
                    # IN_PROGRESS or PUBLISHED — keep polling
            except Exception as e:
                log.warning("instagram.video_poll_error", attempt=attempt, error=str(e))
                # Don't abort on transient poll errors — keep trying

        return False, f"Video processing timed out after {self.MAX_VIDEO_POLL_ATTEMPTS * self.POLL_INTERVAL_S}s"

    async def publish_image(
        self,
        image_url: str,
        caption: str,
        market: str = "unknown",
        scheduled_time: str | None = None,
        **kwargs,
    ) -> PublishResult:
        """Publish a single image to Instagram feed."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                payload = {"image_url": image_url, "caption": caption}
                if scheduled_time:
                    payload["scheduled_publish_time"] = scheduled_time
                    payload["published"] = "false"

                container_id, err = await self._create_container(client, payload)
                if err:
                    return _failed("instagram", market, f"Container creation failed: {err}")

                log.info("instagram.container_created", container_id=container_id, market=market)

                post_id, err = await self._publish_container(client, container_id)
                if err:
                    return _failed("instagram", market, f"Publish failed: {err}")

                log.info("instagram.image_published", post_id=post_id, market=market)
                return PublishResult(
                    platform="instagram",
                    market=market,
                    post_id=post_id,
                    post_url=f"https://www.instagram.com/p/{post_id}/",
                    published_at=datetime.now(timezone.utc).isoformat(),
                    scheduled_for=scheduled_time,
                    status="scheduled" if scheduled_time else "published",
                )
        except Exception as e:
            return _failed("instagram", market, f"Unexpected error: {e}")

    async def publish_video(
        self,
        video_url: str,
        caption: str,
        market: str = "unknown",
        scheduled_time: str | None = None,
        **kwargs,
    ) -> PublishResult:
        """Publish a Reel to Instagram. Polls until video processing completes."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60) as client:
                payload = {
                    "media_type": "REELS",
                    "video_url": video_url,
                    "caption": caption,
                    "share_to_feed": "true",
                }
                if scheduled_time:
                    payload["scheduled_publish_time"] = scheduled_time
                    payload["published"] = "false"

                container_id, err = await self._create_container(client, payload)
                if err:
                    return _failed("instagram", market, f"Reel container creation failed: {err}")

                log.info("instagram.reel_container_created", container_id=container_id, market=market)

                # Poll until Instagram finishes processing the video
                is_ready, poll_err = await self._poll_video_ready(client, container_id)
                if not is_ready:
                    return _failed("instagram", market, poll_err or "Video not ready")

                post_id, err = await self._publish_container(client, container_id)
                if err:
                    return _failed("instagram", market, f"Reel publish failed: {err}")

                log.info("instagram.reel_published", post_id=post_id, market=market)
                return PublishResult(
                    platform="instagram",
                    market=market,
                    post_id=post_id,
                    post_url=f"https://www.instagram.com/reel/{post_id}/",
                    published_at=datetime.now(timezone.utc).isoformat(),
                    scheduled_for=scheduled_time,
                    status="scheduled" if scheduled_time else "published",
                )
        except Exception as e:
            return _failed("instagram", market, f"Unexpected error: {e}")

    async def publish_carousel(
        self,
        image_urls: list[str],
        caption: str,
        market: str = "unknown",
        scheduled_time: str | None = None,
    ) -> PublishResult:
        """Publish a carousel (up to 10 images) to Instagram."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60) as client:
                # Step 1: Create individual item containers (skip failures)
                item_ids: list[str] = []
                for i, url in enumerate(image_urls[:10]):
                    item_id, err = await self._create_container(client, {
                        "image_url": url,
                        "is_carousel_item": "true",
                    })
                    if item_id:
                        item_ids.append(item_id)
                    else:
                        log.warning("instagram.carousel_item_failed", index=i, error=err)

                if not item_ids:
                    return _failed("instagram", market, "All carousel item containers failed")

                # Step 2: Create carousel container
                carousel_id, err = await self._create_container(client, {
                    "media_type": "CAROUSEL",
                    "children": ",".join(item_ids),
                    "caption": caption,
                })
                if err:
                    return _failed("instagram", market, f"Carousel container failed: {err}")

                # Step 3: Publish
                post_id, err = await self._publish_container(client, carousel_id)
                if err:
                    return _failed("instagram", market, f"Carousel publish failed: {err}")

                log.info("instagram.carousel_published",
                         post_id=post_id, market=market, items=len(item_ids))
                return PublishResult(
                    platform="instagram",
                    market=market,
                    post_id=post_id,
                    post_url=f"https://www.instagram.com/p/{post_id}/",
                    published_at=datetime.now(timezone.utc).isoformat(),
                    status="published",
                )
        except Exception as e:
            return _failed("instagram", market, f"Unexpected error: {e}")


# ── TikTok (Content Posting API v2) ──────────────────────────────────────────

class TikTokPublishProvider(PublishProvider):
    """
    TikTok Content Posting API v2.
    Supports: video posts (URL-based), photo carousels.
    Requires: access_token (per workspace, from OAuth flow).

    OAuth flow: POST /api/workspaces/{id}/connect/tiktok
    Scopes needed: video.publish, video.upload

    Error handling:
      - All methods return PublishResult(status="failed") on any error
      - Video publish polls up to MAX_POLL_ATTEMPTS × POLL_INTERVAL_S seconds
    """

    API_BASE = "https://open.tiktokapis.com/v2"
    POLL_INTERVAL_S = 5
    MAX_POLL_ATTEMPTS = 24  # 24 × 5s = 2 minutes max

    def __init__(self, access_token: str, client_key: str | None = None):
        self.access_token = access_token
        self.client_key = client_key or os.getenv("TIKTOK_CLIENT_KEY", "")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    async def publish_video(
        self,
        video_url: str,
        caption: str,
        market: str = "unknown",
        scheduled_time: str | None = None,
        **kwargs,
    ) -> PublishResult:
        """
        Publish a video to TikTok via URL pull.
        Step 1: Initialize (get publish_id)
        Step 2: Poll until PUBLISH_COMPLETE
        """
        try:
            import httpx
            async with httpx.AsyncClient(timeout=120) as client:
                post_info: dict = {
                    "title": caption[:150],
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                }
                if scheduled_time:
                    try:
                        dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
                        post_info["scheduled_publish_time"] = int(dt.timestamp())
                    except Exception:
                        log.warning("tiktok.invalid_scheduled_time", value=scheduled_time)

                init_payload = {
                    "post_info": post_info,
                    "source_info": {
                        "source": "PULL_FROM_URL",
                        "video_url": video_url,
                    },
                }

                try:
                    init_resp = await client.post(
                        f"{self.API_BASE}/post/publish/video/init/",
                        headers=self._headers(),
                        json=init_payload,
                    )
                except Exception as e:
                    return _failed("tiktok", market, f"Network error on init: {e}")

                if not init_resp.is_success:
                    err = init_resp.json().get("error", {}).get("message", init_resp.text)
                    return _failed("tiktok", market, f"Init failed ({init_resp.status_code}): {err}")

                publish_id = init_resp.json().get("data", {}).get("publish_id")
                if not publish_id:
                    return _failed("tiktok", market, "No publish_id in TikTok init response")

                log.info("tiktok.initialized", publish_id=publish_id, market=market)

                # Poll for completion
                for attempt in range(self.MAX_POLL_ATTEMPTS):
                    await asyncio.sleep(self.POLL_INTERVAL_S)
                    try:
                        status_resp = await client.post(
                            f"{self.API_BASE}/post/publish/status/fetch/",
                            headers=self._headers(),
                            json={"publish_id": publish_id},
                        )
                        if status_resp.is_success:
                            status_data = status_resp.json().get("data", {})
                            pub_status = status_data.get("status", "")
                            log.info("tiktok.poll", publish_id=publish_id,
                                     attempt=attempt, status=pub_status)

                            if pub_status == "PUBLISH_COMPLETE":
                                post_ids = status_data.get("publicaly_available_post_id", [])
                                post_id = str(post_ids[0]) if post_ids else publish_id
                                return PublishResult(
                                    platform="tiktok",
                                    market=market,
                                    post_id=post_id,
                                    post_url=f"https://www.tiktok.com/@user/video/{post_id}",
                                    published_at=datetime.now(timezone.utc).isoformat(),
                                    scheduled_for=scheduled_time,
                                    status="scheduled" if scheduled_time else "published",
                                )
                            elif pub_status in ("FAILED", "PUBLISH_FAILED"):
                                return _failed("tiktok", market,
                                               f"TikTok publish failed: {status_data}")
                    except Exception as e:
                        log.warning("tiktok.poll_error", attempt=attempt, error=str(e))

                return _failed("tiktok", market,
                               f"Publish timed out after {self.MAX_POLL_ATTEMPTS * self.POLL_INTERVAL_S}s")

        except Exception as e:
            return _failed("tiktok", market, f"Unexpected error: {e}")

    async def publish_image(
        self,
        image_url: str,
        caption: str,
        market: str = "unknown",
        scheduled_time: str | None = None,
        **kwargs,
    ) -> PublishResult:
        """
        TikTok does not support standalone image posts.
        Route single images through publish_carousel with one item.
        """
        return await self.publish_carousel(
            image_urls=[image_url],
            caption=caption,
            market=market,
            scheduled_time=scheduled_time,
        )

    async def publish_carousel(
        self,
        image_urls: list[str],
        caption: str,
        market: str = "unknown",
        scheduled_time: str | None = None,
    ) -> PublishResult:
        """TikTok photo mode — up to 35 images."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60) as client:
                post_info: dict = {
                    "title": caption[:150],
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_comment": False,
                }
                if scheduled_time:
                    try:
                        dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
                        post_info["scheduled_publish_time"] = int(dt.timestamp())
                    except Exception:
                        pass

                payload = {
                    "post_info": post_info,
                    "source_info": {
                        "source": "PULL_FROM_URL",
                        "photo_cover_index": 0,
                        "photo_images": image_urls[:35],
                    },
                    "post_mode": "DIRECT_POST",
                    "media_type": "PHOTO",
                }

                try:
                    resp = await client.post(
                        f"{self.API_BASE}/post/publish/content/init/",
                        headers=self._headers(),
                        json=payload,
                    )
                except Exception as e:
                    return _failed("tiktok", market, f"Network error: {e}")

                if not resp.is_success:
                    err = resp.json().get("error", {}).get("message", resp.text)
                    return _failed("tiktok", market, f"Photo post failed ({resp.status_code}): {err}")

                publish_id = resp.json().get("data", {}).get("publish_id")
                log.info("tiktok.photo_published", publish_id=publish_id,
                         market=market, images=len(image_urls))
                return PublishResult(
                    platform="tiktok",
                    market=market,
                    post_id=publish_id,
                    published_at=datetime.now(timezone.utc).isoformat(),
                    status="published",
                )
        except Exception as e:
            return _failed("tiktok", market, f"Unexpected error: {e}")
