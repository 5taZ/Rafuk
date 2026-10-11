from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update

from api.models import User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from api.middleware.telegram_auth import TelegramInitData

# Per-user quotas for user-scoped entities (M28). Every unique tracker query adds a Kufar
# request to each scheduler cycle, so the limits keep one account from starving the others.
MAX_ACTIVE_TRACKERS_PER_USER = 50
MAX_ACTIVE_SAVED_SEARCHES_PER_USER = 20
MAX_WATCHLIST_ITEMS_PER_USER = 300

_NAME_MAX_LENGTH = 128


class UserQuotaExceededError(Exception):
    """Raised when a user already owns the maximum number of entities of one kind."""

    def __init__(self, message: str, *, limit: int) -> None:
        super().__init__(message)
        self.message = message
        self.limit = limit


def _clip(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value[:_NAME_MAX_LENGTH] or None


async def ensure_user(
    session: AsyncSession,
    telegram_user_id: int,
    *,
    first_name: str | None = None,
    username: str | None = None,
) -> None:
    """Make sure a ``users`` row exists for the Telegram user (no commit).

    Every ``user_id`` column stores the Telegram user id and references
    ``users.telegram_user_id``, so this must run in the same transaction before any
    user-scoped row is inserted. Uses a dialect-native upsert where available.
    """
    now = datetime.now(UTC)
    first_name = _clip(first_name)
    username = _clip(username)
    insert_values: dict[str, Any] = {
        "telegram_user_id": telegram_user_id,
        "first_name": first_name or "",
        "username": username,
        "last_seen_at": now,
    }
    update_values: dict[str, Any] = {"last_seen_at": now}
    if first_name:
        update_values["first_name"] = first_name
    if username:
        update_values["username"] = username

    dialect_name = session.get_bind().dialect.name
    if dialect_name in {"postgresql", "sqlite"}:
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        statement = (
            insert(User)
            .values(**insert_values)
            .on_conflict_do_update(index_elements=[User.telegram_user_id], set_=update_values)
        )
        await session.execute(statement)
        return

    # Generic fallback: read first, insert inside a savepoint and tolerate a concurrent insert.
    from sqlalchemy.exc import IntegrityError

    exists = await session.scalar(
        select(User.telegram_user_id).where(User.telegram_user_id == telegram_user_id)
    )
    if exists is None:
        try:
            async with session.begin_nested():
                session.add(User(**insert_values))
            return
        except IntegrityError:
            pass
    await session.execute(
        update(User).where(User.telegram_user_id == telegram_user_id).values(**update_values)
    )


async def lock_user(session: AsyncSession, telegram_user_id: int) -> None:
    """Hold the user's ``users`` row lock until the transaction ends (no commit).

    ``SELECT ... FOR UPDATE`` on Postgres, so check-then-insert sequences of one user (the
    duplicate and quota checks of a new tracker) run one at a time. SQLite has no row locks
    and drops ``FOR UPDATE``; there the preceding ``ensure_user`` write already takes the
    database write lock. The row must exist: call ``ensure_user`` first.
    """
    await session.execute(
        select(User.telegram_user_id)
        .where(User.telegram_user_id == telegram_user_id)
        .with_for_update()
    )


def init_data_username(telegram_user: TelegramInitData) -> str | None:
    """Extract ``username`` from the raw initData ``user`` JSON, if present."""
    raw_user = telegram_user.raw.get("user") if telegram_user.raw else None
    if not raw_user:
        return None
    try:
        payload = json.loads(raw_user)
    except (TypeError, ValueError):
        return None
    username = payload.get("username") if isinstance(payload, dict) else None
    return str(username) if username else None


async def ensure_init_data_user(session: AsyncSession, telegram_user: TelegramInitData) -> None:
    """``ensure_user`` for an authenticated Mini App request."""
    await ensure_user(
        session,
        telegram_user.user_id,
        first_name=telegram_user.first_name,
        username=init_data_username(telegram_user),
    )


async def count_user_rows(
    session: AsyncSession,
    model: Any,
    user_id: int,
    *conditions: Any,
) -> int:
    """Count rows of ``model`` owned by ``user_id`` that match the extra conditions."""
    total = await session.scalar(
        select(func.count()).select_from(model).where(model.user_id == user_id, *conditions)
    )
    return int(total or 0)
