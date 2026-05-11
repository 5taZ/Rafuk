from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import create_app
from api.metrics import _reset_metrics_for_tests


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


def test_metrics_records_requests_by_route_template() -> None:
    _reset_metrics_for_tests()
    app = create_app()

    with TestClient(app) as client:
        health_resp = client.get("/api/v1/health")
        metrics_resp = client.get("/metrics")

    assert health_resp.status_code == 200
    assert 'method="GET",path="/api/v1/health",status="200"' in metrics_resp.text
