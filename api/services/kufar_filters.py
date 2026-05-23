from __future__ import annotations

import math
from typing import Any

# SEARCH-6: Kufar's `prc=` parameter is expressed in *kopecks* — the same
# minor-currency unit the API uses for `price_byn` on returned ads. So
# "1500 BYN" on the wire is `prc=r:0,150000` (1500 × 100), not
# `prc=r:0,1500`. Sending BYN directly used to make the user-facing
# "цена до 2500" filter ask Kufar for "≤25 BYN" → the listings panel
# returned 0 results on focused queries (e.g. `iPhone 14 Pro` in
# `Мобильные телефоны`). The constant below is the open-ended ceiling
# in kopecks (≈10M BYN), well above any realistic listing.
KUFAR_PRICE_KOPECKS_PER_BYN = 100
KUFAR_OPEN_PRICE_MAX_KOPECKS = 999_999_999

KUFAR_CONDITION_VALUES = {
    "used": "1",
    "new": "2",
}

KUFAR_SELLER_VALUES = {
    "private": "0",
    "shop": "1",
}

_REGIONS_RAW = {
    "Минск": 7,
    "Брестская область": 1,
    "Гомельская область": 2,
    "Гродненская область": 3,
    "Могилевская область": 4,
    "Минская область": 5,
    "Витебская область": 6,
}

KUFAR_AREAS_BY_REGION = {
    7: {
        26: "Заводской",
        27: "Ленинский",
        29: "Московский",
        28: "Октябрьский",
        25: "Партизанский",
        24: "Первомайский",
        23: "Советский",
        30: "Фрунзенский",
        22: "Центральный",
    },
    1: {
        1: "Брест",
        37: "Барановичи",
        38: "Береза",
        48: "Ганцевичи",
        49: "Дрогичин",
        50: "Жабинка",
        51: "Иваново",
        52: "Ивацевичи",
        53: "Каменец",
        2: "Кобрин",
        3: "Лунинец",
        54: "Ляховичи",
        55: "Малорита",
        4: "Пинск",
        56: "Пружаны",
        57: "Столин",
    },
    6: {
        18: "Витебск",
        125: "Бешенковичи",
        108: "Браслав",
        109: "Верхнедвинск",
        110: "Глубокое",
        111: "Городок",
        112: "Докшицы",
        113: "Дубровно",
        114: "Лепель",
        115: "Лиозно",
        116: "Миоры",
        46: "Новополоцк",
        19: "Орша",
        20: "Полоцк",
        47: "Поставы",
        118: "Россоны",
        119: "Сенно",
        120: "Толочин",
        126: "Ушачи",
        121: "Чашники",
        127: "Шарковщина",
        124: "Шумилино",
    },
    2: {
        5: "Гомель",
        128: "Брагин",
        58: "Буда-Кошелево",
        59: "Ветка",
        60: "Добруш",
        61: "Ельск",
        62: "Житковичи",
        6: "Жлобин",
        63: "Калинковичи",
        129: "Корма",
        130: "Лельчицы",
        131: "Лоев",
        7: "Мозырь",
        64: "Наровля",
        132: "Октябрьский",
        65: "Петриков",
        8: "Речица",
        66: "Рогачев",
        39: "Светлогорск",
        67: "Хойники",
        68: "Чечерск",
    },
    3: {
        9: "Гродно",
        133: "Берестовица",
        40: "Волковыск",
        134: "Вороново",
        70: "Дятлово",
        135: "Зельва",
        71: "Ивье",
        136: "Кореличи",
        10: "Лида",
        72: "Мосты",
        73: "Новогрудок",
        74: "Островец",
        75: "Ошмяны",
        76: "Свислочь",
        11: "Слоним",
        41: "Сморгонь",
        78: "Щучин",
    },
    5: {
        142: "Минский",
        91: "Березино",
        15: "Борисов",
        92: "Вилейка",
        93: "Воложин",
        94: "Дзержинск",
        44: "Жодино",
        95: "Клецк",
        96: "Копыль",
        97: "Крупки",
        98: "Логойск",
        99: "Любань",
        122: "Марьина Горка",
        16: "Молодечно",
        100: "Мядель",
        101: "Несвиж",
        17: "Слуцк",
        102: "Смолевичи",
        45: "Солигорск",
        103: "Старые Дороги",
        104: "Столбцы",
        105: "Узда",
        106: "Червень",
    },
    4: {
        13: "Могилев",
        137: "Белыничи",
        12: "Бобруйск",
        79: "Быхов",
        80: "Глуск",
        42: "Горки",
        138: "Дрибин",
        81: "Кировск",
        82: "Климовичи",
        83: "Кличев",
        84: "Костюковичи",
        139: "Краснополье",
        43: "Кричев",
        140: "Круглое",
        85: "Мстиславль",
        14: "Осиповичи",
        86: "Славгород",
        141: "Хотимск",
        87: "Чаусы",
        88: "Чериков",
        89: "Шклов",
    },
}


