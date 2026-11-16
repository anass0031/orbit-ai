"""FastAPI application factory."""
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import check_secrets, get_settings
from app.core.errors import error_body, install_error_handlers
from app.core.logging import configure_logging, correlation_id_var, log_event, new_correlation_id

logger = logging.getLogger("orbit.http")
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"}


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    for w in check_secrets(settings):  # raises on placeholder / unsafe production secrets
        log_event(logger, logging.WARNING, "config.insecure", detail=w)
    app = FastAPI(
        title="ORBIT AI API",
        version="0.3.0",
        description="ORBIT AI — week 3: JWT authentication, 3 roles, file import with automatic schema "
                    "discovery and Bronze layer in MinIO. All routes are versioned under /api/v1.",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,  # the token travels in the Authorization header, not in cookies
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
        expose_headers=["X-Correlation-ID"],
    )

    @app.middleware("http")
    async def correlation_and_logging(request: Request, call_next):
        cid = (request.headers.get("X-Correlation-ID") or new_correlation_id())[:32]
        token = correlation_id_var.set(cid)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # unexpected error → consistent envelope, logged with the correlation id
            logger.exception("unhandled error")
            response = JSONResponse(status_code=500, content=error_body("internal_error", "Unexpected server error"))
        elapsed = time.perf_counter() - t0
        if request.url.path != f"{settings.api_prefix}/health":
            log_event(logger, logging.INFO, "request", method=request.method, path=request.url.path,
                      status=response.status_code, ms=round(elapsed * 1000, 1))
        response.headers["X-Correlation-ID"] = cid
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith(settings.api_prefix):
            response.headers.setdefault("Cache-Control", "no-store")
        correlation_id_var.reset(token)
        return response

    install_error_handlers(app)
    _install_storage_handler(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    def root():
        return {"name": "ORBIT AI API", "docs": "/docs", "health": f"{settings.api_prefix}/health"}

    return app


def _install_storage_handler(app: FastAPI) -> None:
    """MinIO unreachable → 503 with a clear message instead of a generic 500."""
    try:
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:  # pragma: no cover
        return

    async def _storage(_: Request, exc: Exception):
        logger.warning("object storage error: %s", exc)
        return JSONResponse(status_code=503, content=error_body(
            "storage_unavailable", "Object storage (MinIO) is unreachable or refused the request. Is it running?"))

    app.add_exception_handler(BotoCoreError, _storage)
    app.add_exception_handler(ClientError, _storage)


app = create_app()
