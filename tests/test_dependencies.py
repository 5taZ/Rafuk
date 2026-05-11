from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_session_factory_dependency_returns_lifespan_state() -> None:
    from api.dependencies import get_session_factory_dependency

    factory = object()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_factory=factory)))

    assert get_session_factory_dependency(request) is factory


def test_session_factory_dependency_fails_without_lifespan_state() -> None:
    from api.dependencies import get_session_factory_dependency

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    with pytest.raises(RuntimeError, match="session_factory.*lifespan"):
        get_session_factory_dependency(request)
