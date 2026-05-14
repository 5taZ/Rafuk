from __future__ import annotations

from api.services.kufar_filters import (
    build_kufar_search_filters,
    kufar_location_kwargs,
    kufar_price_range,
)


def test_kufar_price_range_uses_closed_bounds() -> None:
    # SEARCH-6: Kufar's `prc=` is in kopecks, so 1000 BYN must become
    # `100000`. The previous shape (`r:0,1000`) made Kufar interpret
    # the upper bound as 10 BYN and silently dropped real listings.
    assert kufar_price_range(None, 1000) == "r:0,100000"
    assert kufar_price_range(1000, None) == "r:100000,999999999"
    assert kufar_price_range(1000, 2000) == "r:100000,200000"


def test_kufar_price_range_clamps_to_open_max() -> None:
    # SEARCH-6: `min_price`/`max_price` come in as BYN with FastAPI
    # validating `le=9_999_999_999.99`. Multiplying by 100 would
    # overflow Kufar's int parser, so the bounds are clamped to the
    # documented `999_999_999` kopeck ceiling (~10M BYN).
    assert kufar_price_range(None, 9_999_999_999) == "r:0,999999999"
    assert kufar_price_range(9_999_999_999, None) == "r:999999999,999999999"


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
        # SEARCH-6: BYN×100 → kopecks before the value is sent to Kufar.
        "price_range": "r:50000,100000",
        "condition": "1",
        "seller_type": "private",
        "region": 7,
    }
