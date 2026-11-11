"""FastAPI application factory.

Wiring:
* CORS from ``settings.cors_origins``
* stable error envelope (docs/CONTRACTS.md §7) for API errors, validation
  errors and unhandled exceptions
* lifespan: create tables in dev/test, bring up LangGraph checkpoint/Store
  persistence, dispose the DB engine on shutdown
* routers: ``/api/v1`` REST, ``/api/v1/ws`` realtime (backend-ai) and ``/healthz``
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.health import router as health_router
from app.api.router import api_router
from app.board.service import BoardService
from app.config import get_settings
from app.core.errors import ApiError, ErrorCode, envelope
from app.db.session import dispose_engine, init_models
from app.graph import service as graph_service
from app.realtime.router import configure_hub, router as realtime_router

logger = logging.getLogger(__name__)

_STATUS_TO_CODE: dict[int, str] = {
    400: ErrorCode.INVALID_ARGUMENTS,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.INVALID_ARGUMENTS,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.INVALID_ARGUMENTS,
    429: ErrorCode.RATE_LIMITED,
    503: ErrorCode.QWEN_UNAVAILABLE,
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info(
        "starting %s v%s (environment=%s)",
        settings.app_name,
        settings.version,
        settings.environment,
    )
    if settings.environment in {"development", "test"}:
        # Alembic owns production schema; dev/test get an idempotent create_all.
        await init_models()
    await graph_service.init_persistence()
    # Wire the realtime hub into the board service so committed board events
    # publish to live sockets (best-effort; never rolls back persistence).
    board = BoardService()
    configure_hub(board_service=board)
    try:
        yield
    finally:
        try:
            await graph_service.shutdown_persistence()
        except Exception:  # pragma: no cover - shutdown best effort
            logger.warning("LangGraph persistence shutdown failed", exc_info=True)
        await dispose_engine()
        logger.info("shutdown complete")


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=envelope(code, message, details=details, retryable=retryable),
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_payload(),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = []
        for error in exc.errors():
            errors.append(
                {
                    "loc": [str(part) for part in error.get("loc", [])],
                    "msg": error.get("msg", "invalid value"),
                    "type": error.get("type", "value_error"),
                }
            )
        return _error_response(
            422,
            ErrorCode.INVALID_ARGUMENTS,
            "Request validation failed",
            details={"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        status_code = exc.status_code
        code = _STATUS_TO_CODE.get(
            status_code,
            ErrorCode.INTERNAL_ERROR if status_code >= 500 else ErrorCode.INVALID_ARGUMENTS,
        )
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        if isinstance(exc.detail, (dict, list)):
            return _error_response(
                status_code, code, message, details={"detail": jsonable_encoder(exc.detail)}
            )
        return _error_response(
            status_code,
            code,
            message,
            retryable=status_code >= 500,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("unhandled error: %s", exc)
        return _error_response(
            500,
            ErrorCode.INTERNAL_ERROR,
            "Internal server error",
            retryable=True,
        )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="AI Personal Classroom backend — REST + realtime tutor.",
        lifespan=lifespan,
        debug=settings.debug,
    )

    origins = list(settings.cors_origins or ["*"])
    allow_credentials = "*" not in origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_prefix)
    app.include_router(realtime_router, prefix=settings.api_prefix)
    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=get_settings().debug,
    )
