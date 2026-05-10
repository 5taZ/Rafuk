"""AI export report logic — HTML sanitization and export endpoints."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import nh3
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from api.dependencies import get_cache, get_telegram_user
from api.services.ai_task_store import _export_delete, _export_get, _export_set

export_router = APIRouter()


class AIExportReportRequest(BaseModel):
    html: str


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
        # Only allow https: URLs — blocks data: and javascript: schemes
        url_schemes={"https"},
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
                "style-src 'unsafe-inline'; "
                "script-src 'none'; "
                "base-uri 'none'; "
                "form-action 'none'; "
                "frame-ancestors 'none'; "
                "connect-src 'none'"
            ),
        },
    )
