from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from api.services import channel_subscription as cs
from api.services.cache import MemoryCache


def _settings(channel: str | None = "@RafuksToResell", admins: str = ""):
    class _S:
        bot_token = SecretStr("dummy")
        internal_service_token = None
        auth_bypass = False
        telegram_init_data_max_age = 7200
        required_channel = channel
        admin_telegram_user_ids = admins

    return _S()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@RafuksToResell", "RafuksToResell"),
        ("https://t.me/RafuksToResell", "RafuksToResell"),
        ("t.me/RafuksToResell/", "RafuksToResell"),
        ("RafuksToResell", "RafuksToResell"),
        ("", ""),
        (None, ""),
    ],
)
def test_required_channel_normalizes_links(raw, expected) -> None:
    assert cs.required_channel(_settings(raw)) == expected


def _mock_telegram(monkeypatch, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        cs.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(_record), **kw),
    )
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"ok": True, "result": {"status": "member"}}, True),
        ({"ok": True, "result": {"status": "creator"}}, True),
        ({"ok": True, "result": {"status": "administrator"}}, True),
        ({"ok": True, "result": {"status": "restricted", "is_member": True}}, True),
        ({"ok": True, "result": {"status": "restricted", "is_member": False}}, False),
        ({"ok": True, "result": {"status": "left"}}, False),
        ({"ok": True, "result": {"status": "kicked"}}, False),
        ({"ok": False, "error_code": 400, "description": "Bad Request: user not found"}, False),
        (
            {"ok": False, "error_code": 400,
             "description": "Bad Request: member list is inaccessible"},
            None,
        ),
        ({"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}, None),
    ],
)
async def test_fetch_channel_membership_reads_telegram_answer(
    monkeypatch, payload, expected,
) -> None:
    seen = _mock_telegram(monkeypatch, lambda request: httpx.Response(200, json=payload))

    assert await cs.fetch_channel_membership("123:abc", "RafuksToResell", 42) is expected
    assert seen[0].url.path == "/bot123:abc/getChatMember"
    assert seen[0].url.params["chat_id"] == "@RafuksToResell"
    assert seen[0].url.params["user_id"] == "42"


@pytest.mark.asyncio
async def test_fetch_channel_membership_network_error_is_unknown(monkeypatch, caplog) -> None:
    def _fail(request):
        raise httpx.ConnectError("boom https://api.telegram.org/bot123:abc/getChatMember")

    _mock_telegram(monkeypatch, _fail)
    assert await cs.fetch_channel_membership("123:abc", "RafuksToResell", 42) is None
    assert "123:abc" not in caplog.text


@pytest.mark.asyncio
async def test_only_membership_is_cached_and_outages_fail_open(monkeypatch) -> None:
    answers = [False, True, None]
    calls: list[int] = []

    async def fake_fetch(bot_token, channel, user_id):
        calls.append(user_id)
        return answers.pop(0)

    monkeypatch.setattr(cs, "fetch_channel_membership", fake_fetch)
    cache = MemoryCache()
    ttls: list[int | None] = []
    real_set = cache.set

    async def recording_set(key, value, ttl=None):
        ttls.append(ttl)
        await real_set(key, value, ttl=ttl)

    cache.set = recording_set
    kwargs = {"bot_token": "t", "channel": "RafuksToResell", "user_id": 42}

    assert await cs.is_channel_subscriber(cache, **kwargs) is False
    # A refusal is not cached: the user who just subscribed gets in.
    assert await cs.is_channel_subscriber(cache, **kwargs) is True
    assert await cs.is_channel_subscriber(cache, **kwargs) is True
    assert calls == [42, 42]
    # A user who leaves the channel is let in by the cache for a minute at most.
    assert 0 < ttls[-1] <= 60
    # use_cache=False asks Telegram, whose outage lets the user through.
    assert await cs.is_channel_subscriber(cache, use_cache=False, **kwargs) is True
    assert calls == [42, 42, 42]


def _request(cache):
    return SimpleNamespace(
        state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(cache=cache)),
        client=None, headers={},
    )


@pytest.mark.asyncio
async def test_api_refuses_mini_app_without_subscription(monkeypatch) -> None:
    from api import dependencies
    from tests.test_dependencies import _signed_init_data

    members = {7: True}

    async def fake_fetch(bot_token, channel, user_id):
        return members.get(user_id, False)

    monkeypatch.setattr(cs, "fetch_channel_membership", fake_fetch)
    monkeypatch.setattr(dependencies, "get_settings", lambda: _settings(admins="9"))
    now = int(time.time())

    request = _request(MemoryCache())
    with pytest.raises(HTTPException) as exc:
        await dependencies.get_telegram_user(
            request, x_telegram_init_data=_signed_init_data(8, auth_date=now),
            x_internal_service_token=None, x_acting_telegram_user_id=None,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "subscription_required"
    assert exc.value.detail["channel_url"] == "https://t.me/RafuksToResell"
    # Refused before the auto-provision middleware sees a user.
    assert not hasattr(request.state, "telegram_user")

    for user_id in (7, 9):  # subscriber, admin
        user = await dependencies.get_telegram_user(
            _request(MemoryCache()),
            x_telegram_init_data=_signed_init_data(user_id, auth_date=now),
            x_internal_service_token=None, x_acting_telegram_user_id=None,
        )
        assert user.user_id == user_id


@pytest.mark.asyncio
async def test_gate_is_off_without_required_channel(monkeypatch) -> None:
    async def unexpected(*args):
        raise AssertionError("Telegram must not be asked")

    monkeypatch.setattr(cs, "fetch_channel_membership", unexpected)
    await cs.require_channel_subscription(None, _settings(""), 8)
