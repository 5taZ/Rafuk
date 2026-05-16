from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

from api.middleware.telegram_auth import TelegramInitData
from api.services.client_ip import get_client_ip


def _request(headers: dict[str, str] | None = None, client_host: str | None = "10.0.0.1"):
    client = SimpleNamespace(host=client_host) if client_host else None
    return SimpleNamespace(headers=Headers(headers or {}), client=client)


def test_client_ip_prefers_valid_cloudflare_connecting_ip_from_trusted_peer() -> None:
    request = _request(
        {"CF-Connecting-IP": "203.0.113.10", "X-Forwarded-For": "198.51.100.7"},
        client_host="173.245.48.1",
    )
    assert get_client_ip(request) == "203.0.113.10"


def test_client_ip_ignores_cloudflare_connecting_ip_from_untrusted_peer() -> None:
    request = _request({"CF-Connecting-IP": "203.0.113.10"}, client_host="8.8.8.8")
    assert get_client_ip(request) == "8.8.8.8"


def test_client_ip_uses_x_forwarded_for_from_cloudflare_peer() -> None:
    """OPUS-16: pick the FIRST entry — that's the original client.
    The list ``198.51.100.7, 203.0.113.10`` means 198.51.100.7
    started the chain; everything after is intermediate proxy hops.
    """
    request = _request(
        {"X-Forwarded-For": "198.51.100.7, 203.0.113.10"},
        client_host="173.245.48.1",
    )
    assert get_client_ip(request) == "198.51.100.7"


def test_client_ip_uses_x_forwarded_for_single_entry() -> None:
    """Common single-hop topology (CF only): first==last anyway."""
    request = _request(
        {"X-Forwarded-For": "203.0.113.10"},
        client_host="173.245.48.1",
    )
    assert get_client_ip(request) == "203.0.113.10"


def test_client_ip_trusts_cloudflare_ipv6_peer() -> None:
    """SEC-MEDIUM (issues §1.5): the CF IPv6 prefix list must be
    populated, otherwise the trusted-peer check rejects every IPv6
    edge and we silently bucket all IPv6 traffic by a single CF IP."""
    request = _request(
        {"CF-Connecting-IP": "2001:db8::42"},
        client_host="2606:4700:4400::1",
    )
    assert get_client_ip(request) == "2001:db8::42"


def test_client_ip_ignores_x_forwarded_for_from_untrusted_peer() -> None:
    request = _request({"X-Forwarded-For": "203.0.113.10"}, client_host="10.0.0.1")
    assert get_client_ip(request) == "10.0.0.1"


def test_client_ip_falls_back_to_request_client_host() -> None:
    assert get_client_ip(_request()) == "10.0.0.1"


def test_client_ip_ignores_invalid_cloudflare_connecting_ip() -> None:
    request = _request({"CF-Connecting-IP": "not an ip"}, client_host="10.0.0.1")
    assert get_client_ip(request) == "10.0.0.1"


@pytest.mark.asyncio
async def test_telegram_auth_replay_telemetry_uses_client_ip_helper(monkeypatch) -> None:
    from api import dependencies
    from api.services import session_security

    class _Secret:
        def get_secret_value(self) -> str:
            return "fake-token"

    class _Settings:
        auth_bypass = False
        bot_token = _Secret()
        telegram_init_data_max_age = 3600
        # OPUS-12: internal-service path is checked first; setting
        # the token to None forces the test through the legacy
        # initData branch we actually want to exercise here.
        internal_service_token = None

    captured: dict[str, str | None] = {}

    async def _not_blacklisted(_cache, _user_id):
        return False

    async def _track(_cache, _init_data, *, user_id, client_ip):
        captured["user_id"] = user_id
        captured["client_ip"] = client_ip

    monkeypatch.setattr(dependencies, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        dependencies,
        "verify_telegram_init_data",
        lambda *_args, **_kwargs: TelegramInitData(user_id=77, first_name="Test", raw={}),
    )
    monkeypatch.setattr(session_security, "is_user_blacklisted", _not_blacklisted)
    monkeypatch.setattr(session_security, "track_init_data_use", _track)

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(cache=object())),
        client=SimpleNamespace(host="173.245.48.1"),
        headers=Headers({"X-Forwarded-For": "198.51.100.7, 203.0.113.10"}),
        state=SimpleNamespace(),
    )

    user = await dependencies.get_telegram_user(request, x_telegram_init_data="init")

    assert user.user_id == 77
    assert captured["user_id"] == 77
    # OPUS-16: first XFF entry is the original client.
    assert captured["client_ip"] == "198.51.100.7"
