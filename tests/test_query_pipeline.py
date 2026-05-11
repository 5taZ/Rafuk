from __future__ import annotations

from api.services.query_pipeline import _dataset_cache_key, _normalize_response_ads


def test_normalize_response_ads_keeps_real_kufar_minor_units() -> None:
    response = {
        "ads": [
            {"price_byn": 400, "price_usd": 142},
            {"price_byn": 1200, "price_usd": 426},
            {"price_byn": 8000, "price_usd": 2838},
        ]
    }

    normalized = _normalize_response_ads(response)

    assert [ad["price_byn"] for ad in normalized["ads"]] == [400, 1200, 8000]


def test_normalize_response_ads_supports_direct_byn_test_payloads() -> None:
    response = {
        "ads": [
            {"price_byn": 4},
            {"price_byn": 12},
            {"price_byn": 80},
        ]
    }

    normalized = _normalize_response_ads(response)

    assert [ad["price_byn"] for ad in normalized["ads"]] == [400, 1200, 8000]


def test_dataset_cache_key_hashes_canonical_tuple() -> None:
    key_a = _dataset_cache_key(
        "foo:cur=USD",
        "BYN",
        None,
        {"region": "Минск"},
    )
    key_b = _dataset_cache_key(
        "foo",
        "cur=USD:cat=:region=Минск",
        None,
        {},
    )

    assert key_a.startswith("kufar:dataset:")
    assert len(key_a.removeprefix("kufar:dataset:")) == 64
    assert key_a != key_b
