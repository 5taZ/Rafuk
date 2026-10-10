from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Query
from pydantic import AfterValidator

MAX_QUERY_LENGTH = 255
EMPTY_QUERY_MESSAGE = "Запрос не может быть пустым"
LONG_QUERY_MESSAGE = f"Запрос длиннее {MAX_QUERY_LENGTH} символов"

# Currencies supported by market endpoints. BYN is the product default.
CurrencyCode = Literal["BYN", "USD"]
DEFAULT_CURRENCY: CurrencyCode = "BYN"


def validate_query(value: str) -> str:
    """Strip a search query and reject empty or overlong values."""
    if not isinstance(value, str):
        raise ValueError(EMPTY_QUERY_MESSAGE)
    stripped = value.strip()
    if not stripped:
        raise ValueError(EMPTY_QUERY_MESSAGE)
    if len(stripped) > MAX_QUERY_LENGTH:
        raise ValueError(LONG_QUERY_MESSAGE)
    return stripped


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
