"""Adobe Firefly Services adapter: server-to-server OAuth, async Generate Image (v3), poll, download.

Flow (Adobe docs, "Using the Asynchronous Adobe Firefly APIs"):
  1. POST {ims}/ims/token/v3 (client_credentials) -> access_token, expires_in
  2. POST {api}/v3/images/generate-async (x-api-key = client id, Bearer token) -> {jobId, statusUrl}
  3. GET statusUrl until status is "succeeded" or "failed"
  4. download result.outputs[0].image.url (a pre-signed URL: no credentials are sent there)
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from urllib.parse import quote, urlsplit

import httpx

from app.config import get_settings
from app.errors import AgentError
from app.providers.config import FireflyConfig

# Supported output sizes (Firefly API reference, "size"). Image3 has no 9:16, so portrait uses 3:4
# and the image agent centre-crops to the exact aspect ratio, as it does for every provider.
SIZES = {
    "image3": {"16:9": (2688, 1536), "9:16": (1792, 2304), "1:1": (2048, 2048)},
    "image4": {"16:9": (2688, 1536), "9:16": (1440, 2560), "1:1": (2048, 2048)},
}
MAX_PROMPT = 1024

# Access tokens are valid for about a day; share them between jobs instead of signing in per image.
_TOKENS: dict[tuple[str, str], tuple[str, float]] = {}
_TOKEN_LOCK = threading.Lock()


def clear_token_cache() -> None:
    with _TOKEN_LOCK:
        _TOKENS.clear()


def size_for(model_version: str, aspect_ratio: str) -> tuple[int, int]:
    family = "image4" if model_version.startswith("image4") else "image3"
    return SIZES[family].get(aspect_ratio, SIZES[family]["1:1"])


def _error(resp: httpx.Response, what: str) -> AgentError:
    body = resp.text[:400]
    if resp.status_code == 401:
        return AgentError("PROVIDER_AUTH_FAILED", f"{what}: Adobe rejected the credentials (401)")
    if resp.status_code == 403:
        return AgentError("PROVIDER_AUTH_FAILED",
                          f"{what}: these credentials are not entitled to Firefly Services or this model (403): {body}")
    if resp.status_code == 429 or resp.status_code >= 500:
        return AgentError("PROVIDER_UNAVAILABLE", f"{what}: HTTP {resp.status_code} {body}", retryable=True)
    # 400/422/451: invalid input or the prompt was blocked by Adobe's content policy. Retrying won't help.
    return AgentError("PROVIDER_REQUEST_REJECTED", f"{what}: HTTP {resp.status_code} {body}")


class FireflyClient:
    def __init__(self, cfg: FireflyConfig, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self._http = httpx.Client(timeout=httpx.Timeout(60, connect=10), transport=transport)
        self._api_host = urlsplit(cfg.api_url).netloc

    # --- auth ---------------------------------------------------------------------
    def _token(self, *, force: bool = False) -> str:
        key = (self.cfg.client_id, self.cfg.client_secret)
        with _TOKEN_LOCK:
            hit = _TOKENS.get(key)
            if hit and not force and hit[1] > time.time():
                return hit[0]
        try:
            resp = self._http.post(self.cfg.ims_url, data={
                "grant_type": "client_credentials", "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret, "scope": get_settings().firefly_scopes,
            })
        except httpx.HTTPError as exc:
            raise AgentError("PROVIDER_UNAVAILABLE", f"Adobe sign-in: {exc}", retryable=True) from exc
        if resp.status_code in (400, 401, 403):
            # IMS answers 400 invalid_client / invalid_scope for wrong credentials or a project without Firefly.
            raise AgentError("PROVIDER_AUTH_FAILED",
                             f"Adobe sign-in failed ({resp.status_code}): check the Client ID and Client Secret, and that "
                             f"the Developer Console project includes Firefly Services. {resp.text[:200]}")
        if resp.status_code >= 400:
            raise _error(resp, "Adobe sign-in")
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise AgentError("PROVIDER_AUTH_FAILED", "Adobe sign-in returned no access token")
        lifetime = float(data.get("expires_in") or 3600)
        if lifetime > 10 * 86400:  # some IMS versions report milliseconds
            lifetime /= 1000
        with _TOKEN_LOCK:
            _TOKENS[key] = (token, time.time() + max(60.0, lifetime - 300))
        return token

    def _headers(self) -> dict[str, str]:
        headers = {"x-api-key": self.cfg.client_id, "Authorization": f"Bearer {self._token()}"}
        if self.cfg.model_version:
            headers["x-model-version"] = self.cfg.model_version
        return headers

    def check(self) -> tuple[bool, str]:
        """Signing in is free and proves the credentials; it does not generate (or bill) an image."""
        try:
            self._token(force=True)
        except AgentError as exc:
            return False, exc.message
        return True, "Adobe accepted the credentials"

    # --- generation ---------------------------------------------------------------
    def _status_url(self, job: dict) -> str:
        """Only ever send the token to the Firefly API host, whatever URL the response names."""
        url = job.get("statusUrl") or ""
        if url and urlsplit(url).netloc == self._api_host and urlsplit(url).scheme == "https":
            return url
        job_id = job.get("jobId")
        if not job_id:
            raise AgentError("PROVIDER_EMPTY_RESPONSE", "Firefly accepted the request but returned no job id", retryable=True)
        return f"{self.cfg.api_url}/v3/status/{quote(str(job_id), safe=':')}"

    def generate_image(self, prompt: str, aspect_ratio: str, *, negative: str = "",
                       on_wait: Callable[[], None] | None = None) -> tuple[bytes, dict]:
        width, height = size_for(self.cfg.model_version, aspect_ratio)
        body: dict = {"prompt": prompt[:MAX_PROMPT], "contentClass": self.cfg.content_class, "numVariations": 1,
                      "size": {"width": width, "height": height}}
        if negative:
            body["negativePrompt"] = negative[:MAX_PROMPT]
        try:
            resp = self._http.post(f"{self.cfg.api_url}/v3/images/generate-async", json=body, headers=self._headers())
            if resp.status_code == 401:  # token revoked or expired early: sign in again once
                self._token(force=True)
                resp = self._http.post(f"{self.cfg.api_url}/v3/images/generate-async", json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise AgentError("PROVIDER_UNAVAILABLE", f"Firefly request: {exc}", retryable=True) from exc
        if resp.status_code >= 400:
            raise _error(resp, "Firefly generate")
        status_url = self._status_url(resp.json())

        deadline = time.monotonic() + get_settings().firefly_timeout_seconds
        delay = 1.0
        while True:
            try:
                st = self._http.get(status_url, headers=self._headers())
            except httpx.HTTPError as exc:
                raise AgentError("PROVIDER_UNAVAILABLE", f"Firefly status: {exc}", retryable=True) from exc
            if st.status_code >= 400:
                raise _error(st, "Firefly status")
            data = st.json()
            status = str(data.get("status", "")).lower()
            if status == "succeeded":
                break
            if status in ("failed", "cancelled", "canceled"):
                reason = data.get("message") or data.get("error_code") or data.get("errors") or status
                raise AgentError("PROVIDER_REQUEST_REJECTED", f"Firefly job {status}: {str(reason)[:300]}")
            if time.monotonic() > deadline:
                raise AgentError("PROVIDER_TIMEOUT", "Firefly did not finish the image in time", retryable=True)
            if on_wait:
                on_wait()
            time.sleep(delay)
            delay = min(delay * 1.5, 4.0)

        result = data.get("result") or {}
        outputs = result.get("outputs") or []
        url = ((outputs[0] if outputs else {}).get("image") or {}).get("url")
        if not url or urlsplit(url).scheme != "https":
            raise AgentError("PROVIDER_EMPTY_RESPONSE", "Firefly returned no image", retryable=True)
        try:
            img = self._http.get(url)  # pre-signed storage URL: deliberately no Adobe credentials
        except httpx.HTTPError as exc:
            raise AgentError("PROVIDER_UNAVAILABLE", f"Firefly image download: {exc}", retryable=True) from exc
        if img.status_code >= 400 or not img.content:
            raise AgentError("PROVIDER_UNAVAILABLE", f"Firefly image download: HTTP {img.status_code}", retryable=True)
        meta = {"firefly_job": str(data.get("jobId", ""))[:120], "firefly_size": f"{width}x{height}",
                "firefly_model": self.cfg.model_version or "default", "firefly_content_class": self.cfg.content_class}
        if seed := (outputs[0] or {}).get("seed"):
            meta["seed"] = seed
        return img.content, meta
