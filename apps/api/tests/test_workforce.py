from __future__ import annotations

import io
import json
import uuid
from datetime import timedelta

import httpx
import pytest
from PIL import Image
from sqlalchemy import func, select

from app.agents.planner import parse_command_rules
from app.config import Settings
from app.db import session_scope, utcnow
from app.models import Artifact, AuditLog, Job, Task, UsageEvent
from app.services import orchestrator
from app.services.media import probe_media
from app.services.storage import get_storage
from app.worker import runner
from tests.conftest import ACCEPTANCE_COMMAND, PASSWORD


def submit(client, auth, **body):
    payload = {"command": ACCEPTANCE_COMMAND, **body}
    resp = client.post("/v1/jobs", json=payload, headers=auth)
    assert resp.status_code == 202, resp.text
    return client.get(f"/v1/jobs/{resp.json()['id']}", headers=auth).json()


def tasks_of(client, auth, job_id):
    return {t["key"]: t for t in client.get(f"/v1/jobs/{job_id}/tasks", headers=auth).json()["items"]}


def artifacts_of(client, auth, job_id):
    return client.get(f"/v1/jobs/{job_id}/artifacts", headers=auth).json()["items"]


# --- planner -------------------------------------------------------------------
def test_rule_planner_parses_acceptance_command():
    spec = parse_command_rules(ACCEPTANCE_COMMAND, "en")
    assert spec.images.count == 3
    assert (spec.captions.count, spec.captions.language, spec.captions.platform) == (5, "th", "facebook")
    assert spec.document.doc_type == "proposal" and spec.document.formats == ["pdf"]
    assert spec.video.duration_seconds == 60


def test_rule_planner_parses_thai_and_durations():
    spec = parse_command_rules("ทำแคมเปญ: ภาพโปรโมต 2 ภาพ แคปชั่น 4 โพสต์ และวิดีโอ 30 วินาที", "en")
    assert spec.language == "th" and spec.images.count == 2 and spec.captions.count == 4
    assert spec.video.duration_seconds == 30 and spec.document is None
    assert parse_command_rules("Make a 2-minute video and three posters", "en").video.duration_seconds == 120
    assert parse_command_rules("Make a 2-minute video and three posters", "en").images.count == 3
    # other things people ask for that are images
    samui = parse_command_rules("อยากให้ช่วยสร้างแผนที่เกาะสมุย", "en")
    assert samui.images.count == 1 and samui.language == "th" and samui.captions is None
    assert parse_command_rules("Design 2 logos and an infographic", "en").images.count == 2


