from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import get_currency_service
from api.schemas import CurrencyRatesResponse
from api.services.currency_service import CurrencyService

router = APIRouter(tags=["currency"])


@router.get("/currency-rates", response_model=CurrencyRatesResponse)
async def get_currency_rates(
    currency_service: CurrencyService = Depends(get_currency_service),
) -> CurrencyRatesResponse:
    payload = await currency_service.get_rates()
    return CurrencyRatesResponse(**payload)
