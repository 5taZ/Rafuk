from __future__ import annotations

from api.services.kufar_filters import (
    build_kufar_search_filters,
    kufar_location_kwargs,
    kufar_price_range,
)


def test_kufar_price_range_uses_closed_bounds() -> None:
    assert kufar_price_range(None, 1000) == "r:0,1000"
    assert kufar_price_range(1000, None) == "r:1000,999999999"
    assert kufar_price_range(1000, 2000) == "r:1000,2000"


def test_kufar_location_kwargs_maps_regions_and_unique_areas() -> None:
    assert kufar_location_kwargs("Минск") == ({"region": 7}, True)
    assert kufar_location_kwargs("Первомайский") == ({"area": 24}, True)
    assert kufar_location_kwargs("Октябрьский") == ({}, False)


def test_build_kufar_search_filters_returns_supported_native_params() -> None:
    kwargs, unsupported = build_kufar_search_filters(
        min_price=500,
        max_price=1000,
        condition="used",
        seller_type="private",
        region_name="Минск",
    )

    assert unsupported == set()
    assert kwargs == {
        "price_range": "r:500,1000",
        "condition": "1",
        "seller_type": "private",
        "region": 7,
    }
