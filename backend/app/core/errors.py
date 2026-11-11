"""Stable API error envelope (docs/CONTRACTS.md §7).

All application errors raised by routers/services should be :class:`ApiError`
instances; ``app.main`` installs exception handlers that serialise them as::

    {"error": {"code": "...", "message": "...", "details": {...}, "retryable": bool}}
"""
from __future__ import annotations

from typing import Any


class ErrorCode:
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INVALID_ARGUMENTS = "invalid_arguments"
    VALIDATION_ERROR = "validation_error"
    RATE_LIMITED = "rate_limited"
    QWEN_UNAVAILABLE = "qwen_unavailable"
    INTERNAL_ERROR = "internal_error"


#: HTTP status suggested for each stable error code.
STATUS_FOR_CODE: dict[str, int] = {
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.INVALID_ARGUMENTS: 422,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.QWEN_UNAVAILABLE: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}


class ApiError(Exception):
    """Application error carrying the wire envelope fields."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.retryable = retryable
        self.headers = headers

    def to_payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "retryable": self.retryable,
            }
        }


def envelope(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    """Build a bare error envelope dict (used for WS error frames too)."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "retryable": retryable,
        }
    }


# --- convenience constructors ------------------------------------------------


def unauthorized(message: str = "Authentication required", **kwargs: Any) -> ApiError:
    return ApiError(
        401,
        ErrorCode.UNAUTHORIZED,
        message,
        details=kwargs.pop("details", None),
        headers={"WWW-Authenticate": "Bearer"},
        **kwargs,
    )


def forbidden(message: str = "You do not have access to this resource", **kwargs: Any) -> ApiError:
    return ApiError(403, ErrorCode.FORBIDDEN, message, **kwargs)


def not_found(message: str = "Resource not found", **kwargs: Any) -> ApiError:
    return ApiError(404, ErrorCode.NOT_FOUND, message, **kwargs)


def conflict(message: str = "Resource already exists", **kwargs: Any) -> ApiError:
    return ApiError(409, ErrorCode.CONFLICT, message, **kwargs)


def invalid_arguments(message: str = "Invalid arguments", **kwargs: Any) -> ApiError:
    return ApiError(422, ErrorCode.INVALID_ARGUMENTS, message, **kwargs)


def validation_error(message: str = "Validation failed", **kwargs: Any) -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR, message, **kwargs)


def rate_limited(message: str = "Too many requests", **kwargs: Any) -> ApiError:
    return ApiError(429, ErrorCode.RATE_LIMITED, message, retryable=True, **kwargs)


def qwen_unavailable(message: str = "Realtime tutor is unavailable", **kwargs: Any) -> ApiError:
    return ApiError(503, ErrorCode.QWEN_UNAVAILABLE, message, retryable=True, **kwargs)


def internal_error(message: str = "Internal server error", **kwargs: Any) -> ApiError:
    return ApiError(500, ErrorCode.INTERNAL_ERROR, message, **kwargs)
