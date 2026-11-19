"""End-to-end acceptance check against a RUNNING stack (API + workers).

Submits the acceptance command, then polls like a browser that was closed and reopened:
every poll is a fresh, stateless request. Finally downloads every deliverable through its
signed URL and verifies the bytes.

    cd apps/api
    uv run python ../../scripts/e2e_acceptance.py [--api http://localhost:8000] [--approve]

Credentials come from E2E_EMAIL / E2E_PASSWORD, falling back to BOOTSTRAP_ADMIN_* in apps/api/.env.
Exit code 0 only if every requested deliverable exists and verifies.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

COMMAND = (
    "Create a promotional campaign for MCR SME Copilot with 3 promotional images, 5 Thai Facebook captions, "
    "1 business proposal PDF, and 1 promotional MP4 video."
)
EXPECTED = {"image": 3, "caption": 5, "document_pdf": 1, "video": 1}
TERMINAL = {"completed", "partial", "failed", "cancelled", "rejected", "awaiting_approval"}


def credentials() -> tuple[str, str]:
    env = dict(os.environ)
    dotenv = Path(__file__).resolve().parents[1] / "apps" / "api" / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    email = env.get("E2E_EMAIL") or env.get("BOOTSTRAP_ADMIN_EMAIL")
    password = env.get("E2E_PASSWORD") or env.get("BOOTSTRAP_ADMIN_PASSWORD")
    if not email or not password:
        sys.exit("Set E2E_EMAIL / E2E_PASSWORD (or BOOTSTRAP_ADMIN_* in apps/api/.env)")
    return email, password


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--approve", action="store_true", help="approve the job if it ends in awaiting_approval")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    email, password = credentials()
    with httpx.Client(base_url=args.api, timeout=30) as c:
        ready = c.get("/health/ready")
        print("health:", ready.status_code, ready.json())
        login = c.post("/v1/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}

        started = time.time()
        resp = c.post("/v1/jobs", json={"command": COMMAND}, headers={**auth, "Idempotency-Key": str(uuid.uuid4())})
        resp.raise_for_status()
        job_id = resp.json()["id"]
        print(f"job {job_id} accepted in {time.time() - started:.2f}s with status '{resp.json()['status']}'")

    last = None
    max_running = 0
    while True:  # a new connection per poll: nothing is kept alive between polls
        with httpx.Client(base_url=args.api, timeout=30, headers=auth) as c:
            job = c.get(f"/v1/jobs/{job_id}").json()
        max_running = max(max_running, job["task_counts"]["running"])
        line = f"{job['status']:<18} progress={job['progress']:>3}%  {job['task_counts']}"
        if line != last:
            print(f"[{time.time() - started:6.1f}s] {line}")
            last = line
        if job["status"] in TERMINAL:
            break
        if time.time() - started > args.timeout:
            print("TIMEOUT")
            return 1
        time.sleep(1)

    failures: list[str] = []
    with httpx.Client(base_url=args.api, timeout=120) as c:
        tasks = c.get(f"/v1/jobs/{job_id}/tasks", headers=auth).json()["items"]
        arts = c.get(f"/v1/jobs/{job_id}/artifacts", headers=auth).json()["items"]
        qa = c.get(f"/v1/jobs/{job_id}/qa", headers=auth).json()
        print(f"\nmax tasks running at the same time: {max_running}")
        print("tasks:")
        for t in tasks:
            print(f"  {t['key']:<20} {t['status']:<10} provider={t['provider']} ({t['execution_location']}) "
                  f"attempt={t['attempt']} deps={t['depends_on']} {t['error_code'] or ''}")
        print("artifacts:")
        counts: dict[str, int] = {}
        for a in arts:
            counts[a["kind"]] = counts.get(a["kind"], 0) + 1
            data = c.get(a["download_url"]).content  # signed URL, no auth header
            ok = len(data) == a["size_bytes"] and hashlib.sha256(data).hexdigest() == a["sha256"]
            magic = {"image": b"\x89PNG", "document_pdf": b"%PDF-"}.get(a["kind"])
            if magic and not data.startswith(magic):
                ok = False
            if a["kind"] == "video" and data[4:8] != b"ftyp":
                ok = False
            print(f"  {a['kind']:<14} {a['filename']:<24} {a['size_bytes']:>9} bytes  demo={a['is_demo']}  "
                  f"{'verified' if ok else 'CORRUPT'}")
            if not ok:
                failures.append(f"{a['filename']} failed verification")
        for kind, want in EXPECTED.items():
            if counts.get(kind, 0) != want:
                failures.append(f"expected {want} {kind}, found {counts.get(kind, 0)}")
        video = next((a for a in arts if a["kind"] == "video"), None)
        if video:
            m = video["metadata"]
            print(f"video: {m.get('width')}x{m.get('height')} {m.get('video_codec')}/{m.get('audio_codec')} "
                  f"{m.get('duration_seconds')}s voiceover={m.get('has_voiceover')} limitations={m.get('limitations')}")
        print(f"\nQA verdict: {qa['verdict']} — {qa['summary']}")
        print(f"job: status={job['status']} demo_mode={job['demo_mode']} planner={job['planner_mode']} "
              f"cost=${job['actual_cost_usd']:.4f} elapsed={time.time() - started:.1f}s")
        if job["status"] not in ("completed", "awaiting_approval"):
            failures.append(f"job ended as {job['status']}: {job['error_code']} {job['error_message']}")
        if args.approve and job["status"] == "awaiting_approval" and not failures:
            done = c.post(f"/v1/jobs/{job_id}/approve", json={"decision": "approved", "comment": "e2e"}, headers=auth)
            print("approved ->", done.json()["status"])

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        return 1
    print("\nACCEPTANCE CHECK PASSED" + (" (DEMO MODE: placeholders, not AI-generated)" if job["demo_mode"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
