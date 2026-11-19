# Local GPU worker (ComfyUI)

The Image agent talks to ComfyUI over its HTTP API (`/prompt`, `/history/{id}`, `/view`,
`/system_stats`, `/queue`). The adapter is `apps/api/app/providers/comfyui.py`.

## Setup on the Windows host

1. Install ComfyUI (the official Windows portable build or a git checkout) on the machine with the
   NVIDIA GPU, and install a current NVIDIA driver.
2. Put an SDXL checkpoint in `ComfyUI\models\checkpoints\`. The default expected file name is
   `sd_xl_base_1.0.safetensors`; set `COMFYUI_CHECKPOINT` (or the model field in
   Settings → Providers → ComfyUI) if yours differs. A wrong name makes ComfyUI reject the workflow
   and the task fails with `COMFYUI_WORKFLOW_REJECTED`.
3. Start ComfyUI:
   - API and workers running directly on Windows (no Docker): the default `127.0.0.1:8188` is fine.
     Set `COMFYUI_URL=http://127.0.0.1:8188`.
   - API and workers in Docker Desktop: ComfyUI must listen on an address containers can reach.
     Start it with `--listen 0.0.0.0 --port 8188` and set `COMFYUI_URL=http://host.docker.internal:8188`.
4. **Do not expose port 8188 beyond this machine.** ComfyUI has no authentication. Keep the Windows
   Firewall rule for it restricted to local/Docker traffic, and never forward the port on a router.

## How routing uses it

For every image task the router (`services/router.py`) asks ComfyUI for `/system_stats` and `/queue`:

- reachable and queue depth ≤ `COMFYUI_MAX_QUEUE` (default 4) → the image is generated locally at no API cost;
- unreachable or saturated → the cloud image API is used **only if** an OpenAI key is configured,
  the organization allows cloud fallback, the job's data classification permits external AI, and the
  job and monthly budgets have room;
- otherwise the task fails with a specific error code (or produces a labelled placeholder when
  `DEMO_MODE=true`).

The decision and its reason are stored on the task and shown in the Job Monitor.

## Tuning

| Variable | Default | Meaning |
|---|---|---|
| `COMFYUI_STEPS` | 25 | Sampler steps of the built-in SDXL text-to-image workflow |
| `COMFYUI_TIMEOUT_SECONDS` | 600 | Maximum wait for one image |
| `COMFYUI_MAX_QUEUE` | 4 | Queue depth above which the router treats local capacity as unavailable |
| `COMFYUI_WORKFLOW_PATH` | unset | Path to your own API-format workflow JSON |

A custom workflow file may contain the placeholders `%PROMPT%`, `%NEGATIVE%`, `%CHECKPOINT%` (inside
strings) and `"%WIDTH%"`, `"%HEIGHT%"`, `"%SEED%"` (quoted, replaced by numbers). Export it from
ComfyUI with "Save (API Format)". It must end in a node that saves an image.

Native generation sizes are 1344×768 (16:9), 768×1344 (9:16) and 1024×1024; the result is centre-cropped
to the exact aspect ratio.

The `worker-image` container runs two concurrent image tasks (`-c 2`). With a single GPU ComfyUI
queues them itself; lower the concurrency to 1 if VRAM is tight.

## Verification status

Verified on the development machine (2026-11-18): ComfyUI 0.45.0 installed from source in
`C:\ComfyUI` with Python 3.13, PyTorch 2.14.1 (CUDA 13.0 build) and `sd_xl_base_1.0.safetensors`,
running on an RTX 5080 Laptop GPU. An image-only job routed both images to `comfyui`, produced real
1344x756 PNG files, passed QA and completed in about 44 seconds including the first model load.
Routing rules, queue saturation and cloud fallback are covered by automated tests against an
HTTP-level fake of the ComfyUI API.

Start it on this machine with:

```
C:\ComfyUI\.venv\Scripts\python.exe C:\ComfyUI\main.py --listen 127.0.0.1 --port 8188
```

Generated images can contain shapes that resemble real brand logos. Review images before
publishing them.
