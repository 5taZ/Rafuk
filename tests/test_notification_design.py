from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import LinkPreviewOptions

from bot.keyboards import enhanced_alert_keyboard, inline_keyboard_from_json
from bot.notifications import send_notification
from scheduler.messages import _build_new_listing_message, _build_price_drop_message


@pytest.mark.parametrize("legacy", [False, True])
async def test_notification_formats_title_and_price_without_interpreting_listing_html(legacy):
    title = '<b>iPhone</b> & "Pro"'
    state = SimpleNamespace(title=title, last_price_byn=Decimal("1120.25"), price_type=None)
    message = (f"🔔 Новый лот\n{title}\n1120.25 BYN" if legacy
               else _build_new_listing_message(state, discount_pct=12.5))
    keyboard = enhanced_alert_keyboard(ad_id=42, listing_url="https://www.kufar.by/item/42")
    bot = AsyncMock()

    await send_notification(bot, 123, message, reply_markup=keyboard)

    args, kwargs = bot.send_message.call_args
    assert args[0] == 123
    assert args[1].startswith("Новое объявление\n<b>&lt;b&gt;iPhone&lt;/b&gt; &amp;")
    assert "\n\n<b>" in args[1]
    assert kwargs == {
        "reply_markup": keyboard, "parse_mode": ParseMode.HTML,
        "link_preview_options": LinkPreviewOptions(is_disabled=True),
    }
    if not legacy:
        assert "<b>1\u00a0120,25 BYN</b>\n−12% к медиане" in args[1]


async def test_price_drop_exposes_current_previous_and_relative_prices():
    state = SimpleNamespace(title="iPhone 14 Pro 256", last_price_byn=700, price_type=None)
    bot = AsyncMock()

    await send_notification(bot, 123, _build_price_drop_message(state, 150), reply_markup=None)

    assert bot.send_message.call_args.args[1] == (
        "Цена снизилась\n<b>iPhone 14 Pro 256</b>\n\n<b>700 BYN</b>\n"
        "Было <s>850 BYN</s> · −150 BYN (−17,6%)"
    )


@pytest.mark.parametrize(("price", "price_type", "label"), [
    (None, None, "договорная"), (120, "negotiable", "договорная"),
    (None, "free", "бесплатно"), (0, None, "бесплатно"),
])
async def test_special_prices_do_not_turn_into_invented_numeric_prices(price, price_type, label):
    state = SimpleNamespace(title="   ", last_price_byn=price, price_type=price_type)
    bot = AsyncMock()
    await send_notification(bot, 123, _build_new_listing_message(state), reply_markup=None)
    assert bot.send_message.call_args.args[1] == (
        f"Новое объявление\n<b>Без названия</b>\n\n<b>{label}</b>"
    )


def test_keyboard_styles_and_actions_survive_outbox_serialization():
    keyboard = enhanced_alert_keyboard(ad_id=42, listing_url="https://www.kufar.by/item/42")
    restored = inline_keyboard_from_json(keyboard.model_dump_json())
    buttons = [button for row in restored.inline_keyboard for button in row]
    assert [(button.callback_data, button.style) for button in buttons[:2]] == [
        ("add_lead:42", "success"), ("add_watch:42", None),
    ]
    assert buttons[2].url == "https://www.kufar.by/item/42"
    assert buttons[2].style == "primary"


async def test_old_price_drop_keeps_amount_and_non_alert_messages_stay_plain():
    bot = AsyncMock()
    await send_notification(
        bot, 123, "📉 Цена снизилась\niPhone & Pro\n700 BYN · −150 BYN", reply_markup=None,
    )
    assert bot.send_message.call_args.args[1] == (
        "Цена снизилась\n<b>iPhone &amp; Pro</b>\n\n<b>700 BYN</b>\n−150 BYN"
    )
    bot.reset_mock()
    message = 'Напоминание\nЗапрос "iPhone <Pro>"'
    await send_notification(bot, 123, message, reply_markup=None)
    bot.send_message.assert_awaited_once_with(123, message, reply_markup=None)


@pytest.mark.parametrize(("photo_count", "photo_accepted"), [
    (0, False), (1, True), (3, True), (3, False),
])
async def test_listing_alert_keeps_description_and_buttons_when_collage_is_unavailable(
    photo_count, photo_accepted,
):
    bot = AsyncMock()
    rejected = TelegramBadRequest(method=MagicMock(), message="failed to get HTTP URL content")
    bot.send_rich_message.side_effect = rejected
    if not photo_accepted:
        bot.send_photo.side_effect = rejected
    keyboard = enhanced_alert_keyboard(ad_id=42, listing_url="https://www.kufar.by/item/42")
    photos = [f"https://rms.kufar.by/v1/gallery/{i}.jpg" for i in range(photo_count)]

    await send_notification(
        bot, 123, "Новое объявление\niPhone\n700 BYN", reply_markup=keyboard,
        description="Чехол & коробка", photo_urls=photos,
    )

    assert bot.send_rich_message.await_count == (1 if photo_count > 1 else 0)
    assert bot.send_photo.await_count == (1 if photo_count else 0)
    formatted = "Новое объявление\n<b>iPhone</b>\n\n<b>700 BYN</b>\n\nЧехол &amp; коробка"
    if photo_accepted:
        bot.send_photo.assert_awaited_once_with(
            123, photos[0], caption=formatted, parse_mode=ParseMode.HTML, reply_markup=keyboard,
        )
        bot.send_message.assert_not_awaited()
    else:
        bot.send_message.assert_awaited_once_with(
            123, formatted, reply_markup=keyboard, parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