# --- acceptance test: explicit demo mode (no AI credentials on this machine) ----------------
def test_acceptance_command_in_explicit_demo_mode(client, auth, fresh_state):
    fresh_state.demo_mode = True
    job = submit(client, auth)

    # QA never passes demo output silently: a human must look at it.
    assert job["status"] == "awaiting_approval"
    assert job["demo_mode"] is True and job["planner_mode"] == "rules"
    assert job["qa_verdict"] == "HUMAN_REVIEW" and "DEMO" in job["qa_summary"]
    assert job["progress"] == 100

    tasks = tasks_of(client, auth, job["id"])
    assert set(tasks) == {"plan", "content_copy", "image_1", "image_2", "image_3", "document_proposal", "video_promo", "qa_review"}
    assert all(t["status"] == "succeeded" for t in tasks.values())
    assert set(tasks["video_promo"]["depends_on"]) == {"content_copy", "image_1", "image_2", "image_3"}
    assert tasks["document_proposal"]["depends_on"] == ["content_copy"]
    assert tasks["image_1"]["provider"] == "demo" and tasks["video_promo"]["provider"] == "ffmpeg"

    arts = artifacts_of(client, auth, job["id"])
    kinds = [a["kind"] for a in arts]
    assert kinds.count("image") == 3 and kinds.count("caption") == 5
    assert kinds.count("document_pdf") == 1 and kinds.count("video") == 1 and kinds.count("subtitle") == 1
    assert all(a["is_demo"] for a in arts if a["kind"] in ("image", "caption", "document_pdf", "video"))

    # Real files: fetch each through its signed URL and verify the bytes.
    by_kind = {a["kind"]: a for a in arts}
    pdf = client.get(by_kind["document_pdf"]["download_url"], headers=auth)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-") and b"/FontFile2" in pdf.content
    img = client.get(by_kind["image"]["download_url"], headers=auth)
    assert Image.open(io.BytesIO(img.content)).size == (1920, 1080)
    video = by_kind["video"]
    assert video["metadata"]["video_codec"] == "h264" and video["metadata"]["audio_codec"] == "aac"
    assert (video["metadata"]["width"], video["metadata"]["height"]) == (1920, 1080)
    assert abs(video["metadata"]["duration_seconds"] - 60) <= 1.5
    assert any("voiceover" in lim for lim in video["metadata"]["limitations"])
    part = client.get(video["download_url"], headers={**auth, "Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    assert part.headers["content-range"] == f"bytes 0-99/{video['size_bytes']}"

    # the stored MP4 really is what the metadata claims
    with session_scope() as s:
        key = s.scalar(select(Artifact.storage_key).where(Artifact.id == uuid.UUID(video["id"])))
    info = probe_media(get_storage()._path(key))  # type: ignore[attr-defined]
    assert info["video_codec"] == "h264" and info["audio_codec"] == "aac"

    approved = client.post(f"/v1/jobs/{job['id']}/approve", json={"decision": "approved", "comment": "ok"}, headers=auth)
    assert approved.status_code == 200 and approved.json()["status"] == "completed"
    assert client.post(f"/v1/jobs/{job['id']}/approve", json={"decision": "approved"}, headers=auth).status_code == 409


def test_never_reports_success_without_providers(client, auth):
    """DEMO_MODE off and nothing configured: every AI deliverable fails loudly; the job is failed, not completed."""
    job = submit(client, auth)
    assert job["status"] == "failed" and job["error_code"] == "PROVIDER_NOT_CONFIGURED"
    assert job["demo_mode"] is False
    tasks = tasks_of(client, auth, job["id"])
    assert tasks["plan"]["status"] == "succeeded"
    for key in ("content_copy", "image_1", "document_proposal", "video_promo"):
        assert tasks[key]["status"] == "failed" and tasks[key]["error_code"] == "PROVIDER_NOT_CONFIGURED"
    assert [a for a in artifacts_of(client, auth, job["id"]) if a["kind"] != "qa_report"] == []


# --- real adapters against HTTP-level fakes ----------------------------------------------------
def test_full_run_with_cloud_llm_and_local_comfyui(client, auth, fake_openai, fake_comfyui):
    job = submit(client, auth, require_approval=False)
    assert job["status"] == "completed", job
    assert job["qa_verdict"] == "PASS" and job["demo_mode"] is False and job["planner_mode"] == "llm"
    assert job["actual_cost_usd"] > 0 and job["estimated_cost_usd"] > 0

    tasks = tasks_of(client, auth, job["id"])
    assert (tasks["image_1"]["provider"], tasks["image_1"]["execution_location"]) == ("comfyui", "local")
    assert (tasks["content_copy"]["provider"], tasks["content_copy"]["execution_location"]) == ("openai", "cloud")
    assert tasks["video_promo"]["provider"] == "ffmpeg" and tasks["document_proposal"]["provider"] == "openai"
    assert fake_comfyui.prompts == 3
    assert not any(p.endswith("/images/generations") for p in fake_openai.calls)  # local GPU was preferred

    arts = artifacts_of(client, auth, job["id"])
    assert not any(a["is_demo"] for a in arts)
    video = next(a for a in arts if a["kind"] == "video")
    assert video["metadata"]["has_voiceover"] is True and video["metadata"]["limitations"] == []
    assert abs(video["metadata"]["duration_seconds"] - 20) <= 1.5
    assert any(a["kind"] == "audio" for a in arts)
    captions = [a for a in arts if a["kind"] == "caption"]
    assert len(captions) == 5 and "MCR SME Copilot" in captions[0]["text_preview"]

    usage = client.get("/v1/usage", headers=auth).json()
    assert usage["total_cost_usd"] == pytest.approx(job["actual_cost_usd"], rel=1e-6)
    assert {b["provider"] for b in usage["by_provider"]} == {"openai", "comfyui"}
    summary = client.get("/v1/dashboard/summary", headers=auth).json()
    assert summary["completed_jobs"] == 1 and len(summary["recent_artifacts"]) > 0


def test_router_falls_back_to_cloud_only_when_authorized(client, auth, fake_openai, fake_comfyui, org_a):
    fake_comfyui.up = False
    job = submit(client, auth, command="Create 1 promotional image", require_approval=False)
    task = tasks_of(client, auth, job["id"])["image_1"]
    assert job["status"] == "completed" and task["provider"] == "openai" and task["execution_location"] == "cloud"
    assert "unreachable" in task["routing_reason"] and "fallback" in task["routing_reason"]

    assert client.patch("/v1/settings/organization", json={"allow_cloud_fallback": False}, headers=auth).status_code == 200
    job = submit(client, auth, command="Create 1 promotional image", require_approval=False)
    task = tasks_of(client, auth, job["id"])["image_1"]
    assert job["status"] == "failed" and task["error_code"] == "CLOUD_FALLBACK_NOT_AUTHORIZED"


def test_router_respects_saturated_local_queue(client, auth, fake_openai, fake_comfyui):
    fake_comfyui.queue = 50
    job = submit(client, auth, command="Create 1 promotional image", require_approval=False)
    task = tasks_of(client, auth, job["id"])["image_1"]
    assert task["provider"] == "openai" and "saturated" in task["routing_reason"]


def test_restricted_data_never_reaches_external_ai(client, auth, fake_openai):
    job = submit(client, auth, command="Write 2 Thai Facebook captions", data_classification="restricted")
    assert job["status"] == "failed" and job["planner_mode"] == "rules"
    assert tasks_of(client, auth, job["id"])["content_copy"]["error_code"] == "PRIVACY_POLICY_BLOCKED"
    assert fake_openai.calls == []


def test_retry_with_backoff_then_success(client, auth, fake_openai, org_a):
    fake_openai.fail_next = {"/chat/completions": 1}  # planner's first call fails with a 500
    job = submit(client, auth, command="Write 2 Thai Facebook captions", require_approval=False)
    assert job["status"] == "completed"
    assert tasks_of(client, auth, job["id"])["plan"]["attempt"] == 2
    events = [e["action"] for e in client.get(f"/v1/jobs/{job['id']}/events", headers=auth).json()["items"]]
    assert "task.retry_scheduled" in events and events[0] == "job.created" and events[-1] == "job.completed"


def test_retries_are_bounded_and_dead_lettered(client, auth, fake_openai):
    fake_openai.fail_next = {"/chat/completions": 99}
    job = submit(client, auth, command="Write 2 Thai Facebook captions")
    plan = tasks_of(client, auth, job["id"])["plan"]
    assert job["status"] == "failed" and job["error_code"] == "PROVIDER_UNAVAILABLE"
    assert plan["attempt"] == plan["max_attempts"] == 3
    with session_scope() as s:
        dead = s.scalars(select(AuditLog).where(AuditLog.action == "task.failed")).all()
        assert dead and dead[0].detail["dead_letter"] is True


def test_budget_is_enforced_at_planning(client, auth, fake_openai):
    job = submit(client, auth, budget_limit_usd=0.05)
    assert job["status"] == "failed" and job["error_code"] == "BUDGET_EXCEEDED"
    assert not any(p.endswith("/images/generations") or p.endswith("/audio/speech") for p in fake_openai.calls)


def test_qa_requests_bounded_revision_for_missing_file(client, auth, fake_openai, monkeypatch):
    real = runner.AGENTS["content"]
    calls = {"n": 0}

    def flaky_content(ctx):
        result = real(ctx)
        calls["n"] += 1
        if calls["n"] == 1:  # simulate a file lost after the task reported success
            art = ctx.session.scalar(select(Artifact).where(Artifact.task_id == ctx.task.id, Artifact.kind == "caption"))
            ctx.storage.delete(art.storage_key)
        return result

    monkeypatch.setitem(runner.AGENTS, "content", flaky_content)
    job = submit(client, auth, command="Write 2 Thai Facebook captions", require_approval=False)
    assert calls["n"] == 2 and job["status"] == "completed" and job["qa_verdict"] == "PASS"
    events = [e["action"] for e in client.get(f"/v1/jobs/{job['id']}/events", headers=auth).json()["items"]]
    assert events.count("qa.verdict") == 2 and "task.revision_requested" in events


def test_qa_rejects_deliverable_when_revisions_exhausted(client, auth, fake_openai, monkeypatch):
    real = runner.AGENTS["content"]

    def always_loses_file(ctx):
        result = real(ctx)
        art = ctx.session.scalar(select(Artifact).where(Artifact.task_id == ctx.task.id, Artifact.kind == "caption"))
        ctx.storage.delete(art.storage_key)
        return result

    monkeypatch.setitem(runner.AGENTS, "content", always_loses_file)
    job = submit(client, auth, command="Write 2 Thai Facebook captions", require_approval=False)
    assert job["status"] == "failed" and job["qa_verdict"] == "HUMAN_REVIEW"
    assert tasks_of(client, auth, job["id"])["content_copy"]["error_code"] == "QA_REJECTED"


def test_partial_success_when_one_agent_fails(client, auth, fake_openai, monkeypatch):
    from app.errors import AgentError

    def broken(ctx):
        raise AgentError("FFMPEG_FAILED", "boom")

    monkeypatch.setitem(runner.AGENTS, "video", broken)
    job = submit(client, auth, command="2 Thai Facebook captions and 1 promotional MP4 video 15 seconds")
    assert job["status"] == "partial" and job["error_code"] == "PARTIAL_FAILURE"
    tasks = tasks_of(client, auth, job["id"])
    assert tasks["video_promo"]["status"] == "failed" and tasks["content_copy"]["status"] == "succeeded"
    assert tasks["qa_review"]["status"] == "succeeded"  # QA still runs and reports the gap


# --- API behaviour --------------------------------------------------------------------------
def test_idempotent_job_creation(client, auth, fresh_state):
    fresh_state.demo_mode = True
    headers = {**auth, "Idempotency-Key": "abc-123"}
    body = {"command": "Write 1 Thai Facebook caption"}
    first = client.post("/v1/jobs", json=body, headers=headers)
    second = client.post("/v1/jobs", json=body, headers=headers)
    assert (first.status_code, second.status_code) == (202, 200)
    assert first.json()["id"] == second.json()["id"]
    assert client.get("/v1/jobs", headers=auth).json()["total"] == 1


def test_n8n_driven_flow_plan_then_dispatch(client, org_a, fresh_state):
    fresh_state.demo_mode = True
    key = {"X-API-Key": org_a["api_key"]}
    job = client.post("/v1/jobs", json={"command": "Write 2 Thai Facebook captions", "auto_start": False}, headers=key).json()
    assert job["status"] == "queued" and job["task_counts"]["pending"] == 1
    assert client.post(f"/v1/jobs/{job['id']}/dispatch", headers=key).status_code == 409  # not planned yet
    planned = client.post(f"/v1/jobs/{job['id']}/plan", headers=key)
    assert planned.status_code == 202 and planned.json()["status"] == "planned"
    client.post(f"/v1/jobs/{job['id']}/plan", headers=key)  # idempotent
    dispatched = client.post(f"/v1/jobs/{job['id']}/dispatch", headers=key).json()
    assert dispatched["dispatched"] == ["content_copy"]
    assert dispatched["job"]["status"] == "awaiting_approval"
    qa = client.get(f"/v1/jobs/{job['id']}/qa", headers=key).json()
    assert qa["verdict"] == "HUMAN_REVIEW" and any(c["name"] == "qa:demo_output" for c in qa["checks"])
    ev = client.post(f"/v1/jobs/{job['id']}/events", json={"action": "n8n.notified", "detail": {"ok": True}}, headers=key)
    assert ev.status_code == 201 and ev.json()["actor"] == "n8n"


def test_cancel_job(client, auth, fresh_state):
    job = client.post("/v1/jobs", json={"command": "Write 2 Thai Facebook captions", "auto_start": False}, headers=auth).json()
    cancelled = client.post(f"/v1/jobs/{job['id']}/cancel", headers=auth)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert tasks_of(client, auth, job["id"])["plan"]["status"] == "cancelled"
    assert client.post(f"/v1/jobs/{job['id']}/cancel", headers=auth).status_code == 409


def test_running_task_stops_when_job_is_cancelled(client, auth, fake_openai, monkeypatch):
    def cancel_midway(ctx):
        with session_scope() as s:
            orchestrator.cancel_job(s, s.get(Job, ctx.job.id), "user:test")
        ctx.heartbeat()  # raises JobCancelled
        raise AssertionError("unreachable")

    monkeypatch.setitem(runner.AGENTS, "content", cancel_midway)
    job = submit(client, auth, command="Write 2 Thai Facebook captions")
    assert job["status"] == "cancelled"
    assert tasks_of(client, auth, job["id"])["content_copy"]["status"] == "cancelled"


def test_reaper_recovers_task_from_dead_worker(client, auth, fresh_state):
    fresh_state.demo_mode = True
    job = client.post("/v1/jobs", json={"command": "Write 2 Thai Facebook captions", "auto_start": False}, headers=auth).json()
    with session_scope() as s:  # simulate a worker that claimed the planner task and died
        task = s.scalar(select(Task).where(Task.job_id == uuid.UUID(job["id"])))
        task.status, task.attempt = "running", 1
        task.lease_expires_at = utcnow() - timedelta(seconds=5)
        s.get(Job, task.job_id).auto_start = True
    with session_scope() as s:
        stats = orchestrator.reap_expired(s)
    assert stats["requeued"] == 1
    after = client.get(f"/v1/jobs/{job['id']}", headers=auth).json()
    assert after["status"] == "awaiting_approval"
    assert tasks_of(client, auth, job["id"])["plan"]["attempt"] == 2


def test_duplicate_message_does_not_run_task_twice(client, auth, fresh_state):
    fresh_state.demo_mode = True
    job = submit(client, auth, command="Write 2 Thai Facebook captions")
    with session_scope() as s:
        task_id = s.scalar(select(Task.id).where(Task.job_id == uuid.UUID(job["id"]), Task.key == "content_copy"))
        before = s.scalar(select(Task.attempt).where(Task.id == task_id))
    runner.execute_task(task_id)  # redelivered message for an already finished task
    with session_scope() as s:
        assert s.scalar(select(Task.attempt).where(Task.id == task_id)) == before


def test_tenant_isolation(client, auth, org_b, fresh_state):
    fresh_state.demo_mode = True
    job = submit(client, auth, command="Write 1 Thai Facebook caption")
    other = client.post("/v1/auth/login", json={"email": org_b["email"], "password": PASSWORD}).json()
    b = {"Authorization": f"Bearer {other['access_token']}"}
    for path in ("", "/tasks", "/artifacts", "/events", "/qa"):
        assert client.get(f"/v1/jobs/{job['id']}{path}", headers=b).status_code == 404
    assert client.post(f"/v1/jobs/{job['id']}/cancel", headers=b).status_code == 404
    assert client.post(f"/v1/jobs/{job['id']}/approve", json={"decision": "approved"}, headers=b).status_code == 404
    assert client.get("/v1/jobs", headers=b).json()["total"] == 0
    assert client.get("/v1/artifacts", headers=b).json()["total"] == 0
    assert client.get("/v1/usage", headers={"X-API-Key": org_b["api_key"]}).json()["total_cost_usd"] == 0


def test_authentication_and_validation(client, auth):
    assert client.get("/v1/jobs").status_code == 401
    assert client.get("/v1/jobs", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/v1/jobs", headers={"X-API-Key": "mcr_wrong"}).status_code == 401
    bad = client.post("/v1/jobs", json={"command": "hey"}, headers=auth)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "VALIDATION_ERROR"
    assert client.post("/v1/jobs", json={"command": "valid command", "agents": ["hacker"]}, headers=auth).status_code == 422
    assert client.post("/v1/jobs", json={"command": "valid command", "agents": []}, headers=auth).status_code == 422
    assert client.get(f"/v1/jobs/{uuid.uuid4()}", headers=auth).json()["error"]["code"] == "NOT_FOUND"
    assert client.get("/v1/auth/me", headers=auth).json()["role"] == "owner"


def test_login_is_rate_limited(client, org_a):
    codes = [client.post("/v1/auth/login", json={"email": org_a["email"], "password": "wrong-password"}).status_code
             for _ in range(7)]
    assert codes[:5] == [401] * 5 and codes[-1] == 429


def test_signed_urls_reject_tampering_and_expiry(client, auth, fresh_state):
    fresh_state.demo_mode = True
    job = submit(client, auth, command="Write 1 Thai Facebook caption")
    # signed links exist only for released work: approve the (demo, human-review) job first
    assert client.post(f"/v1/jobs/{job['id']}/approve", json={"decision": "approved"}, headers=auth).status_code == 200
    url = artifacts_of(client, auth, job["id"])[0]["download_url"]
    assert "sig=" in url and client.get(url).status_code == 200
    assert client.get(url[:-1] + ("0" if url[-1] != "0" else "1")).status_code == 403
    exp = int(url.split("exp=")[1].split("&")[0])
    assert client.get(url.replace(f"exp={exp}", f"exp={exp + 600}")).status_code == 403
    fresh_state.artifact_url_ttl_seconds = -10
    expired = artifacts_of(client, auth, job["id"])[0]["download_url"]
    assert client.get(expired).status_code == 403


def test_provider_secret_is_encrypted_and_never_returned(client, auth, org_a):
    resp = client.put("/v1/settings/providers/openai", json={"api_key": "sk-super-secret-value-" + "x" * 30, "model": "my-model"}, headers=auth)
    assert resp.status_code == 200 and resp.json()["configured"] is True and "my-model" in resp.json()["detail"]
    settings_view = client.get("/v1/settings", headers=auth)
    assert "sk-super-secret-value" not in settings_view.text
    with session_scope() as s:
        from app.models import ProviderConfig

        row = s.scalar(select(ProviderConfig))
        assert row.encrypted_secret and "sk-super-secret-value" not in row.encrypted_secret
        assert "sk-super-secret-value" not in str(s.scalars(select(AuditLog.detail)).all())
    off = client.put("/v1/settings/providers/openai", json={"enabled": False}, headers=auth).json()
    assert off["configured"] is False and off["enabled"] is False


def test_health_endpoints(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    ready = client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["checks"]["database"] == "ok"


def test_usage_events_survive_and_costs_add_up(client, auth, fake_openai, fake_comfyui):
    job = submit(client, auth, command="Write 2 Thai Facebook captions", require_approval=False)
    with session_scope() as s:
        events = s.scalars(select(UsageEvent).where(UsageEvent.job_id == uuid.UUID(job["id"]))).all()
        assert {e.agent for e in events} == {"planner", "content", "qa"}
        assert float(sum(e.cost_usd for e in events)) == pytest.approx(job["actual_cost_usd"])


def test_production_config_refuses_unsafe_defaults():
    with pytest.raises(ValueError, match="Unsafe production configuration"):
        Settings(environment="production")


def test_assistant_speech_uses_cloud_voice_and_records_cost(client, auth, fake_openai, mp3_bytes, monkeypatch):
    import json

    seen = {}
    real = fake_openai.__call__

    def spy(request):
        if request.url.path.endswith("/audio/speech"):
            seen.update(json.loads(request.content))
        return real(request)

    import httpx

    from app.services import router as hybrid_router

    hybrid_router.TRANSPORTS["openai"] = httpx.MockTransport(spy)
    resp = client.post("/v1/assistant/speech", json={"text": "สวัสดีค่ะ ดิฉันมีร่า", "language": "th"}, headers=auth)
    assert resp.status_code == 200 and resp.headers["content-type"] == "audio/mpeg" and resp.content == mp3_bytes
    assert seen["voice"] == "nova" and seen["input"] == "สวัสดีค่ะ ดิฉันมีร่า" and "instructions" in seen
    usage = client.get("/v1/usage", headers=auth).json()
    assert usage["by_agent"] == [{"agent": "assistant", "cost_usd": pytest.approx(0.015 * 20 / 1000), "events": 1}]
    assert client.post("/v1/assistant/speech", json={"text": ""}, headers=auth).status_code == 422
    assert client.post("/v1/assistant/speech", json={"text": "x" * 601}, headers=auth).status_code == 422
    assert client.post("/v1/assistant/speech", json={"text": "hi"}).status_code == 401


def test_assistant_speech_without_provider_tells_client_to_fall_back(client, auth):
    resp = client.post("/v1/assistant/speech", json={"text": "hello", "language": "en"}, headers=auth)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED"


def test_delete_job_removes_records_and_files_but_keeps_spend(client, auth, org_b, fake_openai):
    job = submit(client, auth, command="Write 2 Thai Facebook captions", require_approval=False)
    spent = client.get("/v1/usage", headers=auth).json()["total_cost_usd"]
    assert spent > 0
    with session_scope() as s:
        keys = list(s.scalars(select(Artifact.storage_key)))
    storage = get_storage()
    assert keys and all(storage.exists(k) for k in keys)

    other = client.post("/v1/auth/login", json={"email": org_b["email"], "password": PASSWORD}).json()
    assert client.delete(f"/v1/jobs/{job['id']}", headers={"Authorization": f"Bearer {other['access_token']}"}).status_code == 404

    assert client.delete(f"/v1/jobs/{job['id']}", headers=auth).status_code == 204
    assert client.get(f"/v1/jobs/{job['id']}", headers=auth).status_code == 404
    assert client.get("/v1/jobs", headers=auth).json()["total"] == 0
    assert not any(storage.exists(k) for k in keys)
    with session_scope() as s:
        assert s.scalar(select(Task.id)) is None and s.scalar(select(Artifact.id)) is None
        deleted = s.scalars(select(AuditLog).where(AuditLog.action == "job.deleted")).one()
        assert deleted.detail["job_id"] == job["id"] and deleted.job_id is None
    assert client.get("/v1/usage", headers=auth).json()["total_cost_usd"] == pytest.approx(spent)
    assert client.delete(f"/v1/jobs/{job['id']}", headers=auth).status_code == 404


def test_active_job_cannot_be_deleted(client, auth):
    job = client.post("/v1/jobs", json={"command": "Write 2 Thai Facebook captions", "auto_start": False}, headers=auth).json()
    resp = client.delete(f"/v1/jobs/{job['id']}", headers=auth)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "CONFLICT"
    client.post(f"/v1/jobs/{job['id']}/cancel", headers=auth)
    assert client.delete(f"/v1/jobs/{job['id']}", headers=auth).status_code == 204


def test_delete_all_jobs_clears_finished_ones_only(client, auth, org_b, fresh_state):
    fresh_state.demo_mode = True
    done = [submit(client, auth, command="Write 1 Thai Facebook caption") for _ in range(2)]
    failed = submit(client, auth, command="Tell me something about cats")
    assert failed["status"] == "failed"
    waiting = client.post("/v1/jobs", json={"command": "Write 2 Thai Facebook captions", "auto_start": False}, headers=auth).json()
    other = client.post("/v1/auth/login", json={"email": org_b["email"], "password": PASSWORD}).json()
    b = {"Authorization": f"Bearer {other['access_token']}"}
    theirs = submit(client, b, command="Write 1 Thai Facebook caption")
    storage = get_storage()
    with session_scope() as s:
        mine = list(s.scalars(select(Artifact.storage_key).where(Artifact.job_id.in_([uuid.UUID(j["id"]) for j in done]))))
    assert mine

    # filter: only failed jobs
    only_failed = client.delete("/v1/jobs", params={"status": "failed"}, headers=auth).json()
    assert only_failed == {"deleted": 1, "skipped_active": 0, "files_removed": 0}
    assert client.get("/v1/jobs", headers=auth).json()["total"] == 3

    result = client.delete("/v1/jobs", headers=auth).json()
    assert result["deleted"] == 2 and result["skipped_active"] == 1 and result["files_removed"] == len(mine)
    left = client.get("/v1/jobs", headers=auth).json()
    assert [j["id"] for j in left["items"]] == [waiting["id"]]  # the queued job is untouched
    assert not any(storage.exists(k) for k in mine)
    # the other organization keeps its job and files
    assert client.get(f"/v1/jobs/{theirs['id']}", headers=b).status_code == 200
    assert len(artifacts_of(client, b, theirs["id"])) > 0
    assert client.delete("/v1/jobs", params={"status": "nope"}, headers=auth).status_code == 422
    assert client.delete("/v1/jobs").status_code == 401


def test_delete_all_artifacts_keeps_jobs_and_other_tenants(client, auth, org_b, fresh_state):
    fresh_state.demo_mode = True
    job = submit(client, auth, command="Create 2 promotional images and 2 Thai Facebook captions")
    other = client.post("/v1/auth/login", json={"email": org_b["email"], "password": PASSWORD}).json()
    b = {"Authorization": f"Bearer {other['access_token']}"}
    theirs = submit(client, b, command="Write 1 Thai Facebook caption")
    storage = get_storage()
    with session_scope() as s:
        images = list(s.scalars(select(Artifact.storage_key).where(Artifact.kind == "image")))
        everything = list(s.scalars(select(Artifact.storage_key).where(Artifact.job_id == uuid.UUID(job["id"]))))
    assert len(images) == 2 and len(everything) > 4

    only_images = client.delete("/v1/artifacts", params={"kind": "image"}, headers=auth).json()
    assert only_images == {"deleted": 2, "skipped_active": 0}
    assert not any(storage.exists(k) for k in images)
    assert client.get("/v1/artifacts", params={"kind": "image"}, headers=auth).json()["total"] == 0
    assert client.get("/v1/artifacts", params={"kind": "caption"}, headers=auth).json()["total"] == 2

    rest = client.delete("/v1/artifacts", headers=auth).json()
    assert rest["deleted"] == len(everything) - 2
    assert client.get("/v1/artifacts", headers=auth).json()["total"] == 0
    assert not any(storage.exists(k) for k in everything)
    # the job itself stays, and the other organization keeps its deliverables
    assert client.get(f"/v1/jobs/{job['id']}", headers=auth).status_code == 200
    assert client.get("/v1/artifacts", headers=b).json()["total"] == len(artifacts_of(client, b, theirs["id"])) > 0
    assert client.delete("/v1/artifacts", headers=auth).json() == {"deleted": 0, "skipped_active": 0}
    assert client.delete("/v1/artifacts").status_code == 401


def test_change_password(client, auth, org_a):
    new = "a-brand-new-passphrase"
    url = "/v1/auth/change-password"
    wrong = client.post(url, json={"current_password": "not-it", "new_password": new}, headers=auth)
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "WRONG_PASSWORD"
    assert client.post(url, json={"current_password": PASSWORD, "new_password": "short"}, headers=auth).status_code == 422
    assert client.post(url, json={"current_password": PASSWORD, "new_password": PASSWORD}, headers=auth).status_code == 422
    assert client.post(url, json={"current_password": PASSWORD, "new_password": new}).status_code == 401
    assert client.post(url, json={"current_password": PASSWORD, "new_password": new},
                       headers={"X-API-Key": org_a["api_key"]}).status_code == 403

    assert client.post(url, json={"current_password": PASSWORD, "new_password": new}, headers=auth).status_code == 204
    assert client.post("/v1/auth/login", json={"email": org_a["email"], "password": PASSWORD}).status_code == 401
    assert client.post("/v1/auth/login", json={"email": org_a["email"], "password": new}).status_code == 200
    with session_scope() as s:
        logs = s.scalars(select(AuditLog).where(AuditLog.action == "auth.password_changed")).all()
        assert len(logs) == 1 and new not in str(logs[0].detail) and PASSWORD not in str(logs[0].detail)


def test_provider_key_is_checked(client, auth, fake_openai):
    bad = client.put("/v1/settings/providers/openai", json={"api_key": "key-1234567890abcdef"}, headers=auth)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_API_KEY"
    masked = client.put("/v1/settings/providers/openai", json={"api_key": "sk-...Q6oA"}, headers=auth)
    assert masked.status_code == 422 and "shortened" in masked.json()["error"]["message"]
    assert client.put("/v1/settings/providers/openai", json={"api_key": "sk-tooshort1234"}, headers=auth).status_code == 422
    # whitespace, quotes and a pasted "Bearer " prefix are cleaned up
    long_key = "sk-proj-" + "a" * 60
    ok = client.put("/v1/settings/providers/openai", json={"api_key": f'  "Bearer {long_key}"  '}, headers=auth).json()
    assert ok["configured"] is True and ok["healthy"] is True and "accepted" in ok["detail"]
    with session_scope() as s:
        from app.models import ProviderConfig
        from app.security import decrypt_secret

        assert decrypt_secret(s.scalar(select(ProviderConfig.encrypted_secret))) == long_key
    # a key OpenAI rejects shows as unhealthy with the reason
    fake_openai_reject = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"message": "bad key"}}))
    from app.services import router as hybrid_router

    hybrid_router.TRANSPORTS["openai"] = fake_openai_reject
    view = [p for p in client.get("/v1/settings", headers=auth).json()["providers"] if p["name"] == "openai"][0]
    assert view["healthy"] is False and "401" in view["detail"]
    # other OpenAI-compatible services may use other key formats
    other = client.put(
        "/v1/settings/providers/openai",
        json={"api_key": "key-for-other-service", "base_url": "https://llm.example.com/v1"},
        headers=auth,
    )
    assert other.status_code == 200


