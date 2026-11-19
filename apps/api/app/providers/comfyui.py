"""Local ComfyUI adapter (HTTP API): queue a txt2img workflow, wait for it, download the image."""
from __future__ import annotations

import json
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.errors import AgentError
from app.providers.config import ComfyUIConfig

# Native SDXL resolutions per aspect ratio.
SIZES = {"16:9": (1344, 768), "9:16": (768, 1344), "1:1": (1024, 1024)}
NEGATIVE = "text, letters, watermark, logo, brand logo, trademark, apple logo, lowres, blurry, distorted, extra fingers, jpeg artifacts"


@dataclass
class ComfyHealth:
    reachable: bool
    queue_depth: int = 0
    vram_free_mb: int | None = None
    detail: str = ""


def default_workflow(prompt: str, negative: str, width: int, height: int, seed: int, checkpoint: str, steps: int) -> dict:
    return {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": 7.0, "sampler_name": "dpmpp_2m", "scheduler": "karras",
            "denoise": 1.0, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "mcr_workforce", "images": ["8", 0]}},
    }


def _custom_workflow(text: str, values: dict[str, object]) -> dict:
    """Custom API-format workflow with %PROMPT% %NEGATIVE% %WIDTH% %HEIGHT% %SEED% %CHECKPOINT% placeholders."""
    for name, value in values.items():
        token = f"%{name}%"
        if isinstance(value, int):
            text = text.replace(f'"{token}"', str(value))
        else:
            text = text.replace(token, json.dumps(str(value), ensure_ascii=False)[1:-1])
    return json.loads(text)


class ComfyUIClient:
    def __init__(self, cfg: ComfyUIConfig, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self._http = httpx.Client(base_url=cfg.url, timeout=httpx.Timeout(30, connect=3), transport=transport)

    def health(self) -> ComfyHealth:
        try:
            stats = self._http.get("/system_stats", timeout=4)
            stats.raise_for_status()
            queue = self._http.get("/queue", timeout=4)
            queue.raise_for_status()
        except httpx.HTTPError as exc:
            return ComfyHealth(False, detail=f"unreachable: {type(exc).__name__}")
        q = queue.json()
        depth = len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
        devices = stats.json().get("devices") or []
        vram = int(devices[0].get("vram_free", 0) / (1024 * 1024)) if devices else None
        return ComfyHealth(True, depth, vram, f"queue={depth}, vram_free={vram}MB")

    def generate(
        self, prompt: str, aspect_ratio: str, *, seed: int | None = None, on_wait: Callable[[], None] | None = None
    ) -> tuple[bytes, dict]:
        s = get_settings()
        width, height = SIZES.get(aspect_ratio, SIZES["1:1"])
        seed = seed if seed is not None else random.randint(1, 2**31 - 1)
        if s.comfyui_workflow_path:
            workflow = _custom_workflow(
                s.comfyui_workflow_path.read_text(encoding="utf-8"),
                {"PROMPT": prompt, "NEGATIVE": NEGATIVE, "WIDTH": width, "HEIGHT": height, "SEED": seed,
                 "CHECKPOINT": self.cfg.checkpoint},
            )
        else:
            workflow = default_workflow(prompt, NEGATIVE, width, height, seed, self.cfg.checkpoint, s.comfyui_steps)
        try:
            resp = self._http.post("/prompt", json={"prompt": workflow, "client_id": uuid.uuid4().hex})
        except httpx.HTTPError as exc:
            raise AgentError("COMFYUI_UNAVAILABLE", f"ComfyUI not reachable: {exc}", retryable=True) from exc
        if resp.status_code >= 400:
            # Typically a missing checkpoint or invalid node: not retryable without operator action.
            raise AgentError("COMFYUI_WORKFLOW_REJECTED", f"ComfyUI rejected the workflow: {resp.text[:600]}")
        prompt_id = resp.json()["prompt_id"]

        deadline = time.monotonic() + s.comfyui_timeout_seconds
        while time.monotonic() < deadline:
            if on_wait:
                on_wait()
            try:
                hist = self._http.get(f"/history/{prompt_id}").json().get(prompt_id)
            except httpx.HTTPError as exc:
                raise AgentError("COMFYUI_UNAVAILABLE", f"ComfyUI went away: {exc}", retryable=True) from exc
            if hist:
                status = hist.get("status") or {}
                if status.get("status_str") == "error":
                    raise AgentError("COMFYUI_EXECUTION_FAILED", f"ComfyUI execution error: {str(status)[:600]}")
                for output in (hist.get("outputs") or {}).values():
                    for image in output.get("images") or []:
                        view = self._http.get("/view", params={
                            "filename": image["filename"], "subfolder": image.get("subfolder", ""),
                            "type": image.get("type", "output")})
                        view.raise_for_status()
                        return view.content, {"width": width, "height": height, "seed": seed,
                                              "checkpoint": self.cfg.checkpoint, "prompt_id": prompt_id}
                if status.get("completed"):
                    raise AgentError("COMFYUI_NO_OUTPUT", "ComfyUI finished without producing an image")
            time.sleep(1.5)
        raise AgentError("COMFYUI_TIMEOUT", "ComfyUI did not finish in time", retryable=True)
