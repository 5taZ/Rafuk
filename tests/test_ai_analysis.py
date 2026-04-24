from __future__ import annotations

from time import monotonic, sleep
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData
from api.services.ai_marketplace import (
    build_marketplace_risk_context,
    build_market_context_fallback,
    choose_best_alternative,
    complete_analysis_sections,
    collect_similar_listings_from_cohorts,
    finalize_red_flags,
    merge_marketplace_red_flags,
)
from api.services.ai_service import AIService, _clean_photo_notes, _normalize_condition_label


def fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=123456, first_name="Test", raw={})


class FakeAIService:
    available = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def analyze_listing_parallel(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {
            "condition": {
                "label": "Хорошее",
                "confidence": 0.88,
                "notes": ["Корпус выглядит аккуратно"],
            },
            "fair_price": {
                "from": 1500,
                "to": 1650,
                "reasoning": "Ориентир по рынку и состоянию",
            },
            "watch_out": [{"point": "Аккумулятор", "why": "Проверьте остаточную ёмкость"}],
            "recommendation": {
                "verdict": "think_twice",
                "text": "Попросите диагностику и торгуйтесь.",
            },
            "summary": "Выглядит адекватно, но нужен осмотр.",
        }

    async def quick_condition(self, image_urls: list[str]) -> dict:
        return {
            "condition": "Хорошее",
            "notes": ["Видны лёгкие потёртости на корпусе"],
        }


class FakeOutlierAIService:
    available = True

    async def quick_condition(self, image_urls: list[str]) -> dict:
        return {"condition": "Удовлетворительное", "notes": []}

    async def analyze_listing_parallel(self, **kwargs) -> dict:
        del kwargs
        return {
            "fair_price": {
                "from": 170000,
                "to": 210000,
                "reasoning": "Это рестайлинг 4M 2022 года.",
            },
            "resale_potential": {
                "fast_price": {"label": "Быстро", "price_byn": 160000, "reasoning": "ниже рынка"},
                "market_price": {"label": "По рынку", "price_byn": 185000, "reasoning": "средняя"},
                "optimal_price": {
                    "label": "Оптимально",
                    "price_byn": 205000,
                    "reasoning": "максимум",
                },
                "reasoning": "Высокий спрос",
            },
            "recommendation": {
                "verdict": "think_twice",
                "text": "Ориентируйтесь на 180 000 BYN.",
            },
            "market_context": "Реальная стоимость около 180 000 BYN.",
            "summary": "Реальная стоимость около 180 000 BYN, а не 44 000 BYN.",
        }


def _wait_for_task_result(client: TestClient, task_id: str, timeout_s: float = 5.0) -> dict:
    deadline = monotonic() + timeout_s
    while monotonic() < deadline:
        response = client.get(f"/api/v1/ai/task/{task_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] == "done":
            return payload["result"]
        if payload["status"] == "error":
            raise AssertionError(payload.get("error") or "AI task failed")
        sleep(0.05)
    raise AssertionError("AI task did not finish in time")


def test_ai_analyze_endpoint_returns_payload(monkeypatch) -> None:
    from api.dependencies import get_telegram_user
    from api.main import create_app
    from api.routers import ai_analysis

    async def fake_load_query_dataset(**kwargs):
        del kwargs
        return SimpleNamespace(
            ads=[
                {
                    "ad_id": 1,
                    "subject": "iPhone 14",
                    "body": "Отличное состояние",
                    "price_byn": 160000,
                    "ad_link": "https://www.kufar.by/item/1",
                    "images": [{"path": "adim1/test.jpg"}],
                    "ad_parameters": [
                        {"p": "condition", "vl": "Б/у"},
                        {"p": "memory", "pl": "Память", "vl": "128 Гб"},
                    ],
                },
                {
                    "ad_id": 2,
                    "subject": "iPhone 14 128GB",
                    "price_byn": 158000,
                    "ad_link": "https://www.kufar.by/item/2",
                    "images": [{"path": "adim1/test2.jpg"}],
                    "ad_parameters": [],
                },
            ],
            price_stats=SimpleNamespace(
                median=1590.0,
                count=24,
                q1=1550.0,
                q3=1630.0,
                min=1500.0,
                max=1700.0,
            ),
        )

    fake_ai = FakeAIService()
    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: fake_ai)
    monkeypatch.setattr(ai_analysis, "load_query_dataset", fake_load_query_dataset)

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ai/analyze",
            json={"ad_id": 1, "query": "iphone 14"},
        )
        assert response.status_code == 200
        start_payload = response.json()
        assert start_payload["task_id"]

        payload = _wait_for_task_result(client, start_payload["task_id"])

    assert payload["ad_id"] == 1
    assert payload["condition"]["label"] == "Хорошее"
    assert payload["fair_price"]["from"] == 1500
    assert payload["best_alternative"]["ad_id"] == 2
    assert fake_ai.calls
    assert fake_ai.calls[0]["is_negotiable_price"] is False


