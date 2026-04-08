from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.dependencies import get_telegram_user
from api.middleware.telegram_auth import TelegramInitData
from api.schemas import RiskAssessmentResponse, RiskItem
from api.services.risk_detector import assess_listing_risks

router = APIRouter(tags=["risks"])


class RiskAssessmentRequest(BaseModel):
    price_byn: float | None = None
    description: str = ""
    photo_count: int = 0
    market_median: float | None = None


@router.post("/risk-assessment", response_model=RiskAssessmentResponse)
async def assess_risk(
    payload: RiskAssessmentRequest,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
) -> RiskAssessmentResponse:
    """Assess risks for a listing."""
    ad = {
        "price_byn": payload.price_byn,
        "description": payload.description,
        "photo_count": payload.photo_count,
    }
    result = assess_listing_risks(ad, market_median=payload.market_median)
    return RiskAssessmentResponse(
        risks=[RiskItem(**r) for r in result["risks"]],
        overall_risk=result["overall_risk"],
        overall_emoji=result["overall_emoji"],
    )
