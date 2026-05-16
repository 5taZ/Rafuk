"""Session-level security primitives — blacklist + replay telemetry.

These two concerns share a backing store (Redis cache) and a
read-fast-path on the request hot path, so they live together.

Both functions degrade gracefully when the cache is unavailable:
``is_user_blacklisted`` returns False (fail-open — losing the
blacklist is preferable to losing all auth on a Redis outage), and
``track_init_data_use`` becomes a no-op. Both reasons are logged.

Key namespaces:

* ``auth:blacklist:{user_id}`` → presence means user is blocked.
  Set by ops via ``redis-cli SET auth:blacklist:42 1 EX 86400``.
* ``auth:initdata:{sha256-of-initdata}`` → JSON {ip, first_seen_ts}.
  Used to flag replay-from-different-IP heuristically.
"""

from __future__ import annotations

import hashlib
import logging
from time import time
from typing import Any

logger = logging.getLogger(__name__)


_BLACKLIST_KEY = "auth:blacklist:{user_id}"
_INITDATA_KEY = "auth:initdata:{digest}"
# Match telegram_init_data_max_age (default 7200s). Longer doesn't help
# because stale init_data can't reach this code anyway.
_INITDATA_TTL_SECONDS = 7200

# OPUS-8: replay auto-blacklist. Per-user counter that increments on
# each IP-mismatch warning; once it crosses _REPLAY_AUTOBLOCK_THRESHOLD
# inside the rolling _REPLAY_WARN_TTL_SECONDS window we mark the user
# blacklisted for _REPLAY_BLOCK_TTL_SECONDS so a stolen-initData
# replay loses access automatically rather than waiting for ops to
# spot the WARNING line in logs.
_REPLAY_WARN_KEY = "auth:replay_warn:{user_id}"
_REPLAY_WARN_TTL_SECONDS = 3600
_REPLAY_AUTOBLOCK_THRESHOLD = 5
_REPLAY_BLOCK_TTL_SECONDS = 24 * 3600


async def is_user_blacklisted(cache: Any, user_id: int) -> bool:
    """Return True if the given Telegram user_id is currently blocked.

    The cache backend may be a real Redis or a per-process MemoryCache;
    both implement ``get_json``. A None / missing entry means "not on
    the list". Cache lookup failures are logged and treated as
    not-blacklisted (fail-open) — alternative would be to lock everyone
    out the moment Redis blips, which is worse.
    """
    if cache is None:
        return False
    try:
        value = await cache.get_json(_BLACKLIST_KEY.format(user_id=int(user_id)))
    except Exception:  # noqa: BLE001 — fail-open, log and continue
        logger.warning(
            "session_security.blacklist_lookup_failed user_id=%s", user_id, exc_info=True,
        )
        return False
    return value is not None


async def track_init_data_use(
    cache: Any,
    init_data: str,
    *,
    user_id: int,
    client_ip: str | None,
) -> None:
    """Record IP per initData hash; warn on IP mismatch.

    The Mini App reuses the same Telegram-signed initData for every
    request during a session, so we cannot reject duplicates outright.
    What we *can* do is notice when the same initData starts being
    presented from multiple distinct IPs in quick succession — that's a
    classic exfiltration pattern: attacker grabs the blob via XSS and
    replays it from their own host while the legitimate user is still
    online.

    This function is best-effort observability. It does NOT block the
    request — instead it logs a WARNING the operator can use to feed
    a blocklist. Cache failures are silent (no-op).
    """
    if cache is None or not init_data:
        return
    try:
        digest = hashlib.sha256(init_data.encode("utf-8")).hexdigest()
        key = _INITDATA_KEY.format(digest=digest)
        existing = await cache.get_json(key)
        ip = client_ip or "unknown"
        now = int(time())
        if existing is None:
            await cache.set_json(
                key,
                {"ip": ip, "first_seen_ts": now, "user_id": int(user_id)},
                ttl=_INITDATA_TTL_SECONDS,
            )
            # PR-11: re-read to catch the race where two concurrent
            # first-time requests both saw ``existing is None`` and
            # both wrote — only one IP wins, but we want to flag
            # the loser the same way an after-the-fact mismatch
            # would. ``get_json`` is cheap on the local Redis hop
            # and this branch only fires on the very first observation
            # for a digest, so the cost is bounded.
            settled = await cache.get_json(key)
            if isinstance(settled, dict):
                settled_ip = settled.get("ip")
                if settled_ip and settled_ip != ip:
                    logger.warning(
                        "session_security.initdata_first_write_race "
                        "user_id=%s digest=%s observed_ip=%s settled_ip=%s",
                        user_id, digest[:12], ip, settled_ip,
                    )
                    await _record_replay_warning(cache, user_id)
            return
        first_ip = existing.get("ip")
        if first_ip and first_ip != ip:
            logger.warning(
                "session_security.initdata_ip_mismatch "
                "user_id=%s digest=%s first_ip=%s observed_ip=%s "
                "first_seen=%s",
                user_id, digest[:12], first_ip, ip, existing.get("first_seen_ts"),
            )
            # OPUS-8: increment a rolling per-user counter and
            # auto-blacklist once it crosses the threshold. ``incr`` is
            # atomic on Redis and best-effort on MemoryCache; either
            # way a transient cache failure here can't escalate the
            # outcome past "warning logged" — the counter just resets
            # on the next failed attempt.
            await _record_replay_warning(cache, user_id)
    except Exception:  # noqa: BLE001 — observability path, never raise
        logger.debug(
            "session_security.track_init_data_use failed", exc_info=True,
        )


async def _record_replay_warning(cache: Any, user_id: int) -> None:
    """Bump the rolling replay counter; auto-blacklist on threshold.

    OPUS-8: ops used to learn about replay attempts only by reading
    the WARNING log line in ``track_init_data_use``. That made the
    signal useless without a human in the loop. The threshold +
    auto-blacklist closes the loop without waiting on ops while
    keeping a generous margin for legitimate IP changes
    (cellular ↔ wifi, CG-NAT churn) — five mismatches inside one
    hour is well past organic noise.
    """
    if cache is None:
        return
    incr = getattr(cache, "incr", None)
    if incr is None:
        return
    warn_key = _REPLAY_WARN_KEY.format(user_id=int(user_id))
    try:
        count = await incr(warn_key, ttl=_REPLAY_WARN_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — best-effort counter
        return
    if count and count >= _REPLAY_AUTOBLOCK_THRESHOLD:
        try:
            await cache.set_json(
                _BLACKLIST_KEY.format(user_id=int(user_id)),
                {"reason": "replay_autoblock", "since_ts": int(time())},
                ttl=_REPLAY_BLOCK_TTL_SECONDS,
            )
        except Exception:  # noqa: BLE001 — best-effort blacklist
            return
        logger.error(
            "session_security.initdata_replay_autoblock "
            "user_id=%s warnings=%d window_seconds=%d block_seconds=%d",
            user_id, count, _REPLAY_WARN_TTL_SECONDS, _REPLAY_BLOCK_TTL_SECONDS,
        )