def test_ai_task_status_reads_from_cache_backend() -> None:
    from api.main import create_app

    app = create_app()

    with TestClient(app) as client:
        cache = client.app.state.cache
        import asyncio

        asyncio.run(cache.set_json(
            "ai_task:cached-task",
            {
                "status": "done",
                "progress": 100,
                "result": {"ad_id": 42, "summary": "ok"},
                "error": None,
                "_created_ts": monotonic(),
            },
            ttl=3600,
        ))

        response = client.get("/api/v1/ai/task/cached-task")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "done"
    assert payload["progress"] == 100
    assert payload["result"]["ad_id"] == 42


def test_ai_negotiable_price_context_does_not_use_zero_as_real_price() -> None:
    service = AIService()

    context = service._build_listing_context(
        title="iPhone 14",
        description="Цена договорная",
        price_byn=0,
        is_negotiable_price=True,
        condition="Б/у",
        parameters=[{"label": "Память", "value": "128 Гб"}],
        market_median=1600,
        market_count=12,
        market_q1=1500,
        market_q3=1700,
        market_min=1400,
        market_max=1800,
        seller_type="private",
        photo_count=3,
        similar_listings=[
            {
                "ad_id": 2,
                "title": "iPhone 14 128GB",
                "price_byn": 1580,
                "condition": "Б/у",
                "seller_type": "private",
                "age_days": 2,
            }
        ],
    )

    assert "Цена: договорная" in context
    assert "цена покупки 0 BYN" not in context
    assert "на 1580 BYN дороже" not in context
    assert "ниже медианы" in context
    assert "Разумный вход после торга" in context


def test_ai_guardrails_clamp_outlier_price_for_negotiable_financing_bait(monkeypatch) -> None:
    from api.dependencies import get_telegram_user
    from api.main import create_app
    from api.routers import ai_analysis

    async def fake_load_query_dataset(**kwargs):
        del kwargs
        return SimpleNamespace(
            ads=[
                {
                    "ad_id": 10,
                    "subject": "БЕЗ ВЗНОСА НА 10 ЛЕТ AUDI Q7 4M Рестайлинг",
                    "body": "Цена договорная, возможен кредит.",
                    "price_byn": 0,
                    "ad_link": "https://www.kufar.by/item/10",
                    "images": [{"path": "adim1/test.jpg"}],
                    "ad_parameters": [{"p": "condition", "vl": "Б/у"}],
                },
                {
                    "ad_id": 11,
                    "subject": "Audi Q7 3.0 tdi",
                    "price_byn": 4469200,
                    "ad_link": "https://www.kufar.by/item/11",
                    "images": [{"path": "adim1/test2.jpg"}],
                    "ad_parameters": [{"p": "condition", "vl": "Б/у"}],
                },
                {
                    "ad_id": 12,
                    "subject": "Audi Q7 (4L) Рестайлинг",
                    "price_byn": 4005400,
                    "ad_link": "https://www.kufar.by/item/12",
                    "images": [{"path": "adim1/test3.jpg"}],
                    "ad_parameters": [{"p": "condition", "vl": "Б/у"}],
                },
            ],
            price_stats=SimpleNamespace(
                median=44411.0,
                count=18,
                q1=40054.0,
                q3=44692.0,
                min=39000.0,
                max=47000.0,
            ),
        )

    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: FakeOutlierAIService())
    monkeypatch.setattr(ai_analysis, "load_query_dataset", fake_load_query_dataset)

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ai/analyze",
            json={"ad_id": 10, "query": "audi q7"},
        )
        assert response.status_code == 200
        start_payload = response.json()
        payload = _wait_for_task_result(client, start_payload["task_id"])

    assert payload["fair_price"]["from"] == 40054
    assert payload["fair_price"]["to"] == 44692
    assert payload["resale_potential"]["market_price"]["price_byn"] == 42373
    assert "180 000" not in payload["summary"]
    assert "40" in payload["market_context"]


