"""API error type and helpers.

Every error reaches the browser as
    HTTP <status>  {"error": {"code": "...", "message": "...", "details": ...}}
"""
from typing import Any, Optional


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def to_json(self) -> dict:
        err = {"code": self.code, "message": self.message}
        if self.details is not None:
            err["details"] = self.details
        return {"error": err}


def bad_request(message: str, details: Any = None, code: str = "bad_request") -> ApiError:
    return ApiError(400, code, message, details)


def field_error(field: str, message: str) -> ApiError:
    """Validation failure tied to one form field (the UI highlights it)."""
    return ApiError(400, "invalid_field", message, {"field": field})


def unauthorized(message: str = "Login required") -> ApiError:
    return ApiError(401, "unauthorized", message)


def forbidden(message: str, code: str = "forbidden", details: Any = None) -> ApiError:
    return ApiError(403, code, message, details)


def not_found(message: str = "Not found") -> ApiError:
    return ApiError(404, "not_found", message)


def conflict(message: str, details: Any = None) -> ApiError:
    return ApiError(409, "conflict", message, details)


def too_many(message: str, retry_after: Optional[int] = None) -> ApiError:
    return ApiError(429, "rate_limited", message, {"retry_after": retry_after} if retry_after else None)


def vendor_error(message: str, details: Any = None) -> ApiError:
    """The vendor CLI/REST layer refused or failed."""
    return ApiError(502, "vendor_error", message, details)


def unavailable(message: str, details: Any = None) -> ApiError:
    """Feature not available on this unit/firmware/state (e.g. no SIM)."""
    return ApiError(503, "unavailable", message, details)
