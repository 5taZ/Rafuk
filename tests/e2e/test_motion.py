"""UI-MOTION: real browser checks for shared motion and stable expense sheets."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote, urlparse

import pytest
from playwright.sync_api import expect


@pytest.fixture
def browser_context_args(browser_context_args):
    return {**browser_context_args, "service_workers": "block"}


def _open_deals(page, frontend_url: str, theme: str, width: int) -> list[str]:
    page.set_viewport_size({"width": width, "height": 844})
    page.emulate_media(reduced_motion="no-preference")
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(f"localStorage.setItem('theme', {json.dumps(theme)});")
    page.add_init_script("""
    localStorage.setItem('rafuk:listing-assistant:history', JSON.stringify([
        {id: 1, ts: new Date().toISOString(), input: {title: 'Тестовый запрос'}, output: {}}
    ]));""")
    page.add_init_script(r"""(() => {
        const fetch = window.fetch;
        window.fetch = async (...args) => {
            const response = await fetch(...args);
            if (/\/leads\/\d+\/expenses/.test(String(args[0]))) {
                await new Promise(resolve => setTimeout(resolve, 130));
            }
            return response;
        };
        window.motionCalls = [];
        const animate = Element.prototype.animate;
        Element.prototype.animate = function(frames, options) {
            window.motionCalls.push({target: this.className, frames, options});
            return animate.call(this, frames, options);
        };
    })();""")

    def respond(route):
        path = urlparse(route.request.url).path.removeprefix("/api/v1/")
        if path == "leads":
            data = [{"id": 51, "ad_id": 501, "title": "Покупка для проверки",
                     "query": "iPhone", "status": "bought", "version": 1,
                     "buy_price_byn": 950, "price_byn": 950,
                     "link": "https://www.kufar.by/item/501"}]
        elif path == "profile/me":
            data = {"user": {"telegram_user_id": 42, "first_name": "Test"},
                    "status": {"code": "bare_search", "display_name": "Free"},
                    "permissions": {"can_use_ai": True, "can_use_assistant": True},
                    "limits": {}}
        elif path == "price-stats":
            data = {"count": 1, "total_results": 1, "median": 950,
                    "min": 950, "max": 950, "q1": 950, "q3": 950, "categories": []}
        elif path == "listings":
            data = {"listings": [{"ad_id": 501, "title": "iPhone", "price_byn": 950,
                                  "link": "https://www.kufar.by/item/501"}],
                    "total": 1, "has_more": False}
        elif path in {"watchlist", "trackers"} or path.endswith(("/expenses", "/reminders")):
            data = []
        else:
            data = {"items": [], "listings": [], "points": [], "regions": []}
        route.fulfill(json=data)

    page.route("**/api/v1/**", respond)
    origin = os.getenv("RAFUK_MOTION_ORIGIN", frontend_url)
    user = quote(json.dumps({"id": 42, "first_name": "Test"}))
    init_data = quote(f"user={user}&auth_date=1&hash=fixture", safe="")
    page.goto(f"{origin}/?view=deals#tgWebAppData={init_data}")
    expect(page.locator('.lead-card [data-role="expenses"]')).to_be_visible()
    return errors


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [320, 390, 768])
def test_expenses_keep_geometry_through_loading_and_exit(page, frontend_url, theme, width):
    errors = _open_deals(page, frontend_url, theme, width)
    page.evaluate("""() => {
        window.expenseFrames = [];
        window.sampleExpenses = true;
        const modal = document.getElementById('expenses-modal');
        const panel = modal.querySelector('.detail-sheet');
        const sample = () => {
            if (!window.sampleExpenses) return;
            if (!modal.hidden) {
                const rootStyle = getComputedStyle(modal);
                window.expenseFrames.push({height: panel.getBoundingClientRect().height,
                    rootOpacity: rootStyle.opacity, rootAnimation: rootStyle.animationName,
                    blur: getComputedStyle(modal.querySelector('.detail-overlay')).backdropFilter
                });
            }
            requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
    }""")
    page.locator('.lead-card [data-role="expenses"]').click()
    expect(page.locator("#expenses-list")).to_contain_text("Расходов пока нет")
    page.wait_for_function("() => !document.getElementById('expenses-modal')._visibilityMotion")
    frames = page.evaluate("window.sampleExpenses = false; window.expenseFrames")
    assert len(frames) >= 3
    assert max(f["height"] for f in frames) - min(f["height"] for f in frames) < 1
    assert all(f["rootOpacity"] == "1" and f["rootAnimation"] == "none" for f in frames)
    assert all(f["blur"] == "none" for f in frames)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if width == 390:
        output = Path(".run/unified-motion")
        output.mkdir(parents=True, exist_ok=True)
        stage = "vps" if os.getenv("RAFUK_MOTION_ORIGIN") else "local"
        page.screenshot(path=str(output / f"{stage}-expenses-{theme}.png"))
    page.locator("#expense-amount-input").fill("42")
    value_during_exit = page.locator("#expenses-close").evaluate("""button => {
        button.click();
        return document.getElementById('expense-amount-input').value;
    }""")
    assert value_during_exit == "42"
    expect(page.locator("#expenses-modal")).to_be_hidden()
    assert page.evaluate("document.getElementById('expense-amount-input').value") == ""
    assert not page.evaluate("document.body.classList.contains('modal-open')")
    assert errors == []


def test_all_dialogs_use_identical_motion_and_reverse_without_jumps(page, frontend_url):
    errors = _open_deals(page, frontend_url, "dark", 390)
    results = page.evaluate("""async () => {
        const results = [];
        const wait = () => new Promise(requestAnimationFrame);
        const settled = async modal => {
            while (modal._visibilityMotion) await wait();
        };
        for (const id of ['detail-modal', 'expenses-modal', 'reminders-modal', 'ai-modal',
                'la-modal', 'edit-tracker-modal', 'consent-modal', 'privacy-modal']) {
            const modal = document.getElementById(id);
            window.motionCalls = [];
            App.openModalAnimated(modal);
            const entry = window.motionCalls.map(call => call);
            await settled(modal);
            App.closeModalAnimated(modal);
            const exit = window.motionCalls.slice(entry.length);
            results.push({id, entry, exit});
            await settled(modal);
        }
        const modal = document.getElementById('expenses-modal');
        const panel = modal.querySelector('.detail-sheet');
        const snapshot = () => {
            const style = getComputedStyle(panel);
            return {opacity: Number(style.opacity), y: new DOMMatrixReadOnly(style.transform).m42};
        };
        App.openModalAnimated(modal);
        await wait(); await wait(); await wait();
        const beforeClose = snapshot();
        App.closeModalAnimated(modal);
        const afterClose = snapshot();
        await wait(); await wait();
        const beforeReopen = snapshot();
        App.openModalAnimated(modal);
        const afterReopen = snapshot();
        await settled(modal);
        App.closeModalAnimated(modal);
        await settled(modal);
        return {results, beforeClose, afterClose, beforeReopen, afterReopen,
            locked: document.body.classList.contains('modal-open')};
    }""")
    for result in results["results"]:
        assert len(result["entry"]) == len(result["exit"]) == 2, result["id"]
        entry, exit_call = result["entry"][0], result["exit"][0]
        assert entry["options"]["duration"] == 280
        assert exit_call["options"]["duration"] == 220
        assert entry["frames"][0] == {"opacity": 0, "transform": "translateY(24px)"}
        assert exit_call["frames"][-1] == {"opacity": 0, "transform": "translateY(24px)"}
        assert entry["options"] == results["results"][0]["entry"][0]["options"]
        assert exit_call["options"] == results["results"][0]["exit"][0]["options"]
    for before, after in [("beforeClose", "afterClose"), ("beforeReopen", "afterReopen")]:
        assert abs(results[before]["opacity"] - results[after]["opacity"]) < 0.02
        assert abs(results[before]["y"] - results[after]["y"]) < 0.1
    assert results["locked"] is False
    assert errors == []


def test_inline_panels_filters_and_assistant_results_share_motion(page, frontend_url):
    errors = _open_deals(page, frontend_url, "light", 390)

    def settled(selector):
        page.wait_for_function("selector => !document.querySelector(selector)._visibilityMotion",
                               arg=selector)

    page.locator('[data-panel-toggle="historyDeals"]').click()
    settled("#history-deals-body")
    page.locator('[data-panel-toggle="historyDeals"]').click()
    expect(page.locator("#history-deals-body")).to_be_hidden()
    page.locator("#tab-ads").click()
    page.locator("#search-input").fill("iPhone")
    page.locator("#search-btn").click()
    expect(page.locator("#search-btn")).to_be_enabled()
    page.locator("#tab-ads").click()
    page.locator("#filter-btn").click()
    settled("#filter-dropdown")
    page.locator(".filter-btn--cancel").click()
    expect(page.locator("#filter-dropdown")).to_be_hidden()
    page.locator("#tab-deals").click()
    page.locator("#listing-assistant-open-btn").click()
    expect(page.locator("#la-modal")).to_be_visible()
    page.locator("#la-tab-history").click()
    page.locator(".la-history-open").click()
    settled("#la-result-overlay")
    page.locator("#la-result-back").click()
    expect(page.locator("#la-result-overlay")).to_be_hidden()
    calls = page.evaluate("window.motionCalls")
    for class_name in ["section-body", "filter-dropdown", "la-result-overlay"]:
        motions = [call for call in calls if class_name in call["target"].split()]
        assert [call["options"]["duration"] for call in motions] == [280, 220], class_name
        assert motions[0]["frames"][0] == {"opacity": 0, "transform": "translateY(24px)"}
        assert motions[1]["frames"][-1] == {"opacity": 0, "transform": "translateY(24px)"}
    page.locator("#la-modal-close").click()
    expect(page.locator("#la-modal")).to_be_hidden()
    assert not page.evaluate("document.body.classList.contains('modal-open')")
    assert errors == []
