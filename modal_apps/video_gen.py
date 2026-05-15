"""
Modal Video Generation App — CreativeOS v4
Model: Wan-AI/Wan2.2-T2V-A14B (bfloat16, ~28GB VRAM → A10G)

Deploy:
    modal deploy modal_apps/video_gen.py

Architecture:
  - GPU: A10G (24GB VRAM)
  - Model cached in Modal Volume after first download
  - Scale-to-zero after 5 min idle
  - Cold start: ~3-5 min (first time), ~30s (warm)
"""
import io
import os
import tempfile
import modal

# ── Modal app definition ──────────────────────────────────────────────────────

app = modal.App("creativeos-video-gen")

model_volume = modal.Volume.from_name("creativeos-model-cache", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libgl1-mesa-glx", "libglib2.0-0")
    .pip_install(
        "torch==2.4.0",
        "torchvision",
        "torchaudio",
        "diffusers>=0.32.0",
        "transformers>=4.44.0",
        "accelerate>=0.33.0",
        "huggingface_hub>=0.24.0",
        "Pillow>=10.0.0",
        "sentencepiece",
        "protobuf",
        "safetensors",
        "imageio[ffmpeg]",
        "imageio-ffmpeg",
        "opencv-python-headless",
        "fastapi[standard]",
        "numpy",
    )
    .env({"HF_HOME": "/model-cache", "TRANSFORMERS_CACHE": "/model-cache"})
)

HF_MODEL_ID = "Wan-AI/Wan2.2-T2V-A14B"
MODEL_CACHE_DIR = "/model-cache"


# ── Model class ───────────────────────────────────────────────────────────────

@app.cls(
    gpu="A10G",
    image=image,
    volumes={MODEL_CACHE_DIR: model_volume},
    timeout=600,
    scaledown_window=300,
    secrets=[modal.Secret.from_name("creativeos-secrets")],
)
class WanVideoModel:
    """Wan2.2 T2V A14B — loaded once per container."""

    @modal.enter()
    def load_model(self):
        import torch
        from diffusers import WanPipeline

        print(f"Loading {HF_MODEL_ID}...")
        hf_token = os.environ.get("HF_TOKEN", "") or None

        try:
            self.pipe = WanPipeline.from_pretrained(
                HF_MODEL_ID,
                torch_dtype=torch.bfloat16,
                cache_dir=MODEL_CACHE_DIR,
                token=hf_token,
            )
            self.pipe = self.pipe.to("cuda")
            self.pipe.enable_attention_slicing()
            try:
                self.pipe.enable_xformers_memory_efficient_attention()
                print("xformers enabled")
            except Exception:
                pass
            print("Wan2.2 loaded.")
        except Exception as e:
            print(f"WanPipeline failed ({e}), trying AutoPipeline...")
            from diffusers import AutoPipelineForText2Video
            self.pipe = AutoPipelineForText2Video.from_pretrained(
                HF_MODEL_ID,
                torch_dtype=torch.bfloat16,
                cache_dir=MODEL_CACHE_DIR,
                token=hf_token,
            )
            self.pipe = self.pipe.to("cuda")
            print("AutoPipeline loaded.")

    @modal.method()
    def generate(
        self,
        prompt: str,
        duration_s: int = 4,
        width: int = 832,
        height: int = 480,
        fps: int = 16,
        guidance_scale: float = 5.0,
        num_inference_steps: int = 30,
        image_b64: str | None = None,
    ) -> bytes:
        import torch, base64, numpy as np
        from PIL import Image

        num_frames = duration_s * fps
        print(f"Generating: {prompt[:80]}... ({width}x{height}, {num_frames}f)")

        kwargs = dict(
            prompt=prompt, width=width, height=height,
            num_frames=num_frames, guidance_scale=guidance_scale,
            num_inference_steps=num_inference_steps,
        )
        if image_b64:
            try:
                img_bytes = base64.b64decode(image_b64)
                ref = Image.open(io.BytesIO(img_bytes)).convert("RGB").resize((width, height))
                kwargs["image"] = ref
                print("I2V mode")
            except Exception as e:
                print(f"Ref image failed: {e}, using T2V")

        with torch.inference_mode():
            result = self.pipe(**kwargs)

        mp4_bytes = self._frames_to_mp4(result.frames[0], fps=fps)
        print(f"Generated {len(mp4_bytes):,} bytes")
        return mp4_bytes

    def _frames_to_mp4(self, frames, fps: int = 16) -> bytes:
        import imageio, numpy as np
        from PIL import Image

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            writer = imageio.get_writer(tmp_path, fps=fps, codec="libx264",
                                        quality=8, pixelformat="yuv420p")
            for frame in frames:
                if isinstance(frame, Image.Image):
                    writer.append_data(np.array(frame.convert("RGB")))
                elif isinstance(frame, np.ndarray):
                    if frame.dtype != np.uint8:
                        frame = (frame * 255).clip(0, 255).astype(np.uint8)
                    writer.append_data(frame)
            writer.close()
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            import pathlib
            pathlib.Path(tmp_path).unlink(missing_ok=True)


# ── FastAPI web endpoint ──────────────────────────────────────────────────────

@app.function(
    image=image,
    secrets=[modal.Secret.from_name("creativeos-secrets")],
    timeout=360,
)
@modal.fastapi_endpoint(method="POST", label="creativeos-video-gen")
async def generate_endpoint(request: dict):
    """
    POST /  — generate video
    Body: {"prompt": str, "duration_s": int, "width": int, "height": int, "image_b64": str}
    Returns: MP4 bytes
    """
    from fastapi.responses import Response, JSONResponse

    prompt = request.get("prompt", "")
    if not prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)

    duration_s = min(int(request.get("duration_s", 4)), 8)
    width      = min(int(request.get("width",  832)), 1280)
    height     = min(int(request.get("height", 480)), 720)
    fps        = min(int(request.get("fps",    16)),  24)
    image_b64  = request.get("image_b64")

    try:
        model = WanVideoModel()
        mp4_bytes = model.generate.remote(
            prompt=prompt, duration_s=duration_s,
            width=width, height=height, fps=fps, image_b64=image_b64,
        )
        return Response(
            content=mp4_bytes,
            media_type="video/mp4",
            headers={"X-Model": "Wan2.2-T2V-A14B", "Content-Length": str(len(mp4_bytes))},
        )
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)


@app.function(image=image, timeout=30)
@modal.fastapi_endpoint(method="GET", label="creativeos-video-health")
async def health_endpoint():
    return {"status": "ok", "model": HF_MODEL_ID, "service": "creativeos-video-gen"}


@app.local_entrypoint()
def test():
    model = WanVideoModel()
    mp4_bytes = model.generate.remote(
        prompt="a red sneaker rotating on a white pedestal, product showcase",
        duration_s=3, width=512, height=512, fps=16, num_inference_steps=15,
    )
    with open("/tmp/test_wan.mp4", "wb") as f:
        f.write(mp4_bytes)
    print(f"Saved: /tmp/test_wan.mp4 ({len(mp4_bytes):,} bytes)")
