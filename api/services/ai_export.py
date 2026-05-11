"""AI export report logic — HTML sanitization and export endpoints."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from urllib.parse import urlparse

import nh3
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from api.dependencies import get_cache, get_telegram_user
from api.services.ai_task_store import _export_delete, _export_get, _export_set

logger = logging.getLogger(__name__)
export_router = APIRouter()


class AIExportReportRequest(BaseModel):
    html: str


# SEC-10 / Wave 29: trusted hosts for ``<img src>`` and ``<a href>`` in
# AI-exported HTML reports. The frontend pipeline that builds these
# reports (see ``frontend/js/api_ai_pdf.js``) only ever references
#
#   * Kufar's image CDN (``rms.kufar.by``) for listing thumbnails
#   * Kufar listing URLs (``www.kufar.by/item/...``, sometimes
#     bare ``kufar.by`` redirects)
#
# Anything else — tracking pixels, attacker-controlled domains, or
# CSP-bypass beacons — is dropped by the attribute filter below. Using
# an explicit allow-list (rather than relying on
# ``nh3.clean(url_schemes={"https"})`` alone) means a future
# refactor that introduces a new image source needs a deliberate
# audit-bearing edit to this file, not a silent accept.
_ALLOWED_EXPORT_HOSTS: frozenset[str] = frozenset({
    "kufar.by",
    "www.kufar.by",
    "rms.kufar.by",
    "cre.kufar.by",
    "re.kufar.by",
})


def _is_allowed_export_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host in _ALLOWED_EXPORT_HOSTS


def _export_attribute_filter(tag: str, attr: str, value: str) -> str | None:
    """Drop ``src`` / ``href`` attributes pointing outside the Kufar
    domain set. Returning ``None`` strips the attribute (leaving the
    tag rendered without it) which is preferable to dropping the whole
    tag — text content survives and the broken-image placeholder is
    obvious feedback during debugging."""
    is_url_attr = (tag == "img" and attr == "src") or (tag == "a" and attr == "href")
    if is_url_attr and not _is_allowed_export_url(value):
        logger.info(
            "ai_export: dropped %s.%s pointing to untrusted URL (head=%r)",
            tag,
            attr,
            value[:64],
        )
        return None
    return value


def _sanitize_export_html(html: str) -> str:
    return nh3.clean(
        html,
        tags={
            "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
            "table", "thead", "tbody", "tr", "th", "td",
            "ul", "ol", "li", "strong", "em", "b", "i", "u",
            "br", "hr", "img", "a", "blockquote", "code", "pre",
        },
        attributes={
            "*": {"class"},
            "img": {"src", "alt", "width", "height"},
            "a": {"href", "target"},
            "td": {"colspan", "rowspan"},
            "th": {"colspan", "rowspan"},
        },
        clean_content_tags={"script", "style"},
        # Only allow https: URLs — blocks data: and javascript: schemes.
        # The per-attribute filter below narrows further to a host
        # allow-list (SEC-10).
        url_schemes={"https"},
        attribute_filter=_export_attribute_filter,
    )


@export_router.post("/export-report")
async def create_export_report(
    payload: AIExportReportRequest,
    request: Request,
    _user=Depends(get_telegram_user),
):
    html = (payload.html or "").strip()
    if not html:
        raise HTTPException(status_code=400, detail="Пустой HTML отчёта")
    if len(html) > 300_000:
        raise HTTPException(status_code=400, detail="HTML отчёта слишком большой")

    html = _sanitize_export_html(html)
    if len(html) > 150_000:
        html = html[:150_000]

    cache = get_cache(request)
    token = secrets.token_urlsafe(18)
    await _export_set(cache, token, {
        "html": html,
        "_telegram_user_id": _user.user_id,
        "_created_ts": datetime.now(UTC).timestamp(),
    })
    return {"url": str(request.url_for("get_export_report", token=token))}


@export_router.get(
    "/export-report/{token}",
    name="get_export_report",
    response_class=HTMLResponse,
)
async def get_export_report(
    token: str,
    request: Request,
    _user=Depends(get_telegram_user),
):
    cache = get_cache(request)
    item = await _export_get(cache, token)
    if not item:
        raise HTTPException(status_code=404, detail="Экспорт не найден или истёк")
    if item.get("_telegram_user_id") != _user.user_id:
        raise HTTPException(status_code=404, detail="Экспорт не найден или истёк")
    await _export_delete(cache, token)
    return HTMLResponse(
        item["html"],
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Content-Security-Policy": (
                "default-src 'none'; "
                "img-src https:; "
                "style-src 'none'; "
                "script-src 'none'; "
                "base-uri 'none'; "
                "form-action 'none'; "
                "frame-ancestors 'none'; "
                "connect-src 'none'"
            ),
        },
    )
