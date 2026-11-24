"""Command signing: ECDSA P-256 / SHA-256 over canonical JSON, signature as IEEE P1363 (r||s) base64url.

The seat client pins the public key at enrolment and refuses any command whose signature, device, organisation,
expiry or replay check fails. Signing a command does not make it safe to run: the client still only launches IDs
from its own local allowlist.
"""
from __future__ import annotations

import base64
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

from app.config import get_settings


def canonical(obj: dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@lru_cache(maxsize=4)
def _load(pem: str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("CAFE_COMMAND_SIGNING_KEY must be an EC P-256 private key")
    return key


def _dev_key_pem() -> str:
    """Development/test only: one key per DATA_DIR, created on first use, readable by the owner only."""
    path = Path(get_settings().data_dir) / "cafe_command_signing_key.pem"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
    return path.read_text("ascii")


def private_key() -> ec.EllipticCurvePrivateKey:
    settings = get_settings()
    pem = settings.cafe_command_signing_key.replace("\\n", "\n").strip()
    if not pem:
        if settings.environment == "production":
            raise RuntimeError("CAFE_COMMAND_SIGNING_KEY is not configured")
        pem = _dev_key_pem()
    return _load(pem)


def public_key_pem() -> str:
    return private_key().public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def sign(envelope: dict[str, Any]) -> tuple[str, str]:
    """Returns (canonical envelope text, signature). The client verifies the exact text it receives."""
    text = canonical(envelope)
    der = private_key().sign(text.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return text, _b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def verify(text: str, signature: str, public_pem: str | None = None) -> bool:
    """Used by tests and the Python test client; the real verifier is the C# client."""
    pub = (serialization.load_pem_public_key(public_pem.encode("ascii")) if public_pem else private_key().public_key())
    assert isinstance(pub, ec.EllipticCurvePublicKey)
    try:
        raw = _unb64u(signature)
        if len(raw) != 64:
            return False
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        pub.verify(der, text.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False
