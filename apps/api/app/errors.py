from __future__ import annotations

from typing import Any


class AppError(Exception):
    """API-level error rendered as {"error": {"code", "message", "details"}}."""

    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def not_found(what: str = "Resource") -> AppError:
    return AppError(404, "NOT_FOUND", f"{what} not found")


def conflict(message: str) -> AppError:
    return AppError(409, "CONFLICT", message)


def forbidden(message: str = "Not allowed") -> AppError:
    return AppError(403, "FORBIDDEN", message)


def unauthenticated(message: str = "Authentication required") -> AppError:
    return AppError(401, "UNAUTHENTICATED", message)


class AgentError(Exception):
    """Raised by agents/providers. `retryable` drives the retry/backoff policy."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class JobCancelled(Exception):
    pass
