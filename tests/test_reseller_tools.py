from __future__ import annotations

from api.services.reseller_tools import analyze_query_text


def test_analyze_query_text_extracts_spaced_ram_and_storage() -> None:
    insights = analyze_query_text("macbook air m1 8gb 256gb")
    assert insights.ram_gb == 8
    assert insights.storage_gb == 256
    assert insights.config_summary == "Air 8/256"


def test_analyze_query_text_handles_console_aliases() -> None:
    insights = analyze_query_text("пс5 слим 1 тб")
    assert insights.normalized_query == "ps5 slim 1024"
    assert insights.storage_gb == 1024
    assert insights.config_summary == "Slim 1024GB"
