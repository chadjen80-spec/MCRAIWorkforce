"""OpenAI-compatible adapter (chat with validated JSON output, image generation, text-to-speech).

Uses plain HTTPS so any OpenAI-compatible endpoint can be configured via base_url.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.errors import AgentError
from app.providers.config import OpenAIConfig

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_usd(self) -> Decimal:
        s = get_settings()
        return (
            Decimal(self.input_tokens) * s.llm_input_usd_per_1m + Decimal(self.output_tokens) * s.llm_output_usd_per_1m
        ) / Decimal(1_000_000)

    def add(self, other: LLMUsage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


def _raise_for(resp: httpx.Response, what: str) -> None:
    if resp.status_code < 400:
        return
    body = resp.text[:500]
    if resp.status_code in (401, 403):
        raise AgentError("PROVIDER_AUTH_FAILED", f"{what}: provider rejected the credentials ({resp.status_code})")
    if resp.status_code == 429 and ("insufficient_quota" in body or "no credits" in body.lower() or "billing" in body.lower()):
        # Out of credit is not temporary: retrying only delays the error.
        raise AgentError("PROVIDER_NO_CREDIT", f"{what}: the provider account has no credit left. Add credit in the provider's billing settings.")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise AgentError("PROVIDER_UNAVAILABLE", f"{what}: HTTP {resp.status_code} {body}", retryable=True)
    raise AgentError("PROVIDER_REQUEST_REJECTED", f"{what}: HTTP {resp.status_code} {body}")


class OpenAIClient:
    def __init__(self, cfg: OpenAIConfig, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self._http = httpx.Client(
            base_url=cfg.base_url,
            headers={"Authorization": f"Bearer {cfg.api_key}"},
            timeout=httpx.Timeout(get_settings().openai_timeout_seconds, connect=10),
            transport=transport,
        )

    def _post(self, path: str, payload: dict, what: str) -> httpx.Response:
        try:
            resp = self._http.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise AgentError("PROVIDER_UNAVAILABLE", f"{what}: {exc}", retryable=True) from exc
        _raise_for(resp, what)
        return resp

    # -- chat -----------------------------------------------------------------
    def complete_json(self, system: str, user: str, schema: type[T], *, temperature: float = 0.6) -> tuple[T, LLMUsage]:
        """Ask for a JSON object and validate it against `schema`. One repair round on invalid output."""
        usage = LLMUsage()
        messages = [
            {
                "role": "system",
                "content": system
                + "\n\nRespond with a single JSON object that conforms to this JSON Schema. No prose.\n"
                + json.dumps(schema.model_json_schema(), ensure_ascii=False),
            },
            {"role": "user", "content": user},
        ]
        last_error = ""
        for _ in range(2):
            resp = self._post(
                "/chat/completions",
                {
                    "model": self.cfg.model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "temperature": temperature,
                },
                "chat completion",
            )
            data = resp.json()
            u = data.get("usage") or {}
            usage.add(LLMUsage(int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))))
            try:
                content = data["choices"][0]["message"]["content"] or ""
                return schema.model_validate_json(content), usage
            except (KeyError, IndexError, ValidationError, ValueError) as exc:
                last_error = str(exc)[:1500]
                messages.append({"role": "assistant", "content": str(data.get("choices", ""))[:3000]})
                messages.append(
                    {"role": "user", "content": f"That output was invalid: {last_error}\nReturn corrected JSON only."}
                )
        raise AgentError("LLM_INVALID_OUTPUT", f"Model did not return valid structured output: {last_error}", retryable=True)

    # -- images -----------------------------------------------------------------
    def generate_image(self, prompt: str, aspect_ratio: str) -> bytes:
        size = {"16:9": "1536x1024", "9:16": "1024x1536"}.get(aspect_ratio, "1024x1024")
        resp = self._post(
            "/images/generations",
            {"model": self.cfg.image_model, "prompt": prompt, "size": size, "n": 1},
            "image generation",
        )
        item = (resp.json().get("data") or [{}])[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            try:
                img = httpx.get(item["url"], timeout=60)
                img.raise_for_status()
                return img.content
            except httpx.HTTPError as exc:
                raise AgentError("PROVIDER_UNAVAILABLE", f"image download failed: {exc}", retryable=True) from exc
        raise AgentError("PROVIDER_EMPTY_RESPONSE", "Image provider returned no image data", retryable=True)

    # -- text to speech -----------------------------------------------------------
    def synthesize_speech(self, text: str, *, voice: str | None = None, instructions: str | None = None) -> bytes:
        payload = {"model": self.cfg.tts_model, "voice": voice or self.cfg.tts_voice, "input": text, "response_format": "mp3"}
        # Style instructions are only understood by the gpt-4o TTS family; older models reject unknown fields.
        if instructions and self.cfg.tts_model.startswith("gpt-4o"):
            payload["instructions"] = instructions
        resp = self._post("/audio/speech", payload, "text-to-speech")
        if not resp.content:
            raise AgentError("PROVIDER_EMPTY_RESPONSE", "TTS provider returned no audio", retryable=True)
        return resp.content

    def check(self) -> tuple[bool, str]:
        """Cheap credential check (listing models is free). Returns (ok, human-readable reason)."""
        try:
            resp = self._http.get("/models", timeout=8)
        except httpx.HTTPError as exc:
            return False, f"cannot reach {self.cfg.base_url} ({type(exc).__name__})"
        if resp.status_code == 200:
            return True, "API key accepted"
        if resp.status_code == 401:
            return False, "the provider rejected the API key (401): check that you copied the whole secret key"
        if resp.status_code == 403:
            return False, "the API key is not allowed to use this API (403): check the key's project and permissions"
        if resp.status_code == 429:
            return False, "rate limited or out of credit (429): check billing on the provider's site"
        return False, f"unexpected response HTTP {resp.status_code}"

    def ping(self) -> bool:
        try:
            return self._http.get("/models", timeout=8).status_code == 200
        except httpx.HTTPError:
            return False
