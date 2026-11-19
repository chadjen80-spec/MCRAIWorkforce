from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from datetime import timedelta

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.db import utcnow
from app.errors import AppError

JWT_ALG = "HS256"


# --- passwords ---------------------------------------------------------------
def _prehash(password: str) -> bytes:
    # bcrypt only reads 72 bytes; pre-hash so long passphrases keep their entropy.
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("ascii")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("ascii"))
    except ValueError:
        return False


# --- JWT -----------------------------------------------------------------------
def create_access_token(user_id: uuid.UUID, organization_id: uuid.UUID, role: str) -> tuple[str, int]:
    settings = get_settings()
    expires = settings.jwt_expires_minutes * 60
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "org": str(organization_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG), expires


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALG])


# --- API keys --------------------------------------------------------------------
def generate_api_key() -> tuple[str, str, str]:
    """Returns (plaintext, prefix, sha256 hash). The plaintext is shown once and never stored."""
    key = "mcr_" + secrets.token_urlsafe(32)
    return key, key[:12], hash_api_key(key)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# --- signed artifact URLs ----------------------------------------------------------
def _artifact_sig(artifact_id: str, exp: int) -> str:
    secret = (get_settings().artifact_url_secret or "").encode("utf-8")
    return hmac.new(secret, f"{artifact_id}:{exp}".encode(), hashlib.sha256).hexdigest()


def sign_artifact_url(artifact_id: uuid.UUID) -> str:
    exp = int(time.time()) + get_settings().artifact_url_ttl_seconds
    return f"/v1/artifacts/{artifact_id}/download?exp={exp}&sig={_artifact_sig(str(artifact_id), exp)}"


def verify_artifact_signature(artifact_id: uuid.UUID, exp: int, sig: str) -> bool:
    if exp < int(time.time()):
        return False
    return hmac.compare_digest(_artifact_sig(str(artifact_id), exp), sig)


# Logos use their own signature namespace, so an artifact link can never open an asset or vice versa.
def sign_asset_url(asset_id: uuid.UUID) -> str:
    exp = int(time.time()) + get_settings().artifact_url_ttl_seconds
    return f"/v1/assets/{asset_id}/file?exp={exp}&sig={_artifact_sig(f'asset:{asset_id}', exp)}"


def verify_asset_signature(asset_id: uuid.UUID, exp: int, sig: str) -> bool:
    if exp < int(time.time()):
        return False
    return hmac.compare_digest(_artifact_sig(f"asset:{asset_id}", exp), sig)


# --- provider secret encryption -------------------------------------------------------
def _fernet() -> Fernet:
    key = get_settings().secrets_encryption_key
    if not key:
        raise AppError(
            409,
            "ENCRYPTION_NOT_CONFIGURED",
            "SECRETS_ENCRYPTION_KEY is not set; provider keys cannot be stored in the database. "
            "Set it or configure the provider via environment variables.",
        )
    return Fernet(key.encode("ascii"))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, AppError):
        return None
