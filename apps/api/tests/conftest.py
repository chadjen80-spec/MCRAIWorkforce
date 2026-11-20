from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import httpx
import pytest

# Environment must be set before the app (and Celery) are imported.
_TMP = Path(tempfile.mkdtemp(prefix="mcr-tests-"))
os.environ.update({
    "ENVIRONMENT": "test",
    "DATABASE_URL": f"sqlite:///{(_TMP / 'boot.db').as_posix()}",
    "DATA_DIR": str(_TMP),
    "CELERY_TASK_ALWAYS_EAGER": "true",
    "CELERY_BROKER_URL": "memory://",
    "JWT_SECRET": "test-secret-test-secret-test-secret-123456",
    "SECRETS_ENCRYPTION_KEY": "x8Qm3s1bq1bVJmJ0wqk3v8u0y6oE2p0kq6m1p9i4yDg=",
    "LOG_JSON": "false",
    "LOG_LEVEL": "WARNING",
    "RETRY_BASE_SECONDS": "0",
    "RATE_LIMIT_PER_MINUTE": "100000",
    "LOGIN_RATE_LIMIT_PER_MINUTE": "5",
    "DEMO_MODE": "false",
})
# Tests must never reach real providers. An empty environment value overrides whatever the
# developer's local .env file configures (a real OpenAI key, a running ComfyUI, Redis).
for var in ("OPENAI_API_KEY", "OPENAI_CONTENT_MODEL", "COMFYUI_URL", "REDIS_URL"):
    os.environ[var] = ""

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import deps  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Base, get_engine, reset_engine, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ApiKey, Membership, Organization, Project, User  # noqa: E402
from app.security import generate_api_key, hash_password  # noqa: E402
from app.services import router as hybrid_router  # noqa: E402
from app.services.media import find_ffmpeg  # noqa: E402
from app.services.storage import get_storage  # noqa: E402

ACCEPTANCE_COMMAND = (
    "Create a promotional campaign for MCR SME Copilot with 3 promotional images, 5 Thai Facebook captions, "
    "1 business proposal PDF, and 1 promotional MP4 video."
)
PASSWORD = "correct-horse-battery"  # noqa: S105 - test fixture


@pytest.fixture(autouse=True)
def fresh_state(tmp_path):
    """Isolated database, storage and settings for every test."""
    get_settings.cache_clear()
    settings = get_settings()
    # TEST_DATABASE_URL runs the suite on a real server (CI uses PostgreSQL); default: a SQLite file per test.
    settings.database_url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    settings.storage_local_dir = tmp_path / "artifacts"
    reset_engine()
    get_storage.cache_clear()
    deps._limiter = None
    hybrid_router.TRANSPORTS.clear()
    # Default: provider calls fail fast offline. Fixtures below install working fakes.
    hybrid_router.TRANSPORTS["openai"] = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "offline test"}))
    hybrid_router.TRANSPORTS["comfyui"] = httpx.MockTransport(lambda r: httpx.Response(503))
    hybrid_router.TRANSPORTS["firefly"] = httpx.MockTransport(lambda r: httpx.Response(503))
    from app.providers import firefly as firefly_provider

    firefly_provider.clear_token_cache()
    if os.environ.get("TEST_DATABASE_URL"):
        Base.metadata.drop_all(get_engine())  # a shared server database: start every test empty
    Base.metadata.create_all(get_engine())
    yield settings
    hybrid_router.TRANSPORTS.clear()
    reset_engine()


def make_org(name: str, email: str) -> dict:
    with session_scope() as session:
        org = Organization(name=name, slug=name.lower().replace(" ", "-"),
                           business_context="MCR SME Copilot is an AI assistant platform for Thai SMEs by MCR Technology.")
        session.add(org)
        session.flush()
        session.add(Project(organization_id=org.id, name="Default", brand_guidelines={}))
        user = User(email=email, full_name="Test Owner", password_hash=hash_password(PASSWORD))
        session.add(user)
        session.flush()
        session.add(Membership(organization_id=org.id, user_id=user.id, role="owner"))
        key, prefix, digest = generate_api_key()
        session.add(ApiKey(organization_id=org.id, name="n8n", key_prefix=prefix, key_hash=digest))
        return {"org_id": org.id, "email": email, "api_key": key}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def org_a():
    return make_org("Org A", "owner-a@example.com")


@pytest.fixture
def org_b():
    return make_org("Org B", "owner-b@example.com")


