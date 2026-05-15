"""
Modal Video Generation App — CreativeOS v4
Model: QuantStack/Wan2.2-T2V-A14B-GGUF (Q4_K_M, ~8GB)

Deploy:
    modal deploy modal_apps/video_gen.py

Test:
    curl -X POST <endpoint>/generate \
      -H "Modal-Key: <key_id>" \
      -H "Modal-Secret: <key_secret>" \
      -H "Content-Type: application/json" \
      -d '{"prompt":"product showcase cinematic motion","duration_s":4,"width":832,"height":832}' \
      --output test.mp4

Architecture:
  - GPU: A10G (24GB VRAM) — GGUF Q4_K_M fits in ~8GB
  - Model cached in Modal Volume after first download
  - Scale-to-zero after 5 min idle
  - Cold start: ~2-3 min (first time), ~20s (warm)
  - Timeout: 300s per request
  - Uses diffusers Wan pipeline (official HF integration)
"""
import io
import os
import base64
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
        "fastapi",
        "uvicorn",
        "numpy",
    )
    .env({"HF_HOME": "/model-cache", "TRANSFORMERS_CACHE": "/model-cache"})
)

# Wan2.2 T2V A14B — best quality/size tradeoff
HF_MODEL_ID = "Wan-AI/Wan2.2-T2V-A14B"
# GGUF quantized version for lower VRAM
HF_GGUF_REPO = "QuantStack/Wan2.2-T2V-A14B-GGUF"
MODEL_CACHE_DIR = "/model-cache"


# ── Model class ───────────────────────────────────────────────────────────────

@app.cls(
    gpu=modal.gpu.A10G(),
    image=image,
    volumes={MODEL_CACHE_DIR: model_volume},
    timeout=600,
    container_idle_timeout=300,
    secrets=[modal.Secret.from_name("creativeos-secrets")],
)
class WanVideoModel:
    """
    Wan2.2 T2V A14B model for text-to-video generation.
    Uses diffusers WanPipeline (official integration).
    """

    @modal.enter()
    def load_model(self):
        """Download and load model on container startup."""
        import torch
        from diffusers import WanPipeline, AutoencoderKLWan
        from transformers import UMT5EncoderModel

        print(f"Loading {HF_MODEL_ID}...")

        hf_token = os.environ.get("HF_TOKEN", "")
        token_arg = {"token": hf_token} if hf_token else {}

        try:
            # Load with bfloat16 for A10G
            self.pipe = WanPipeline.from_pretrained(
                HF_MODEL_ID,
                torch_dtype=torch.bfloat16,
                cache_dir=MODEL_CACHE_DIR,
                **token_arg,
            )
            self.pipe = self.pipe.to("cuda")
            self.pipe.enable_attention_slicing()

            try:
                self.pipe.enable_xformers_memory_efficient_attention()
                print("xformers enabled")
            except Exception:
                pass

            print("Wan2.2 T2V A14B loaded successfully.")

        except Exception as e:
            print(f"WanPipeline failed ({e}), trying AutoPipeline...")
            from diffusers import AutoPipelineForText2Video
            self.pipe = AutoPipelineForText2Video.from_pretrained(
                HF_MODEL_ID,
                torch_dtype=torch.bfloat16,
                cache_dir=MODEL_CACHE_DIR,
                **token_arg,
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
        """Generate video and return MP4 bytes."""
        import torch
        import numpy as np

        num_frames = duration_s * fps
        print(f"Generating video: {prompt[:80]}... ({width}x{height}, {num_frames} frames)")

        kwargs: dict = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
        }

        # Image-to-video if reference image provided
        if image_b64:
            try:
                from PIL import Image
                img_bytes = base64.b64decode(image_b64)
                ref_image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                ref_image = ref_image.resize((width, height))
                kwargs["image"] = ref_image
                print("Using reference image for I2V")
            except Exception as e:
                print(f"Could not load reference image: {e}, using T2V")

        with torch.inference_mode():
            result = self.pipe(**kwargs)

        # Export frames to MP4
        frames = result.frames[0]  # list of PIL Images or numpy array
        mp4_bytes = self._frames_to_mp4(frames, fps=fps)

        print(f"Generated video: {len(mp4_bytes):,} bytes")
        return mp4_bytes

    def _frames_to_mp4(self, frames, fps: int = 16) -> bytes:
        """Convert frames (PIL Images or numpy arrays) to MP4 bytes."""
        import imageio
        import numpy as np
        from PIL import Image

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            # Normalize frames to numpy uint8
            np_frames = []
            for frame in frames:
                if isinstance(frame, Image.Image):
                    np_frames.append(np.array(frame.convert("RGB")))
                elif isinstance(frame, np.ndarray):
                    if frame.dtype != np.uint8:
                        frame = (frame * 255).clip(0, 255).astype(np.uint8)
                    np_frames.append(frame)

            writer = imageio.get_writer(
                tmp_path,
                fps=fps,
                codec="libx264",
                quality=8,
                pixelformat="yuv420p",
            )
            for frame in np_frames:
                writer.append_data(frame)
            writer.close()

            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            import pathlib
            pathlib.Path(tmp_path).unlink(missing_ok=True)


