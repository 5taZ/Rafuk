"""Compact category profiles injected into AI request context."""

from __future__ import annotations

from dataclasses import dataclass

from api.services.ai_category_data import detect_category


@dataclass(frozen=True)
class AICategoryProfile:
    label: str
    buyer_focus: str
    seller_focus: str


AI_CATEGORY_PROFILES: dict[str, AICategoryProfile] = {
    "plants": AICategoryProfile(
        label="растения",
        buyer_focus=(
            "Проверь листья на пятна, есть ли вредители и паутинка, стебель на мягкие "
            "участки, корневую систему на гниль, размер горшка, грунт, полив и освещение."
        ),
        seller_focus=(
            "Подсвети размер растения, состояние листьев, горшок, грунт, полив, "
            "освещение и необходимость пересадки."
        ),
    ),
    "books": AICategoryProfile(
        label="книги/издания",
        buyer_focus=(
            "Проверь страницы, переплёт, обложку, заломы, пятна, пометки, комплектность "
            "томов и год/тираж для коллекционных изданий."
        ),
        seller_focus=(
            "Подсвети автора, издание, состояние страниц и переплёта, комплектность, "
            "год выпуска и отсутствие пометок."
        ),
    ),
    "clothing": AICategoryProfile(
        label="одежда/обувь",
        buyer_focus=(
            "Проверь размер по бирке и реальные замеры, состав ткани, пятна, катышки, "
            "швы, молнии, пуговицы, подкладку и сезонность."
        ),
        seller_focus=(
            "Подсвети размер, реальные замеры, состав ткани, сезон, бренд, состояние "
            "швов/молний и сколько раз вещь носили."
        ),
    ),
    "furniture": AICategoryProfile(
        label="мебель",
        buyer_focus=(
            "Проверь размеры, каркас, люфт, скрип, обивку, пятна, механизмы, фурнитуру "
            "и стоимость доставки/самовывоза."
        ),
        seller_focus=(
            "Подсвети точные размеры, материал, состояние каркаса и обивки, механизмы, "
            "разборность и условия самовывоза."
        ),
    ),
    "baby": AICategoryProfile(
        label="детские товары",
        buyer_focus=(
            "Проверь возрастную маркировку, безопасность, ремни/крепления, целостность, "
            "пятна, комплектацию и инструкцию."
        ),
        seller_focus=(
            "Подсвети возраст/рост, безопасность, комплект, срок использования, состояние "
            "и что нужно постирать или докупить."
        ),
    ),
    "tools": AICategoryProfile(
        label="инструменты",
        buyer_focus=(
            "Проверь работу под нагрузкой, кабель, аккумуляторы, патрон, расходники, кейс, "
            "гарантию и историю обслуживания."
        ),
        seller_focus=(
            "Подсвети мощность/модель, комплект, батареи, расходники, проверку под нагрузкой "
            "и гарантию."
        ),
    ),
    "animal": AICategoryProfile(
        label="животные/товары для животных",
        buyer_focus=(
            "Проверь возраст, здоровье, прививки, ветпаспорт, документы, условия содержания, "
            "поведение и причину продажи."
        ),
        seller_focus=(
            "Подсвети возраст, здоровье, прививки, документы, характер, питание и условия "
            "передачи."
        ),
    ),
    "real_estate": AICategoryProfile(
        label="недвижимость",
        buyer_focus=(
            "Проверь документы, собственника, коммунальные, состояние коммуникаций, шум, "
            "транспорт, соседей и реальные расходы после сделки."
        ),
        seller_focus=(
            "Подсвети район, метраж, планировку, состояние, коммунальные, транспорт, "
            "инфраструктуру и условия сделки."
        ),
    ),
    "auto": AICategoryProfile(
        label="авто/транспорт",
        buyer_focus=(
            "Проверь VIN, документы, кузов толщиномером, пробег, двигатель, коробку, "
            "ходовую, историю ДТП и техосмотр."
        ),
        seller_focus=(
            "Подсвети год, пробег, двигатель, коробку, обслуживание, кузов, комплект шин "
            "и документы."
        ),
    ),
    "phone": AICategoryProfile(
        label="телефоны/электроника",
        buyer_focus=(
            "Проверь батарею, экран, камеры, звук, микрофоны, разъём, блокировки, "
            "аккаунты, комплект и оригинальность."
        ),
        seller_focus=(
            "Подсвети модель, память, батарею, состояние экрана/корпуса, комплект, "
            "отвязку от аккаунтов и гарантию."
        ),
    ),
}

_EXTRA_PROFILE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "plants": ("монстера", "фикус", "орхидея", "суккулент", "кактус", "рассада"),
    "books": ("роман", "учебник", "комикс", "манга", "энциклопедия"),
    "clothing": ("пальто", "куртка", "платье", "джинсы", "кроссовки", "ботинки"),
    "furniture": ("диван", "кровать", "шкаф", "стул", "комод", "матрас"),
    "baby": ("коляска", "автокресло", "кроватка", "ходунки"),
    "tools": ("шуруповерт", "дрель", "болгарка", "перфоратор", "пила"),
}


def _profile(title: str, parameters: list[dict] | None) -> AICategoryProfile | None:
    category = detect_category(title, parameters)
    if category in AI_CATEGORY_PROFILES:
        return AI_CATEGORY_PROFILES[category]
    lowered = (title or "").lower()
    for fallback_category, keywords in _EXTRA_PROFILE_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return AI_CATEGORY_PROFILES[fallback_category]
    return None


def buyer_category_profile_text(title: str, parameters: list[dict] | None) -> str:
    profile = _profile(title, parameters)
    if profile is None:
        return ""
    return (
        "## КАТЕГОРИЙНАЯ АДАПТАЦИЯ\n"
        f"Тип товара: {profile.label}. "
        f"Фокус анализа: {profile.buyer_focus}"
    )


def seller_category_profile_text(title: str, parameters: list[dict] | None) -> str:
    profile = _profile(title, parameters)
    if profile is None:
        return ""
    return (
        "## КАТЕГОРИЙНАЯ АДАПТАЦИЯ\n"
        f"Тип товара: {profile.label}. "
        f"Фокус объявления: {profile.seller_focus}"
    )
