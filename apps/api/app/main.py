from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import assets, assistant, jobs, posters, system
from app.config import get_settings
from app.errors import AppError
from app.logging_config import configure_logging

log = logging.getLogger("mcr.api")


def _error(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "details": details or {}}}, status_code=status)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    app = FastAPI(
        title="MCR AI Workforce API",
        version="0.1.0",
        description="Hybrid multi-agent AI automation platform for MCR SME Copilot. "
                    "Authenticate with `Authorization: Bearer <jwt>` or `X-API-Key`.",
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type", "X-API-Key", "Idempotency-Key"],
    )

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"request_id": request_id, "path": request.url.path})
            return _error(500, "INTERNAL_ERROR", "Unexpected server error")
        response.headers["X-Request-ID"] = request_id
        if not request.url.path.startswith("/health"):
            log.info("request", extra={"request_id": request_id, "method": request.method, "path": request.url.path,
                                       "status": response.status_code,
                                       "ms": round((time.perf_counter() - started) * 1000, 1)})
        return response

    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError):
        return _error(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        fields = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        message = "; ".join(f"{f['field']}: {f['message']}" for f in fields) or "Invalid request"
        return _error(422, "VALIDATION_ERROR", message, {"fields": fields})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        codes = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT"}
        return _error(exc.status_code, codes.get(exc.status_code, "HTTP_ERROR"), str(exc.detail))

    app.include_router(system.health_router)
    app.include_router(system.auth_router)
    app.include_router(system.settings_router)
    app.include_router(jobs.router)
    app.include_router(jobs.public_router)
    app.include_router(assistant.router)
    app.include_router(assets.router)
    app.include_router(assets.public_router)
    app.include_router(posters.router)
    return app


app = create_app()
