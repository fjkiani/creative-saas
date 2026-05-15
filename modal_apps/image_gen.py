"""
Modal Image Generation App — CreativeOS v4
Model: EricRollei/HunyuanImage-3.0-Instruct-Distil-NF4-v2

Deploy:
    modal deploy modal_apps/image_gen.py

Architecture:
  - GPU: A10G (24GB VRAM) — NF4 quant fits in ~20GB
  - Model cached in Modal Volume after first download
  - Scale-to-zero after 5 min idle
  - Cold start: ~3-4 min (first time), ~30s (warm)
"""
import io
import os
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
        "fastapi[standard]",
    )
    .env({"HF_HOME": "/model-cache", "TRANSFORMERS_CACHE": "/model-cache"})
)

HF_MODEL_ID = "EricRollei/HunyuanImage-3.0-Instruct-Distil-NF4-v2"
MODEL_CACHE_DIR = "/model-cache"


# ── Model class (loaded once per container) ───────────────────────────────────

@app.cls(
    gpu="A10G",
    image=image,
    volumes={MODEL_CACHE_DIR: model_volume},
    timeout=300,
    scaledown_window=300,
    secrets=[modal.Secret.from_name("creativeos-secrets")],
)
class HunyuanImageModel:
    """HunyuanImage-3.0 NF4 — loaded once per container."""

    @modal.enter()
    def load_model(self):
        import torch
        from diffusers import HunyuanDiTPipeline
        from transformers import BitsAndBytesConfig

        print(f"Loading {HF_MODEL_ID}...")
        hf_token = os.environ.get("HF_TOKEN", "") or None

        nf4_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )

        try:
            self.pipe = HunyuanDiTPipeline.from_pretrained(
                HF_MODEL_ID,
                torch_dtype=torch.bfloat16,
                quantization_config=nf4_config,
                token=hf_token,
                cache_dir=MODEL_CACHE_DIR,
            )
        except Exception as e:
            print(f"HunyuanDiTPipeline failed ({e}), trying AutoPipeline...")
            from diffusers import AutoPipelineForText2Image
            self.pipe = AutoPipelineForText2Image.from_pretrained(
                HF_MODEL_ID,
                torch_dtype=torch.bfloat16,
                token=hf_token,
                cache_dir=MODEL_CACHE_DIR,
            )

        self.pipe = self.pipe.to("cuda")
        self.pipe.enable_attention_slicing()
        try:
            self.pipe.enable_xformers_memory_efficient_attention()
            print("xformers enabled")
        except Exception:
            pass
        print("Model loaded.")

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
        buf = io.BytesIO()
        result.images[0].save(buf, format="PNG", optimize=True)
        buf.seek(0)
        png_bytes = buf.read()
        print(f"Generated {len(png_bytes):,} bytes")
        return png_bytes


# ── FastAPI web endpoint ──────────────────────────────────────────────────────

@app.function(
    image=image,
    secrets=[modal.Secret.from_name("creativeos-secrets")],
    timeout=180,
)
@modal.fastapi_endpoint(method="POST", label="creativeos-image-gen")
async def generate_endpoint(request: dict):
    """
    POST /  — generate image
    Body: {"prompt": str, "width": int, "height": int, "steps": int}
    Returns: PNG bytes (Content-Type: image/png)
    """
    from fastapi.responses import Response, JSONResponse

    prompt = request.get("prompt", "")
    if not prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)

    width  = min(int(request.get("width",  1024)), 2048)
    height = min(int(request.get("height", 1024)), 2048)
    steps  = min(int(request.get("steps",  20)),   50)
    guidance_scale = float(request.get("guidance_scale", 5.0))

    try:
        model = HunyuanImageModel()
        png_bytes = model.generate.remote(
            prompt=prompt, width=width, height=height,
            steps=steps, guidance_scale=guidance_scale,
        )
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "X-Model": "HunyuanImage-3.0-Instruct-Distil-NF4-v2",
                "Content-Length": str(len(png_bytes)),
            },
        )
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)


@app.function(image=image, timeout=30)
@modal.fastapi_endpoint(method="GET", label="creativeos-image-health")
async def health_endpoint():
    return {"status": "ok", "model": HF_MODEL_ID, "service": "creativeos-image-gen"}


@app.local_entrypoint()
def test():
    model = HunyuanImageModel()
    png_bytes = model.generate.remote(
        prompt="a red sneaker on white background, product photography",
        width=512, height=512, steps=10,
    )
    with open("/tmp/test_hunyuan.png", "wb") as f:
        f.write(png_bytes)
    print(f"Saved: /tmp/test_hunyuan.png ({len(png_bytes):,} bytes)")
