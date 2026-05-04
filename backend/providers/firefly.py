"""
Adobe Firefly Image5 provider.

Activate via: IMAGE_PROVIDER=firefly

Implements the full Firefly Services auth flow (client credentials → bearer token)
and the Generate Image API with Image5 model.

Custom Models support is stubbed — pass custom_model_id in brand_config to activate.

References:
- Image5 API: https://developer.adobe.com/firefly-services/docs/firefly-api/guides/how-tos/cm-generate-image/
- Custom Models: https://developer.adobe.com/firefly-services/docs/firefly-api/guides/concepts/custom-models
"""
import base64
import structlog
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from backend.providers.base import ImageProvider
from backend.config import settings

log = structlog.get_logger(__name__)

FIREFLY_AUTH_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
FIREFLY_API_BASE = "https://firefly-api.adobe.io"


class FireflyImageProvider(ImageProvider):
    """
    Adobe Firefly Image5 — commercially safe, brand-trained generation.

    Enterprise upgrade path: set custom_model_id in brand_config.yaml to use
    Firefly Custom Models trained on your brand assets.
    """

    def __init__(self):
        self._token: str | None = None
        self._client_id = settings.firefly_client_id
        self._client_secret = settings.firefly_client_secret

    def name(self) -> str:
        return "adobe/firefly-image5"

    async def _get_token(self) -> str:
        """Fetch a short-lived bearer token via client credentials flow."""
        if self._token:
            return self._token

        log.info("firefly.auth.requesting_token")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                FIREFLY_AUTH_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": "openid,AdobeID,firefly_enterprise,firefly_api",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            resp.raise_for_status()
            self._token = resp.json()["access_token"]
            log.info("firefly.auth.token_acquired")
            return self._token

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
    async def generate(
        self,
        prompt: str,
        width: int,
        height: int,
        custom_model_id: str | None = None,
    ) -> bytes:
        """
        Generate an image using Firefly Image5.
        Pass custom_model_id to use a brand-trained Custom Model.
        """
        token = await self._get_token()
        aspect = _dims_to_firefly_size(width, height)

        payload: dict = {
            "prompt": prompt,
            "size": aspect,
            "numVariations": 1,
            "contentClass": "photo",
        }

        # Custom Model support — enterprise brand training
        if custom_model_id:
            payload["customModel"] = {"id": custom_model_id}
            log.info("firefly.image.custom_model", model_id=custom_model_id)

        endpoint = f"{FIREFLY_API_BASE}/v3/images/generate"
        log.info("firefly.image.request", size=aspect, prompt=prompt[:80])

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-api-key": self._client_id,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()

        # Firefly returns a presigned URL — fetch the actual image bytes
        image_url = data["outputs"][0]["image"]["url"]
        async with httpx.AsyncClient() as client:
            img_resp = await client.get(image_url, timeout=60)
            img_resp.raise_for_status()

        log.info("firefly.image.success", bytes=len(img_resp.content))
        return img_resp.content


def _dims_to_firefly_size(width: int, height: int) -> dict:
    """Convert pixel dimensions to Firefly size object."""
    return {"width": width, "height": height}
