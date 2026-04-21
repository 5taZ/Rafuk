"""Shared rate limiter instance for the application.

Keys by Telegram user_id when available (authenticated endpoints),
falls back to client IP for public endpoints.
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _rate_limit_key(request: Request) -> str:
    """Use telegram user_id when available, otherwise fall back to IP."""
    # Check if a Telegram user was already parsed by the dependency.
    # This works when the endpoint uses Depends(get_telegram_user) and
    # the dependency has already run (it runs before the limiter check).
    init_data = getattr(request.state, "telegram_user", None)
    if init_data is not None:
        return f"tg:{init_data.user_id}"

    # Try to extract from the header directly (for endpoints that check
    # auth inside the handler rather than via Depends).
    auth_header = request.headers.get("x-telegram-init-data", "")
    if auth_header:
        try:
            from api.middleware.telegram_auth import verify_telegram_init_data

            user = verify_telegram_init_data(
                auth_header, request.app.state.settings.bot_token.get_secret_value()
            )
            return f"tg:{user.user_id}"
        except Exception:
            pass

    return get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key)
