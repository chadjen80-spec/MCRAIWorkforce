"""Natural caption generation: creative brief, layered prompts, full rewrites, quality checks, version history."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import httpx
import pytest

import tests.conftest as conftest
from tests.test_workforce import artifacts_of, submit

PREVIOUS_SAFE = "เช้าวันจันทร์ที่งานโพสต์ทั้งสัปดาห์เสร็จก่อนกาแฟแก้วแรก"


def _captions(client, auth, job_id):
    return sorted((a for a in artifacts_of(client, auth, job_id) if a["kind"] == "caption"), key=lambda a: a["filename"])


def _text(client, auth, art):
    return client.get(art["download_url"], headers=auth).content.decode()


def _regen(client, auth, art, **body):
    return client.post(f"/v1/artifacts/{art['id']}/regenerate-caption", json=body, headers=auth)


def _set_context(org_id, text):
    from app.db import session_scope
    from app.models import Organization

    with session_scope() as s:
        s.get(Organization, org_id).business_context = text


def _replies(monkeypatch, fn):
    """Route the fake model's caption requests through `fn(user) -> dict | None` (None: default fake reply)."""
    real = conftest.llm_reply

    def reply(system, user):
        if "Content Agent" in system:
            out = fn(user)
            if out is not None:
                return out
        return real(system, user)

    monkeypatch.setattr(conftest, "llm_reply", reply)


def _one(text, **kw):
    return {"captions": [{"text": text, "tone": kw.get("tone", "friendly"), "platform": "facebook",
                          "creative_angle": kw.get("angle", "conversational"), "hashtags": kw.get("hashtags", [])}]}


def _chat_bodies(fake):
    return [b for b in fake.requests if any(m["role"] == "user" for m in b["messages"])]


# 1 ---------------------------------------------------------------------------------------------------
def test_captions_are_written_whole_by_the_model_from_a_creative_brief(client, auth, fake_openai):
    job = submit(client, auth, command="เขียนแคปชัน Facebook ภาษาไทย 3 โพสต์ สำหรับ MCR SME Copilot", require_approval=False)
    caps = _captions(client, auth, job["id"])
    assert len(caps) == 3
    body = next(b for b in _chat_bodies(fake_openai) if "Write exactly 3 captions" in b["messages"][-1]["content"])
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "developer", "user"]  # system persona, developer rules, user brief
    system, developer, user = (m["content"] for m in body["messages"])
    assert "Thai creative copywriter" in system
    assert "ในยุคดิจิทัล" in developer and "Do not force every caption into hook, body, call to action" in developer
    assert "CREATIVE BRIEF" in user and "Target audience: (not provided: write neutrally" in user
    assert "caption 1: creative angle = conversational" in user and "caption 3: creative angle = problem_solution" in user
    # stored text is exactly the model's caption plus the hashtags it chose: nothing is added by code
    for i, art in enumerate(caps):
        assert _text(client, auth, art) == conftest.SAMPLE_CAPTIONS[i] + "\n\n#MCRSMECopilot #SMEไทย"
        assert art["metadata"]["creative_angle"] == conftest.ANGLE_NAMES[i] and art["metadata"]["tone"] == "friendly"
    from app.db import session_scope
    from app.models import Task

    with session_scope() as s:
        task = s.query(Task).filter(Task.job_id == uuid.UUID(job["id"]), Task.agent == "content").one()
        assert task.result["creative_brief"]["command"].startswith("เขียนแคปชัน") and task.result["creative_brief"]["language"] == "th"


# 2 + 4 -------------------------------------------------------------------------------------------------
def test_regenerate_calls_the_model_again_with_the_brief_previous_version_and_feedback(client, auth, fake_openai, monkeypatch):
    job = submit(client, auth, command="Write 2 Thai Facebook captions", require_approval=False)
    target = _captions(client, auth, job["id"])[0]
    before = len(_chat_bodies(fake_openai))

    def fn(user):
        if "Task: rewrite one caption" not in user:
            return None
        if "ร้านกาแฟ" in user.split("Feedback from the business owner", 1)[-1]:
            return _one("เปิดร้านกาแฟเช้า ๆ แล้วยังต้องคิดโพสต์อีก ให้ MCR SME Copilot ร่างให้ระหว่างที่คุณชงกาแฟแก้วแรกดีกว่า")
        return _one("ข้อความทั่วไปที่ไม่ได้ทำตามคำแนะนำ เขียนใหม่ทั้งหมดสำหรับ MCR SME Copilot")

    _replies(monkeypatch, fn)
    r = _regen(client, auth, target, action="regenerate", instruction="เน้นร้านกาแฟ")
    assert r.status_code == 200, r.text
    calls = _chat_bodies(fake_openai)[before:]
    assert len(calls) >= 1  # the model was called again
    prompt = calls[0]["messages"][-1]["content"]
    assert "CREATIVE BRIEF" in prompt and "Write 2 Thai Facebook captions" in prompt  # the original brief
    assert "Previous version (do not copy it):" in prompt and target["text_preview"].split("\n")[0] in prompt
    assert "Feedback from the business owner (follow it): เน้นร้านกาแฟ" in prompt
    assert _text(client, auth, r.json()["artifact"]).startswith("เปิดร้านกาแฟเช้า ๆ")  # feedback shaped the result