def test_out_of_credit_fails_fast_with_clear_code(client, auth, fake_openai):
    from app.services import router as hybrid_router

    body = {"error": {"message": "You have no credits remaining.", "type": "insufficient_quota", "code": "insufficient_quota"}}
    hybrid_router.TRANSPORTS["openai"] = httpx.MockTransport(lambda r: httpx.Response(429, json=body))
    job = submit(client, auth, command="Write 2 Thai Facebook captions")
    plan = tasks_of(client, auth, job["id"])["plan"]
    assert job["status"] == "failed" and job["error_code"] == "PROVIDER_NO_CREDIT"
    assert plan["attempt"] == 1  # not retried


def test_edit_text_deliverable(client, auth, org_b, fresh_state):
    import hashlib

    fresh_state.demo_mode = True
    job = submit(client, auth, command="Write 2 Thai Facebook captions and 1 promotional image")
    arts = artifacts_of(client, auth, job["id"])
    cap = next(a for a in arts if a["filename"] == "caption-02.txt")
    img = next(a for a in arts if a["kind"] == "image")
    url = f"/v1/artifacts/{cap['id']}/text"
    new = "แคปชันที่แก้ไขแล้ว ✨\n\n#MCRSMECopilot"

    edited = client.put(url, json={"text": new}, headers=auth)
    assert edited.status_code == 200
    out = edited.json()
    assert out["text_preview"] == new and out["metadata"]["edited"] is True
    data = client.get(out["download_url"], headers=auth).content
    assert data.decode() == new and hashlib.sha256(data).hexdigest() == out["sha256"] and len(data) == out["size_bytes"]
    with session_scope() as s:
        log = s.scalars(select(AuditLog).where(AuditLog.action == "artifact.edited")).one()
        assert log.detail["file"] == "caption-02.txt" and "แก้ไข" not in str(log.detail)  # sizes only, no text

    assert client.put(f"/v1/artifacts/{img['id']}/text", json={"text": "x"}, headers=auth).status_code == 409
    assert client.put(url, json={"text": ""}, headers=auth).status_code == 422
    other = client.post("/v1/auth/login", json={"email": org_b["email"], "password": PASSWORD}).json()
    assert client.put(url, json={"text": "hack"}, headers={"Authorization": f"Bearer {other['access_token']}"}).status_code == 404

    running = client.post("/v1/jobs", json={"command": "Write 1 Thai Facebook caption", "auto_start": False}, headers=auth).json()
    with session_scope() as s:  # a running job's files are locked
        j = s.get(Job, uuid.UUID(running["id"]))
        a = s.scalar(select(Artifact).where(Artifact.id == uuid.UUID(cap["id"])))
        a.job_id = j.id
    assert client.put(url, json={"text": "later"}, headers=auth).status_code == 409


