from __future__ import annotations

import math

from api.models import QueryListingState, Tracker, TrackerEvent
from api.services.history_service import QuerySyncResult, TrendReversal


def _format_price_byn(value: float | None, price_type: str | None = None) -> str:
    if price_type == "negotiable" or (value is None and price_type != "free"):
        return "договорная"
    if price_type == "free" or value == 0:
        return "бесплатно"
    fval = float(value)
    if fval >= 1000:
        compact = f"{fval / 1000:.2f}".rstrip("0").rstrip(".")
        return f"{compact} тыс. р."
    return f"{round(fval)} р."


def _build_threshold_message(
    tracker: Tracker,
    threshold_events: list[TrackerEvent],
) -> str | None:
    """Format a Telegram notification for threshold alert events."""
    if not threshold_events:
        return None

    lines: list[str] = []
    label = f"{tracker.query} [строгий]" if tracker.strict_mode else tracker.query

    price_alerts = [e for e in threshold_events if e.event_type == "price_threshold_alert"]
    discount_alerts = [e for e in threshold_events if e.event_type == "discount_alert"]

    if price_alerts:
        lines.append(f'🎯 ПОРОГ ЦЕНЫ: "{label}"')
        for event in price_alerts[:3]:
            threshold = event.parameters.get("threshold") if event.parameters else None
            threshold_str = f" (порог: {float(threshold):,.0f} BYN)" if threshold else ""
            lines.append(f"💰 {float(event.price_byn):,.0f} BYN{threshold_str}")
            lines.append(f"  {event.title}")
            if event.link:
                lines.append(event.link)
        if len(price_alerts) > 3:
            lines.append(f"  и ещё {len(price_alerts) - 3}")

    if discount_alerts:
        if lines:
            lines.append("")
        lines.append(f'📉 СКИДКА ОТ МЕДИАНЫ: "{label}"')
        for event in discount_alerts[:3]:
            discount_pct = event.delta_byn
            median_byn = event.parameters.get("median_byn") if event.parameters else None
            discount_str = f" (-{float(discount_pct):.0f}% от медианы" if discount_pct else ""
            if median_byn:
                discount_str += f" {float(median_byn):,.0f} BYN"
            discount_str += ")" if discount_str else ""
            lines.append(f"💰 {float(event.price_byn):,.0f} BYN{discount_str}")
            lines.append(f"  {event.title}")
            if event.link:
                lines.append(event.link)
        if len(discount_alerts) > 3:
            lines.append(f"  и ещё {len(discount_alerts) - 3}")

    if not lines:
        return None
    return "\n".join(lines)


def _build_tracker_message(
    query: str,
    strict_mode: bool,
    sync_result: QuerySyncResult,
    trend_signal: TrendReversal | None = None,
    trend_already_sent: bool = False,
) -> str | None:
    lines: list[str] = []
    label = f"{query} [строгий]" if strict_mode else query

    if sync_result.new_listings:
        lines.append(f'Запрос "{label}"')
        lines.append(f"Новые объявления: {len(sync_result.new_listings)}")
        for state in sync_result.new_listings[:3]:
            price_label = _format_price_byn(state.last_price_byn, state.price_type)
            lines.append(f"• {state.title} - {price_label}")
            if state.link:
                lines.append(state.link)
        if len(sync_result.new_listings) > 3:
            lines.append(f"• и ещё {len(sync_result.new_listings) - 3}")

    if sync_result.price_drops:
        if lines:
            lines.append("")
        if not sync_result.new_listings:
            lines.append(f'Запрос "{label}"')
        lines.append(f"Снижение цены: {len(sync_result.price_drops)}")
        for state, delta in sync_result.price_drops[:3]:
            delta_str = f"(-{math.ceil(float(delta))} р.)" if float(delta) >= 0.5 else ""
            lines.append(
                (
                    f"• {state.title} - "
                    f"{_format_price_byn(state.last_price_byn, state.price_type)} {delta_str}"
                ).rstrip()
            )
            if state.link:
                lines.append(state.link)
        if len(sync_result.price_drops) > 3:
            lines.append(f"• и ещё {len(sync_result.price_drops) - 3}")

    if trend_signal is not None and not trend_already_sent:
        if lines:
            lines.append("")
        if not sync_result.new_listings and not sync_result.price_drops:
            lines.append(f'Запрос "{label}"')
        lines.append(
            f"📈 Цена снова растёт после падения на "
            f"{trend_signal.decline_pct:.1f}%"
        )
        lines.append(
            f"• минимум: {_format_price_byn(trend_signal.low_byn)} "
            f"→ сейчас: {_format_price_byn(trend_signal.today_byn)} "
            f"(+{trend_signal.rebound_pct:.1f}%)"
        )
        lines.append(
            "• выкупайте до повторного роста, если ловили это окно"
        )

    if not lines:
        return None
    return "\n".join(lines)


def _build_new_listing_message(
    state: QueryListingState,
    *,
    median_byn: float | None = None,
    discount_pct: float | None = None,
    liquidity: str | None = None,
) -> str:
    """Format a single new-listing notification with market context.

    Includes median price, discount from median, and a liquidity hint
    so the user can decide at a glance whether the deal is worth pursuing.
    """
    price = _format_price_byn(state.last_price_byn, state.price_type)
    title = (state.title or "Без названия").replace("\n", " ")[:120]

    lines = [f"🔔 НОВЫЙ ЛОТ: {title}"]
    if median_byn is not None and median_byn > 0:
        lines.append(f"💰 {price} (медиана: {_format_price_byn(median_byn)})")
    else:
        lines.append(f"💰 {price}")

    if discount_pct is not None and discount_pct > 0:
        lines.append(f"📉 -{discount_pct:.0f}% от медианы")

    if liquidity:
        lines.append(f"⚡ {liquidity}")

    return "\n".join(lines)


def _build_price_drop_message(
    state: QueryListingState,
    delta: float,
    *,
    median_byn: float | None = None,
    discount_pct: float | None = None,
) -> str:
    """Format a single price-drop notification with market context."""
    price = _format_price_byn(state.last_price_byn, state.price_type)
    title = (state.title or "Без названия").replace("\n", " ")[:120]
    delta_str = f"(-{round(float(delta))} р.)" if round(float(delta)) > 0 else ""

    lines = [f"📉 СНИЖЕНИЕ ЦЕНЫ: {title}"]
    if median_byn is not None and median_byn > 0:
        lines.append(f"💰 {price} {delta_str} (медиана: {_format_price_byn(median_byn)})")
    else:
        lines.append(f"💰 {price} {delta_str}".rstrip())

    if discount_pct is not None and discount_pct > 0:
        lines.append(f"📊 -{discount_pct:.0f}% от медианы")

    return "\n".join(lines)
