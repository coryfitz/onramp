"""Opinionated helpers for OnRamp JSON APIs."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping, TypeVar

from starlette.requests import Request
from starlette.responses import JSONResponse


class APIError(RuntimeError):
    """A safe, structured API error that may be returned to clients."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 400,
        code: str = "invalid",
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = dict(details or {})


async def api_exception_handler(_request: Request, error: APIError) -> JSONResponse:
    payload: dict[str, Any] = {"error": str(error), "code": error.code}
    if error.details:
        payload["details"] = error.details
    return JSONResponse(payload, status_code=error.status)


async def json_body(request: Request) -> dict[str, Any]:
    """Read a JSON object or raise a consistent client-safe error."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise APIError("Send a valid JSON request body.") from error
    if not isinstance(body, dict):
        raise APIError("Send a JSON object.")
    return body


T = TypeVar("T")


def validate_body(body: Mapping[str, Any], schema: Callable[[Mapping[str, Any]], T]) -> T:
    """Validate a body with a callable or a Pydantic-compatible model class."""
    try:
        validator = getattr(schema, "model_validate", None)
        return validator(body) if validator else schema(body)
    except APIError:
        raise
    except Exception as error:
        details = getattr(error, "errors", lambda: [])()
        raise APIError(
            "The request body is invalid.",
            code="validation_error",
            details={"errors": details} if details else None,
        ) from error


def require_fields(body: Mapping[str, Any], *names: str) -> None:
    missing = [
        name
        for name in names
        if body.get(name) is None or body.get(name) == ""
    ]
    if missing:
        raise APIError(
            "Required fields are missing.",
            code="missing_fields",
            details={"fields": missing},
        )


def bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


@dataclass(frozen=True)
class PageRequest:
    limit: int
    offset: int


def pagination(request: Request, *, default_limit: int = 50, max_limit: int = 200) -> PageRequest:
    """Parse bounded limit/offset query parameters."""
    try:
        limit = int(request.query_params.get("limit", default_limit))
        offset = int(request.query_params.get("offset", 0))
    except (TypeError, ValueError) as error:
        raise APIError("Pagination values must be integers.") from error
    if limit < 1 or limit > max_limit or offset < 0:
        raise APIError(
            f"Use limit 1–{max_limit} and a non-negative offset.",
            code="invalid_pagination",
        )
    return PageRequest(limit=limit, offset=offset)


def page_response(items: list[Any], page: PageRequest, *, total: int | None = None) -> JSONResponse:
    payload: dict[str, Any] = {
        "items": items,
        "limit": page.limit,
        "offset": page.offset,
    }
    if total is not None:
        payload["total"] = total
    return JSONResponse(payload)