def test_regenerate_image_replaces_it(client, auth, org_b, fake_comfyui, fresh_state):
    fresh_state.demo_mode = True  # captions etc. may be placeholders; the image comes from (fake) ComfyUI
    job = submit(client, auth, command="Create 1 promotional image", require_approval=False)
    old = next(a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "image")
    with session_scope() as s:
        old_key = s.get(Artifact, uuid.UUID(old["id"])).storage_key
    prompts_before = fake_comfyui.prompts

    other = client.post("/v1/auth/login", json={"email": org_b["email"], "password": PASSWORD}).json()
    b = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.post(f"/v1/artifacts/{old['id']}/regenerate", json={}, headers=b).status_code == 404

    resp = client.post(f"/v1/artifacts/{old['id']}/regenerate", json={"concept": "a bakery owner with a tablet"}, headers=auth)
    assert resp.status_code == 202  # eager Celery in tests: the work has already run
    images = [a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "image"]
    assert len(images) == 1 and images[0]["id"] != old["id"] and images[0]["task_key"] == old["task_key"]
    assert images[0]["metadata"]["regenerated"] is True and "bakery owner" in images[0]["metadata"]["prompt"]
    assert fake_comfyui.prompts == prompts_before + 1
    assert not get_storage().exists(old_key)
    events = [e["action"] for e in client.get(f"/v1/jobs/{job['id']}/events", headers=auth).json()["items"]]
    assert "artifact.regenerate_requested" in events and "artifact.regenerated" in events
    # QA ran again after the swap and its single report describes the new file
    assert events[events.index("artifact.regenerated"):].count("qa.verdict") == 1
    after = client.get(f"/v1/jobs/{job['id']}", headers=auth).json()
    assert after["status"] == "completed" and after["qa_verdict"] in ("PASS", "HUMAN_REVIEW")
    reports = [a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "qa_report"]
    assert len(reports) == 1
    checks = client.get(f"/v1/jobs/{job['id']}/qa", headers=auth).json()["checks"]
    assert any(c["name"] == "image:decodable" and c["status"] == "pass" for c in checks)

    caption_job = submit(client, auth, command="Write 1 Thai Facebook caption")
    cap = next(a for a in artifacts_of(client, auth, caption_job["id"]) if a["kind"] == "caption")
    assert client.post(f"/v1/artifacts/{cap['id']}/regenerate", json={}, headers=auth).status_code == 409


def test_regenerate_failure_keeps_the_image(client, auth, fake_comfyui, fresh_state):
    fresh_state.demo_mode = True
    job = submit(client, auth, command="Create 1 promotional image", require_approval=False)
    old = next(a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "image")
    fresh_state.demo_mode = False
    fake_comfyui.up = False  # local GPU gone and no cloud provider: generation must fail
    assert client.post(f"/v1/artifacts/{old['id']}/regenerate", json={}, headers=auth).status_code == 202
    images = [a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "image"]
    assert [a["id"] for a in images] == [old["id"]]
    assert "PROVIDER_NOT_CONFIGURED" in images[0]["metadata"]["regenerate_error"]
    assert "regenerating_since" not in images[0]["metadata"]
    assert client.get(old["download_url"], headers=auth).status_code == 200


