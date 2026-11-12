"""LEGAL-*: personal-data and Kufar-terms fixes."""

from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from api.services.ai_sanitize import mask_seller_names

LEGAL_DIR = Path(__file__).resolve().parents[1] / "frontend" / "legal"


def _ad(name: str, *, company: bool = False, key: str = "name") -> dict:
    return {"company_ad": company, "account_parameters": [{"p": key, "v": name, "pl": "Имя"}]}


def test_mask_seller_names_hides_a_private_sellers_own_name() -> None:
    # LEGAL-03: the seller never agreed to an AI provider abroad.
    text = "Продаю диван. Звонить Наталье, спросить Наталья Ивановна, наталья."
    masked = mask_seller_names(text, _ad("Наталья Ивановна"))
    assert "Наталья" not in masked and "наталья" not in masked
    assert "Ивановна" not in masked
    assert "Наталье" in masked, "other word forms are not the profile name"
    assert masked.startswith("Продаю диван.")
    assert mask_seller_names("Пётр продаёт", _ad("Пётр", key="contact_person")) == "[имя] продаёт"


def test_mask_seller_names_keeps_shop_names_and_unrelated_text() -> None:
    assert mask_seller_names("Мебель Плюс доставит", _ad("Мебель Плюс", company=True)) == (
        "Мебель Плюс доставит"
    )
    assert mask_seller_names("Иванов продаёт", {"account_parameters": []}) == "Иванов продаёт"
    assert mask_seller_names("Ан продаёт", _ad("Ан")) == "Ан продаёт", "too short to be safe"
    assert mask_seller_names("", _ad("Наталья")) == ""


@pytest.mark.parametrize("page", ["terms.html", "privacy.html"])
def test_legal_pages_are_public_and_csp_safe(page: str) -> None:
    # LEGAL-01: published without Telegram auth; the CSP forbids inline code.
    soup = BeautifulSoup((LEGAL_DIR / page).read_text(encoding="utf-8"), "html.parser")
    assert all(script.get("src") for script in soup.find_all("script"))
    assert not soup.find_all("style")
    assert not soup.select("[style]")
    assert not [tag for tag in soup.find_all(True) if any(a.startswith("on") for a in tag.attrs)]
    main = soup.select_one("main#legal-document")
    assert main is not None and main.get("data-legal-title")
    assert soup.select('[data-operator="name"]')
    assert soup.select('[data-operator="email"]')
    assert soup.select("[data-policy-version]")
    other = "privacy" if page == "terms.html" else "terms"
    assert soup.select_one(f'a[data-legal-link="{other}"][href="{other}.html"]') is not None


def test_privacy_policy_names_bases_recipients_and_rights() -> None:
    soup = BeautifulSoup((LEGAL_DIR / "privacy.html").read_text(encoding="utf-8"), "html.parser")
    text = soup.get_text(" ", strip=True)
    for phrase in (
        "99-З", "пользовательское соглашение", "отдельное согласие", "Telegram", "Kufar",
        "AI-сервис", "Национальный центр защиты персональных данных",
    ):
        assert phrase in text, phrase
    assert soup.select("[data-retention-days]")
    assert soup.select("[data-ai-provider]")