def test_marketplace_risk_context_detects_hot_words() -> None:
    ad = {
        "subject": "Audi Q7 из автохауса, кредит без взноса",
        "body": "Автохаус, лизинг, рассрочка. Поможем с оформлением.",
        "company_ad": True,
        "ad_parameters": [],
    }

    context = build_marketplace_risk_context(ad)
    merged = merge_marketplace_red_flags([], context)

    assert context.score >= 3.0
    assert any(word in " ".join(context.hot_words) for word in ("автохаус", "кредит"))
    assert any("автохаус" in flag.lower() or "кредит" in flag.lower() for flag in merged)


def test_finalize_red_flags_prioritizes_marketplace_risk_and_limits_count() -> None:
    risk = build_marketplace_risk_context(
        {
            "subject": "iPhone 15, кредит, рассрочка, перекуп",
            "body": "Магазин. Кредит и рассрочка.",
            "company_ad": True,
            "ad_parameters": [],
        }
    )
    flags = finalize_red_flags(
        [
            "Очень длинный и размытый флаг, который повторяет то же самое про магазин.",
            "Продавец нечастный.",
            "Есть риск по кредиту.",
            "Продавец нечастный.",
        ],
        risk,
    )

    assert 1 <= len(flags) <= 3
    assert any("кредит" in flag.lower() or "площад" in flag.lower() or "автохаус" in flag.lower() for flag in flags)