# ── Web endpoint ──────────────────────────────────────────────────────────────

@app.function(
    image=image,
    secrets=[modal.Secret.from_name("creativeos-secrets")],
    timeout=360,
)
@modal.web_endpoint(method="POST", label="creativeos-video-gen")
async def generate_endpoint(request: dict) -> modal.Response:
    """
    POST /generate
    Body: {
      "prompt": str,
      "duration_s": int (default 4, max 8),
      "width": int (default 832),
      "height": int (default 480),
      "fps": int (default 16),
      "image_b64": str (optional, base64 PNG for I2V)
    }
    Response: MP4 bytes
    """
    import json

    prompt = request.get("prompt", "")
    if not prompt:
        return modal.Response(
            content=json.dumps({"error": "prompt is required"}).encode(),
            status_code=400,
            headers={"Content-Type": "application/json"},
        )

    duration_s = min(int(request.get("duration_s", 4)), 8)
    width = min(int(request.get("width", 832)), 1280)
    height = min(int(request.get("height", 480)), 720)
    fps = min(int(request.get("fps", 16)), 24)
    image_b64 = request.get("image_b64")

    try:
        model = WanVideoModel()
        mp4_bytes = model.generate.remote(
            prompt=prompt,
            duration_s=duration_s,
            width=width,
            height=height,
            fps=fps,
            image_b64=image_b64,
        )

        return modal.Response(
            content=mp4_bytes,
            status_code=200,
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(len(mp4_bytes)),
                "X-Model": "Wan2.2-T2V-A14B",
                "X-Duration": str(duration_s),
            },
        )

    except Exception as e:
        import traceback
        print(f"Video generation error: {e}\n{traceback.format_exc()}")
        return modal.Response(
            content=json.dumps({"error": str(e)}).encode(),
            status_code=500,
            headers={"Content-Type": "application/json"},
        )


@app.function(image=image, timeout=30)
@modal.web_endpoint(method="GET", label="creativeos-video-health")
async def health_endpoint() -> dict:
    return {
        "status": "ok",
        "model": HF_MODEL_ID,
        "service": "creativeos-video-gen",
    }


@app.local_entrypoint()
def test():
    """modal run modal_apps/video_gen.py"""
    model = WanVideoModel()
    mp4_bytes = model.generate.remote(
        prompt="a red sneaker rotating slowly on a white pedestal, product showcase, cinematic",
        duration_s=3,
        width=512,
        height=512,
        fps=16,
        num_inference_steps=15,
    )
    with open("/tmp/test_wan.mp4", "wb") as f:
        f.write(mp4_bytes)
    print(f"Saved test video: /tmp/test_wan.mp4 ({len(mp4_bytes):,} bytes)")
