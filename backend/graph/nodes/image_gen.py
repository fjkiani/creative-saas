"""
Node 4: image_gen
For each product × market combination:
  1. Check if an existing_asset is provided in the brief → reuse or use as reference
  2. Check the asset cache (Supabase Storage) by prompt_hash → reuse if found
  3. Otherwise generate a new image via the configured image provider

Parallelism: all prompts are dispatched concurrently via asyncio.gather, capped
by IMAGE_GEN_CONCURRENCY (default 3) to avoid rate-limit hammering.

Hero asset support:
  - existing_asset = local path → load bytes, pass as reference to generate_with_reference()
  - existing_asset = http(s) URL → fetch bytes, pass as reference
  - existing_asset = None → generate from scratch
"""
import os
import asyncio
import hashlib
import structlog
from pathlib import Path
from backend.graph.state import PipelineState, CampaignBrief, ImagePrompt, GeneratedAsset
from backend.providers.base import get_image_provider
from backend.storage.base import get_storage_backend
from backend.graph.nodes._broadcast import broadcast

log = structlog.get_logger(__name__)

# Native dimensions for each aspect ratio
ASPECT_DIMENSIONS = {
    "1:1":  (1024, 1024),
    "9:16": (1024, 1792),
    "16:9": (1792, 1024),
}

# Concurrency cap — configurable via env var
IMAGE_GEN_CONCURRENCY = int(os.getenv("IMAGE_GEN_CONCURRENCY", "3"))


def compute_prompt_hash(product_id: str, market: str, prompt: str) -> str:
    """Content-addressable cache key: SHA256 of (product_id + market + prompt)."""
    content = f"{product_id}|{market}|{prompt}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


async def _fetch_asset_bytes(path_or_url: str) -> bytes | None:
    """
    Load asset bytes from a local path or HTTP(S) URL.
    Returns None if the asset cannot be loaded (non-blocking).
    """
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(path_or_url)
                resp.raise_for_status()
                return resp.content
        except Exception as e:
            log.warning("image_gen.fetch_url_failed", url=path_or_url, error=str(e))
            return None
    else:
        local = Path(path_or_url)
        if local.exists():
            return local.read_bytes()
        log.warning("image_gen.local_asset_not_found", path=path_or_url)
        return None


async def _generate_one(
    img_prompt: ImagePrompt,
    existing_asset_path: str | None,
    image_provider,
    storage,
    run_id: str,
    sem: asyncio.Semaphore,
    index: int,
    total: int,
) -> dict | Exception:
    """
    Process a single product × market image prompt.
    Wrapped in a semaphore to cap concurrent API calls.
    Returns a GeneratedAsset dict on success, or the Exception on failure.
    """
    async with sem:
        product_id = img_prompt.product_id
        market = img_prompt.market
        prompt_hash = compute_prompt_hash(product_id, market, img_prompt.prompt)
        width, height = ASPECT_DIMENSIONS["1:1"]
        storage_path = f"{run_id}/{product_id}/{market}/base.png"
        cache_path = f"cache/{prompt_hash}/base.png"

        await broadcast(run_id, "image_gen", "STARTED", {
            "message": f"Processing {product_id} × {market} ({index + 1}/{total})...",
            "product_id": product_id,
            "market": market,
        })

        # ── Strategy 1: Hero asset as reference (editing mode) ────────────────
        if existing_asset_path:
            ref_bytes = await _fetch_asset_bytes(existing_asset_path)
            if ref_bytes:
                log.info("image_gen.hero_reference", product_id=product_id,
                         source=existing_asset_path[:60])
                try:
                    # Use generate_with_reference if provider supports it,
                    # otherwise fall back to generate (graceful degradation)
                    if hasattr(image_provider, "generate_with_reference"):
                        img_bytes = await image_provider.generate_with_reference(
                            img_prompt.prompt, ref_bytes, width, height
                        )
                    else:
                        img_bytes = await image_provider.generate(
                            img_prompt.prompt, width, height
                        )
                    url = await storage.save(storage_path, img_bytes)
                    await storage.save(cache_path, img_bytes)
                    return GeneratedAsset(
                        product_id=product_id, market=market,
                        storage_url=url, storage_path=storage_path,
                        prompt_hash=prompt_hash, reused=False,
                        provider=f"{image_provider.name()}+reference",
                    ).model_dump()
                except Exception as e:
                    log.warning("image_gen.reference_failed_fallback",
                                product_id=product_id, error=str(e))
                    # Fall through to cache / generate

        # ── Strategy 2: Check asset cache by prompt_hash ─────────────────────
        try:
            if await storage.exists(cache_path):
                log.info("image_gen.cache.hit", product_id=product_id, hash=prompt_hash)
                img_bytes = await storage.load(cache_path)
                url = await storage.save(storage_path, img_bytes)
                return GeneratedAsset(
                    product_id=product_id, market=market,
                    storage_url=url, storage_path=storage_path,
                    prompt_hash=prompt_hash, reused=True,
                    provider="cache",
                ).model_dump()
        except Exception:
            pass  # Cache miss or error — proceed to generate

        # ── Strategy 3: Generate new image ───────────────────────────────────
        log.info("image_gen.generate", product_id=product_id, market=market,
                 provider=image_provider.name())
        img_bytes = await image_provider.generate(img_prompt.prompt, width, height)
        url = await storage.save(storage_path, img_bytes)
        await storage.save(cache_path, img_bytes)

        return GeneratedAsset(
            product_id=product_id, market=market,
            storage_url=url, storage_path=storage_path,
            prompt_hash=prompt_hash, reused=False,
            provider=image_provider.name(),
        ).model_dump()