# 3 ---------------------------------------------------------------------------------------------------
def test_a_rewrite_that_just_appends_to_the_old_caption_is_rejected(client, auth, fake_openai, monkeypatch):
    job = submit(client, auth, command="Write 2 Thai Facebook captions", require_approval=False)
    target = _captions(client, auth, job["id"])[0]
    old = _text(client, auth, target)
    from app.agents.captions import body_of

    attempts = []

    def fn(user):
        if "Task: rewrite one caption" not in user:
            return None
        attempts.append(user)
        if len(attempts) == 1:
            return _one(body_of(old) + " สนใจทักแชทได้เลยนะคะ")  # concatenation, not a rewrite
        return _one(PREVIOUS_SAFE + " MCR SME Copilot ช่วยร่างให้ คุณแค่เลือกแบบที่ชอบแล้วกดอนุมัติ")

    _replies(monkeypatch, fn)
    r = _regen(client, auth, target, action="rewrite")
    assert r.status_code == 200, r.text
    assert len(attempts) == 2 and "rejected for: the new caption contains or is cut from the previous one" in attempts[1]
    new = _text(client, auth, r.json()["artifact"])
    assert not new.startswith(body_of(old)) and body_of(old) not in new


def test_shorten_must_be_a_coherent_shorter_caption_not_a_cut(client, auth, fake_openai, monkeypatch):
    job = submit(client, auth, command="Write 2 Thai Facebook captions", require_approval=False)
    target = _captions(client, auth, job["id"])[0]
    old = _text(client, auth, target)
    from app.agents.captions import body_of

    _replies(monkeypatch, lambda user: _one(body_of(old)[:40]) if "Task: rewrite one caption" in user else None)  # a mere cut
    r = _regen(client, auth, target, action="shorten")
    assert r.status_code == 422 and r.json()["error"]["code"] == "CAPTION_QUALITY_FAILED"
    assert _text(client, auth, target) == old  # kept


# 5 ---------------------------------------------------------------------------------------------------
def test_verified_facts_are_preserved_in_meaning_preserving_rewrites(client, auth, fake_openai, monkeypatch, org_a):
    _set_context(org_a["org_id"], "MCR SME Copilot แพ็กเกจเริ่มต้น ราคา 990 บาทต่อเดือน")
    with_fact = "แพ็กเกจเริ่มต้นของ MCR SME Copilot ราคา 990 บาทต่อเดือน ให้ทีม AI ช่วยร่างโพสต์ ทำภาพ และสรุปงานให้คุณทุกวัน"
    brief = {"headline": "h", "tagline": "t", "key_messages": ["k"]}
    _replies(monkeypatch, lambda user: {**_one(with_fact), "brief": brief} if "Write exactly" in user else None)
    job = submit(client, auth, command="Write 1 Thai Facebook caption", require_approval=False)
    target = _captions(client, auth, job["id"])[0]
    assert "990 บาท" in _text(client, auth, target)

    drops = _one("ลองให้ทีม AI ช่วยงานคอนเทนต์ของร้านคุณดูสิ เริ่มต้นง่ายกว่าที่คิดเยอะเลย", tone="playful")
    _replies(monkeypatch, lambda user: drops if "Task: rewrite one caption" in user else None)
    r = _regen(client, auth, target, action="tone", tone="playful")
    assert r.status_code == 422 and "990" in r.json()["error"]["message"]
    keeps = _one("เดือนละ 990 บาท ได้ผู้ช่วยคอนเทนต์ที่ไม่เคยลาป่วย ร่างโพสต์ ทำภาพ สรุปงานให้ครบ ลองดูไหมคะ", tone="playful")
    _replies(monkeypatch, lambda user: keeps if "Task: rewrite one caption" in user else None)
    r = _regen(client, auth, target, action="tone", tone="playful")
    assert r.status_code == 200 and r.json()["artifact"]["metadata"]["tone"] == "playful"
    rewrites = [b["messages"][-1]["content"] for b in _chat_bodies(fake_openai) if "Task: rewrite one caption" in b["messages"][-1]["content"]]
    assert "Tone to use: playful" in rewrites[-1]
    assert _regen(client, auth, target, action="tone").status_code == 422  # a tone must be chosen


