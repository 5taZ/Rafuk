from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from api.services.aggregator import get_param, normalize_price_byn, normalize_search_text, tokenize_search_text
from api.services.ai_guardrails import contains_financing_bait
from api.services.ai_service import detect_category
from api.services.listing_mapper import first_image_url
from api.services.reseller_tools import analyze_query_text

_IGNORED_PARAM_KEYS = {"condition", "currency", "price", "users_synonyms"}
_STOP_TOKENS = {
    "купить",
    "продам",
    "продаю",
    "в",
    "на",
    "для",
    "и",
    "с",
    "без",
    "по",
    "из",
    "от",
    "до",
}
_FUEL_WORDS = {
    "diesel": ("дизель", "tdi", "dci", "hdi", "cdi"),
    "petrol": ("бензин", "fsi", "tsi", "tfsi", "gdi"),
    "hybrid": ("гибрид", "hybrid"),
    "electric": ("электро", "electric", "ev"),
    "gas": ("газ", "lpg", "метан"),
}
_TRANS_WORDS = {
    "auto": ("автомат", "акпп", "tiptronic", "вариатор", "dsg", "робот"),
    "manual": ("механика", "мкпп"),
}
_HOT_WORD_GROUPS: dict[str, tuple[str, ...]] = {
    "finance": (
        "кредит",
        "лизинг",
        "рассрочка",
        "платеж",
        "платёж",
        "/мес",
        "в месяц",
        "без взноса",
    ),
    "reseller": (
        "перекуп",
        "автохаус",
        "автосалон",
        "площадка",
        "комиссион",
        "магазин",
        "trade-in",
        "трейд ин",
    ),
    "sales": (
        "доставка",
        "гарантия",
        "оформим",
        "подберем",
        "подберём",
        "в наличии",
        "под заказ",
    ),
}
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_ENGINE_RE = re.compile(r"\b([1-8]\.\d)\b")
_AUTO_GEN_RE = re.compile(r"\b([a-z]?\d{1,3}[a-z]{0,2})\b")
_SCREEN_RE = re.compile(r"\b(\d{1,2}(?:[.,]\d)?)\s*(?:['\"″]|дюйм)")
_MM_RE = re.compile(r"\b(\d{2,3})\s*мм\b")
_FOCAL_RE = re.compile(r"\b(\d{2,3})\s*mm\b")
_APERTURE_RE = re.compile(r"\bf/?(\d(?:\.\d)?)\b")
_AUTO_PART_STEMS = (
    "форсунк",
    "тнвд",
    "датчик",
    "бампер",
    "капот",
    "двер",
    "крыл",
    "фар",
    "фонар",
    "турбин",
    "амортиз",
    "насос",
    "рейк",
    "коробк",
    "акпп",
    "мкпп",
    "двигател",
    "мотор",
    "стартер",
    "генератор",
    "радиатор",
    "интеркулер",
    "зеркал",
    "сиден",
    "салон",
    "рул",
)
_COLOR_WORDS = (
    "black",
    "white",
    "blue",
    "green",
    "red",
    "pink",
    "gold",
    "silver",
    "purple",
    "gray",
    "grey",
    "space gray",
    "черный",
    "чёрный",
    "белый",
    "синий",
    "голубой",
    "зеленый",
    "зелёный",
    "красный",
    "розовый",
    "золотой",
    "серый",
    "фиолетовый",
)
_AUTO_BRAND_TOKENS = {
    "audi",
    "bmw",
    "mercedes",
    "volkswagen",
    "toyota",
    "honda",
    "ford",
    "hyundai",
    "kia",
    "nissan",
    "skoda",
    "mazda",
    "opel",
    "renault",
    "peugeot",
    "q7",
    "q5",
    "q3",
    "x5",
    "x3",
    "a4",
    "a6",
    "a8",
}
_CATEGORY_GENERIC_TOKENS: dict[str, set[str]] = {
    "headphones": {
        "наушники", "headphones", "buds", "airpods", "pro", "max", "wireless", "bluetooth",
    },
    "watch": {
        "watch", "часы", "smartwatch", "series", "ultra", "classic", "band",
    },
    "camera": {
        "camera", "камера", "объектив", "lens", "зеркалка", "беззеркалка", "body",
    },
    "phone": {
        "iphone", "phone", "телефон", "смартфон", "pro", "max", "plus", "mini",
    },
    "tablet": {
        "tablet", "планшет", "ipad", "tab",
    },
    "laptop": {
        "laptop", "ноутбук", "macbook", "ультрабук", "gaming", "игровой",
    },
}


@dataclass(slots=True, frozen=True)
class MarketplaceRiskContext:
    score: float
    flags: list[str]
    summary: str
    hot_words: list[str]


@dataclass(slots=True, frozen=True)
class BestAlternativeDecision:
    item: dict[str, Any] | None
    reason: str


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip(" .;"))