async def image_gen_node(state: PipelineState) -> PipelineState:
    run_id = state["run_id"]
    await broadcast(run_id, "image_gen", "STARTED", {
        "message": f"Generating hero images (concurrency={IMAGE_GEN_CONCURRENCY})..."
    })
    log.info("node.image_gen.start", run_id=run_id, concurrency=IMAGE_GEN_CONCURRENCY)

    brief = CampaignBrief.model_validate(state["brief"])
    prompts = [ImagePrompt.model_validate(p) for p in state.get("image_prompts", [])]
    image_provider = get_image_provider()
    storage = get_storage_backend()

    # Build lookup: product_id → existing_asset path/URL
    existing_assets: dict[str, str | None] = {
        p.id: p.existing_asset for p in brief.products
    }

    sem = asyncio.Semaphore(IMAGE_GEN_CONCURRENCY)
    total = len(prompts)

    # Dispatch all prompts concurrently, capped by semaphore
    tasks = [
        _generate_one(
            img_prompt=p,
            existing_asset_path=existing_assets.get(p.product_id),
            image_provider=image_provider,
            storage=storage,
            run_id=run_id,
            sem=sem,
            index=i,
            total=total,
        )
        for i, p in enumerate(prompts)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    generated_assets: list[dict] = []
    errors: list[str] = list(state.get("errors", []))

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            p = prompts[i]
            err_msg = f"image_gen {p.product_id}×{p.market}: {result}"
            log.error("image_gen.task_failed", error=err_msg)
            errors.append(err_msg)
        else:
            generated_assets.append(result)

    reused = sum(1 for a in generated_assets if a.get("reused"))
    generated = len(generated_assets) - reused

    log.info("node.image_gen.complete", run_id=run_id,
             total=len(generated_assets), reused=reused, generated=generated,
             failed=len(results) - len(generated_assets))

    await broadcast(run_id, "image_gen", "COMPLETED", {
        "asset_count": len(generated_assets),
        "reused": reused,
        "generated": generated,
        "failed": len(results) - len(generated_assets),
        "provider": image_provider.name(),
        "concurrency": IMAGE_GEN_CONCURRENCY,
    })

    return {
        **state,
        "generated_assets": generated_assets,
        "errors": errors,
        "current_node": "image_gen",
        "provider_image": image_provider.name(),
    }
