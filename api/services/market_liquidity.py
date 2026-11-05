from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import MarketLiquidityObservation
from api.schemas import LiquidityInsight
from api.services.aggregator import normalize_price_byn
from api.services.history_service import as_utc
from api.services.watchlist_market import liquidity_segment

OBSERVATION_AGE = timedelta(days=7)
CHECK_GRACE = timedelta(days=1)
HISTORY_WINDOW = timedelta(days=30)
RETENTION = timedelta(days=45)
_MAX_INITIAL_FEED_AGE = timedelta(days=1)
_MIN_SAMPLE = 15


def _fresh_feed_entry(ad: dict[str, Any], now: datetime) -> bool:
    raw = ad.get("list_time")
    if not isinstance(raw, str):
        return False
    try:
        listed_at = as_utc(datetime.fromisoformat(raw))
    except ValueError:
        return False
    return now - _MAX_INITIAL_FEED_AGE <= listed_at <= now


async def observe_market_listings(
    session: AsyncSession,
    ads: Iterable[dict[str, Any]],
    *,
    now: datetime,
    only_segment: str | None = None,
) -> None:
    observations = {}
    for ad in ads:
        raw_id = str(ad.get("ad_id"))
        if len(raw_id) > 19 or not raw_id.isascii() or not raw_id.isdigit():
            continue
        ad_id = int(raw_id)
        if not 0 < ad_id < 2**63 or ad_id in observations or not _fresh_feed_entry(ad, now):
            continue
        price = normalize_price_byn(ad.get("price_byn"))
        segment = liquidity_segment(ad) if price else None
        if segment is not None and (only_segment is None or segment == only_segment):
            observations[ad_id] = {
                "ad_id": ad_id, "segment": segment, "price_byn": price, "first_seen_at": now,
            }
    if observations:
        insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
        # A raise or price change must never reset the start of the same observation.
        await session.execute(
            insert(MarketLiquidityObservation)
            .values([observations[ad_id] for ad_id in sorted(observations)])
            .on_conflict_do_nothing(index_elements=["ad_id"])
        )


async def load_liquidity_insight(
    session: AsyncSession, ad: dict[str, Any], *, segment: str | None, now: datetime,
) -> LiquidityInsight:
    price = normalize_price_byn(ad.get("price_byn"))
    insufficient = LiquidityInsight(label="Недостаточно наблюдений")
    if not price or segment is None:
        insufficient.reasons = ["Нужны сопоставимые объявления с указанной ценой."]
        return insufficient

    price_byn = Decimal(str(price))
    observation = MarketLiquidityObservation
    mature_at = now - OBSERVATION_AGE - CHECK_GRACE
    total, checked, unavailable = (await session.execute(
        select(
            func.count(),
            func.count(observation.available),
            func.count().filter(observation.available.is_(False)),
        ).where(
            observation.segment == segment,
            observation.ad_id != int(ad["ad_id"]),
            observation.first_seen_at.between(mature_at - HISTORY_WINDOW, mature_at),
            observation.price_byn.between(price_byn * Decimal("0.8"), price_byn * Decimal("1.2")),
        )
    )).one()
    if total == 0:
        insufficient.reasons = [
            "Проверка похожих объявлений начинается через неделю после первого наблюдения. "
            "Историю пополняют открытые карточки и активные трекеры.",
        ]
        return insufficient
    if checked < _MIN_SAMPLE or checked * 5 < total * 4:
        insufficient.sample_size = checked
        insufficient.reasons = [
            f"Проверено {checked} из {total} похожих объявлений с близкой ценой. "
            f"Для оценки нужно минимум {_MIN_SAMPLE} проверенных объявлений и покрытие от 80%.",
        ]
        return insufficient

    share = unavailable / checked
    label = "Высокая" if share >= 0.60 else "Низкая" if share <= 0.25 else "Средняя"
    return LiquidityInsight(
        score=round(share * 100, 1),
        label=label,
        sample_size=checked,
        unavailable_count=unavailable,
        reasons=["Причина недоступности страницы неизвестна."],
    )
