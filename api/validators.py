from __future__ import annotations

from fastapi import Query

MAX_QUERY_LENGTH = 255


def validate_query(value: str) -> str:
    """Validate and sanitize query parameter."""
    if not value or not value.strip():
        raise ValueError("Query cannot be empty")
    if len(value) > MAX_QUERY_LENGTH:
        raise ValueError(f"Query exceeds maximum length of {MAX_QUERY_LENGTH} characters")
    return value.strip()


def query_param(
    default: str = ...,
    *,
    description: str = "Search query string",
    max_length: int = MAX_QUERY_LENGTH,
    **kwargs,
) -> str:
    """Create a validated query parameter for FastAPI endpoints."""
    return Query(
        default=default,
        min_length=1,
        max_length=max_length,
        description=description,
        **kwargs,
    )