def normalize_kufar_filter_text(value: str | None) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


KUFAR_REGION_IDS = {
    normalize_kufar_filter_text(name): region_id
    for name, region_id in _REGIONS_RAW.items()
}

_area_ids_by_name: dict[str, set[int]] = {}
for _areas in KUFAR_AREAS_BY_REGION.values():
    for _area_id, _area_name in _areas.items():
        _area_ids_by_name.setdefault(normalize_kufar_filter_text(_area_name), set()).add(_area_id)

KUFAR_UNIQUE_AREA_IDS = {
    name: next(iter(ids))
    for name, ids in _area_ids_by_name.items()
    if len(ids) == 1
}


def kufar_price_range(min_price: float | None, max_price: float | None) -> str | None:
    # SEARCH-6: convert user-supplied BYN to kopecks before placing them
    # in `prc=`. `min_price` and `max_price` arrive as BYN (FastAPI
    # `Query(le=9_999_999_999.99)`); Kufar's `prc=r:lo,hi` expects
    # kopecks. We floor the lower bound and ceil the upper bound so the
    # "round number" the user typed always stays inclusive.
    if min_price is None and max_price is None:
        return None
    lower = (
        max(0, math.floor(min_price * KUFAR_PRICE_KOPECKS_PER_BYN))
        if min_price is not None
        else 0
    )
    upper = (
        max(0, math.ceil(max_price * KUFAR_PRICE_KOPECKS_PER_BYN))
        if max_price is not None
        else KUFAR_OPEN_PRICE_MAX_KOPECKS
    )
    # Stay below the legacy 999_999_999 sentinel so requests don't get
    # truncated by Kufar's int parser if the user types an extreme upper
    # bound (the FastAPI validator allows up to 9_999_999_999.99 BYN
    # which would overflow the kopeck range otherwise).
    upper = min(upper, KUFAR_OPEN_PRICE_MAX_KOPECKS)
    lower = min(lower, KUFAR_OPEN_PRICE_MAX_KOPECKS)
    if upper < lower:
        lower, upper = upper, lower
    return f"r:{lower},{upper}"


def kufar_location_kwargs(region_name: str | None) -> tuple[dict[str, int], bool]:
    normalized = normalize_kufar_filter_text(region_name)
    if not normalized:
        return {}, True
    region_id = KUFAR_REGION_IDS.get(normalized)
    if region_id is not None:
        return {"region": region_id}, True
    area_id = KUFAR_UNIQUE_AREA_IDS.get(normalized)
    if area_id is not None:
        return {"area": area_id}, True
    return {}, False


def build_kufar_search_filters(
    *,
    min_price: float | None = None,
    max_price: float | None = None,
    condition: str | None = None,
    seller_type: str | None = None,
    region_name: str | None = None,
) -> tuple[dict[str, Any], set[str]]:
    kwargs: dict[str, Any] = {}
    unsupported: set[str] = set()
    price_range = kufar_price_range(min_price, max_price)
    if price_range:
        kwargs["price_range"] = price_range
    if condition:
        condition_value = KUFAR_CONDITION_VALUES.get(condition, condition)
        if condition_value:
            kwargs["condition"] = condition_value
    if seller_type:
        if seller_type in KUFAR_SELLER_VALUES:
            kwargs["seller_type"] = seller_type
        else:
            unsupported.add("seller_type")
    location_kwargs, location_supported = kufar_location_kwargs(region_name)
    kwargs.update(location_kwargs)
    if not location_supported:
        unsupported.add("region_name")
    return kwargs, unsupported
