from __future__ import annotations

import math

from api.models import QueryListingState, TrackerEvent
from api.services.history_service import QuerySyncResult, TrendReversal

_DESCRIPTION_PREVIEW_LIMIT = 300


def _description_preview(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    description = " ".join(value.split())
    if len(description) > _DESCRIPTION_PREVIEW_LIMIT:
        return description[:_DESCRIPTION_PREVIEW_LIMIT - 1].rstrip() + "…"
    return description or None


def _format_price_byn(value: float | None, price_type: str | None = None) -> str:
    if price_type == "negotiable" or (value is None and price_type != "free"):
        return "договорная"
    if price_type == "free" or value == 0:
        return "бесплатно"
    return f"{round(float(value))} BYN"


def _notification_title(value: str | None) -> str:
    return " ".join((value or "").split())[:120] or "Без названия"


def _notification_number(value: float) -> str:
    return f"{float(value):,.2f}".rstrip("0").rstrip(".").replace(
        ",", "\u00a0",
    ).replace(".", ",")


def _notification_price(value: float | None, price_type: str | None = None) -> str:
    if value is None or value == 0 or price_type in {"free", "negotiable"}:
        return _format_price_byn(value, price_type)
    return f"{_notification_number(value)} BYN"


def _build_threshold_message(threshold_events: list[TrackerEvent]) -> str | None:
    if not threshold_events:
        return None

    primary = threshold_events[0]
    price = _notification_price(primary.price_byn, primary.price_type)
    detail = ""
    if primary.event_type == "price_threshold_alert":
        heading = "Порог цены"
        threshold = (primary.parameters or {}).get("threshold")
        if threshold:
            detail = f"Ваш порог: {_notification_price(threshold)}"
    else:
        heading = "Скидка от медианы"
        if primary.delta_byn:
            detail = f"На {_notification_number(primary.delta_byn)}% ниже медианы"
    lines = [heading, _notification_title(primary.title), price]
    if detail:
        lines.append(detail)
    others = {event.ad_id for event in threshold_events[1:] if event.ad_id != primary.ad_id}
    if others:
        lines.append(f"Ещё {len(others)} в автопоиске")
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
            delta_str = f"(-{math.ceil(float(delta))} BYN)" if float(delta) >= 0.5 else ""
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
        lines.extend((
            "Цена снова растёт",
            query,
            f"{_format_price_byn(trend_signal.low_byn)} → "
            f"{_format_price_byn(trend_signal.today_byn)} · +{trend_signal.rebound_pct:.1f}%",
        ))

    if not lines:
        return None
    return "\n".join(lines)


def _build_new_listing_message(
    state: QueryListingState,
    *,
    discount_pct: float | None = None,
) -> str:
    price = _notification_price(state.last_price_byn, state.price_type)
    lines = ["Новое объявление", _notification_title(state.title), price]
    if discount_pct is not None and discount_pct > 0:
        lines.append(f"На {_notification_number(discount_pct)}% ниже медианы")
    return "\n".join(lines)


def _build_price_drop_message(state: QueryListingState, delta: float) -> str:
    price = _notification_price(state.last_price_byn, state.price_type)
    lines = ["Цена снизилась", _notification_title(state.title), price]
    drop = round(float(delta), 2)
    if drop > 0 and state.last_price_byn is not None and state.price_type != "negotiable":
        previous = float(state.last_price_byn) + drop
        detail = f"Было {_notification_price(previous)} · −{_notification_number(drop)} BYN"
        percent = round(drop / previous * 100, 1)
        if percent > 0:
            detail += f" (−{_notification_number(percent)}%)"
        lines.append(detail)
    return "\n".join(lines)
