"""Deprecated AI intent profiles kept for request compatibility."""

from __future__ import annotations

BUYER_GOAL_BALANCED = "balanced"
BUYER_GOAL_SAFE = "safe_buy"
BUYER_GOAL_RESALE = "resale"
SELLER_GOAL_BALANCED = "balanced"
SELLER_GOAL_FAST = "sell_fast"
SELLER_GOAL_MAX_PRICE = "maximize_price"

def buyer_intent_text(goal: str | None) -> str:
    return ""


def seller_intent_text(goal: str | None) -> str:
    return ""
