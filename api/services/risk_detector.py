from __future__ import annotations

from typing import Any


def assess_listing_risks(ad: dict[str, Any], market_median: float | None = None) -> dict:
    """Analyze a listing for potential risks (scams, too-good-to-be-true deals)."""
    risks: list[dict[str, str]] = []

    price = ad.get("price_byn")
    description = (ad.get("description") or ad.get("subject") or "").lower()
    photo_count = ad.get("photo_count", 0)

    # Check price risks
    if price and market_median and market_median > 0:
        ratio = price / market_median
        if ratio < 0.5:
            risks.append({
                "type": "too_cheap",
                "level": "high",
                "message": "Цена подозрительно низкая (менее 50% от рыночной)",
            })
        elif ratio < 0.7:
            risks.append({
                "type": "too_cheap_moderate",
                "level": "medium",
                "message": "Цена ниже рыночной (50-70% от медианы)",
            })

    # Check suspicious words in description
    suspicious_words = ["предоплата", "предварительная оплата", "перевод на карту", "без встреч"]
    found_words = [word for word in suspicious_words if word in description]
    if found_words:
        risks.append({
            "type": "suspicious_words",
            "level": "medium",
            "message": f"Подозрительные слова в описании: {', '.join(found_words)}",
        })

    # Check photo count
    if photo_count == 0:
        risks.append({
            "type": "no_photos",
            "level": "low",
            "message": "Нет фотографий",
        })

    # Determine overall risk
    if any(r["level"] == "high" for r in risks):
        overall_risk = "high"
        overall_emoji = "🔴"
    elif any(r["level"] == "medium" for r in risks):
        overall_risk = "medium"
        overall_emoji = "🟡"
    else:
        overall_risk = "low"
        overall_emoji = "🟢"

    return {
        "risks": risks,
        "overall_risk": overall_risk,
        "overall_emoji": overall_emoji,
    }
