from __future__ import annotations

from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app
from api.metrics import (
    _reset_metrics_for_tests,
    observe_http_request_with_backend,
    observe_query_dataset_event,
    observe_query_dataset_event_with_backend,
    observe_query_dataset_upstream_fetch,
    observe_query_dataset_upstream_fetch_with_backend,
    render_prometheus_metrics,
    render_prometheus_metrics_with_backend,
)


def _remote_settings(metrics_bearer_token: str | None = None) -> Settings:
    return Settings(
        bot_token="test",
        database_url="postgresql+asyncpg://user:pass@db.production.example.com:5432/kufar",
        redis_url="redis://redis:6379/0",
        api_base_url="https://example.com",
        mini_app_url="https://example.com/app",
        debug=False,
        auth_bypass=False,
        metrics_bearer_token=metrics_bearer_token,
        _env_file=None,
    )


class _FakeRedisClient:
    def __init__(self) -> None:
        self.hashes: defaultdict[str, dict[str, str]] = defaultdict(dict)

    async def hincrby(self, key: str, field: str, amount: int) -> int:
        value = int(self.hashes[key].get(field, "0")) + amount
        self.hashes[key][field] = str(value)
        return value

    async def hincrbyfloat(self, key: str, field: str, amount: float) -> float:
        value = float(self.hashes[key].get(field, "0")) + amount
        self.hashes[key][field] = str(value)
        return value

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes[key])


class _FakeRedisCache:
    def __init__(self) -> None:
        self._client = _FakeRedisClient()

    async def pipeline_hgetall(self, keys: list[str]) -> list[dict[str, str]]:
        return [dict(self._client.hashes[k]) for k in keys]


def test_metrics_endpoint_exposes_prometheus_text() -> None:
    _reset_metrics_for_tests()
    app = create_app()

    with TestClient(app) as client:
        client.get("/api/v1/health")
        resp = client.get("/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "# TYPE kufar_http_requests_total counter" in resp.text
    assert "kufar_http_request_duration_seconds_count" in resp.text
    assert "# TYPE kufar_process_info gauge" in resp.text
    assert "# TYPE kufar_metrics_backend_info gauge" in resp.text


def test_metrics_records_requests_by_route_template() -> None:
    _reset_metrics_for_tests()
    app = create_app()

    with TestClient(app) as client:
        health_resp = client.get("/api/v1/health")
        metrics_resp = client.get("/metrics")

    assert health_resp.status_code == 200
    assert 'method="GET",path="/api/v1/health",status="200"' in metrics_resp.text


def test_metrics_endpoint_denies_production_like_without_token(monkeypatch) -> None:
    _reset_metrics_for_tests()
    from api import main

    monkeypatch.setattr(main, "get_settings", lambda: _remote_settings())
    app = main.create_app()

    resp = TestClient(app).get("/metrics")

    assert resp.status_code == 403
    assert "# TYPE kufar_http_requests_total counter" not in resp.text


def test_metrics_endpoint_accepts_production_like_bearer_token(monkeypatch) -> None:
    _reset_metrics_for_tests()
    from api import main

    monkeypatch.setattr(main, "get_settings", lambda: _remote_settings("metrics-secret"))
    app = main.create_app()
    client = TestClient(app)

    assert client.get("/metrics").status_code == 403
    resp = client.get("/metrics", headers={"Authorization": "Bearer metrics-secret"})

    assert resp.status_code == 200
    assert "# TYPE kufar_http_requests_total counter" in resp.text


def test_metrics_render_process_identity_and_dataset_counters() -> None:
    _reset_metrics_for_tests()
    observe_query_dataset_event("cache_miss")
    observe_query_dataset_event("singleflight_wait")
    observe_query_dataset_upstream_fetch(status="success", duration_seconds=0.125)

    text = render_prometheus_metrics()

    assert 'kufar_process_info{pid="' in text
    assert 'kufar_metrics_backend_info{backend="memory"} 1' in text
    assert 'kufar_query_dataset_events_total{event="cache_miss"} 1' in text
    assert 'kufar_query_dataset_events_total{event="singleflight_wait"} 1' in text
    assert (
        'kufar_query_dataset_upstream_fetch_duration_seconds_count{status="success"} 1'
        in text
    )


@pytest.mark.asyncio
async def test_metrics_redis_backend_renders_aggregate_counters() -> None:
    _reset_metrics_for_tests()
    cache = _FakeRedisCache()

    await observe_http_request_with_backend(
        cache,
        method="GET",
        path="/api/v1/health",
        status_code=200,
        duration_seconds=0.25,
    )
    await observe_query_dataset_event_with_backend(cache, "cache_miss")
    await observe_query_dataset_upstream_fetch_with_backend(
        cache, status="success", duration_seconds=0.125
    )

    text = await render_prometheus_metrics_with_backend(cache)

    assert 'kufar_metrics_backend_info{backend="redis"} 1' in text
    assert 'method="GET",path="/api/v1/health",status="200"' in text
    assert 'kufar_query_dataset_events_total{event="cache_miss"} 1' in text
    assert (
        'kufar_query_dataset_upstream_fetch_duration_seconds_count{status="success"} 1'
        in text
    )


@pytest.mark.asyncio
async def test_metrics_backend_falls_back_to_memory_without_redis() -> None:
    _reset_metrics_for_tests()
    observe_query_dataset_event("cache_miss")

    text = await render_prometheus_metrics_with_backend(None)

    assert 'kufar_metrics_backend_info{backend="memory"} 1' in text
    assert 'kufar_query_dataset_events_total{event="cache_miss"} 1' in text