def test_ai_analyze_prefers_precise_analogs_and_adds_marketplace_red_flag(monkeypatch) -> None:
    from api.dependencies import get_telegram_user
    from api.main import create_app
    from api.routers import ai_analysis

    target_ad = {
        "ad_id": 10,
        "subject": "Audi Q7 I (4L) Рестайлинг",
        "body": "Автохаус. Кредит без взноса. Пробег 382000 км.",
        "price_byn": 4005400,
        "ad_link": "https://auto.kufar.by/vi/10",
        "company_ad": True,
        "images": [{"path": "adim1/test.jpg"}],
        "ad_parameters": [
            {"p": "condition", "vl": "Б/у"},
            {"p": "generation", "pl": "Поколение", "vl": "4L"},
            {"p": "engine", "pl": "Двигатель", "vl": "3.0"},
            {"p": "fuel", "pl": "Топливо", "vl": "Дизель"},
            {"p": "category", "pl": "Категория", "vl": "Легковые авто"},
        ],
        "category": 2010,
    }
    precise_analog = {
        "ad_id": 11,
        "subject": "Audi Q7 4L 3.0 tdi",
        "body": "Продаю Q7 4L, дизель, 319000 км.",
        "price_byn": 3091900,
        "ad_link": "https://auto.kufar.by/vi/11",
        "company_ad": False,
        "images": [{"path": "adim1/test2.jpg"}],
        "ad_parameters": [
            {"p": "condition", "vl": "Б/у"},
            {"p": "generation", "pl": "Поколение", "vl": "4L"},
            {"p": "engine", "pl": "Двигатель", "vl": "3.0"},
            {"p": "fuel", "pl": "Топливо", "vl": "Дизель"},
            {"p": "category", "pl": "Категория", "vl": "Легковые авто"},
        ],
        "category": 2010,
    }
    weak_broad = {
        "ad_id": 12,
        "subject": "Audi Q7 4M 2.0 бензин",
        "body": "Другое поколение.",
        "price_byn": 4010000,
        "ad_link": "https://auto.kufar.by/vi/12",
        "company_ad": False,
        "images": [{"path": "adim1/test3.jpg"}],
        "ad_parameters": [
            {"p": "condition", "vl": "Б/у"},
            {"p": "generation", "pl": "Поколение", "vl": "4M"},
            {"p": "engine", "pl": "Двигатель", "vl": "2.0"},
            {"p": "fuel", "pl": "Топливо", "vl": "Бензин"},
            {"p": "category", "pl": "Категория", "vl": "Легковые авто"},
        ],
        "category": 2010,
    }

    async def fake_load_query_dataset(**kwargs):
        strict_search = kwargs.get("strict_search", False)
        ads = [target_ad, precise_analog] if strict_search else [target_ad, precise_analog, weak_broad]
        return SimpleNamespace(
            ads=ads,
            price_stats=SimpleNamespace(
                median=40054.0,
                count=len(ads),
                q1=35000.0,
                q3=43000.0,
                min=30919.0,
                max=40100.0,
            ),
        )

    class QuietAIService(FakeAIService):
        async def analyze_listing_parallel(self, **kwargs) -> dict:
            self.calls.append(kwargs)
            return {
                "condition": {
                    "label": "Хорошее",
                    "confidence": 0.8,
                    "notes": ["Нормальное состояние"],
                },
                "fair_price": {
                    "from": 32000,
                    "to": 36000,
                    "reasoning": "Ориентир по точным аналогам.",
                },
                "recommendation": {
                    "verdict": "overpriced",
                    "text": "Торговаться или искать другой вариант.",
                },
                "red_flags": [],
                "summary": "Цена высокая.",
            }

    fake_ai = QuietAIService()
    monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: fake_ai)
    monkeypatch.setattr(ai_analysis, "load_query_dataset", fake_load_query_dataset)

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ai/analyze",
            json={"ad_id": 10, "query": "audi q7", "category": 2010},
        )
        assert response.status_code == 200
        payload = _wait_for_task_result(client, response.json()["task_id"])

    assert payload["best_alternative"]["ad_id"] == 11
    assert len(payload["similar_listings"]) == 1
    assert any("автохаус" in flag.lower() or "кредит" in flag.lower() for flag in payload["red_flags"])
    assert fake_ai.calls
    assert fake_ai.calls[0]["risk_context_summary"]
    assert fake_ai.calls[0]["risk_context_flags"]


def test_choose_best_alternative_prefers_strong_and_cheaper_candidate() -> None:
    decision = choose_best_alternative(
        [
            {"ad_id": 1, "price_byn": 1100, "deal_score": 40, "seller_type": "shop", "image_url": None},
            {"ad_id": 2, "price_byn": 900, "deal_score": 39, "seller_type": "private", "image_url": "x"},
        ],
        target_price=1200,
        is_negotiable_price=False,
    )

    assert decision.item is not None
    assert decision.item["ad_id"] == 2
    assert decision.reason


def test_market_context_fallback_mentions_best_analog() -> None:
    risk = build_marketplace_risk_context(
        {
            "subject": "Audi Q7 автохаус кредит",
            "body": "Автохаус. Кредит.",
            "company_ad": True,
            "ad_parameters": [],
        }
    )
    context = build_market_context_fallback(
        price_byn=40054,
        is_negotiable_price=False,
        market_median=35000,
        similar_listings=[{"ad_id": 11, "price_byn": 30919}],
        risk_context=risk,
        ai_market_context="",
    )

    assert "30919" in context
    assert "40054" in context


