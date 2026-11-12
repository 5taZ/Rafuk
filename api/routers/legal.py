"""Public operator details for the legal pages.

LEGAL-01: /legal/terms.html and /legal/privacy.html are static and public
(Law 99-З requires unrestricted access to the processing policy), so this
endpoint needs no Telegram auth or channel subscription.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.config import get_settings
from api.limiter import limiter
from api.routers.consent import ai_consent_info
from api.services.consent_policy import CURRENT_POLICY_VERSION

router = APIRouter(prefix="/legal", tags=["legal"])


class OperatorInfoResponse(BaseModel):
    policy_version: str
    operator_name: str
    operator_address: str
    operator_email: str
    inactive_account_retention_days: int
    ai_provider: str


@router.get("/operator", response_model=OperatorInfoResponse)
@limiter.limit("30/minute")
async def get_operator_info(request: Request) -> OperatorInfoResponse:
    settings = get_settings()
    return OperatorInfoResponse(
        policy_version=CURRENT_POLICY_VERSION,
        operator_name=settings.operator_name.strip(),
        operator_address=settings.operator_address.strip(),
        operator_email=settings.operator_email.strip(),
        inactive_account_retention_days=settings.inactive_account_retention_days,
        ai_provider=ai_consent_info(settings).display_label,
    )
