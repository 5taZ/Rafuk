"""G-05: AI audit write failure propagation under strict/lax modes."""
from __future__ import annotations

import pytest

from api.services.ai_audit import _log_ai_audit


class _BrokenSessionFactory:
    """Session factory that raises on commit (simulates audit write failure)."""

    def __call__(self):
        return _BrokenSession()


class _BrokenSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    def add(self, obj):
        pass

    async def scalar(self, stmt):
        return 1

    async def commit(self):
        raise RuntimeError("DB write failed")

    async def execute(self, stmt):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.scalar_one_or_none.return_value = 1
        return m


@pytest.mark.asyncio
async def test_ai_audit_swallows_error_when_lax(monkeypatch) -> None:
    """Default (ai_audit_required=False): write failure is swallowed."""
    from unittest.mock import MagicMock
    fake_settings = MagicMock()
    fake_settings.ai_audit_required = False
    fake_settings.audit_hash_secret = None
    fake_settings.bot_token.get_secret_value.return_value = "test-token"
    monkeypatch.setattr("api.config.get_settings", lambda: fake_settings)
    # Clear cached secret so it re-derives from our fake settings
    import api.services.ai_audit as _mod
    _mod._audit_secret_cache = None
    try:
        # Should NOT raise
        await _log_ai_audit(
            _BrokenSessionFactory(),
            telegram_user_id=1,
            endpoint="test",
            model="m",
        )
    finally:
        _mod._audit_secret_cache = None


@pytest.mark.asyncio
async def test_ai_audit_propagates_error_when_strict(monkeypatch) -> None:
    """G-05: ai_audit_required=True → write failure propagates."""
    from unittest.mock import MagicMock
    fake_settings = MagicMock()
    fake_settings.ai_audit_required = True
    fake_settings.audit_hash_secret = None
    fake_settings.bot_token.get_secret_value.return_value = "test-token"
    monkeypatch.setattr("api.config.get_settings", lambda: fake_settings)
    import api.services.ai_audit as _mod
    _mod._audit_secret_cache = None
    try:
        with pytest.raises(RuntimeError, match="DB write failed"):
            await _log_ai_audit(
                _BrokenSessionFactory(),
                telegram_user_id=1,
                endpoint="test",
                model="m",
            )
    finally:
        _mod._audit_secret_cache = None
