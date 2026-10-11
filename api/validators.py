from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import AfterValidator

MAX_QUERY_LENGTH = 255
EMPTY_QUERY_MESSAGE = "Запрос не может быть пустым"
LONG_QUERY_MESSAGE = f"Запрос длиннее {MAX_QUERY_LENGTH} символов"
NUL_CHARACTER_MESSAGE = "Строка содержит недопустимый символ NUL"
NUL_CHARACTER = "\x00"

# Currencies supported by market endpoints. BYN is the product default.
CurrencyCode = Literal["BYN", "USD"]
DEFAULT_CURRENCY: CurrencyCode = "BYN"


def reject_nul(value: str) -> str:
    """Reject strings with NUL characters: Postgres text columns cannot store 0x00."""
    if NUL_CHARACTER in value:
        raise ValueError(NUL_CHARACTER_MESSAGE)
    return value


def validate_query(value: str) -> str:
    """Strip a search query and reject empty, overlong or NUL-containing values."""
    if not isinstance(value, str):
        raise ValueError(EMPTY_QUERY_MESSAGE)
    stripped = reject_nul(value).strip()
    if not stripped:
        raise ValueError(EMPTY_QUERY_MESSAGE)
    if len(stripped) > MAX_QUERY_LENGTH:
        raise ValueError(LONG_QUERY_MESSAGE)
    return stripped


def _nul_error(location: str, name: str, value: str) -> dict[str, object]:
    return {
        "type": "value_error",
        "loc": (location, name),
        "msg": f"Value error, {NUL_CHARACTER_MESSAGE}",
        "input": value,
    }


def reject_nul_in_request(request: Request) -> None:
    """App-wide dependency: answer 422 when a query or path parameter contains NUL.

    JSON bodies are covered by the string types in api/schemas.py; this catches the plain
    ``str`` parameters (sort, compare_query and so on) before they reach Kufar or the database.
    """
    errors = [
        _nul_error("query", name, value)
        for name, value in request.query_params.multi_items()
        if NUL_CHARACTER in name or NUL_CHARACTER in value
    ]
    errors.extend(
        _nul_error("path", name, value)
        for name, value in request.path_params.items()
        if isinstance(value, str) and NUL_CHARACTER in value
    )
    if errors:
        raise RequestValidationError(errors)


def query_param(
    default: str = ...,
    *,
    description: str = "Search query string",
    max_length: int = MAX_QUERY_LENGTH,
    **kwargs,
) -> str:
    """Create a length-limited query parameter for FastAPI endpoints."""
    return Query(
        default=default,
        min_length=1,
        max_length=max_length,
        description=description,
        **kwargs,
    )


# Market query parameter: stripped, non-empty, at most MAX_QUERY_LENGTH characters.
SearchQuery = Annotated[
    str,
    Query(min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    AfterValidator(validate_query),
]
BaseSearchQuery = Annotated[
    str,
    Query(min_length=1, max_length=MAX_QUERY_LENGTH, description="Base query"),
    AfterValidator(validate_query),
]