# 6 ---------------------------------------------------------------------------------------------------
def test_unsupported_claims_are_rejected_and_the_caption_kept(client, auth, fake_openai, monkeypatch):
    job = submit(client, auth, command="Write 2 Thai Facebook captions", require_approval=False)
    target = _captions(client, auth, job["id"])[0]
    old = _text(client, auth, target)
    _replies(monkeypatch, lambda user: _one("ใช้แล้วรับประกันยอดขายเพิ่ม 100% ภายในเดือนเดียว") if "Task: rewrite" in user else None)
    r = _regen(client, auth, target, action="regenerate")
    assert r.status_code == 422 and r.json()["error"]["code"] == "CAPTION_QUALITY_FAILED" and "100%" in r.json()["error"]["message"]
    assert _text(client, auth, target) == old
    rewrites = [b for b in _chat_bodies(fake_openai) if "Task: rewrite one caption" in b["messages"][-1]["content"]]
    assert len(rewrites) == 2  # one bounded retry, not a loop


def test_generation_rewrites_failing_captions_once(client, auth, fake_openai, monkeypatch):
    def fn(user):
        if "Write exactly 2 captions" in user:
            return {"captions": [
                {"text": "ในยุคดิจิทัล MCR SME Copilot จะยกระดับธุรกิจของคุณ รับประกันยอดขาย", "creative_angle": "conversational"},
                {"text": conftest.SAMPLE_CAPTIONS[1], "creative_angle": "storytelling"},
            ], "brief": {"headline": "h", "tagline": "t", "key_messages": ["k"]}}
        return None

    _replies(monkeypatch, fn)
    job = submit(client, auth, command="Write 2 Thai Facebook captions", require_approval=False)
    texts = [_text(client, auth, a) for a in _captions(client, auth, job["id"])]
    assert texts[0].startswith("ฉบับแก้ใหม่") and "ในยุคดิจิทัล" not in texts[0] and "รับประกัน" not in texts[0]
    assert texts[1] == conftest.SAMPLE_CAPTIONS[1]  # the accepted caption is left as written
    fix = next(b for b in _chat_bodies(fake_openai) if "caption(s) completely" in b["messages"][-1]["content"])["messages"][-1]["content"]
    assert "ในยุคดิจิทัล" in fix and "รับประกัน" in fix  # the reasons were given to the model


# 7 ---------------------------------------------------------------------------------------------------
def test_near_duplicate_captions_are_detected():
    from app.agents.captions import CreativeBrief, issues_for, similarity

    brief = CreativeBrief(brand="MCR", product="MCR SME Copilot", command="x")
    same = conftest.SAMPLE_CAPTIONS[0]
    found = issues_for([same, same.replace("ได้นะ", "ได้นะคะ"), conftest.SAMPLE_CAPTIONS[3]], brief, "")
    assert any(x["code"] == "similar" for x in found[1]) and not any(x["code"] == "similar" for x in found[2])
    pairs = [similarity(a, b) for i, a in enumerate(conftest.SAMPLE_CAPTIONS) for b in conftest.SAMPLE_CAPTIONS[i + 1 :]]
    assert max(pairs) < 0.55  # the six angles really differ
    cliche = issues_for(["ในยุคดิจิทัล ร้านของคุณต้องก้าวไปอีกขั้น 🚀🚀🚀🚀🚀🚀🚀"], brief, "")[0]
    assert {"cliche", "emoji"} <= {x["code"] for x in cliche}


# 8 ---------------------------------------------------------------------------------------------------
def test_provider_failure_keeps_the_caption_and_its_history(client, auth, fake_openai):
    job = submit(client, auth, command="Write 2 Thai Facebook captions", require_approval=False)
    target = _captions(client, auth, job["id"])[0]
    old = _text(client, auth, target)
    fake_openai.fail_next = {"/chat/completions": 10}
    r = _regen(client, auth, target, action="expand")
    assert r.status_code == 502 and r.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert _text(client, auth, target) == old
    art = next(a for a in artifacts_of(client, auth, job["id"]) if a["id"] == target["id"])
    assert "versions" not in art["metadata"]


def test_demo_mode_never_replaces_an_ai_caption(client, auth, fake_openai, fresh_state):
    job = submit(client, auth, command="Write 1 Thai Facebook caption", require_approval=False)
    target = _captions(client, auth, job["id"])[0]
    old = _text(client, auth, target)
    fresh_state.openai_api_key, fresh_state.demo_mode = "", True
    r = _regen(client, auth, target)
    assert r.status_code == 409 and r.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED"
    assert _text(client, auth, target) == old and not target["is_demo"]


