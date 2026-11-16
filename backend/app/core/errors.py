"""Consistent error envelope: {"error": {code, message, details, correlation_id}}."""
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_correlation_id


class OrbitError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, details: Any = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details
        if code:
            self.code = code


class NotFound(OrbitError):
    status_code = 404
    code = "not_found"


class Forbidden(OrbitError):
    status_code = 403
    code = "forbidden"


class Unauthorized(OrbitError):
    status_code = 401
    code = "unauthorized"


class Conflict(OrbitError):
    status_code = 409
    code = "conflict"


class ValidationFailed(OrbitError):
    status_code = 422
    code = "validation_failed"


class ServiceUnavailable(OrbitError):
    status_code = 503
    code = "service_unavailable"


def error_body(code: str, message: str, details: Any = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "correlation_id": get_correlation_id(),
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(OrbitError)
    async def _orbit(_: Request, exc: OrbitError):
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content=error_body("validation_failed", "Request validation failed", details))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content=error_body("http_error", str(exc.detail)))
