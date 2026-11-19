"""Authentication, organization context and rate limiting dependencies."""
from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, utcnow
from app.errors import AppError, forbidden, unauthenticated
from app.models import ApiKey, Membership, Organization, User
from app.security import decode_access_token, hash_api_key


@dataclass
class Principal:
    organization: Organization
    role: str
    user: User | None = None
    api_key_name: str | None = None

    @property
    def actor(self) -> str:
        if self.user is not None:
            return f"user:{self.user.email}"
        return "n8n" if (self.api_key_name or "").lower().startswith("n8n") else f"api_key:{self.api_key_name}"

    @property
    def is_admin(self) -> bool:
        return self.role in ("owner", "admin")


# --- rate limiting -----------------------------------------------------------
class RateLimiter:
    """Sliding window. Uses Redis when REDIS_URL is set (shared across API replicas), else process memory."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._redis = None
        url = get_settings().redis_url
        if url:
            import redis

            self._redis = redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)

    def allow(self, key: str, limit: int, window: int = 60) -> bool:
        if limit <= 0:
            return True
        if self._redis is not None:
            try:
                bucket = f"rl:{key}:{int(time.time()) // window}"
                pipe = self._redis.pipeline()
                pipe.incr(bucket)
                pipe.expire(bucket, window * 2)
                return int(pipe.execute()[0]) <= limit
            except Exception:  # noqa: BLE001 - fall back to local limiting if Redis is down
                pass
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True


_limiter: RateLimiter | None = None


def limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limited(key: str, limit: int) -> None:
    if not limiter().allow(key, limit):
        raise AppError(429, "RATE_LIMITED", "Too many requests. Please retry in a minute.")


# --- authentication ------------------------------------------------------------
def get_principal(
    request: Request,
    session: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Principal:
    settings = get_settings()
    principal: Principal | None = None
    if x_api_key:
        key = session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(x_api_key), ApiKey.revoked_at.is_(None)))
        if key is None:
            rate_limited(f"authfail:{client_ip(request)}", settings.login_rate_limit_per_minute * 3)
            raise unauthenticated("Invalid API key")
        org = session.get(Organization, key.organization_id)
        assert org is not None
        key.last_used_at = utcnow()
        session.commit()
        # Service keys (n8n, integrations) may submit and follow jobs but are never admins: approving
        # deliverables, deleting shared data and changing settings or provider keys need a signed-in
        # owner or admin, so an automation can never approve the work it submitted.
        principal = Principal(organization=org, role="service", api_key_name=key.name)
    elif authorization and authorization.lower().startswith("bearer "):
        try:
            claims = decode_access_token(authorization[7:].strip())
            user_id, org_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["org"])
        except (jwt.PyJWTError, KeyError, ValueError) as exc:
            raise unauthenticated("Invalid or expired token") from exc
        user = session.get(User, user_id)
        membership = session.scalar(
            select(Membership).where(Membership.user_id == user_id, Membership.organization_id == org_id))
        org = session.get(Organization, org_id)
        if user is None or not user.is_active or membership is None or org is None:
            raise unauthenticated("Account is not active")
        principal = Principal(organization=org, role=membership.role, user=user)
    if principal is None:
        raise unauthenticated()
    rate_limited(f"org:{principal.organization.id}:{principal.actor}", settings.rate_limit_per_minute)
    return principal


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin:
        raise forbidden("Owner or admin role required")
    return principal
