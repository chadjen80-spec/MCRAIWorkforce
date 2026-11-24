"""Feature flags. The café product is off unless CAFE_ENABLED=true; each module is off unless listed in CAFE_MODULES."""
from __future__ import annotations

from app.config import get_settings
from app.errors import AppError

ALL_MODULES = ("devices", "sessions", "launcher", "assistant", "assist", "orders", "pos", "members", "printing", "reports")


def enabled(module: str) -> bool:
    return module in get_settings().cafe_module_set


def require(module: str) -> None:
    if not enabled(module):
        raise AppError(404, "FEATURE_DISABLED", f"The café '{module}' module is not enabled on this server")


def module_dep(module: str):
    """FastAPI dependency factory: Depends(module_dep("launcher"))."""

    def _check() -> None:
        require(module)

    return _check


def features() -> dict:
    s = get_settings()
    return {"enabled": s.cafe_enabled, "modules": {m: m in s.cafe_module_set for m in ALL_MODULES}}
