from __future__ import annotations


def test_create_app_has_expected_routes() -> None:
    from api.main import create_app

    app = create_app()
    paths = {route.path for route in app.router.routes}
    assert "/api/v1/price-stats" in paths
    assert "/api/v1/listings" in paths
    assert "/api/v1/segments" in paths
    assert "/api/v1/geography" in paths
    assert "/api/v1/leads" in paths
    assert "/api/v1/watchlist" in paths
    assert "/metrics" in paths