# 9 ---------------------------------------------------------------------------------------------------
def test_budget_limit_stops_a_regeneration(client, auth, fake_openai, org_a):
    job = submit(client, auth, command="Write 1 Thai Facebook caption", require_approval=False)
    target = _captions(client, auth, job["id"])[0]
    old = _text(client, auth, target)
    from app.db import session_scope
    from app.models import Organization

    with session_scope() as s:
        s.get(Organization, org_a["org_id"]).monthly_budget_usd = Decimal("0.000001")
    calls = len(fake_openai.calls)
    r = _regen(client, auth, target)
    assert r.status_code == 402 and _text(client, auth, target) == old
    assert len(fake_openai.calls) == calls  # refused before calling the model


# version history + usage ----------------------------------------------------------------------------
def test_versions_are_kept_and_can_be_restored(client, auth, fake_openai):
    job = submit(client, auth, command="Write 2 Thai Facebook captions", require_approval=False)
    target = _captions(client, auth, job["id"])[0]
    original = _text(client, auth, target)
    first = _regen(client, auth, target, action="regenerate").json()["artifact"]
    assert first["metadata"]["versions"] == [{**first["metadata"]["versions"][0], "text": original, "source": "original"}]
    assert first["metadata"]["source"] == "regenerate"
    restored = client.put(f"/v1/artifacts/{target['id']}/text", json={"text": original}, headers=auth).json()
    assert _text(client, auth, restored) == original
    assert [v["source"] for v in restored["metadata"]["versions"]] == ["original", "regenerate"]
    from app.db import session_scope
    from app.models import UsageEvent

    with session_scope() as s:
        rows = [u for u in s.query(UsageEvent).all() if (u.units or {}).get("purpose") == "captions"]
        assert rows and all(u.units["input_tokens"] == 1000 and u.cost_usd > 0 for u in rows)  # tokens and cost recorded


# adapter -----------------------------------------------------------------------------------------------
def test_developer_layer_falls_back_to_the_system_message_when_a_provider_rejects_it():
    from pydantic import BaseModel

    from app.providers.config import OpenAIConfig
    from app.providers.openai_client import OpenAIClient

    class Out(BaseModel):
        ok: bool

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append([m["role"] for m in body["messages"]])
        if any(m["role"] == "developer" for m in body["messages"]):
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'developer' role"}})
        assert body["model"] == "caption-model"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {}})

    cfg = OpenAIConfig(api_key="sk-x", base_url="http://x", model="m", image_model="i", tts_model="t", tts_voice="v")
    OpenAIClient._developer_role_ok = True
    try:
        out, _ = OpenAIClient(cfg, transport=httpx.MockTransport(handler)).complete_json(
            "sys", "user", Out, developer="rules", model="caption-model")
    finally:
        OpenAIClient._developer_role_ok = True
    assert out.ok and seen == [["system", "developer", "user"], ["system", "user"]]


@pytest.mark.parametrize("action", ["rewrite", "shorten", "expand"])
def test_each_action_asks_for_a_full_rewrite(action):
    from app.agents.captions import CreativeBrief, rewrite_user_prompt

    p = rewrite_user_prompt(CreativeBrief(brand="b", product="p", command="c"), action, "เดิม", [], None, None, None)
    assert "Previous version (its meaning to keep):" in p and {"rewrite": "write every", "shorten": "not a trimmed",
                                                               "expand": "not a padded"}[action] in p


def test_caption_model_is_used_and_costed_at_its_own_price(client, auth, fake_openai, fresh_state):
    fresh_state.openai_content_model = "gpt-4.1"
    job = submit(client, auth, command="Write 1 Thai Facebook caption", require_approval=False)
    assert _captions(client, auth, job["id"])
    caption_calls = [b for b in _chat_bodies(fake_openai) if "Write exactly 1 captions" in b["messages"][-1]["content"]]
    assert caption_calls and all(b["model"] == "gpt-4.1" for b in caption_calls)
    planner = [b for b in _chat_bodies(fake_openai) if "Planner Agent" in b["messages"][0]["content"]]
    assert all(b["model"] == "gpt-4o-mini" for b in planner)  # other agents keep the default model
    from app.db import session_scope
    from app.models import UsageEvent

    with session_scope() as s:
        [row] = [u for u in s.query(UsageEvent).all() if (u.units or {}).get("purpose") == "captions"]
        # fake usage: 1,000 input + 500 output tokens at gpt-4.1 prices (2.00 / 8.00 per 1M)
        assert row.units["model"] == "gpt-4.1" and row.cost_usd == Decimal("0.006")
