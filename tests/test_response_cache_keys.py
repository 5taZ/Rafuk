from __future__ import annotations

from api.schemas import AIPriceAdviceRequest

RAW_QUERY = "iphone 15 pro max " + "x" * 120 + "\n:redis:key:probe"


def _assert_digest_key(key: str, prefix: str) -> None:
    assert key.startswith(f"{prefix}:")
    digest = key.removeprefix(f"{prefix}:")
    assert len(digest) == 64
    int(digest, 16)
    assert RAW_QUERY not in key
    assert "\n" not in key


def test_listings_cache_key_digests_user_query() -> None:
    from api.routers.listings import _listings_cache_key

    key = _listings_cache_key(
        query=RAW_QUERY,
        sort="deal_score",
        currency="BYN",
        discount_percent=10.0,
        effective_from=10.0,
        effective_to=30.0,
        strict_search=True,
        category=2010,
        reference_context="base_query",
        min_price=100.0,
        max_price=500.0,
        condition="used",
        seller_type="private",
        region_name="минск",
        limit=50,
        offset=100,
    )

    _assert_digest_key(key, "listings")


def test_price_stats_cache_key_digests_user_query() -> None:
    from api.routers.price_stats import _price_stats_cache_key

    key = _price_stats_cache_key(
        query=RAW_QUERY,
        currency="USD",
        strict_search=False,
        category=2010,
    )

    _assert_digest_key(key, "price-stats")


def test_ai_price_advice_cache_key_digests_user_query() -> None:
    from api.routers.ai_tools import _ai_price_advice_cache_key

    key = _ai_price_advice_cache_key(
        AIPriceAdviceRequest(
            query=RAW_QUERY,
            current_price_byn=950.0,
            category=2010,
        )
    )

    _assert_digest_key(key, "ai_price_advice")