def test_regenerate_caption(client, auth, org_b, fake_openai):
    job = submit(client, auth, command="Write 2 Thai Facebook captions", require_approval=False)
    caps = sorted((a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "caption"), key=lambda a: a["filename"])
    target, other = caps[0], caps[1]
    url = f"/v1/artifacts/{target['id']}/regenerate-caption"

    other_org = client.post("/v1/auth/login", json={"email": org_b["email"], "password": PASSWORD}).json()
    assert client.post(url, json={}, headers={"Authorization": f"Bearer {other_org['access_token']}"}).status_code == 404

    resp = client.post(url, json={"instruction": "สั้นลง"}, headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    art = body["artifact"]
    assert art["id"] == target["id"] and art["filename"] == "caption-01.txt"  # same file, new content
    text = client.get(art["download_url"], headers=auth).content.decode()
    assert text.startswith("แคปชันใหม่") and "สอบถามข้อมูลเพิ่มเติม" in text and "#MCRSMECopilot" in text
    assert art["metadata"]["regenerated"] is True and art["text_preview"] == text
    assert any("100%" in w for w in body["warnings"])  # unsupported claim reported straight away
    sent = [c for c in fake_openai.calls if c.endswith("/chat/completions")]
    assert sent  # the rewrite went through the (fake) cloud model
    # the other caption is untouched; QA re-ran and flagged the new claim for review
    assert client.get(other["download_url"], headers=auth).content.decode() == other["text_preview"]
    after = client.get(f"/v1/jobs/{job['id']}", headers=auth).json()
    assert after["qa_verdict"] == "HUMAN_REVIEW" and after["status"] == "completed"
    events = [e["action"] for e in client.get(f"/v1/jobs/{job['id']}/events", headers=auth).json()["items"]]
    assert events.index("artifact.regenerated") < len(events) - 1 and events[-1] == "qa.verdict"
    assert len([a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "qa_report"]) == 1

    img_job = submit(client, auth, command="Create 1 promotional image", agents=["image"])
    img = next(a for a in artifacts_of(client, auth, img_job["id"]) if a["kind"] == "image") if img_job["status"] != "failed" else None
    if img:
        assert client.post(f"/v1/artifacts/{img['id']}/regenerate-caption", json={}, headers=auth).status_code == 409


def test_regenerate_caption_without_provider_is_refused(client, auth, fresh_state):
    fresh_state.demo_mode = True
    job = submit(client, auth, command="Write 1 Thai Facebook caption")
    cap = next(a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "caption")
    fresh_state.demo_mode = False
    resp = client.post(f"/v1/artifacts/{cap['id']}/regenerate-caption", json={}, headers=auth)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED"
    assert client.get(cap["download_url"], headers=auth).content.decode() == cap["text_preview"]  # unchanged


def test_regenerate_document_replaces_pdf_and_docx(client, auth, fake_openai):
    job = submit(client, auth, command="Write 1 business proposal as PDF and DOCX for MCR SME Copilot", require_approval=False)
    docs = {a["kind"]: a for a in artifacts_of(client, auth, job["id"]) if a["kind"] in ("document_pdf", "document_docx")}
    assert set(docs) == {"document_pdf", "document_docx"}
    with session_scope() as s:
        old_keys = [s.get(Artifact, uuid.UUID(a["id"])).storage_key for a in docs.values()]

    resp = client.post(f"/v1/artifacts/{docs['document_pdf']['id']}/regenerate",
                       json={"instruction": "เน้นกลุ่มร้านอาหาร"}, headers=auth)
    assert resp.status_code == 202
    new = {a["kind"]: a for a in artifacts_of(client, auth, job["id"]) if a["kind"] in ("document_pdf", "document_docx")}
    assert set(new) == {"document_pdf", "document_docx"}  # both formats replaced together, no duplicates
    assert all(new[k]["id"] != docs[k]["id"] and new[k]["metadata"]["regenerated"] for k in new)
    assert client.get(new["document_pdf"]["download_url"], headers=auth).content.startswith(b"%PDF-")
    assert not any(get_storage().exists(k) for k in old_keys)
    with session_scope() as s:
        task = s.scalar(select(Task).where(Task.job_id == uuid.UUID(job["id"]), Task.agent == "document"))
        assert task.params["instruction"] == "เน้นกลุ่มร้านอาหาร"
    events = [e["action"] for e in client.get(f"/v1/jobs/{job['id']}/events", headers=auth).json()["items"]]
    assert events[events.index("artifact.regenerated"):].count("qa.verdict") == 1
    assert len([a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "qa_report"]) == 1

    # a second request without an instruction clears it, and is refused while one is still running
    with session_scope() as s:
        a = s.get(Artifact, uuid.UUID(new["document_docx"]["id"]))
        a.meta = {**a.meta, "regenerating_since": utcnow().isoformat()}
    assert client.post(f"/v1/artifacts/{new['document_pdf']['id']}/regenerate", json={}, headers=auth).status_code == 409


def test_regenerate_video_replaces_the_whole_set(client, auth, fake_openai):
    job = submit(client, auth, command="Make 1 promotional MP4 video 15 seconds for MCR SME Copilot",
                 agents=["video"], require_approval=False)
    kinds = ("video", "script", "storyboard", "subtitle", "manifest", "audio")
    old = {a["kind"]: a for a in artifacts_of(client, auth, job["id"]) if a["kind"] in kinds}
    assert {"video", "script", "subtitle", "manifest"} <= set(old)
    with session_scope() as s:
        old_keys = [s.get(Artifact, uuid.UUID(a["id"])).storage_key for a in old.values()]

    manifest = old["manifest"]
    assert client.post(f"/v1/artifacts/{manifest['id']}/regenerate", json={}, headers=auth).status_code == 409  # derived file

    resp = client.post(f"/v1/artifacts/{old['video']['id']}/regenerate", json={"instruction": "สนุกขึ้น"}, headers=auth)
    assert resp.status_code == 202
    new = {a["kind"]: a for a in artifacts_of(client, auth, job["id"]) if a["kind"] in kinds}
    assert set(new) == set(old)  # one of each, nothing duplicated or lost
    assert all(new[k]["id"] != old[k]["id"] and new[k]["metadata"]["regenerated"] for k in new)
    meta = new["video"]["metadata"]
    assert meta["video_codec"] == "h264" and meta["audio_codec"] == "aac" and abs(meta["duration_seconds"] - 15) <= 1.5
    assert client.get(new["video"]["download_url"], headers={"Range": "bytes=0-7"}).content[4:8] == b"ftyp"
    assert not any(get_storage().exists(k) for k in old_keys)
    with session_scope() as s:
        task = s.scalar(select(Task).where(Task.job_id == uuid.UUID(job["id"]), Task.agent == "video"))
        assert task.params["instruction"] == "สนุกขึ้น"
    events = [e["action"] for e in client.get(f"/v1/jobs/{job['id']}/events", headers=auth).json()["items"]]
    assert events[events.index("artifact.regenerated"):].count("qa.verdict") == 1
    checks = client.get(f"/v1/jobs/{job['id']}/qa", headers=auth).json()["checks"]
    assert any(c["name"] == "video:codecs" and c["status"] == "pass" for c in checks)


def test_narration_budget_and_shortening(client, auth, fake_openai):
    from app.agents.video import narration_budget, narration_size

    assert narration_budget(20, "th") == 155 and narration_budget(60, "en") == 142
    assert narration_size("สวัสดี ครับ", "th") == 10 and narration_size("hello there friend", "en") == 3

    long_scene = "ผู้ช่วย AI ที่รับคำสั่งเสียงภาษาไทยและสร้างผลงานหลายชิ้นพร้อมกันได้ทันที " * 3
    calls = {"n": 0}

    def too_long_then_short(system, reply):
        if "Video Agent" not in system:
            return reply
        calls["n"] += 1
        text = long_scene if calls["n"] == 1 else "สั่งงานครั้งเดียว"
        return {**reply, "scenes": [{**s, "narration": text} for s in reply["scenes"]]}

    fake_openai.mutate = too_long_then_short
    job = submit(client, auth, command="Make 1 promotional MP4 video 15 seconds", agents=["video"], require_approval=False)
    assert calls["n"] == 2  # one shortening pass was enough here, so no second pass
    script = next(a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "script")
    assert long_scene.strip()[:20] not in script["text_preview"] and "สั่งงานครั้งเดียว" in script["text_preview"]
    assert job["status"] == "completed"


def _srt_starts(srt: str) -> list[float]:
    import re as _re

    def secs(ts: str) -> float:
        h, m, rest = ts.split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000

    return [secs(m) for m in _re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) -->", srt)]


def test_voiceover_is_synced_per_scene(client, auth, fake_openai):
    job = submit(client, auth, command="Make 1 promotional MP4 video 15 seconds", agents=["video"], require_approval=False)
    arts = {a["kind"]: a for a in artifacts_of(client, auth, job["id"])}
    manifest = json.loads(client.get(arts["manifest"]["download_url"], headers=auth).content)
    scenes = manifest["scenes"]
    speech_calls = [c for c in fake_openai.calls if c.endswith("/audio/speech")]
    assert len(speech_calls) == len(scenes)  # one clip per scene
    # scenes fill the requested length exactly, and each is long enough for its 2 s clip
    assert abs(sum(s["duration_seconds"] for s in scenes) - 15) < 0.05
    assert all(s["duration_seconds"] >= 2.0 for s in scenes)
    # subtitle cues start where the scenes start
    starts, t = [], 0.0
    for s in scenes:
        starts.append(round(t, 2))
        t += s["duration_seconds"]
    srt = client.get(arts["subtitle"]["download_url"], headers=auth).content.decode()
    assert [round(x, 2) for x in _srt_starts(srt)] == starts
    # the voice track covers the whole video
    from app.services.media import probe_media

    with session_scope() as s:
        audio_key = s.get(Artifact, uuid.UUID(arts["audio"]["id"])).storage_key
    path = get_storage()._path(audio_key)  # type: ignore[attr-defined]
    assert abs(probe_media(path)["duration_seconds"] - 15) < 0.3
    assert arts["video"]["metadata"]["limitations"] == []


def test_long_narration_extends_video_without_cutting(client, auth, fake_openai, tmp_path):
    import subprocess

    from app.services.media import find_ffmpeg

    long_clip = tmp_path / "long.mp3"
    subprocess.run([find_ffmpeg(), "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=6", "-c:a", "libmp3lame", str(long_clip)],
                   check=True, capture_output=True)
    fake_openai.mp3 = long_clip.read_bytes()  # each scene's sentence now takes 6 s: far too long for 15 s
    job = submit(client, auth, command="Make 1 promotional MP4 video 15 seconds", agents=["video"], require_approval=False)
    video = next(a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "video")
    assert video["metadata"]["duration_seconds"] > 20
    assert any(lim.startswith("duration_extended") for lim in video["metadata"]["limitations"])


def test_stubborn_long_script_gets_a_second_pass(client, auth, fake_openai):
    long_scene = "ผู้ช่วย AI ที่รับคำสั่งเสียงภาษาไทยและสร้างผลงานหลายชิ้นพร้อมกันได้ทันที " * 3
    still_long = "ผู้ช่วย AI ที่รับคำสั่งเสียงภาษาไทยและสร้างผลงานหลายชิ้นพร้อมกันได้ " * 2
    calls = {"n": 0}

    def shorten_slowly(system, reply):
        if "Video Agent" not in system:
            return reply
        calls["n"] += 1
        text = {1: long_scene, 2: still_long}.get(calls["n"], "สั่งงานครั้งเดียว")
        return {**reply, "scenes": [{**s, "narration": text} for s in reply["scenes"]]}

    fake_openai.mutate = shorten_slowly
    job = submit(client, auth, command="Make 1 promotional MP4 video 15 seconds", agents=["video"], require_approval=False)
    assert calls["n"] == 3  # first pass was not enough, the second reached the budget
    script = next(a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "script")
    assert "สั่งงานครั้งเดียว" in script["text_preview"]


def test_tiny_overrun_is_absorbed_by_the_pauses(tmp_path):
    import subprocess

    from app.agents.video import SCENE_GAP, Scene, fit_scenes_to_voice
    from app.services.media import find_ffmpeg

    clips = []
    for i in range(4):  # 4 clips of 4.8 s: 19.2 s of speech + 4 pauses = 20.6 s for a 20 s video
        clip = tmp_path / f"c{i}.mp3"
        subprocess.run([find_ffmpeg(), "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=4.8", "-c:a", "libmp3lame", str(clip)],
                       check=True, capture_output=True)
        clips.append(clip)
    scenes = [Scene(heading="h", on_screen_text="t", narration="n", duration_seconds=5) for _ in range(4)]
    limitations: list[str] = []
    fit_scenes_to_voice(scenes, clips, 20.0, tmp_path, limitations)
    assert abs(sum(s.duration_seconds for s in scenes) - 20.0) < 0.01
    assert limitations == []
    assert all(s.duration_seconds >= 4.8 for s in scenes)  # never shorter than its sentence
    assert SCENE_GAP > 0.1


def test_regenerate_from_storyboard_remakes_the_video_set(client, auth, fake_openai):
    job = submit(client, auth, command="Make 1 promotional MP4 video 15 seconds", agents=["video"], require_approval=False)
    kinds = ("video", "script", "storyboard", "subtitle", "manifest", "audio")
    old = {a["kind"]: a for a in artifacts_of(client, auth, job["id"]) if a["kind"] in kinds}
    resp = client.post(f"/v1/artifacts/{old['storyboard']['id']}/regenerate", json={"instruction": "เปิดด้วยคำถาม"}, headers=auth)
    assert resp.status_code == 202
    new = {a["kind"]: a for a in artifacts_of(client, auth, job["id"]) if a["kind"] in kinds}
    assert set(new) == set(old) and all(new[k]["id"] != old[k]["id"] for k in new)  # storyboard and video stay a matching pair
    assert client.post(f"/v1/artifacts/{new['manifest']['id']}/regenerate", json={}, headers=auth).status_code == 409


def test_regenerate_from_script_remakes_the_video_set(client, auth, fake_openai):
    job = submit(client, auth, command="Make 1 promotional MP4 video 15 seconds", agents=["video"], require_approval=False)
    kinds = ("video", "script", "storyboard", "subtitle", "manifest", "audio")
    old = {a["kind"]: a for a in artifacts_of(client, auth, job["id"]) if a["kind"] in kinds}
    assert client.post(f"/v1/artifacts/{old['script']['id']}/regenerate", json={}, headers=auth).status_code == 202
    new = {a["kind"]: a for a in artifacts_of(client, auth, job["id"]) if a["kind"] in kinds}
    assert set(new) == set(old) and all(new[k]["id"] != old[k]["id"] for k in new)


def test_regenerate_from_subtitles_remakes_the_video_set(client, auth, fake_openai):
    job = submit(client, auth, command="Make 1 promotional MP4 video 15 seconds", agents=["video"], require_approval=False)
    kinds = ("video", "script", "storyboard", "subtitle", "manifest", "audio")
    old = {a["kind"]: a for a in artifacts_of(client, auth, job["id"]) if a["kind"] in kinds}
    assert client.post(f"/v1/artifacts/{old['subtitle']['id']}/regenerate", json={}, headers=auth).status_code == 202
    new = {a["kind"]: a for a in artifacts_of(client, auth, job["id"]) if a["kind"] in kinds}
    assert set(new) == set(old) and all(new[k]["id"] != old[k]["id"] for k in new)
    srt = client.get(new["subtitle"]["download_url"], headers=auth).content.decode()
    assert srt.splitlines()[:2] == ["1", srt.splitlines()[1]] and srt.splitlines()[1].startswith("00:00:00,000 -->")


def test_regenerate_voiceover_keeps_the_script(client, auth, fake_openai):
    calls = {"n": 0}

    def number_the_scripts(system, reply):
        if "Video Agent" not in system:
            return reply
        calls["n"] += 1
        return {**reply, "scenes": [{**s, "narration": f"บทพูดรุ่นที่ {calls['n']}"} for s in reply["scenes"]]}

    fake_openai.mutate = number_the_scripts
    job = submit(client, auth, command="Make 1 promotional MP4 video 15 seconds", agents=["video"], require_approval=False)
    kinds = ("video", "script", "storyboard", "subtitle", "manifest", "audio")
    old = {a["kind"]: a for a in artifacts_of(client, auth, job["id"]) if a["kind"] in kinds}
    chats = calls["n"]
    speech_before = sum(c.endswith("/audio/speech") for c in fake_openai.calls)

    # an instruction is ignored: a new voiceover reads the same script again
    resp = client.post(f"/v1/artifacts/{old['audio']['id']}/regenerate", json={"instruction": "พูดเร็วขึ้น"}, headers=auth)
    assert resp.status_code == 202
    new = {a["kind"]: a for a in artifacts_of(client, auth, job["id"]) if a["kind"] in kinds}
    assert set(new) == set(old) and all(new[k]["id"] != old[k]["id"] for k in new)  # voice and video stay in sync
    assert calls["n"] == chats  # no new script was written
    assert new["script"]["text_preview"] == old["script"]["text_preview"]
    scenes = json.loads(client.get(new["manifest"]["download_url"], headers=auth).content)["scenes"]
    assert sum(c.endswith("/audio/speech") for c in fake_openai.calls) - speech_before == len(scenes)
    assert abs(new["video"]["metadata"]["duration_seconds"] - 15) <= 1.5
    with session_scope() as s:
        task = s.scalar(select(Task).where(Task.job_id == uuid.UUID(job["id"]), Task.agent == "video"))
        assert "reuse_script" not in task.params and "instruction" not in task.params


# --- event posters ------------------------------------------------------------------
def _png(color=(220, 30, 30, 255), size=(300, 120)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def _upload(client, auth, name, data=None):
    resp = client.post("/v1/assets", files={"file": (f"{name}.png", data or _png(), "image/png")}, data={"name": name},
                       headers=auth)
    return resp


POSTER = {"theme": "gold", "tag": "ROV", "title": "Valor City", "edition": "2026",
          "date_text": "วันอาทิตย์ที่ 3 พฤษภาคม 2569", "headline": ["ภาคใต้ตอนบน รอบ 8 ทีมสุดท้าย", "สายแข่งศูนย์สุราษฎร์ธานี"],
          "venue": "ห้างโลตัส สุราษฎร์ธานี (ชั้น 3)", "time_text": "10.00 น. เป็นต้นไป"}


def test_logo_library_upload_list_view_delete(client, auth):
    resp = _upload(client, auth, "Sponsor A")
    assert resp.status_code == 201, resp.text
    asset = resp.json()
    assert asset["name"] == "Sponsor A" and (asset["width"], asset["height"]) == (300, 120)
    assert [a["id"] for a in client.get("/v1/assets", headers=auth).json()] == [asset["id"]]
    img = client.get(asset["url"])  # signed link, no auth header
    assert img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get(asset["url"].replace("sig=", "sig=0")).status_code == 403
    # an artifact signature never opens an asset
    from app.security import sign_artifact_url

    forged = sign_artifact_url(uuid.UUID(asset["id"])).split("?")[1]
    assert client.get(f"/v1/assets/{asset['id']}/file?{forged}").status_code == 403
    bad = client.post("/v1/assets", files={"file": ("x.png", b"not an image", "image/png")}, headers=auth)
    assert bad.status_code == 422
    assert client.delete(f"/v1/assets/{asset['id']}", headers=auth).status_code == 204
    assert client.get("/v1/assets", headers=auth).json() == []


def test_poster_job_typesets_text_and_logos(client, auth, fake_openai):
    sponsor = _upload(client, auth, "Sponsor").json()
    presenter = _upload(client, auth, "Presenter", _png((40, 90, 220, 255), (160, 160))).json()
    poster = {**POSTER, "sponsor_logo_ids": [sponsor["id"]], "presenter_logo_ids": [presenter["id"]], "variants": 2}
    job = submit(client, auth, command="โปสเตอร์งานแข่ง ROV Valor City", poster=poster, require_approval=False)
    assert job["requested_agents"] == ["image"] and job["aspect_ratio"] == "9:16"
    assert job["status"] == "completed", job
    assert set(tasks_of(client, auth, job["id"])) == {"plan", "poster_1", "poster_2", "qa_review"}
    images = [a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "image"]
    assert sorted(a["filename"] for a in images) == ["event-poster-01.png", "event-poster-02.png"]
    meta = images[0]["metadata"]
    assert (meta["width"], meta["height"]) == (1080, 1920) and meta["poster"]["title"] == "Valor City"
    assert "No text" in meta["prompt"] and "not based on any existing game" in meta["prompt"]
    png = Image.open(io.BytesIO(client.get(images[0]["download_url"], headers=auth).content)).convert("RGB")
    # the date banner is white and the sponsor bar holds the red logo
    assert any(png.getpixel((x, y)) == (220, 30, 30) for x in range(0, 1080, 4) for y in range(10, 110, 4))
    checks = client.get(f"/v1/jobs/{job['id']}/qa", headers=auth).json()["checks"]
    assert any(c["name"] == "poster:layout" and c["status"] == "pass" for c in checks)


def test_poster_rejects_logos_from_outside_the_library(client, auth):
    resp = client.post("/v1/jobs", json={"command": "poster test", "poster": {**POSTER, "sponsor_logo_ids": [str(uuid.uuid4())]}},
                       headers=auth)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "ASSET_NOT_FOUND"
    too_long = {**POSTER, "headline": ["ก" * 60]}
    assert client.post("/v1/jobs", json={"command": "poster test", "poster": too_long}, headers=auth).status_code == 422


def test_poster_layout_draws_every_layer():
    from app.agents.poster import H, W, compose, difference

    art = Image.new("RGB", (768, 1344), (90, 70, 40))
    bare = compose(art, {"title": "X"}, {})
    full = compose(art, {**POSTER, "sponsor_logo_ids": ["a"], "presenter_logo_ids": ["b"]},
                   {"a": Image.new("RGBA", (200, 80), (0, 200, 0, 255)), "b": Image.new("RGBA", (90, 90), (0, 0, 255, 255))})
    assert full.size == bare.size == (W, H)
    assert difference(full, bare) > 5
    assert full.getpixel((540, 1880)) != (0, 0, 255)  # the presenter row stays above the bottom margin


def test_poster_preview_is_instant_and_free(client, auth):
    sponsor = _upload(client, auth, "Sponsor").json()
    resp = client.post("/v1/posters/preview", json={**POSTER, "sponsor_logo_ids": [sponsor["id"]]}, headers=auth)
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(resp.content))
    assert img.size == (540, 960)
    # the red sponsor logo is in the white bar at the top (half scale)
    assert any(abs(img.convert("RGB").getpixel((x, y))[0] - 220) < 30 and img.convert("RGB").getpixel((x, y))[1] < 70
               for x in range(0, 540, 3) for y in range(8, 52, 3))
    assert client.post("/v1/posters/preview", json={**POSTER, "title": ""}, headers=auth).status_code == 422
    assert client.post("/v1/posters/preview", json=POSTER).status_code == 401
    with session_scope() as s:  # nothing stored, nothing billed
        assert s.scalar(select(func.count()).select_from(UsageEvent)) == 0
        assert s.scalar(select(func.count()).select_from(Job)) == 0


# --- Adobe Firefly ------------------------------------------------------------------
def _firefly_settings(client, auth, **body):
    resp = client.put("/v1/settings/providers/firefly", json=body, headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_firefly_is_the_fallback_after_local_comfyui(client, auth, fake_openai, fake_comfyui, fake_firefly):
    job = submit(client, auth, command="Make 1 promotional image", agents=["image"], require_approval=False)
    assert tasks_of(client, auth, job["id"])["image_1"]["provider"] == "comfyui"  # local GPU first by default
    assert fake_firefly.bodies == []

    from app.services import router as hybrid_router

    hybrid_router.TRANSPORTS["comfyui"] = httpx.MockTransport(lambda r: httpx.Response(503))
    job = submit(client, auth, command="Make 1 promotional image", agents=["image"], require_approval=False)
    assert job["status"] == "completed", job
    task = tasks_of(client, auth, job["id"])["image_1"]
    assert (task["provider"], task["execution_location"]) == ("firefly", "cloud")
    assert "authorized cloud fallback" in task["routing_reason"]
    img = next(a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "image")
    assert img["metadata"]["provider"] == "firefly" and img["metadata"]["firefly_size"] == "2688x1536"
    body = fake_firefly.bodies[0]
    assert body["contentClass"] == "art" and body["numVariations"] == 1 and "No text" in body["prompt"]
    assert len(body["prompt"]) <= 1024 and "watermark" in body["negativePrompt"] and body["_model"] is None
    assert not any(p.endswith("/images/generations") for p in fake_openai.calls)  # Firefly before OpenAI
    usage = client.get("/v1/usage", headers=auth).json()
    assert any(b["provider"] == "firefly" and b["cost_usd"] > 0 for b in usage["by_provider"])


def test_firefly_first_with_image4_makes_native_portrait_posters(client, auth, fake_comfyui, fake_firefly):
    view = _firefly_settings(client, auth, priority="first", model="image4_standard", content_class="photo")
    assert view["configured"] and view["healthy"] and view["options"]["priority"] == "first"
    job = submit(client, auth, command="poster", poster={**POSTER, "variants": 2}, require_approval=False)
    assert job["status"] == "completed", job
    assert fake_comfyui.prompts == 0  # healthy local GPU skipped: the organization chose Firefly
    assert [b["size"] for b in fake_firefly.bodies] == [{"width": 1440, "height": 2560}] * 2
    assert {b["_model"] for b in fake_firefly.bodies} == {"image4_standard"}
    assert {b["contentClass"] for b in fake_firefly.bodies} == {"photo"}
    assert fake_firefly.tokens == 1  # the health check signed in once; both posters reused that token
    meta = next(a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "image")["metadata"]
    assert (meta["width"], meta["height"]) == (1080, 1920) and meta["firefly_model"] == "image4_standard"


def test_firefly_never_gets_restricted_data_and_never_leaks_its_token(client, auth, fake_comfyui, fake_firefly):
    _firefly_settings(client, auth, priority="first")
    job = submit(client, auth, command="Make 1 promotional image", agents=["image"], require_approval=False,
                 data_classification="restricted")
    assert tasks_of(client, auth, job["id"])["image_1"]["provider"] == "comfyui"
    assert fake_firefly.bodies == []
    # a status URL on another host is ignored: the token only ever goes to the Firefly API host
    fake_firefly.status_host = "evil.test"
    job = submit(client, auth, command="Make 1 promotional image", agents=["image"], require_approval=False)
    assert job["status"] == "completed", job
    assert all(c.url.host in ("firefly.test", "ims.test", "presigned.test") for c in fake_firefly.calls)


def test_firefly_errors_are_explained(client, auth, fake_firefly):
    _firefly_settings(client, auth, priority="first")
    fake_firefly.fail_job = True
    job = submit(client, auth, command="Make 1 promotional image", agents=["image"], require_approval=False)
    task = tasks_of(client, auth, job["id"])["image_1"]
    assert task["error_code"] == "PROVIDER_REQUEST_REJECTED" and "prompt blocked" in task["error_message"]
    assert task["attempt"] == 1  # a blocked prompt is not retried

    from app.providers import firefly as firefly_provider

    firefly_provider.clear_token_cache()
    fake_firefly.fail_token = True
    view = [p for p in client.get("/v1/settings", headers=auth).json()["providers"] if p["name"] == "firefly"][0]
    assert view["healthy"] is False and "Client ID and Client Secret" in view["detail"]


def test_firefly_settings_validation_and_secrecy(client, auth, fresh_state):
    view = [p for p in client.get("/v1/settings", headers=auth).json()["providers"] if p["name"] == "firefly"][0]
    assert view["configured"] is False and view["options"]["priority"] == "fallback"
    bad = [({"model": "image9"}, "firefly"), ({"client_id": "has spaces in it"}, "firefly"),
           ({"api_key": "abcd...wxyz"}, "firefly"), ({"base_url": "https://x.test"}, "firefly"),
           ({"priority": "first"}, "openai")]
    for body, name in bad:
        assert client.put(f"/v1/settings/providers/{name}", json=body, headers=auth).status_code == 422, body
    fresh_state.firefly_ims_url = "https://ims.test/ims/token/v3"
    view = _firefly_settings(client, auth, client_id="ff-client-id-1", api_key="ff-secret-123")
    assert view["configured"] and view["options"]["client_id"] == "ff-c…id-1"
    raw = json.dumps(client.get("/v1/settings", headers=auth).json())
    assert "ff-secret-123" not in raw and "ff-client-id-1" not in raw
    with session_scope() as s:
        events = [e.detail for e in s.scalars(select(AuditLog).where(AuditLog.action == "settings.provider_updated"))]
    assert "ff-secret-123" not in json.dumps(events)


# --- your own poster artwork ------------------------------------------------------------
def _artwork(client, auth, size=(1440, 2560), color=(10, 160, 90)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return client.post("/v1/assets", files={"file": ("firefly-art.png", buf.getvalue(), "image/png")},
                       data={"name": "Firefly art", "kind": "artwork"}, headers=auth)


def test_artwork_library_is_separate_from_logos(client, auth):
    art = _artwork(client, auth)
    assert art.status_code == 201, art.text
    a = art.json()
    assert a["kind"] == "artwork" and a["content_type"] == "image/jpeg" and (a["width"], a["height"]) == (1440, 2560)
    logo = _upload(client, auth, "Sponsor").json()
    assert logo["kind"] == "logo"
    assert [x["id"] for x in client.get("/v1/assets?kind=artwork", headers=auth).json()] == [a["id"]]
    assert [x["id"] for x in client.get("/v1/assets?kind=logo", headers=auth).json()] == [logo["id"]]
    img = client.get(a["url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert _artwork(client, auth, size=(300, 400)).status_code == 422  # too small for a poster
    # a logo cannot be used as artwork
    bad = client.post("/v1/jobs", json={"command": "poster test", "poster": {**POSTER, "art_asset_id": logo["id"]}},
                      headers=auth)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "ASSET_NOT_FOUND"


def test_poster_with_own_artwork_skips_ai_and_costs_nothing(client, auth, fake_openai, fake_comfyui, fake_firefly):
    art = _artwork(client, auth).json()
    job = submit(client, auth, command="poster", poster={**POSTER, "art_asset_id": art["id"], "variants": 3},
                 require_approval=False)
    assert job["status"] == "completed", job
    assert set(tasks_of(client, auth, job["id"])) == {"plan", "poster_1", "qa_review"}  # one version only
    task = tasks_of(client, auth, job["id"])["poster_1"]
    assert (task["provider"], task["execution_location"]) == ("uploaded", "local")
    assert fake_comfyui.prompts == 0 and fake_firefly.bodies == []
    assert not any(p.endswith("/images/generations") for p in fake_openai.calls)
    assert job["estimated_cost_usd"] == 0 and job["actual_cost_usd"] == 0
    img = next(a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "image")
    assert img["metadata"]["artwork"] == "uploaded" and img["metadata"]["license"] == "user-supplied"
    png = Image.open(io.BytesIO(client.get(img["download_url"], headers=auth).content)).convert("RGB")
    assert png.size == (1080, 1920)
    # the artwork is used as framed (not lowered): its colour reaches the top edge under the dark grade
    r, g, b = png.getpixel((540, 3))
    assert g > r and g > b
    # regenerating would give the same poster
    assert client.post(f"/v1/artifacts/{img['id']}/regenerate", json={}, headers=auth).status_code == 409


def test_poster_preview_uses_own_artwork(client, auth):
    art = _artwork(client, auth, color=(200, 20, 200)).json()
    resp = client.post("/v1/posters/preview", json={**POSTER, "art_asset_id": art["id"]}, headers=auth)
    assert resp.status_code == 200
    r, g, b = Image.open(io.BytesIO(resp.content)).convert("RGB").getpixel((270, 560))
    assert r > g and b > g  # magenta artwork shows through mid-poster


def test_deleted_artwork_fails_clearly(client, auth):
    art = _artwork(client, auth).json()
    from app.models import BrandAsset

    resp = client.post("/v1/jobs", json={"command": "poster test", "poster": {**POSTER, "art_asset_id": art["id"]},
                                         "require_approval": False, "auto_start": False}, headers=auth)
    assert resp.status_code == 202
    with session_scope() as s:  # deleted after the job was accepted
        s.delete(s.get(BrandAsset, uuid.UUID(art["id"])))
    assert client.post(f"/v1/jobs/{resp.json()['id']}/plan", headers=auth).status_code == 202
    assert client.post(f"/v1/jobs/{resp.json()['id']}/dispatch", headers=auth).status_code == 202
    task = tasks_of(client, auth, resp.json()["id"])["poster_1"]
    assert task["error_code"] == "ASSET_NOT_FOUND" and task["attempt"] == 1


# --- OpenAI image priority -------------------------------------------------------------------
def test_openai_images_before_comfyui_when_chosen(client, auth, fake_openai, fake_comfyui):
    view = [p for p in client.get("/v1/settings", headers=auth).json()["providers"] if p["name"] == "openai"][0]
    assert view["options"]["image_priority"] == "fallback" and "after ComfyUI" in view["detail"]
    resp = client.put("/v1/settings/providers/openai", json={"image_priority": "first"}, headers=auth)
    assert resp.status_code == 200 and resp.json()["options"]["image_priority"] == "first"

    job = submit(client, auth, command="Make 1 promotional image", agents=["image"], require_approval=False)
    task = tasks_of(client, auth, job["id"])["image_1"]
    assert (task["provider"], task["execution_location"]) == ("openai", "cloud") and "preferred provider" in task["routing_reason"]
    assert fake_comfyui.prompts == 0 and any(p.endswith("/images/generations") for p in fake_openai.calls)
    assert job["estimated_cost_usd"] > 0

    # restricted data still stays on the local GPU
    job = submit(client, auth, command="Make 1 promotional image", agents=["image"], require_approval=False,
                 data_classification="restricted")
    assert tasks_of(client, auth, job["id"])["image_1"]["provider"] == "comfyui"

    # back to fallback: ComfyUI first again
    client.put("/v1/settings/providers/openai", json={"image_priority": "fallback"}, headers=auth)
    job = submit(client, auth, command="Make 1 promotional image", agents=["image"], require_approval=False)
    assert tasks_of(client, auth, job["id"])["image_1"]["provider"] == "comfyui"


def test_firefly_first_wins_over_openai_first(client, auth, fake_openai, fake_comfyui, fake_firefly):
    client.put("/v1/settings/providers/openai", json={"image_priority": "first"}, headers=auth)
    _firefly_settings(client, auth, priority="first")
    job = submit(client, auth, command="Make 1 promotional image", agents=["image"], require_approval=False)
    assert tasks_of(client, auth, job["id"])["image_1"]["provider"] == "firefly"
    for name in ("comfyui", "firefly"):
        assert client.put(f"/v1/settings/providers/{name}", json={"image_priority": "first"},
                          headers=auth).status_code == 422


# --- poster design chat --------------------------------------------------------------------
def test_poster_chat_fills_and_edits_the_draft(client, auth, fake_openai):
    sponsor = _upload(client, auth, "Sponsor A").json()
    first = client.post("/v1/posters/chat", json={"messages": [
        {"role": "user", "content": "ทำโปสเตอร์แข่ง ROV Valor City 2026 วันอาทิตย์ที่ 3 พ.ค. 2569"}]}, headers=auth)
    assert first.status_code == 200, first.text
    out = first.json()
    draft = out["draft"]
    assert draft["title"] == "Valor City" and draft["theme"] == "gold" and out["ready"] is True
    assert draft["sponsor_logo_ids"] == [sponsor["id"]]  # names mapped to ids; nothing invented
    assert out["unknown_assets"] == ["Ghost Sponsor"] and draft["art_asset_id"] is None
    assert out["reply"]

    # second turn: change only the theme; the rest of the draft is kept
    second = client.post("/v1/posters/chat", json={"messages": [
        {"role": "user", "content": "ทำโปสเตอร์แข่ง ROV"}, {"role": "assistant", "content": out["reply"]},
        {"role": "user", "content": "เปลี่ยนเป็นโทนนีออน"}], "draft": draft}, headers=auth)
    d2 = second.json()["draft"]
    assert d2["theme"] == "neon" and d2["title"] == "Valor City" and d2["sponsor_logo_ids"] == [sponsor["id"]]
    # the draft is a valid poster: it can be previewed (and created) as-is
    assert client.post("/v1/posters/preview", json=d2, headers=auth).status_code == 200
    usage = client.get("/v1/usage", headers=auth).json()
    assert any(b["agent"] == "assistant" and b["events"] == 2 for b in usage["by_agent"])


def test_poster_chat_needs_openai(client, auth):
    resp = client.post("/v1/posters/chat", json={"messages": [{"role": "user", "content": "poster"}]}, headers=auth)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED"


def test_poster_chat_empty_answer_never_wipes_the_form(client, auth, fake_openai):
    draft = {"theme": "neon", "tag": "ROV", "title": "Valor City", "edition": "2026", "date_text": "3 พ.ค. 2569",
             "headline": ["รอบ 8 ทีมสุดท้าย", ""], "venue": "โลตัส", "time_text": "10.00 น.", "art": "a cyber swordsman"}
    resp = client.post("/v1/posters/chat", json={"messages": [{"role": "user", "content": "EMPTY_ANSWER"}],
                                                 "draft": draft}, headers=auth)
    out = resp.json()
    assert resp.status_code == 200
    kept = out["draft"]
    assert (kept["title"], kept["theme"], kept["tag"], kept["art"]) == ("Valor City", "neon", "ROV", "a cyber swordsman")
    assert kept["headline"] == ["รอบ 8 ทีมสุดท้าย"] and "ไม่ได้เปลี่ยน" in out["reply"]


def test_poster_art_starts_under_the_title():
    """The artwork is moved so its subject starts just below the title: up to close an empty gap,
    down when the model painted the subject behind the title."""
    from PIL import Image, ImageDraw

    from app.agents import poster as P

    def art(subject_top: int) -> Image.Image:
        im = Image.new("RGB", (P.W, P.H), (6, 8, 20))
        d = ImageDraw.Draw(im)
        for x in range(0, P.W, 24):  # a busy, high-contrast "subject" from subject_top down
            d.rectangle((x, subject_top, x + 11, P.H), fill=(230, 220, 255))
        return im

    assert abs(P._subject_top(art(900)) - 900) <= 30
    assert P._art_shift(art(900), title_bottom=400, text_top=1300) == -260  # moved up (as far as allowed)
    assert P._art_shift(art(150), title_bottom=400, text_top=1300) > 250  # moved down
    assert P._art_shift(Image.new("RGB", (P.W, P.H), (40, 40, 40)), 400, 1300) == 200  # no subject: fixed drop
    assert P._lower(art(900), -260).size == (P.W, P.H)


TOURNAMENT = {
    "template": "tournament", "title": "Surat Esports", "edition": "2026",
    "subtitle": "โครงการจัดแข่งขันกีฬาอีสปอร์ตจังหวัดสุราษฎร์ธานี", "prize_label": "ชิงเงินรางวัลรวมกว่า",
    "prize_amount": "8,000 บาท", "registration_text": "เปิดรับสมัครถึงวันที่ 6 สิงหาคม 2569 (ออนไลน์)",
    "badge_text": "สมัครฟรี! ไม่มีค่าใช้จ่าย",
    "category_lines": ["รุ่นประชาชนทั่วไป *ประเภทบุคคล*", "อายุ 13 ปี - 60 ปี (จำกัด 16 ทีม)"],
    "schedule": [{"label": "ชี้แจง กฎ กติกา จับฉลาก", "big": "7", "lines": ["สิงหาคม 2569", "เวลา 17.00 น."]},
                 {"label": "แข่งขัน", "big": "8-9", "lines": ["สิงหาคม", "2569"]}],
    "cta_text": "สมัครเลย", "qr_url": "https://example.com/register",
    "venue": "ณ ลานโปรโมชัน ชั้น 1", "venue_detail": "ห้างสหไทย การ์เด้น พลาซ่า",
}


def test_tournament_poster_job_is_4_by_5_with_qr_and_sponsor_strip(client, auth, fake_openai):
    game = _upload(client, auth, "Game").json()
    sponsor = _upload(client, auth, "Sponsor").json()
    poster = {**TOURNAMENT, "game_logo_id": game["id"], "sponsor_logo_ids": [sponsor["id"]]}
    job = submit(client, auth, command="โปสเตอร์ทัวร์นาเมนต์", poster=poster, require_approval=False)
    assert job["status"] == "completed", job
    assert job["aspect_ratio"] == "4:5"
    image = next(a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "image")
    meta = image["metadata"]
    assert (meta["width"], meta["height"]) == (1600, 2000) and meta["aspect_ratio"] == "4:5"
    assert meta["poster"]["template"] == "tournament" and "energy orb" in meta["prompt"]
    png = Image.open(io.BytesIO(client.get(image["download_url"], headers=auth).content)).convert("RGB")
    assert png.getpixel((800, 1990)) == (0, 0, 0)  # black sponsor strip
    assert any(png.getpixel((x, 1960)) == (220, 30, 30) for x in range(0, 1600, 2))  # the red sponsor logo in it
    qr = [png.getpixel((x, y)) for x in range(1180, 1540, 6) for y in range(1530, 1800, 6)]
    assert (0, 0, 0) in qr and (255, 255, 255) in qr  # a QR code: black modules on white


def test_tournament_preview_is_4_by_5(client, auth):
    resp = client.post("/v1/posters/preview", json=TOURNAMENT, headers=auth)
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(resp.content)).size == (540, 675)


def test_tournament_fields_are_validated(client, auth):
    bad_link = {**TOURNAMENT, "qr_url": "javascript:alert(1)"}
    assert client.post("/v1/posters/preview", json=bad_link, headers=auth).status_code == 422
    too_many = {**TOURNAMENT, "schedule": TOURNAMENT["schedule"] * 2}
    assert client.post("/v1/posters/preview", json=too_many, headers=auth).status_code == 422


def test_poster_chat_tournament_category_never_lost_in_the_headline(client, auth, fake_openai):
    resp = client.post("/v1/posters/chat", json={"messages": [{"role": "user", "content": "TOURNAMENT_MIXUP"}]},
                       headers=auth)
    assert resp.status_code == 200, resp.text
    draft = resp.json()["draft"]
    assert draft["template"] == "tournament"
    assert draft["category_lines"] == ["รุ่นประชาชนทั่วไป *ประเภทบุคคล*", "อายุ 13-60 ปี"]
    assert draft["schedule"] == [{"label": "แข่งขัน", "big": "8-9", "lines": ["สิงหาคม", "2569"]}]
    assert client.post("/v1/posters/preview", json=draft, headers=auth).status_code == 200


def test_ai_poster_keeps_its_artwork_for_free_relayouts(client, auth, fake_openai):
    first = submit(client, auth, command="โปสเตอร์ทัวร์นาเมนต์", poster=TOURNAMENT, require_approval=False)
    assert first["status"] == "completed", first
    image = next(a for a in artifacts_of(client, auth, first["id"]) if a["kind"] == "image")
    art_id = image["metadata"]["artwork_asset_id"]
    assert image["metadata"]["artwork"] == "ai"
    saved = next(a for a in client.get("/v1/assets?kind=artwork", headers=auth).json() if a["id"] == art_id)
    assert saved["source"] == "ai" and saved["name"].startswith("AI art · Surat Esports")
    calls = len(fake_openai.calls)

    # Re-lay it out with a new prize and link: same artwork, no new image, no cost.
    again = submit(client, auth, command="โปสเตอร์ทัวร์นาเมนต์ แก้ไข", require_approval=False,
                   poster={**TOURNAMENT, "prize_amount": "9,000 บาท", "qr_url": "https://example.org/new",
                           "art_asset_id": art_id})
    assert again["status"] == "completed", again
    redo = next(a for a in artifacts_of(client, auth, again["id"]) if a["kind"] == "image")
    assert redo["metadata"]["artwork"] == "reused" and redo["metadata"]["artwork_asset_id"] == art_id
    assert not any("images/generations" in c for c in fake_openai.calls[calls:])
    assert float(again["estimated_cost_usd"]) == 0
    # a reused-artwork poster is not "regenerated" (it would come out the same): make a new poster instead
    resp = client.post(f"/v1/artifacts/{redo['id']}/regenerate", json={}, headers=auth)
    assert resp.status_code == 409


def test_poster_artwork_can_be_moved_and_resized(client, auth):
    from PIL import ImageDraw

    from app.agents.poster import difference, frame_art

    art = Image.new("RGB", (900, 1600), (20, 20, 40))
    ImageDraw.Draw(art).ellipse((350, 300, 550, 500), fill=(250, 220, 180))  # a "head"
    centred = frame_art(art, 1080, 1920)
    lower = frame_art(art, 1080, 1920, 1.0, 0.0, 0.2)
    bigger = frame_art(art, 1080, 1920, 1.4, 0.0, 0.0)
    assert centred.size == lower.size == bigger.size == (1080, 1920)
    assert difference(centred, lower) > 2 and difference(centred, bigger) > 2
    # the head really moved down by about a fifth of the frame
    head_y = lambda im: min(y for y in range(0, 1920, 8) if im.getpixel((540, y))[0] > 200)  # noqa: E731
    assert abs((head_y(lower) - head_y(centred)) - 384) < 24
    spec = {**POSTER, "art_y": 0.2, "art_zoom": 1.2}
    assert client.post("/v1/posters/preview", json=spec, headers=auth).status_code == 200
    assert client.post("/v1/posters/preview", json={**POSTER, "art_zoom": 9}, headers=auth).status_code == 422


def test_tournament_with_own_characters_needs_no_ai_image(client, auth, fake_openai):
    left = _upload(client, auth, "Hero Left", _png((40, 90, 220, 255), (300, 500))).json()
    right = _upload(client, auth, "Hero Right", _png((220, 40, 40, 255), (300, 500))).json()
    calls = len(fake_openai.calls)
    job = submit(client, auth, command="โปสเตอร์ทัวร์นาเมนต์ตัวละคร", require_approval=False,
                 poster={**TOURNAMENT, "hero_left_id": left["id"], "hero_right_id": right["id"]})
    assert job["status"] == "completed", job
    assert float(job["estimated_cost_usd"]) == 0
    assert not any("images/generations" in c for c in fake_openai.calls[calls:])
    image = next(a for a in artifacts_of(client, auth, job["id"]) if a["kind"] == "image")
    assert image["metadata"]["artwork"] == "composed" and (image["metadata"]["width"], image["metadata"]["height"]) == (1600, 2000)
    png = Image.open(io.BytesIO(client.get(image["download_url"], headers=auth).content)).convert("RGB")
    # the left character is on the left, the right one on the right
    assert png.getpixel((120, 520))[2] > 150 and png.getpixel((1480, 520))[0] > 150
    assert client.post(f"/v1/artifacts/{image['id']}/regenerate", json={}, headers=auth).status_code == 409


# --- roles and delivery gating (Phase 0) -------------------------------------------------
def _member_auth(client, org_id, email="member@example.com"):
    from app.db import session_scope
    from app.models import Membership, User
    from app.security import hash_password
    from tests.conftest import PASSWORD

    with session_scope() as session:
        user = User(email=email, full_name="Team Member", password_hash=hash_password(PASSWORD))
        session.add(user)
        session.flush()
        session.add(Membership(organization_id=org_id, user_id=user.id, role="member"))
    token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_only_owners_and_admins_approve_jobs_or_delete_assets(client, auth, org_a, fake_openai):
    member = _member_auth(client, org_a["org_id"])
    job = submit(client, auth, require_approval=True)
    assert job["status"] == "awaiting_approval", job
    resp = client.post(f"/v1/jobs/{job['id']}/approve", json={"decision": "approved"}, headers=member)
    assert resp.status_code == 403
    asset = _upload(client, auth, "Logo").json()
    assert client.delete(f"/v1/assets/{asset['id']}", headers=member).status_code == 403
    # members can still create and review work
    assert client.get(f"/v1/jobs/{job['id']}/artifacts", headers=member).status_code == 200
    assert client.post(f"/v1/jobs/{job['id']}/approve", json={"decision": "approved"}, headers=auth).status_code == 200
    assert client.delete(f"/v1/assets/{asset['id']}", headers=auth).status_code == 204


def test_service_api_keys_run_jobs_but_never_act_as_admins(client, auth, org_a, fake_openai):
    """Found in the n8n end-to-end run: API keys were treated as admins, so n8n could approve its own jobs."""
    key = {"X-API-Key": org_a["api_key"]}
    job = submit(client, key, require_approval=True)  # automations can still submit and follow work
    assert job["status"] == "awaiting_approval", job
    assert client.get(f"/v1/jobs/{job['id']}/tasks", headers=key).status_code == 200
    assert client.post(f"/v1/jobs/{job['id']}/events", json={"action": "n8n.workflow_completed", "detail": {}},
                       headers=key).status_code == 201
    for decision in ("approved", "rejected"):
        resp = client.post(f"/v1/jobs/{job['id']}/approve", json={"decision": decision}, headers=key)
        assert resp.status_code == 403 and resp.json()["error"]["code"] == "FORBIDDEN"
    asset = _upload(client, auth, "Logo").json()
    assert client.delete(f"/v1/assets/{asset['id']}", headers=key).status_code == 403
    assert client.delete(f"/v1/jobs/{job['id']}", headers=key).status_code == 403
    assert client.delete("/v1/jobs", headers=key).status_code == 403
    assert client.delete("/v1/artifacts", headers=key).status_code == 403
    assert client.patch("/v1/settings/organization", json={"monthly_budget_usd": 99999}, headers=key).status_code == 403
    assert client.put("/v1/settings/providers/openai", json={"api_key": "sk-" + "x" * 40}, headers=key).status_code == 403
    # a signed-in owner still approves
    assert client.post(f"/v1/jobs/{job['id']}/approve", json={"decision": "approved"}, headers=auth).status_code == 200


def test_automations_get_no_file_links_until_the_job_is_approved(client, auth, org_a, fake_openai):
    key = {"X-API-Key": org_a["api_key"]}
    job = submit(client, auth, require_approval=True)
    assert job["status"] == "awaiting_approval", job
    by_key = client.get(f"/v1/jobs/{job['id']}/artifacts", headers=key).json()["items"]
    assert by_key and all(a["released"] is False and a["download_url"] is None for a in by_key)
    by_person = artifacts_of(client, auth, job["id"])
    assert all(a["released"] is False and a["download_url"] for a in by_person)  # reviewers still see drafts
    client.post(f"/v1/jobs/{job['id']}/approve", json={"decision": "approved"}, headers=auth)
    after = client.get(f"/v1/jobs/{job['id']}/artifacts", headers=key).json()["items"]
    assert all(a["released"] is True and a["download_url"] for a in after)
    # rejected work is never released
    job2 = submit(client, auth, require_approval=True, command="Create 1 promotional image for MCR SME Copilot")
    client.post(f"/v1/jobs/{job2['id']}/approve", json={"decision": "rejected"}, headers=auth)
    rejected = client.get(f"/v1/jobs/{job2['id']}/artifacts", headers=key).json()["items"]
    assert all(a["released"] is False and a["download_url"] is None for a in rejected)


def test_worker_heartbeats_show_which_queues_have_no_worker(client, auth, fresh_state):
    from datetime import timedelta

    from app.db import session_scope
    from app.models import WorkerHeartbeat, utcnow
    from app.services.workers import beat, queue_status

    assert client.get("/v1/dashboard/summary", headers=auth).json()["offline_queues"] == []  # eager: inline
    fresh_state.celery_task_always_eager = False
    try:
        summary = client.get("/v1/dashboard/summary", headers=auth).json()
        assert set(summary["offline_queues"]) == {"planner", "content", "image", "document", "video", "qa"}
        beat("host:1", ["planner", "content", "image"])
        with session_scope() as session:
            session.add(WorkerHeartbeat(name="host:2", queues=["video"], last_seen=utcnow() - timedelta(minutes=5)))
        with session_scope() as session:
            status = queue_status(session)
        assert status["image"] == 1 and status["video"] == 0  # the video worker's heartbeat is stale
        summary = client.get("/v1/dashboard/summary", headers=auth).json()
        assert set(summary["offline_queues"]) == {"document", "video", "qa"}
    finally:
        fresh_state.celery_task_always_eager = True


def test_drafts_never_download_through_signed_links(client, auth, org_a, fake_openai):
    from app.security import sign_artifact_url

    key = {"X-API-Key": org_a["api_key"]}
    job = submit(client, auth, require_approval=True)
    assert job["status"] == "awaiting_approval", job
    arts = artifacts_of(client, auth, job["id"])
    cap = next(a for a in arts if a["kind"] == "caption")
    # reviewers get the authenticated preview path, not a signed link
    assert cap["download_url"] == f"/v1/artifacts/{cap['id']}/preview" and cap["text_preview"]
    # a freshly signed link (e.g. leaked or minted earlier) is refused while the job is a draft
    signed = sign_artifact_url(uuid.UUID(cap["id"]))
    denied = client.get(signed)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "NOT_RELEASED"
    # preview: needs a signed-in person of the same org; API keys and strangers are refused
    assert client.get(cap["download_url"]).status_code == 401
    assert client.get(cap["download_url"], headers=key).status_code == 403
    ok = client.get(cap["download_url"], headers=auth)
    assert ok.status_code == 200 and "no-store" in ok.headers["cache-control"]
    # automations see neither link nor text of drafts
    by_key = next(a for a in client.get(f"/v1/jobs/{job['id']}/artifacts", headers=key).json()["items"] if a["id"] == cap["id"])
    assert by_key["download_url"] is None and by_key["text_preview"] is None
    # after approval the signed link works for everyone holding it
    client.post(f"/v1/jobs/{job['id']}/approve", json={"decision": "approved"}, headers=auth)
    released = next(a for a in client.get(f"/v1/jobs/{job['id']}/artifacts", headers=key).json()["items"] if a["id"] == cap["id"])
    assert "sig=" in released["download_url"] and client.get(released["download_url"]).status_code == 200
    assert client.get(signed).status_code == 200  # the earlier link now works too


def test_draft_preview_is_scoped_to_the_organisation(client, auth, org_b, fake_openai):
    job = submit(client, auth, require_approval=True)
    art = artifacts_of(client, auth, job["id"])[0]
    other = client.post("/v1/auth/login", json={"email": org_b["email"], "password": "correct-horse-battery"}).json()
    assert client.get(art["download_url"], headers={"Authorization": f"Bearer {other['access_token']}"}).status_code == 404


# --- reliability: long renders keep their lease (Phase 0) ----------------------------------
SLOW_RENDER = ["-y", "-re", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "3", "-f", "null", "-"]


def test_long_ffmpeg_renders_keep_calling_the_heartbeat():
    import time

    from app.services.media import run_ffmpeg

    beats = []
    started = time.monotonic()
    run_ffmpeg(SLOW_RENDER, on_wait=lambda: beats.append(time.monotonic()), every=0.5)
    assert time.monotonic() - started >= 2.5
    assert len(beats) >= 3  # about one beat per 0.5 s while FFmpeg ran


def test_a_cancelled_job_stops_ffmpeg_promptly():
    import time

    from app.agents.base import JobCancelled
    from app.services.media import run_ffmpeg

    def cancel():
        raise JobCancelled()

    started = time.monotonic()
    with pytest.raises(JobCancelled):
        run_ffmpeg(SLOW_RENDER, on_wait=cancel, every=0.3)
    assert time.monotonic() - started < 2  # killed at the first beat, not after the 3 s render


def test_ffmpeg_timeout_still_applies():
    from app.errors import AgentError
    from app.services.media import run_ffmpeg

    with pytest.raises(AgentError) as err:
        run_ffmpeg(SLOW_RENDER, timeout=1, on_wait=lambda: None, every=0.2)
    assert err.value.code == "FFMPEG_TIMEOUT" and err.value.retryable


def test_video_task_lease_is_extended_during_the_final_render(client, auth, fake_openai, monkeypatch):
    """The real video agent passes its heartbeat to the final FFmpeg render."""
    from app.agents import video as video_agent

    seen = {}
    real = video_agent.run_ffmpeg

    def spy(args, timeout=None, **kw):
        if "libx264" in args:
            seen["on_wait"] = kw.get("on_wait")
        return real(args, timeout, **kw)

    monkeypatch.setattr(video_agent, "run_ffmpeg", spy)
    job = submit(client, auth, command="Create a 15 second promo video for MCR SME Copilot", require_approval=False)
    assert job["status"] in ("completed", "awaiting_approval", "partial"), job
    assert seen.get("on_wait") is not None and getattr(seen["on_wait"], "__name__", "") == "heartbeat"
