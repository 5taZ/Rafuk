from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from api.schemas import LiquidityInsight
from api.services.aggregator import MIN_CLUSTER_SIZE, normalize_price_byn
from api.services.history_service import as_utc

if TYPE_CHECKING:
    from api.services.watchlist_market import MarketComparables

_OLD_AFTER_DAYS = 14


def _listing_date(ad: dict[str, Any]) -> datetime | None:
    value = ad.get("list_time")
    if not isinstance(value, str):
        return None
    try:
        return as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def estimate_liquidity(
    ad: dict[str, Any],
    comparables: MarketComparables,
    *,
    now: datetime | None = None,
    price_filtered: bool = False,
) -> LiquidityInsight:
    price = normalize_price_byn(ad.get("price_byn"))
    sample_size = comparables.stats.count
    if not price:
        return LiquidityInsight(
            label="Недостаточно данных", sample_size=sample_size,
            reasons=["Для оценки нужна указанная цена объявления."],
        )
    if sample_size < MIN_CLUSTER_SIZE:
        return LiquidityInsight(
            label="Недостаточно аналогов", sample_size=sample_size,
            reasons=[
                f"В текущей выдаче нужно хотя бы {MIN_CLUSTER_SIZE} сопоставимых лота с ценой.",
            ],
        )
    if not comparables.reliable:
        return LiquidityInsight(
            label="Неоднородная выборка", sample_size=sample_size,
            reasons=["Цены аналогов слишком различаются для уверенной оценки."],
        )

    relative_price = price / comparables.stats.median
    delta = round((relative_price - 1) * 100)
    if abs(delta) < 3:
        reasons = ["Цена близка к медиане аналогов."]
    else:
        direction = "ниже" if delta < 0 else "выше"
        reasons = [f"Цена на {abs(delta)}% {direction} медианы аналогов."]

    now = now or datetime.now(UTC)
    dates = [_listing_date(peer) for peer in comparables.ads]
    dated = [date for date in dates if date is not None and date <= now]
    cutoff = now - timedelta(days=_OLD_AFTER_DAYS)
    old_count = sum(date <= cutoff for date in dated)
    enough_dates = len(dated) >= MIN_CLUSTER_SIZE and len(dated) * 2 >= sample_size
    old_supply = enough_dates and old_count * 2 >= len(dated)
    if enough_dates:
        reasons.append(f"У {old_count} из {len(dated)} аналогов дата в выдаче старше 14 дней.")
    if price_filtered:
        reasons.append("Выборка ограничена фильтром цены.")

    # Prices position the listing among active alternatives. Feed dates are a weaker
    # signal, since Kufar can refresh a listing's date when it is promoted.
    if relative_price >= 1.12 or (relative_price >= 1 and old_supply):
        label = "Низкая"
    elif relative_price <= 0.90 and enough_dates and not old_supply:
        label = "Высокая"
    else:
        label = "Средняя"
    return LiquidityInsight(label=label, sample_size=sample_size, reasons=reasons)