@pytest.fixture
def auth(client, org_a):
    resp = client.post("/v1/auth/login", json={"email": org_a["email"], "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# --- fake providers (HTTP-level, so the real adapters are exercised) ----------------------
def png_bytes(size=(1344, 768), color=(20, 60, 160)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(scope="session")
def mp3_bytes() -> bytes:
    out = _TMP / "voice.mp3"
    subprocess.run([find_ffmpeg(), "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "libmp3lame",
                    "-b:a", "64k", str(out)], check=True, capture_output=True)
    return out.read_bytes()


# Six captions in different creative angles, written the way a person would (used by the fake model).
ANGLE_NAMES = ["conversational", "storytelling", "problem_solution", "educational", "direct_promotional", "community_engagement"]
SAMPLE_CAPTIONS = [
    "ร้านเล็กก็มีทีมคอนเทนต์ได้นะ MCR SME Copilot ช่วยคิดแคปชัน ทำภาพ และร่างเอกสารให้จากคำสั่งเดียว คุณมีเวลาไปดูแลลูกค้ามากขึ้น",
    "เมื่อคืนพี่เจ้าของร้านขนมนั่งคิดโพสต์จนตีสอง วันนี้เธอพิมพ์สั่งงานแค่ประโยคเดียว แล้วไปนอนเร็วกว่าเดิมเยอะเลย",
    "โพสต์ไม่ทันเพราะงานหน้าร้านล้นมือใช่ไหม ให้ผู้ช่วย AI ร่างให้ก่อน แล้วคุณค่อยตรวจและกดอนุมัติเอง ไม่ต้องเริ่มจากหน้าว่าง",
    "เคล็ดลับเล็ก ๆ ก่อนโพสต์: บอกให้ชัดว่าลูกค้าจะได้อะไร ไม่ต้องยาว ลองสั่งให้ผู้ช่วยเขียนสามแบบแล้วเลือกแบบที่ใช่ที่สุด",
    "อยากลองใช้ผู้ช่วยสร้างคอนเทนต์สำหรับธุรกิจไทย ทักแชทมาได้เลย ทีมงานพาดูการใช้งานจริงให้ค่ะ",
    "ถามเพื่อน ๆ เจ้าของร้านหน่อย ทุกวันนี้ใช้เวลาเขียนโพสต์กันวันละกี่นาที มาแชร์กันในคอมเมนต์ได้นะคะ",
]


def llm_reply(system: str, user: str) -> dict:
    if "Planner Agent" in system:
        # behave like a competent model: extract exactly what the command asks for
        from app.agents.planner import parse_command_rules

        command = user.split('"""')[1].strip()
        spec = parse_command_rules(command, "th").model_dump()
        if spec["images"]:
            spec["images"]["concepts"] = ["a shop owner with a laptop", "a team at a tablet", "abstract data lines"]
        if spec["video"] and spec["video"]["duration_seconds"] == 60:
            spec["video"]["duration_seconds"] = 20  # keep the test render short
        return spec
    if "Content Agent" in system and "Task: rewrite one caption" in user:
        return {"captions": [{"text": "เช้าวันจันทร์ที่งานโพสต์ทั้งสัปดาห์เสร็จก่อนกาแฟแก้วแรก ฟังดูดีใช่ไหมคะ "
                                      "MCR SME Copilot ช่วยร่างให้ คุณแค่เลือกและปรับนิดหน่อย ลองทักมาคุยกันได้ค่ะ",
                              "tone": "friendly", "platform": "facebook", "creative_angle": "storytelling",
                              "hashtags": ["#MCRSMECopilot"]}]}
    if "Content Agent" in system and "caption(s) completely" in user:
        n = int(re.search(r"Rewrite (\d+) caption", user)[1])
        return {"captions": [{"text": f"ฉบับแก้ใหม่ {i}: {SAMPLE_CAPTIONS[-1 - i]}", "tone": "friendly", "platform": "facebook",
                              "creative_angle": "conversational"} for i in range(n)]}
    if "Content Agent" in system:
        n = int(re.search(r"Write exactly (\d+) captions", user)[1])
        return {"captions": [{"text": SAMPLE_CAPTIONS[i % len(SAMPLE_CAPTIONS)], "tone": "friendly", "platform": "facebook",
                              "creative_angle": ANGLE_NAMES[i % len(ANGLE_NAMES)], "hashtags": ["#MCRSMECopilot", "#SMEไทย"]}
                             for i in range(n)],
                "brief": {"headline": "สั่งครั้งเดียว งานครบ", "tagline": "ผู้ช่วย AI สำหรับ SME ไทย",
                          "key_messages": ["ประหยัดเวลา", "ใช้งานง่าย", "ทำงานต่อเนื่องแม้ปิดเบราว์เซอร์"]},
                "youtube_title": "MCR SME Copilot", "youtube_description": "ผู้ช่วย AI สำหรับธุรกิจ SME"}
    if "Document Agent" in system:
        return {"title": "ข้อเสนอแคมเปญ MCR SME Copilot", "subtitle": "MCR Technology",
                "sections": [{"heading": h, "paragraphs": ["เนื้อหาทดสอบภาษาไทยสำหรับตรวจสอบการแสดงผลของเอกสาร " * 4],
                              "bullets": ["รายการที่หนึ่ง", "รายการที่สอง"]}
                             for h in ["บทสรุปผู้บริหาร", "วัตถุประสงค์", "ขอบเขตงาน", "สิ่งที่ส่งมอบ", "กำหนดเวลา"]]}
    if "Video Agent" in system:
        return {"title": "MCR SME Copilot", "scenes": [
            {"heading": f"ฉากที่ {i}", "on_screen_text": "สั่งครั้งเดียว งานครบ", "narration": "ผู้ช่วย AI สำหรับธุรกิจของคุณ",
             "duration_seconds": 4} for i in range(1, 6)]}
    if "QA Agent" in system:
        return {"issues": []}
    if "Poster Designer" in system:
        # behave like the assistant: keep the draft, apply the latest request, use library names only
        logos = json.loads(re.search(r"Logo library \(names\): (.*)", user)[1])
        current = json.loads(user.split("Current draft:\n", 1)[1].split("\n\nConversation", 1)[0])
        last = user.rsplit("USER: ", 1)[1]
        if "EMPTY_ANSWER" in last:
            return {"reply": "", "title": ""}  # a model that answers with (almost) nothing
        draft = {**current, "reply": "อัปเดตแล้วค่ะ ต้องการเวลาเริ่มงานด้วยนะคะ", "ready": True}
        if "ROV" in last:
            draft.update(tag="ROV", title="Valor City", edition="2026", date_text="วันอาทิตย์ที่ 3 พฤษภาคม 2569",
                         headline=["ภาคใต้ตอนบน รอบ 8 ทีมสุดท้าย"], art="an original sharpshooter hero in gold armour",
                         sponsor_logos=[*logos, "Ghost Sponsor"])
        if "TOURNAMENT_MIXUP" in last:  # a model that puts a who-can-enter line in the esports headline
            draft.update(template="tournament", title="Surat Esports", subtitle="โครงการแข่งอีสปอร์ต",
                         headline=["โครงการแข่งอีสปอร์ต", "รุ่นประชาชนทั่วไป *ประเภทบุคคล*"],
                         category_lines=["อายุ 13-60 ปี"],
                         schedule=[{"label": "แข่งขัน", "big": "8-9", "lines": ["สิงหาคม", "2569"]}])
        if "นีออน" in last or "neon" in last:
            draft["theme"] = "neon"
        return draft
    raise AssertionError("unexpected LLM prompt")


class FakeOpenAI:
    def __init__(self, mp3: bytes):
        self.mp3 = mp3
        self.calls: list[str] = []
        self.fail_next: dict[str, int] = {}  # path suffix -> number of 500s to return first
        self.mutate = None  # optional hook(system, reply) -> reply
        self.requests: list[dict] = []  # chat request bodies, for prompt assertions

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        assert request.headers["authorization"].startswith("Bearer sk-")
        for suffix, remaining in self.fail_next.items():
            if path.endswith(suffix) and remaining > 0:
                self.fail_next[suffix] = remaining - 1
                return httpx.Response(500, text="upstream exploded")
        if path.endswith("/chat/completions"):
            body = json.loads(request.content)
            msgs = body["messages"]
            # system + developer layers form the instructions; the last user message is the request
            system = "\n\n".join(m["content"] for m in msgs if m["role"] in ("system", "developer"))
            user = [m["content"] for m in msgs if m["role"] == "user"][-1]
            self.requests.append(body)
            reply = llm_reply(system, user)
            if self.mutate:
                reply = self.mutate(system, reply)
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}],
                                             "usage": {"prompt_tokens": 1000, "completion_tokens": 500}})
        if path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png_bytes((1536, 1024))).decode()}]})
        if path.endswith("/audio/speech"):
            return httpx.Response(200, content=self.mp3)
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)


