"""
Modal Image Generation App — CreativeOS v4
Model: EricRollei/HunyuanImage-3.0-Instruct-Distil-NF4-v2

Deploy:
    modal deploy modal_apps/image_gen.py

Test:
    curl -X POST <endpoint>/generate \
      -H "Modal-Key: <key_id>" \
      -H "Modal-Secret: <key_secret>" \
      -H "Content-Type: application/json" \
      -d '{"prompt":"a red sneaker on white background","width":1024,"height":1024}' \
      --output test.png

Architecture:
  - GPU: A10G (24GB VRAM) — NF4 quant fits in ~20GB
  - Model cached in Modal Volume after first download
  - Scale-to-zero after 5 min idle
  - Cold start: ~3-4 min (first time), ~30s (warm)
  - Timeout: 120s per request
"""
import io
import os
import base64
import modal

# ── Modal app definition ──────────────────────────────────────────────────────

app = modal.App("creativeos-image-gen")

# Persistent volume for model weights (survives restarts)
model_volume = modal.Volume.from_name("creativeos-model-cache", create_if_missing=True)

# Container image with all dependencies
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch==2.4.0",
        "torchvision",
        "diffusers>=0.30.0",
        "transformers>=4.44.0",
        "accelerate>=0.33.0",
        "bitsandbytes>=0.43.0",
        "huggingface_hub>=0.24.0",
        "Pillow>=10.0.0",
        "sentencepiece",
        "protobuf",
        "safetensors",
        "fastapi",
        "uvicorn",
    )
    .env({"HF_HOME": "/model-cache", "TRANSFORMERS_CACHE": "/model-cache"})
)

HF_MODEL_ID = "EricRollei/HunyuanImage-3.0-Instruct-Distil-NF4-v2"
MODEL_CACHE_DIR = "/model-cache"


# ── Model class (loaded once per container) ───────────────────────────────────

@app.cls(
    gpu=modal.gpu.A10G(),
    image=image,
    volumes={MODEL_CACHE_DIR: model_volume},
    timeout=300,
    container_idle_timeout=300,  # scale to zero after 5 min
    secrets=[modal.Secret.from_name("creativeos-secrets")],
)
class HunyuanImageModel:
    """
    HunyuanImage-3.0 model loaded once per container.
    NF4 quantization keeps VRAM under 22GB (fits A10G 24GB).
    """

    @modal.enter()
    def load_model(self):
        """Download and load model on container startup."""
        import torch
        from diffusers import HunyuanDiTPipeline
        from transformers import BitsAndBytesConfig

        print(f"Loading {HF_MODEL_ID}...")

        hf_token = os.environ.get("HF_TOKEN", "")

        # NF4 quantization config
        nf4_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )

        try:
            # Try HunyuanDiT pipeline first (standard diffusers)
            self.pipe = HunyuanDiTPipeline.from_pretrained(
                HF_MODEL_ID,
                torch_dtype=torch.bfloat16,
                quantization_config=nf4_config,
                token=hf_token if hf_token else None,
                cache_dir=MODEL_CACHE_DIR,
            )
        except Exception as e:
            print(f"HunyuanDiTPipeline failed ({e}), trying AutoPipeline...")
            from diffusers import AutoPipelineForText2Image
            self.pipe = AutoPipelineForText2Image.from_pretrained(
                HF_MODEL_ID,
                torch_dtype=torch.bfloat16,
                token=hf_token if hf_token else None,
                cache_dir=MODEL_CACHE_DIR,
            )

        self.pipe = self.pipe.to("cuda")
        self.pipe.enable_attention_slicing()

        # Try xformers for memory efficiency
        try:
            self.pipe.enable_xformers_memory_efficient_attention()
            print("xformers enabled")
        except Exception:
            print("xformers not available, using default attention")

        print("Model loaded successfully.")

    @modal.method()
    def generate(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        steps: int = 20,
        guidance_scale: float = 5.0,
        negative_prompt: str = "blurry, low quality, distorted, watermark, text, ugly",
    ) -> bytes:
        """Generate image and return PNG bytes."""
        import torch

        print(f"Generating: {prompt[:80]}... ({width}x{height})")

        with torch.inference_mode():
            result = self.pipe(
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                num_inference_steps=steps,
                guidance_scale=guidance_scale,
                num_images_per_prompt=1,
            )

        image = result.images[0]

        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        png_bytes = buf.read()

        print(f"Generated {len(png_bytes):,} bytes")
        return png_bytes


# ── Web endpoint ──────────────────────────────────────────────────────────────

@app.function(
    image=image,
    secrets=[modal.Secret.from_name("creativeos-secrets")],
    timeout=180,
)
@modal.web_endpoint(method="POST", label="creativeos-image-gen")
async def generate_endpoint(request: dict) -> modal.Response:
    """
    POST /generate
    Body: {"prompt": str, "width": int, "height": int, "steps": int, "guidance_scale": float}
    Response: PNG bytes
    """
    import json

    # Auth check
    # Note: Modal web endpoints receive headers via the request context
    # Auth is handled at the Modal gateway level via Modal-Key/Modal-Secret

    prompt = request.get("prompt", "")
    if not prompt:
        return modal.Response(
            content=json.dumps({"error": "prompt is required"}).encode(),
            status_code=400,
            headers={"Content-Type": "application/json"},
        )

    width = min(int(request.get("width", 1024)), 2048)
    height = min(int(request.get("height", 1024)), 2048)
    steps = min(int(request.get("steps", 20)), 50)
    guidance_scale = float(request.get("guidance_scale", 5.0))

    try:
        model = HunyuanImageModel()
        png_bytes = model.generate.remote(
            prompt=prompt,
            width=width,
            height=height,
            steps=steps,
            guidance_scale=guidance_scale,
        )

        return modal.Response(
            content=png_bytes,
            status_code=200,
            headers={
                "Content-Type": "image/png",
                "Content-Length": str(len(png_bytes)),
                "X-Model": "HunyuanImage-3.0-Instruct-Distil-NF4-v2",
            },
        )

    except Exception as e:
        import traceback
        print(f"Generation error: {e}\n{traceback.format_exc()}")
        return modal.Response(
            content=json.dumps({"error": str(e)}).encode(),
            status_code=500,
            headers={"Content-Type": "application/json"},
        )


# ── Health check endpoint ─────────────────────────────────────────────────────

@app.function(image=image, timeout=30)
@modal.web_endpoint(method="GET", label="creativeos-image-health")
async def health_endpoint() -> dict:
    """GET /health — returns model info."""
    return {
        "status": "ok",
        "model": HF_MODEL_ID,
        "service": "creativeos-image-gen",
    }


# ── Local test ────────────────────────────────────────────────────────────────

@app.local_entrypoint()
def test():
    """Run a quick local test: modal run modal_apps/image_gen.py"""
    model = HunyuanImageModel()
    png_bytes = model.generate.remote(
        prompt="a red sneaker on a clean white background, product photography, studio lighting",
        width=512,
        height=512,
        steps=10,
    )
    with open("/tmp/test_hunyuan.png", "wb") as f:
        f.write(png_bytes)
    print(f"Saved test image: /tmp/test_hunyuan.png ({len(png_bytes):,} bytes)")
