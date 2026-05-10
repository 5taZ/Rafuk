"""Smoke + regression tests for the leads export endpoint.

Covers both formats:
  * format=csv (default) — same shape as before, with a new
    ``round`` step that lands cleanly on the spreadsheet side.
  * format=xlsx — openpyxl-rendered workbook with a styled header,
    number formatting on money columns, and a percent-suffixed ROI
    column. We don't validate the styling itself (openpyxl handles
    it deterministically), just that the bytes are a valid xlsx
    workbook with the expected sheet name + header row.
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from api.middleware.telegram_auth import TelegramInitData


def fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=987_654, first_name="ExportUser", raw={})


def _seed_lead(client: TestClient) -> None:
    response = client.post(
        "/api/v1/leads",
        json={
            "query": "iPhone 15 128GB",
            "ad_id": 50101,
            "title": "iPhone 15 чёрный, как новый",
            "link": "https://www.kufar.by/item/50101",
            "price_byn": 1799,
            "target_resale_byn": 1899,
            "status": "new",
            "source": "manual",
        },
    )
    assert response.status_code == 201, response.text


def test_export_csv_round_trip() -> None:
    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        _seed_lead(client)
        response = client.get("/api/v1/leads/export?format=csv")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        body = response.content.decode("utf-8")
        # Header + at least one data row.
        lines = [line for line in body.splitlines() if line.strip()]
        assert len(lines) >= 2
        assert lines[0].startswith("id,query,title,link,price_byn,")
        assert "iPhone 15 128GB" in body
        assert "50101" in body


def test_export_xlsx_returns_valid_workbook() -> None:
    """The new format=xlsx path renders a parseable .xlsx workbook
    with the same column schema as the CSV. We open the bytes back
    with openpyxl and assert the header row + the first data row."""
    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        _seed_lead(client)
        response = client.get("/api/v1/leads/export?format=xlsx")
        assert response.status_code == 200
        assert (
            response.headers["content-type"]
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert "leads_export.xlsx" in response.headers.get(
            "content-disposition", ""
        )

    workbook = load_workbook(io.BytesIO(response.content), read_only=False)
    assert workbook.sheetnames == ["Leads"]
    sheet = workbook["Leads"]
    header = [cell.value for cell in sheet[1]]
    assert header[:5] == ["id", "query", "title", "link", "price_byn"]

    first_row = [cell.value for cell in sheet[2]]
    assert first_row[1] == "iPhone 15 128GB"
    assert first_row[2] == "iPhone 15 чёрный, как новый"
    # price_byn column should be a number (Excel-formatted), not a
    # string — that's the whole reason xlsx is preferable to CSV.
    assert isinstance(first_row[4], (int, float))


def test_export_rejects_unknown_format() -> None:
    """An invalid `format=` query value must be rejected by FastAPI's
    Literal validator with 422 — not silently picked.

    TEST-M2 / BE-M12: was previously ``status_code in (400, 422)``
    because the handler had a redundant ``if fmt not in (...): 400``
    check on top of the Literal validation. Wave 12 removed the
    handler-side branch (it was unreachable), so now the response is
    deterministically 422 from Pydantic / FastAPI.
    """
    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        response = client.get("/api/v1/leads/export?format=pdf")
        assert response.status_code == 422