def test_collect_similar_listings_prefers_exact_phone_config() -> None:
    target = {
        "ad_id": 1,
        "subject": "iPhone 15 Pro 256 Black",
        "body": "Оригинал",
        "price_byn": 260000,
        "images": [{"path": "adim1/a.jpg"}],
        "ad_parameters": [{"p": "category", "vl": "Телефоны"}],
    }
    exact = {
        "ad_id": 2,
        "subject": "iPhone 15 Pro 256GB Black",
        "body": "В хорошем состоянии",
        "price_byn": 258000,
        "images": [{"path": "adim1/b.jpg"}],
        "ad_parameters": [{"p": "category", "vl": "Телефоны"}],
    }
    weak = {
        "ad_id": 3,
        "subject": "iPhone 15 128 Blue",
        "body": "Тоже продаю",
        "price_byn": 255000,
        "images": [{"path": "adim1/c.jpg"}],
        "ad_parameters": [{"p": "category", "vl": "Телефоны"}],
    }

    sims = collect_similar_listings_from_cohorts(
        cohorts=[("strict_category", SimpleNamespace(ads=[target, exact, weak]))],
        query="iphone 15 pro",
        target_ad=target,
        target_ad_id=1,
        target_price=2600.0,
        market_median=2500.0,
    )

    assert sims
    assert sims[0]["ad_id"] == 2
    assert sims[0]["deal_score"] > next(item["deal_score"] for item in sims if item["ad_id"] == 3)


def test_collect_similar_listings_rejects_wrong_auto_part_type() -> None:
    target = {
        "ad_id": 10,
        "subject": "Форсунка Audi Q7 4L",
        "body": "Запчасти для Q7",
        "price_byn": 15000,
        "images": [{"path": "adim1/a.jpg"}],
        "category": 2040,
        "ad_parameters": [{"p": "category", "vl": "Запчасти"}],
    }
    exact = {
        "ad_id": 11,
        "subject": "Форсунка Audi Q7 4L дизель",
        "body": "Оригинал",
        "price_byn": 14000,
        "images": [{"path": "adim1/b.jpg"}],
        "category": 2040,
        "ad_parameters": [{"p": "category", "vl": "Запчасти"}],
    }
    wrong_part = {
        "ad_id": 12,
        "subject": "ТНВД Audi Q7 4L",
        "body": "Тоже запчасть",
        "price_byn": 14500,
        "images": [{"path": "adim1/c.jpg"}],
        "category": 2040,
        "ad_parameters": [{"p": "category", "vl": "Запчасти"}],
    }

    sims = collect_similar_listings_from_cohorts(
        cohorts=[("broad_category", SimpleNamespace(ads=[target, exact, wrong_part]))],
        query="форсунка audi q7",
        target_ad=target,
        target_ad_id=10,
        target_price=150.0,
        market_median=145.0,
    )

    assert [item["ad_id"] for item in sims] == [11]


def test_collect_similar_listings_prefers_matching_watch_size_and_series() -> None:
    target = {
        "ad_id": 20,
        "subject": "Apple Watch Series 9 45мм Black",
        "body": "Оригинал",
        "price_byn": 120000,
        "images": [{"path": "adim1/a.jpg"}],
        "ad_parameters": [{"p": "category", "vl": "Часы"}],
    }
    exact = {
        "ad_id": 21,
        "subject": "Apple Watch Series 9 45 мм",
        "body": "Полный комплект",
        "price_byn": 119000,
        "images": [{"path": "adim1/b.jpg"}],
        "ad_parameters": [{"p": "category", "vl": "Часы"}],
    }
    weak = {
        "ad_id": 22,
        "subject": "Apple Watch SE 41мм",
        "body": "Тоже часы",
        "price_byn": 118000,
        "images": [{"path": "adim1/c.jpg"}],
        "ad_parameters": [{"p": "category", "vl": "Часы"}],
    }

    sims = collect_similar_listings_from_cohorts(
        cohorts=[("strict_category", SimpleNamespace(ads=[target, exact, weak]))],
        query="apple watch 45",
        target_ad=target,
        target_ad_id=20,
        target_price=1200.0,
        market_median=1190.0,
    )

    assert sims
    assert sims[0]["ad_id"] == 21