def _unique_texts(items: list[str], *, max_items: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _clean_text(item)
        if not text:
            continue
        key = normalize_search_text(text)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    return cleaned


def _normalized_ad_text(ad: dict[str, Any]) -> str:
    title = str(ad.get("subject", "") or ad.get("title", ""))
    description = str(ad.get("body", "") or ad.get("description", ""))
    param_values = " ".join(
        str(param.get("vl") or param.get("v") or "")
        for param in ad.get("ad_parameters", [])
    )
    seller_text = get_param(ad, "seller_type") or ""
    return normalize_search_text(" ".join((title, description, param_values, seller_text)))


def _ad_condition(ad: dict[str, Any]) -> str | None:
    return get_param(ad, "condition")


def _ad_parameters_string(ad: dict[str, Any]) -> str:
    items: list[str] = []
    for param in ad.get("ad_parameters", []):
        key = str(param.get("p", ""))
        if key in _IGNORED_PARAM_KEYS:
            continue
        label = str(param.get("pl") or key).strip()
        value = str(param.get("vl") or param.get("v") or "").strip()
        if label and value:
            items.append(f"{label}: {value}")
    return ", ".join(items[:8])


def _parse_age_days(list_time_str: str | None) -> int | None:
    if not list_time_str:
        return None
    try:
        dt = datetime.fromisoformat(list_time_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0, (datetime.now(UTC) - dt).days)
    except (TypeError, ValueError):
        return None


def _ad_param_map(ad: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for param in ad.get("ad_parameters", []):
        key = str(param.get("p", "")).strip()
        if not key or key in _IGNORED_PARAM_KEYS:
            continue
        value = str(param.get("vl") or param.get("v") or "").strip()
        if value:
            result[key] = normalize_search_text(value)
    return result


def _token_set(value: str) -> set[str]:
    return {token for token in tokenize_search_text(value) if token and token not in _STOP_TOKENS}


def _extract_hot_words(text: str) -> list[str]:
    found: list[str] = []
    for keywords in _HOT_WORD_GROUPS.values():
        for keyword in keywords:
            if keyword in text and keyword not in found:
                found.append(keyword)
    return found


def _extract_auto_features(text: str) -> dict[str, Any]:
    years = {int(match) for match in _YEAR_RE.findall(text)}
    engines = {match for match in _ENGINE_RE.findall(text)}
    generations = {
        match
        for match in _AUTO_GEN_RE.findall(text)
        if any(ch.isdigit() for ch in match) and len(match) <= 5
    }
    fuels = {label for label, words in _FUEL_WORDS.items() if any(word in text for word in words)}
    transmissions = {
        label for label, words in _TRANS_WORDS.items() if any(word in text for word in words)
    }
    return {
        "years": years,
        "engines": engines,
        "generations": generations,
        "fuels": fuels,
        "transmissions": transmissions,
    }


def build_marketplace_risk_context(ad: dict[str, Any]) -> MarketplaceRiskContext:
    title = str(ad.get("subject", "") or ad.get("title", ""))
    description = str(ad.get("body", "") or ad.get("description", ""))
    normalized_text = _normalized_ad_text(ad)
    hot_words = _extract_hot_words(normalized_text)
    flags: list[str] = []
    score = 0.0

    if bool(ad.get("company_ad")):
        score += 1.5
        flags.append("Объявление размещено магазином/площадкой, а не частным лицом.")

    if contains_financing_bait(title, description):
        score += 2.0
        flags.append("В объявлении есть акцент на кредите, рассрочке или ежемесячном платеже.")

    reseller_words = [word for word in hot_words if word in _HOT_WORD_GROUPS["reseller"]]
    if reseller_words:
        score += 2.0
        if "автохаус" in reseller_words:
            flags.append("В тексте есть явный маркер автохауса или дилерской площадки.")
        else:
            flags.append(
                "В тексте есть признаки перепродажи или площадки: "
                + ", ".join(reseller_words[:3])
                + "."
            )

    sales_words = [word for word in hot_words if word in _HOT_WORD_GROUPS["sales"]]
    if sales_words:
        score += 0.5

    desc_len = len(description.strip())
    if desc_len < 60 and (bool(ad.get("company_ad")) or reseller_words or contains_financing_bait(title, description)):
        score += 1.0
        flags.append("Описание малоинформативное и больше похоже на продажный шаблон.")

    summary_parts = list(dict.fromkeys(flags))
    summary = " ".join(summary_parts[:3])
    return MarketplaceRiskContext(
        score=round(score, 2),
        flags=summary_parts[:4],
        summary=summary,
        hot_words=hot_words[:8],
    )


def merge_marketplace_red_flags(
    red_flags: list[str] | None,
    risk_context: MarketplaceRiskContext,
) -> list[str]:
    merged = [flag for flag in (red_flags or []) if isinstance(flag, str) and flag.strip()]
    normalized = normalize_search_text(" ".join(merged))
    if risk_context.score < 2.5:
        return merged
    if (
        not any(word in risk_context.hot_words for word in (*_HOT_WORD_GROUPS["finance"], *_HOT_WORD_GROUPS["reseller"]))
        and risk_context.score < 3.5
    ):
        return merged

    if any(word in normalized for word in ("автохаус", "кредит", "рассрочка", "перекуп", "площадка")):
        return merged

    synthesized = ""
    if "автохаус" in risk_context.hot_words:
        synthesized = (
            "Продавец — автохаус/дилерская площадка; возможна наценка и слабая "
            "прозрачность истории обслуживания."
        )
    elif any(word in risk_context.hot_words for word in _HOT_WORD_GROUPS["reseller"]):
        synthesized = (
            "В объявлении есть признаки перепродажи или площадки; проверяйте "
            "происхождение товара и прозрачность истории."
        )
    elif any(word in risk_context.hot_words for word in _HOT_WORD_GROUPS["finance"]):
        synthesized = (
            "В объявлении акцент на кредите/рассрочке, а не на фактическом состоянии "
            "товара; условия и реальную стоимость нужно перепроверить отдельно."
        )
    elif risk_context.flags:
        synthesized = risk_context.flags[0]

    if synthesized:
        merged.append(synthesized)
    return merged


def finalize_red_flags(
    red_flags: list[str] | None,
    risk_context: MarketplaceRiskContext,
    *,
    max_items: int = 3,
) -> list[str]:
    merged = merge_marketplace_red_flags(red_flags, risk_context)
    cleaned: list[str] = []
    seen: set[str] = set()
    priority_words = ("автохаус", "кредит", "рассрочка", "перекуп", "площадка", "лизинг")

    for item in merged:
        text = re.sub(r"\s+", " ", str(item or "").strip(" .;"))
        if not text:
            continue
        key = normalize_search_text(text)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)

    cleaned.sort(
        key=lambda text: (
            0 if any(word in normalize_search_text(text) for word in priority_words) else 1,
            len(text),
        )
    )
    return cleaned[:max_items]


def _cohort_weight(cohort_key: str) -> float:
    if cohort_key == "strict_category":
        return 26.0
    if cohort_key == "broad_category":
        return 18.0
    if cohort_key == "broad_query":
        return 10.0
    return 0.0


def choose_best_alternative(
    similar_listings: list[dict[str, Any]],
    *,
    target_price: float,
    is_negotiable_price: bool,
    ai_best_pick_ad_id: int | None = None,
) -> BestAlternativeDecision:
    if not similar_listings:
        return BestAlternativeDecision(item=None, reason="")

    if ai_best_pick_ad_id:
        chosen = next((item for item in similar_listings if item["ad_id"] == ai_best_pick_ad_id), None)
        if chosen is not None:
            return BestAlternativeDecision(item=chosen, reason="")

    def _score(item: dict[str, Any]) -> float:
        score = float(item.get("deal_score") or 0.0)
        if item.get("seller_type") == "private":
            score += 2.0
        if item.get("image_url"):
            score += 1.0
        if not is_negotiable_price and target_price > 0:
            candidate_price = float(item.get("price_byn") or 0.0)
            if candidate_price > 0:
                score += ((target_price - candidate_price) / target_price) * 20.0
        return score

    chosen = max(
        similar_listings,
        key=lambda item: (_score(item), -float(item.get("price_byn") or 0.0)),
    )
    if not is_negotiable_price and target_price > 0 and float(chosen.get("price_byn") or 0.0) > 0:
        diff = int(round(target_price - float(chosen["price_byn"])))
        if diff > 0:
            reason = f"Ближайший сильный аналог: дешевле на {diff} BYN и выглядит наиболее сопоставимым."
        elif diff < 0:
            reason = "Ближайший сильный аналог: немного дороже, но самый сопоставимый по конфигурации."
        else:
            reason = "Ближайший сильный аналог по конфигурации и структуре объявления."
    else:
        reason = "Ближайший сильный аналог по конфигурации и структуре объявления."
    return BestAlternativeDecision(item=chosen, reason=reason)


def build_market_context_fallback(
    *,
    price_byn: float,
    is_negotiable_price: bool,
    market_median: float | None,
    similar_listings: list[dict[str, Any]],
    risk_context: MarketplaceRiskContext,
    ai_market_context: str | None = None,
) -> str:
    base = str(ai_market_context or "").strip()
    if len(base) >= 90 and any(ch.isdigit() for ch in base):
        return base

    best = similar_listings[0] if similar_listings else None
    parts: list[str] = []
    if is_negotiable_price:
        if market_median:
            parts.append(
                f"Цена в объявлении не указана, поэтому ориентир по рынку сейчас около {int(round(market_median))} BYN."
            )
    elif market_median:
        delta = ((price_byn - market_median) / market_median) * 100 if market_median else 0.0
        if delta <= -8:
            parts.append(
                f"Цена {int(round(price_byn))} BYN заметно ниже рынка, медиана по выборке около {int(round(market_median))} BYN."
            )
        elif delta >= 8:
            parts.append(
                f"Цена {int(round(price_byn))} BYN выше рынка, медиана по выборке около {int(round(market_median))} BYN."
            )
        else:
            parts.append(
                f"Цена {int(round(price_byn))} BYN близка к рынку, медиана по выборке около {int(round(market_median))} BYN."
            )

    if best is not None:
        best_price = int(round(float(best.get("price_byn") or 0.0)))
        if not is_negotiable_price and price_byn > 0 and best_price > 0:
            diff = int(round(price_byn - best_price))
            if diff > 0:
                parts.append(
                    f"Самый близкий аналог [{best.get('ad_id')}] стоит {best_price} BYN, то есть дешевле примерно на {diff} BYN."
                )
            elif diff < 0:
                parts.append(
                    f"Самый близкий аналог [{best.get('ad_id')}] стоит {best_price} BYN, то есть дороже примерно на {abs(diff)} BYN."
                )
            else:
                parts.append(
                    f"Самый близкий аналог [{best.get('ad_id')}] стоит примерно столько же: {best_price} BYN."
                )
        else:
            parts.append(
                f"Сильный аналог [{best.get('ad_id')}] находится в районе {best_price} BYN и задаёт ориентир по рынку."
            )

    if risk_context.score >= 3.0 and risk_context.summary:
        parts.append(risk_context.summary)

    fallback = " ".join(parts).strip()
    return fallback or base


_CATEGORY_WATCH_OUT: dict[str, list[tuple[str, str]]] = {
    "phone": [
        ("Аккумулятор", "Проверь ёмкость батареи и скорость разряда, это главный скрытый расход."),
        ("Экран и камеры", "Осмотри экран на выгорание и проверь все камеры без ошибок и запотевания."),
        ("Регион и блокировки", "Уточни модель, регион и отсутствие Activation Lock или операторских ограничений."),
    ],
    "laptop": [
        ("Батарея и нагрев", "Проверь износ батареи, шум вентиляторов и температуру под нагрузкой."),
        ("Экран и петли", "Осмотри матрицу на засветы, пиксели и люфт крышки."),
        ("Порты и SSD", "Проверь все порты и состояние диска, чтобы не получить скрытый ремонт сразу после покупки."),
    ],
    "tablet": [
        ("Экран и сенсор", "Проверь сенсор по всей площади и наличие пятен или засветов."),
        ("Батарея", "Спроси про автономность и посмотри, не проседает ли заряд слишком быстро."),
        ("Комплект", "Уточни, идёт ли оригинальная зарядка, стилус или клавиатура, если это важно для модели."),
    ],
    "auto": [
        ("Кузов и история", "Проверь VIN, толщиномер и историю ДТП, потому что именно здесь обычно скрывают риски."),
        ("Техника", "Слушай двигатель и коробку на холодную и после короткой поездки."),
        ("Документы", "Уточни владельца, техосмотр и совпадение документов с VIN."),
    ],
    "auto_parts": [
        ("Совместимость", "Сверь OEM-номер, поколение и модификацию, иначе деталь может не подойти."),
        ("Состояние узла", "Попроси крупные фото посадочных мест, разъёмов и следов ремонта."),
        ("Возврат", "Уточни возможность возврата после примерки или проверки на стенде."),
    ],
    "watch": [
        ("Батарея и экран", "Проверь автономность и экран на царапины по краям и олеофобку."),
        ("Оригинальность", "Сверь серийный номер и интерфейс, чтобы исключить копию."),
        ("Комплект и ремешок", "Уточни состояние ремешка, зарядки и защиту от воды."),
    ],
    "headphones": [
        ("Батарея и звук", "Проверь оба канала, микрофон и реальное время работы от батареи."),
        ("Оригинальность", "Для популярных моделей обязательно сверяй серийный номер и упаковку."),
        ("Амбушюры и кейс", "Износ расходников кажется мелочью, но часто превращается в быстрые траты."),
    ],
    "camera": [
        ("Матрица и объектив", "Проверь пыль, грибок, царапины и равномерность кадра на закрытой диафрагме."),
        ("Затвор", "Уточни пробег затвора и сравни его с ресурсом модели."),
        ("Стабилизация и видео", "Проверь автофокус, стабилизацию и запись видео без артефактов."),
    ],
    "default": [
        ("Состояние", "Сверь фото, описание и фактические следы износа, чтобы не купить товар хуже заявленного."),
        ("Комплект", "Уточни, что реально входит в комплект и нет ли скрытых недостающих частей."),
        ("Проверка на месте", "Договорись о демонстрации ключевых функций до оплаты."),
    ],
}

_CATEGORY_CHECKLIST: dict[str, list[str]] = {
    "phone": [
        "Проверь IMEI, серийный номер и отсутствие блокировок Apple ID или Google.",
        "Сделай тест экрана: яркость, сенсор, пиксели, True Tone или Face ID если применимо.",
        "Открой камеру, видео и микрофон, чтобы исключить дефекты модулей.",
        "Посмотри здоровье батареи и как аппарат держит заряд при нагрузке.",
        "Проверь разъём, динамики, кнопки и работу связи.",
    ],
    "laptop": [
        "Проверь экран на битые пиксели, засветы и люфт крышки.",
        "Сделай короткий стресс-тест: вентиляторы, нагрев, шум.",
        "Посмотри состояние батареи и циклы зарядки.",
        "Проверь SSD, Wi-Fi, клавиатуру, тачпад и все порты.",
        "Уточни, не стоит ли BIOS или iCloud/MDM-ограничение.",
    ],
    "tablet": [
        "Проверь сенсор по краям экрана и отсутствие пятен или засветов.",
        "Открой камеры, звук и зарядку на месте.",
        "Сверь серийный номер и модель устройства.",
        "Проверь батарею и скорость разряда на ярком экране.",
        "Уточни комплект и совместимость со стилусом или клавиатурой.",
    ],
    "auto": [
        "Сверь VIN в объявлении, на кузове и в документах.",
        "Сделай диагностику ошибок и осмотр кузова толщиномером.",
        "Проверь холодный запуск, коробку и тормоза в короткой поездке.",
        "Осмотри салон, электрику, кондиционер и подушки безопасности.",
        "Уточни количество владельцев и историю обслуживания.",
    ],
    "auto_parts": [
        "Сверь OEM-номер и совместимость именно с твоей модификацией.",
        "Осмотри разъёмы, крепления и следы ремонта.",
        "Уточни пробег донора или историю детали, если это агрегат.",
        "Попроси видео работы или проверку на стенде, если это возможно.",
        "Заранее договорись о возврате при несовместимости.",
    ],
    "default": [
        "Сверь модель, версию и комплектацию с объявлением.",
        "Проверь ключевые функции товара до оплаты.",
        "Осмотри корпус, экран, разъёмы и следы вскрытия.",
        "Уточни комплект, документы и историю использования.",
        "Сравни состояние на месте с фото из объявления.",
    ],
}


def _price_anchor_discount(
    *,
    price_byn: float,
    market_median: float | None,
    best_alternative: dict[str, Any] | None,
    is_negotiable_price: bool,
) -> tuple[int | None, str]:
    if is_negotiable_price:
        if market_median and market_median > 0:
            return int(round(market_median * 0.08)), "от медианы рынка"
        return None, ""

    anchors = [float(x) for x in [market_median, best_alternative.get("price_byn") if best_alternative else None] if x]
    if not anchors or price_byn <= 0:
        return None, ""
    reference = min(anchors)
    overpay = price_byn - reference
    if overpay <= 0:
        return max(40, int(round(price_byn * 0.03))), "за найденные риски и мелкие дефекты"
    target = max(int(round(overpay)), int(round(price_byn * 0.05)))
    return target, "от ближайшего рыночного ориентира"


def _build_fallback_watch_out(
    *,
    category: str,
    photo_condition_notes: list[str],
    risk_context: MarketplaceRiskContext,
    best_alternative: dict[str, Any] | None,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for note in _unique_texts(photo_condition_notes, max_items=2):
        items.append({"point": "Нужно перепроверить по фото", "why": note})
    for point, why in _CATEGORY_WATCH_OUT.get(category, _CATEGORY_WATCH_OUT["default"]):
        items.append({"point": point, "why": why})
    if risk_context.score >= 3.0:
        items.append({
            "point": "Профиль продавца",
            "why": risk_context.summary or "Есть сигналы площадки или перепродажи, поэтому важна дополнительная проверка.",
        })
    if best_alternative and float(best_alternative.get("price_byn") or 0) > 0:
        items.append({
            "point": "Сравнение с альтернативой",
            "why": f"Перед покупкой сравни состояние с вариантом за {int(round(float(best_alternative['price_byn'])))} BYN.",
        })
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in items:
        key = normalize_search_text(f"{item['point']} {item['why']}")
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= 5:
            break
    return result


def _build_fallback_meeting_checklist(category: str) -> list[str]:
    return _CATEGORY_CHECKLIST.get(category, _CATEGORY_CHECKLIST["default"])[:6]


def _build_fallback_negotiation_tips(
    *,
    category: str,
    price_byn: float,
    market_median: float | None,
    best_alternative: dict[str, Any] | None,
    is_negotiable_price: bool,
    risk_context: MarketplaceRiskContext,
) -> list[str]:
    amount, basis = _price_anchor_discount(
        price_byn=price_byn,
        market_median=market_median,
        best_alternative=best_alternative,
        is_negotiable_price=is_negotiable_price,
    )
    tips: list[str] = []
    if is_negotiable_price and market_median:
        tips.append(
            f"Начни торг с диапазона на 5-10% ниже рынка: предложи около {int(round(market_median * 0.9))} BYN и смотри на реакцию продавца."
        )
    elif amount:
        tips.append(
            f"Проси скидку около {amount} BYN, опираясь на разницу {basis} и текущее состояние товара."
        )
    if best_alternative and float(best_alternative.get("price_byn") or 0) > 0:
        tips.append(
            f"Сошлись на ближайший аналог за {int(round(float(best_alternative['price_byn'])))} BYN как на более сильный рыночный ориентир."
        )
    if risk_context.score >= 3.0:
        tips.append(
            "Если продавец уводит разговор в кредит, рассрочку или общие обещания, возвращай торг к фактическому состоянию и итоговой цене."
        )
    if category in {"phone", "tablet", "watch"}:
        tips.append("Любой износ батареи, экрана или комплекта сразу переводить в конкретную скидку, а не в абстрактный торг.")
    elif category in {"auto", "auto_parts"}:
        tips.append("Все найденные дефекты сразу считай в стоимости ремонта и проси скидку минимум на эту сумму.")
    else:
        tips.append("Торгуйся от конкретных проверяемых недостатков: состояние, комплект, гарантия, расходники.")
    return _unique_texts(tips, max_items=4)


def _build_summary_fallback(
    *,
    verdict: str,
    title: str,
    price_byn: float,
    market_median: float | None,
    best_alternative: dict[str, Any] | None,
    red_flags: list[str],
    is_negotiable_price: bool,
) -> str:
    if verdict == "worth_it":
        lead = "Вариант выглядит рабочим, если фактическое состояние подтвердится при проверке."
    elif verdict == "overpriced":
        lead = "Покупать без заметного торга невыгодно: цена сейчас выглядит завышенной."
    else:
        lead = "Покупка возможна, но только после проверки ключевых рисков и предметного торга."

    parts = [lead]
    if not is_negotiable_price and price_byn > 0 and market_median:
        diff = int(round(price_byn - market_median))
        if diff > 0:
            parts.append(f"Объявление примерно на {diff} BYN выше медианного ориентира.")
        elif diff < 0:
            parts.append(f"Объявление примерно на {abs(diff)} BYN ниже медианного ориентира.")
    if best_alternative and float(best_alternative.get("price_byn") or 0) > 0:
        parts.append(
            f"Ближайшая альтернатива находится около {int(round(float(best_alternative['price_byn'])))} BYN, поэтому её стоит держать как ориентир перед оплатой."
        )
    if red_flags:
        parts.append(f"Главный риск сейчас: {red_flags[0].rstrip('.')}.")
    return " ".join(parts)


def complete_analysis_sections(
    *,
    result: dict[str, Any],
    title: str,
    parameters: list[dict[str, Any]],
    price_byn: float,
    market_median: float | None,
    best_alternative: dict[str, Any] | None,
    risk_context: MarketplaceRiskContext,
    photo_condition_label: str | None,
    photo_condition_notes: list[str],
    is_negotiable_price: bool,
    red_flags: list[str],
) -> dict[str, Any]:
    completed = dict(result)
    category = detect_category(title, parameters)

    condition = completed.get("condition")
    if not isinstance(condition, dict):
        condition = {}
    label = _clean_text(condition.get("label")) or _clean_text(photo_condition_label)
    notes = [
        _clean_text(note)
        for note in (condition.get("notes") or [])
        if _clean_text(note)
    ]
    notes = _unique_texts(notes + photo_condition_notes, max_items=4)
    if label or notes:
        completed["condition"] = {
            "label": label or "Удовлетворительное",
            "confidence": float(condition.get("confidence") or (0.72 if photo_condition_notes else 0.64)),
            "notes": notes,
        }

    raw_watch_out = completed.get("watch_out") or []
    watch_items: list[dict[str, str]] = []
    for item in raw_watch_out:
        if not isinstance(item, dict):
            continue
        point = _clean_text(item.get("point"))
        why = _clean_text(item.get("why"))
        if point and why:
            watch_items.append({"point": point, "why": why})
    for item in _build_fallback_watch_out(
        category=category,
        photo_condition_notes=photo_condition_notes,
        risk_context=risk_context,
        best_alternative=best_alternative,
    ):
        if len(watch_items) >= 5:
            break
        key = normalize_search_text(f"{item['point']} {item['why']}")
        if any(normalize_search_text(f"{x['point']} {x['why']}") == key for x in watch_items):
            continue
        watch_items.append(item)
    completed["watch_out"] = watch_items[:5]

    checklist = _unique_texts(
        [str(item) for item in (completed.get("meeting_checklist") or [])],
        max_items=6,
    )
    if len(checklist) < 4:
        checklist = _unique_texts(checklist + _build_fallback_meeting_checklist(category), max_items=6)
    completed["meeting_checklist"] = checklist

    tips = _unique_texts(
        [str(item) for item in (completed.get("negotiation_tips") or [])],
        max_items=4,
    )
    if len(tips) < 3:
        tips = _unique_texts(
            tips + _build_fallback_negotiation_tips(
                category=category,
                price_byn=price_byn,
                market_median=market_median,
                best_alternative=best_alternative,
                is_negotiable_price=is_negotiable_price,
                risk_context=risk_context,
            ),
            max_items=4,
        )
    completed["negotiation_tips"] = tips

    summary = _clean_text(completed.get("summary"))
    verdict = _clean_text((completed.get("recommendation") or {}).get("verdict"))
    if len(summary) < 90:
        completed["summary"] = _build_summary_fallback(
            verdict=verdict,
            title=title,
            price_byn=price_byn,
            market_median=market_median,
            best_alternative=best_alternative,
            red_flags=red_flags,
            is_negotiable_price=is_negotiable_price,
        )

    return completed


def _fallback_fair_price(
    *,
    market_median: float | None,
    market_q1: float | None,
    market_q3: float | None,
    best_alternative: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if market_q1 and market_q3:
        reasoning = f"Базовый рыночный диапазон по выборке находится около {int(round(market_q1))} — {int(round(market_q3))} BYN."
        if best_alternative and float(best_alternative.get("price_byn") or 0) > 0:
            reasoning += f" Ближайший сильный аналог стоит около {int(round(float(best_alternative['price_byn'])))} BYN."
        return {"from": int(round(market_q1)), "to": int(round(market_q3)), "reasoning": reasoning}
    if market_median:
        low = int(round(market_median * 0.92))
        high = int(round(market_median * 1.08))
        return {
            "from": low,
            "to": high,
            "reasoning": f"При отсутствии устойчивого квартильного диапазона ориентир взят вокруг медианы рынка {int(round(market_median))} BYN.",
        }
    return None


def _fallback_recommendation(
    *,
    price_byn: float,
    is_negotiable_price: bool,
    market_median: float | None,
    market_q1: float | None,
    market_q3: float | None,
    risk_context: MarketplaceRiskContext,
) -> dict[str, str]:
    if is_negotiable_price:
        verdict = "think_twice"
        if risk_context.score >= 3.0:
            text = "Брать стоит только после проверки состояния и жёсткого торга от рыночного ориентира."
        else:
            text = "Сделка может быть интересной, но итог зависит от цены после торга и фактического состояния."
        return {"verdict": verdict, "text": text}

    if market_q3 and price_byn > market_q3:
        return {
            "verdict": "overpriced",
            "text": "Без заметного торга покупка выглядит завышенной относительно текущего рынка.",
        }
    if market_q1 and price_byn <= market_q1 and risk_context.score < 2.5:
        return {
            "verdict": "worth_it",
            "text": "По цене это выглядит сильным вариантом, если проверка состояния не выявит скрытых проблем.",
        }
    return {
        "verdict": "think_twice",
        "text": "Сделка возможна, но только после проверки ключевых рисков и предметного торга.",
    }


def build_fallback_analysis_result(
    *,
    title: str,
    parameters: list[dict[str, Any]],
    price_byn: float,
    is_negotiable_price: bool,
    market_median: float | None,
    market_q1: float | None,
    market_q3: float | None,
    best_alternative: dict[str, Any] | None,
    similar_listings: list[dict[str, Any]],
    risk_context: MarketplaceRiskContext,
    photo_condition_label: str | None,
    photo_condition_notes: list[str],
    red_flags: list[str],
) -> dict[str, Any]:
    recommendation = _fallback_recommendation(
        price_byn=price_byn,
        is_negotiable_price=is_negotiable_price,
        market_median=market_median,
        market_q1=market_q1,
        market_q3=market_q3,
        risk_context=risk_context,
    )
    category = detect_category(title, parameters)
    summary = _build_summary_fallback(
        verdict=recommendation["verdict"],
        title=title,
        price_byn=price_byn,
        market_median=market_median,
        best_alternative=best_alternative,
        red_flags=red_flags,
        is_negotiable_price=is_negotiable_price,
    )
    watch_out = _build_fallback_watch_out(
        category=category,
        photo_condition_notes=photo_condition_notes,
        risk_context=risk_context,
        best_alternative=best_alternative,
    )
    checklist = _build_fallback_meeting_checklist(category)
    tips = _build_fallback_negotiation_tips(
        category=category,
        price_byn=price_byn,
        market_median=market_median,
        best_alternative=best_alternative,
        is_negotiable_price=is_negotiable_price,
        risk_context=risk_context,
    )
    return {
        "condition": (
            {
                "label": photo_condition_label or "Удовлетворительное",
                "confidence": 0.72 if photo_condition_label else 0.58,
                "notes": photo_condition_notes,
            }
            if photo_condition_label or photo_condition_notes
            else None
        ),
        "fair_price": _fallback_fair_price(
            market_median=market_median,
            market_q1=market_q1,
            market_q3=market_q3,
            best_alternative=best_alternative,
        ),
        "resale_potential": None,
        "watch_out": watch_out,
        "recommendation": recommendation,
        "meeting_checklist": checklist,
        "negotiation_tips": tips,
        "red_flags": red_flags,
        "market_context": build_market_context_fallback(
            price_byn=price_byn,
            is_negotiable_price=is_negotiable_price,
            market_median=market_median,
            similar_listings=similar_listings,
            risk_context=risk_context,
            ai_market_context="",
        ),
        "summary": summary,
    }


def merge_analysis_result_with_fallback(
    *,
    ai_result: dict[str, Any] | None,
    fallback_result: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(fallback_result)
    for key, value in (ai_result or {}).items():
        if value is None:
            continue
        if isinstance(value, str) and not _clean_text(value):
            continue
        if isinstance(value, list) and not value:
            continue
        merged[key] = value
    return merged


def _query_similarity_bonus(query_tokens: set[str], candidate_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & candidate_tokens)
    coverage = overlap / max(len(query_tokens), 1)
    return coverage * 16.0 + overlap * 1.5


def _title_similarity_bonus(target_tokens: set[str], candidate_tokens: set[str]) -> float:
    if not target_tokens:
        return 0.0
    overlap = len(target_tokens & candidate_tokens)
    return overlap / max(len(target_tokens), 1) * 12.0


def _parameter_similarity_bonus(target_params: dict[str, str], candidate_params: dict[str, str]) -> float:
    if not target_params or not candidate_params:
        return 0.0
    score = 0.0
    for key, target_value in target_params.items():
        candidate_value = candidate_params.get(key)
        if not candidate_value:
            continue
        if candidate_value == target_value:
            score += 4.0
        elif target_value in candidate_value or candidate_value in target_value:
            score += 2.0
        else:
            score -= 1.5
    return score


def _parameter_generation_value(params: dict[str, str]) -> str | None:
    for key, value in params.items():
        if key in {"generation", "generation_name"} and value:
            return value
    return None


def _extract_colors(text: str) -> set[str]:
    found = set()
    for color in _COLOR_WORDS:
        if color in text:
            found.add(color)
    return found


def _extract_screen_size(text: str) -> float | None:
    match = _SCREEN_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def _profile_similarity_bonus(target_text: str, candidate_text: str) -> float:
    target_profile = analyze_query_text(target_text)
    candidate_profile = analyze_query_text(candidate_text)
    score = 0.0

    if target_profile.storage_gb and candidate_profile.storage_gb:
        if target_profile.storage_gb == candidate_profile.storage_gb:
            score += 6.0
        else:
            score -= 8.0

    if target_profile.ram_gb and candidate_profile.ram_gb:
        if target_profile.ram_gb == candidate_profile.ram_gb:
            score += 4.0
        else:
            score -= 6.0

    if target_profile.model_tokens:
        overlap = len(set(target_profile.model_tokens) & set(candidate_profile.model_tokens))
        coverage = overlap / max(len(set(target_profile.model_tokens)), 1)
        score += coverage * 8.0

    return score


def _screen_similarity_bonus(target_text: str, candidate_text: str) -> float:
    target_screen = _extract_screen_size(target_text)
    candidate_screen = _extract_screen_size(candidate_text)
    if target_screen is None or candidate_screen is None:
        return 0.0
    diff = abs(target_screen - candidate_screen)
    if diff <= 0.2:
        return 4.0
    if diff <= 1.0:
        return 1.5
    return -3.0


def _color_similarity_bonus(target_text: str, candidate_text: str) -> float:
    target_colors = _extract_colors(target_text)
    candidate_colors = _extract_colors(candidate_text)
    if not target_colors or not candidate_colors:
        return 0.0
    if target_colors & candidate_colors:
        return 2.0
    return -2.0


def _category_identity_bonus(segment: str, target_text: str, candidate_text: str) -> float:
    generic = _CATEGORY_GENERIC_TOKENS.get(segment)
    target_tokens = _token_set(target_text)
    candidate_tokens = _token_set(candidate_text)
    if generic:
        target_tokens = {token for token in target_tokens if token not in generic}
        candidate_tokens = {token for token in candidate_tokens if token not in generic}
    if not target_tokens or not candidate_tokens:
        return 0.0
    overlap = target_tokens & candidate_tokens
    if not overlap:
        return -3.0
    coverage = len(overlap) / max(len(target_tokens), 1)
    return coverage * 8.0


def _watch_similarity_bonus(target_text: str, candidate_text: str) -> float:
    target_mm = _MM_RE.search(target_text)
    candidate_mm = _MM_RE.search(candidate_text)
    score = 0.0
    if target_mm and candidate_mm:
        if target_mm.group(1) == candidate_mm.group(1):
            score += 3.0
        else:
            score -= 2.0
    return score + _category_identity_bonus("watch", target_text, candidate_text)


def _headphones_similarity_bonus(target_text: str, candidate_text: str) -> float:
    return _category_identity_bonus("headphones", target_text, candidate_text)


def _camera_similarity_bonus(target_text: str, candidate_text: str) -> float:
    score = _category_identity_bonus("camera", target_text, candidate_text)
    target_focal = _FOCAL_RE.findall(target_text)
    candidate_focal = _FOCAL_RE.findall(candidate_text)
    if target_focal and candidate_focal:
        if set(target_focal) & set(candidate_focal):
            score += 3.0
        else:
            score -= 2.5
    target_aperture = _APERTURE_RE.findall(target_text)
    candidate_aperture = _APERTURE_RE.findall(candidate_text)
    if target_aperture and candidate_aperture:
        if set(target_aperture) & set(candidate_aperture):
            score += 1.5
    return score


def _detect_market_segment(ad: dict[str, Any]) -> str:
    text = _normalized_ad_text(ad)
    if any(keyword in text for keyword in ("запчаст", "запчасти", "деталь", "разбор", "авторазбор")):
        return "auto_parts"
    title = str(ad.get("subject", "") or ad.get("title", ""))
    parameters = [{"value": param.get("vl") or param.get("v")} for param in ad.get("ad_parameters", [])]
    return detect_category(title, parameters)


def _extract_part_tokens(text: str) -> set[str]:
    tokens = _token_set(text)
    result = {
        token
        for token in tokens
        if len(token) >= 4
        and token not in _AUTO_BRAND_TOKENS
        and not re.fullmatch(r"[a-z]?\d+[a-z]{0,2}", token)
        and any(stem in token for stem in _AUTO_PART_STEMS)
    }
    return result


def _auto_parts_similarity_bonus(target_text: str, candidate_text: str) -> tuple[bool, float]:
    target_parts = _extract_part_tokens(target_text)
    candidate_parts = _extract_part_tokens(candidate_text)
    if not target_parts:
        return True, 0.0
    if not candidate_parts:
        return True, -4.0
    overlap = target_parts & candidate_parts
    if not overlap:
        return False, -999.0
    coverage = len(overlap) / max(len(target_parts), 1)
    return True, 8.0 + coverage * 8.0


def _auto_similarity_bonus(target_text: str, candidate_text: str) -> float:
    target = _extract_auto_features(target_text)
    candidate = _extract_auto_features(candidate_text)
    score = 0.0

    if target["generations"]:
        if target["generations"] & candidate["generations"]:
            score += 14.0
        elif candidate["generations"]:
            score -= 16.0

    if target["years"] and candidate["years"]:
        year_diff = min(abs(a - b) for a in target["years"] for b in candidate["years"])
        if year_diff <= 1:
            score += 6.0
        elif year_diff <= 3:
            score += 3.0
        elif year_diff >= 8:
            score -= 4.0

    if target["engines"]:
        if target["engines"] & candidate["engines"]:
            score += 6.0
        elif candidate["engines"]:
            score -= 5.0

    if target["fuels"]:
        if target["fuels"] & candidate["fuels"]:
            score += 5.0
        elif candidate["fuels"]:
            score -= 5.0

    if target["transmissions"]:
        if target["transmissions"] & candidate["transmissions"]:
            score += 3.0
        elif candidate["transmissions"]:
            score -= 2.0

    return score


def _has_auto_generation_conflict(target_text: str, candidate_text: str) -> bool:
    target = _extract_auto_features(target_text)
    candidate = _extract_auto_features(candidate_text)
    return bool(
        target["generations"]
        and candidate["generations"]
        and not (target["generations"] & candidate["generations"])
    )


def _price_tiebreaker(target_price: float, candidate_price: float, market_median: float | None) -> float:
    if candidate_price <= 0:
        return -10.0
    reference = target_price if target_price > 0 else (market_median or 0.0)
    if reference <= 0:
        return 0.0
    delta_ratio = abs(candidate_price - reference) / reference
    if delta_ratio <= 0.1:
        return 4.0
    if delta_ratio <= 0.25:
        return 2.0
    if delta_ratio <= 0.6:
        return 0.0
    if delta_ratio <= 1.0:
        return -3.0
    return -7.0


def collect_similar_listings_from_cohorts(
    *,
    cohorts: list[tuple[str, Any]],
    query: str,
    target_ad: dict[str, Any],
    target_ad_id: int,
    target_price: float,
    market_median: float | None,
    max_pool_size: int = 16,
    max_results: int = 5,
) -> list[dict[str, Any]]:
    target_title = str(target_ad.get("subject", "") or target_ad.get("title", ""))
    target_text = _normalized_ad_text(target_ad)
    target_tokens = _token_set(target_title)
    query_tokens = _token_set(query)
    target_params = _ad_param_map(target_ad)
    target_condition = _ad_condition(target_ad)
    target_category = target_ad.get("category")
    target_segment = _detect_market_segment(target_ad)

    ranked: dict[int, dict[str, Any]] = {}

    for cohort_key, dataset in cohorts:
        for ad in getattr(dataset, "ads", []):
            ad_id = int(ad.get("ad_id", 0))
            if ad_id == target_ad_id:
                continue

            candidate_price = normalize_price_byn(ad.get("price_byn")) or 0.0
            if candidate_price <= 0:
                continue
            if target_category is not None and ad.get("category") not in (None, target_category):
                continue

            candidate_title = str(ad.get("subject", "") or ad.get("title", ""))
            candidate_text = _normalized_ad_text(ad)
            candidate_tokens = _token_set(candidate_title)
            if not candidate_tokens:
                continue

            score = _cohort_weight(cohort_key)
            score += _query_similarity_bonus(query_tokens, candidate_tokens)
            score += _title_similarity_bonus(target_tokens, candidate_tokens)
            score += _parameter_similarity_bonus(target_params, _ad_param_map(ad))
            score += _price_tiebreaker(target_price, candidate_price, market_median)
            score += _color_similarity_bonus(target_text, candidate_text)

            candidate_condition = _ad_condition(ad)
            if target_condition and candidate_condition:
                score += 4.0 if target_condition == candidate_condition else -2.0

            if first_image_url(ad):
                score += 1.0
            if str(ad.get("body", "") or ad.get("description", "")).strip():
                score += 1.0

            if target_segment in {"phone", "tablet", "laptop", "tv", "console"}:
                score += _profile_similarity_bonus(target_text, candidate_text)
                score += _screen_similarity_bonus(target_text, candidate_text)
                score += _category_identity_bonus(target_segment, target_text, candidate_text)

            if target_segment == "headphones":
                score += _headphones_similarity_bonus(target_text, candidate_text)

            if target_segment == "watch":
                score += _watch_similarity_bonus(target_text, candidate_text)

            if target_segment == "camera":
                score += _camera_similarity_bonus(target_text, candidate_text)

            if target_segment == "auto_parts":
                compatible, part_score = _auto_parts_similarity_bonus(target_text, candidate_text)
                if not compatible:
                    continue
                score += part_score

            if target_segment == "auto":
                target_generation = _parameter_generation_value(target_params)
                candidate_generation = _parameter_generation_value(_ad_param_map(ad))
                if (
                    target_generation
                    and candidate_generation
                    and target_generation != candidate_generation
                ):
                    continue
                if _has_auto_generation_conflict(target_text, candidate_text):
                    continue
                score += _auto_similarity_bonus(target_text, candidate_text)

            if bool(target_ad.get("company_ad")) == bool(ad.get("company_ad")):
                score += 1.0

            if score < 24.0:
                continue

            risk_context = build_marketplace_risk_context(ad)
            score -= min(risk_context.score, 3.0) * 0.5

            item = {
                "ad_id": ad_id,
                "title": candidate_title[:80],
                "price_byn": candidate_price,
                "image_url": first_image_url(ad),
                "image_urls": [
                    f"https://rms.kufar.by/v1/gallery/{img.get('path')}"
                    for img in (ad.get("images") or [])[:3]
                    if img.get("path")
                ],
                "link": ad.get("ad_link", f"https://www.kufar.by/item/{ad_id}"),
                "deal_score": round(score, 1),
                "condition": candidate_condition,
                "description": str(ad.get("body", "") or ad.get("description", ""))[:300],
                "seller_type": "shop" if ad.get("company_ad") else "private",
                "parameters": _ad_parameters_string(ad),
                "age_days": _parse_age_days(ad.get("list_time")),
                "cohort": cohort_key,
            }

            existing = ranked.get(ad_id)
            if existing is None or item["deal_score"] > existing["deal_score"]:
                ranked[ad_id] = item

    candidates = sorted(
        ranked.values(),
        key=lambda item: (-float(item["deal_score"]), item["price_byn"], item["title"]),
    )
    return candidates[: min(max_pool_size, max_results)]