class FakeComfyUI:
    def __init__(self, up: bool = True, queue: int = 0):
        self.up, self.queue, self.prompts = up, queue, 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == "/system_stats":
            return httpx.Response(200, json={"devices": [{"vram_free": 12 * 1024**3}]})
        if path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": [[0]] * self.queue})
        if path == "/prompt":
            self.prompts += 1
            workflow = json.loads(request.content)["prompt"]
            assert workflow["5"]["inputs"]["width"] == 1344
            return httpx.Response(200, json={"prompt_id": f"p{self.prompts}"})
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={pid: {"status": {"status_str": "success", "completed": True},
                                                   "outputs": {"9": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}}}})
        if path == "/view":
            return httpx.Response(200, content=png_bytes())
        return httpx.Response(404)


@pytest.fixture
def fake_openai(fresh_state, mp3_bytes):
    fake = FakeOpenAI(mp3_bytes)
    fresh_state.openai_api_key = "sk-test"
    hybrid_router.TRANSPORTS["openai"] = httpx.MockTransport(fake)
    return fake


@pytest.fixture
def fake_comfyui(fresh_state):
    fake = FakeComfyUI()
    fresh_state.comfyui_url = "http://comfy.test:8188"
    hybrid_router.TRANSPORTS["comfyui"] = httpx.MockTransport(fake)
    return fake