def test_quick_condition_normalization_cleans_template_output() -> None:
    assert _normalize_condition_label("Отличное|Хорошее|Удовлетворительное|Требует внимания") == "Хорошее"
    assert _clean_photo_notes(["заметка1", "Есть реальные фото устройства", "Есть реальные фото устройства"]) == [
        "Есть реальные фото устройства"
    ]


def test_complete_analysis_sections_restores_full_sections() -> None:
    risk = build_marketplace_risk_context(
        {
            "subject": "iPhone 14 Pro Max из магазина, возможна рассрочка",
            "body": "Магазин, кредит, рассрочка.",
            "company_ad": True,
            "ad_parameters": [],
        }
    )
    result = complete_analysis_sections(
        result={
            "recommendation": {"verdict": "think_twice", "text": "Нужен торг."},
            "summary": "Коротко.",
            "watch_out": [],
            "meeting_checklist": [],
            "negotiation_tips": [],
        },
        title="iPhone 14 Pro Max 256 GB",
        parameters=[{"label": "Память", "value": "256 Гб"}],
        price_byn=1450,
        market_median=1300,
        best_alternative={"ad_id": 1, "price_byn": 1250},
        risk_context=risk,
        photo_condition_label="Хорошее",
        photo_condition_notes=["Есть реальные фото устройства", "На корпусе видны лёгкие потёртости"],
        is_negotiable_price=False,
        red_flags=["В объявлении есть акцент на кредите, а не на фактическом состоянии товара"],
    )

    assert result["condition"]["label"] == "Хорошее"
    assert len(result["watch_out"]) >= 3
    assert len(result["meeting_checklist"]) >= 4
    assert len(result["negotiation_tips"]) >= 3
    assert len(result["summary"]) > 90


def test_complete_analysis_sections_adds_resale_potential_fallback() -> None:
    from api.services.ai_marketplace import _fallback_resale_potential

    # When AI returns no resale_potential, complete_analysis_sections fills it
    risk = build_marketplace_risk_context(
        {"subject": "iPhone 14", "body": "Продаю", "ad_parameters": []}
    )
    result = complete_analysis_sections(
        result={
            "recommendation": {"verdict": "think_twice", "text": "Нужен торг."},
            "summary": "Коротко.",
        },
        title="iPhone 14 128GB",
        parameters=[{"label": "Память", "value": "128 Гб"}],
        price_byn=1500,
        market_median=1400,
        best_alternative={"ad_id": 2, "price_byn": 1350},
        risk_context=risk,
        photo_condition_label="",
        photo_condition_notes=[],
        is_negotiable_price=False,
        red_flags=[],
        market_q1=1300,
        market_q3=1500,
    )

    resale = result.get("resale_potential")
    assert resale is not None
    assert isinstance(resale, dict)
    assert resale["fast_price"]["price_byn"] > 0
    assert resale["market_price"]["price_byn"] > 0
    assert resale["optimal_price"]["price_byn"] > 0
    # fast < market < optimal
    assert resale["fast_price"]["price_byn"] < resale["market_price"]["price_byn"]
    assert resale["market_price"]["price_byn"] < resale["optimal_price"]["price_byn"]

    # Also test the standalone function
    standalone = _fallback_resale_potential(
        price_byn=1500,
        is_negotiable_price=False,
        market_median=1400,
        market_q1=1300,
        market_q3=1500,
        best_alternative={"ad_id": 2, "price_byn": 1350},
    )
    assert standalone is not None
    assert standalone["fast_price"]["price_byn"] < standalone["market_price"]["price_byn"]

    # Returns None when no market data at all
    empty = _fallback_resale_potential(
        price_byn=0,
        is_negotiable_price=True,
        market_median=None,
        market_q1=None,
        market_q3=None,
        best_alternative=None,
    )
    assert empty is None