class FakeFirefly:
    """Adobe IMS + Firefly v3 async API: token, generate-async, status (running, then succeeded), download."""

    API = "https://firefly.test"
    IMS = "https://ims.test/ims/token/v3"

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.bodies: list[dict] = []
        self.tokens = 0
        self.polls: dict[str, int] = {}
        self.fail_token = False
        self.fail_job = False
        self.status_host = "firefly.test"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = request.url
        if str(url).startswith(self.IMS):
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            if self.fail_token or form.get("client_secret") != "ff-secret-123":
                return httpx.Response(400, json={"error": "invalid_client"})
            assert form["grant_type"] == "client_credentials" and "firefly_api" in form["scope"].replace("%2C", ",")
            self.tokens += 1
            return httpx.Response(200, json={"access_token": f"tok-{self.tokens}", "expires_in": 86399})
        if url.host == "presigned.test":
            assert "authorization" not in request.headers and "x-api-key" not in request.headers
            return httpx.Response(200, content=png_bytes())
        assert url.host == "firefly.test", f"credentials sent to {url.host}"
        assert request.headers["x-api-key"] == "ff-client-id-1" and request.headers["authorization"].startswith("Bearer tok-")
        if url.path == "/v3/images/generate-async":
            body = json.loads(request.content)
            body["_model"] = request.headers.get("x-model-version")
            self.bodies.append(body)
            job = f"urn:ff:jobs:test:{len(self.bodies)}"
            return httpx.Response(202, json={"jobId": job, "statusUrl": f"https://{self.status_host}/v3/status/{job}",
                                             "cancelUrl": f"https://firefly.test/v3/cancel/{job}"})
        if url.path.startswith("/v3/status/"):
            job = url.path.rsplit("/", 1)[1]
            self.polls[job] = self.polls.get(job, 0) + 1
            if self.fail_job:
                return httpx.Response(200, json={"status": "failed", "jobId": job, "message": "prompt blocked"})
            if self.polls[job] == 1:
                return httpx.Response(200, json={"status": "running", "jobId": job})
            size = self.bodies[-1]["size"]
            return httpx.Response(200, json={"status": "succeeded", "jobId": job, "result": {
                "size": size, "outputs": [{"seed": 42, "image": {"url": f"https://presigned.test/{job}.png"}}]}})
        return httpx.Response(404)


@pytest.fixture
def fake_firefly(fresh_state, monkeypatch):
    from app.providers import firefly as firefly_provider

    fake = FakeFirefly()
    fresh_state.firefly_client_id = "ff-client-id-1"
    fresh_state.firefly_client_secret = "ff-secret-123"  # noqa: S105 - fake credential for the fake server
    fresh_state.firefly_api_url = FakeFirefly.API
    fresh_state.firefly_ims_url = FakeFirefly.IMS
    monkeypatch.setattr(firefly_provider.time, "sleep", lambda s: None)
    hybrid_router.TRANSPORTS["firefly"] = httpx.MockTransport(fake)
    return fake
