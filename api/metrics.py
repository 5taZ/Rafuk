from __future__ import annotations

import os
import time
from collections import Counter, defaultdict
from secrets import compare_digest
from threading import Lock

from api.config import Settings, is_production_like_deployment

_lock = Lock()
_requests: Counter[tuple[str, str, str]] = Counter()
_duration_sum: defaultdict[tuple[str, str], float] = defaultdict(float)
_duration_count: Counter[tuple[str, str]] = Counter()
_query_dataset_events: Counter[str] = Counter()
_query_dataset_fetch_duration_sum: defaultdict[str, float] = defaultdict(float)
_query_dataset_fetch_duration_count: Counter[str] = Counter()
_PROCESS_PID = os.getpid()
_PROCESS_START_TIME_SECONDS = time.time()


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def observe_http_request(
    *,
    method: str,
    path: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    method = method.upper()
    status = str(status_code)
    key = (method, path, status)
    duration_key = (method, path)
    with _lock:
        _requests[key] += 1
        _duration_sum[duration_key] += max(duration_seconds, 0.0)
        _duration_count[duration_key] += 1


def observe_query_dataset_event(event: str) -> None:
    with _lock:
        _query_dataset_events[event] += 1


def observe_query_dataset_upstream_fetch(
    *,
    status: str,
    duration_seconds: float,
) -> None:
    with _lock:
        _query_dataset_fetch_duration_sum[status] += max(duration_seconds, 0.0)
        _query_dataset_fetch_duration_count[status] += 1


def render_prometheus_metrics() -> str:
    lines = [
        "# HELP kufar_process_info API worker process identity. Metrics are per-process.",
        "# TYPE kufar_process_info gauge",
        f'kufar_process_info{{pid="{_PROCESS_PID}"}} 1',
        "# HELP kufar_process_start_time_seconds API worker process start time.",
        "# TYPE kufar_process_start_time_seconds gauge",
        f"kufar_process_start_time_seconds {_PROCESS_START_TIME_SECONDS:.3f}",
        "# HELP kufar_http_requests_total Total HTTP requests by method, route, and status.",
        "# TYPE kufar_http_requests_total counter",
    ]
    with _lock:
        request_items = sorted(_requests.items())
        duration_sum_items = sorted(_duration_sum.items())
        duration_count_items = sorted(_duration_count.items())
        dataset_event_items = sorted(_query_dataset_events.items())
        dataset_fetch_sum_items = sorted(_query_dataset_fetch_duration_sum.items())
        dataset_fetch_count_items = sorted(_query_dataset_fetch_duration_count.items())

    for (method, path, status), value in request_items:
        lines.append(
            'kufar_http_requests_total{'
            f'method="{_label(method)}",path="{_label(path)}",status="{_label(status)}"'
            f"}} {value}"
        )
    lines.extend([
        "# HELP kufar_http_request_duration_seconds Request duration seconds.",
        "# TYPE kufar_http_request_duration_seconds summary",
    ])
    for (method, path), value in duration_sum_items:
        lines.append(
            'kufar_http_request_duration_seconds_sum{'
            f'method="{_label(method)}",path="{_label(path)}"'
            f"}} {value:.9f}"
        )
    for (method, path), value in duration_count_items:
        lines.append(
            'kufar_http_request_duration_seconds_count{'
            f'method="{_label(method)}",path="{_label(path)}"'
            f"}} {value}"
        )
    lines.extend([
        "# HELP kufar_query_dataset_events_total Dataset cache/singleflight events.",
        "# TYPE kufar_query_dataset_events_total counter",
    ])
    for event, value in dataset_event_items:
        lines.append(f'kufar_query_dataset_events_total{{event="{_label(event)}"}} {value}')
    lines.extend([
        "# HELP kufar_query_dataset_upstream_fetch_duration_seconds "
        "Kufar dataset upstream fetch duration.",
        "# TYPE kufar_query_dataset_upstream_fetch_duration_seconds summary",
    ])
    for status, value in dataset_fetch_sum_items:
        lines.append(
            "kufar_query_dataset_upstream_fetch_duration_seconds_sum{"
            f'status="{_label(status)}"'
            f"}} {value:.9f}"
        )
    for status, value in dataset_fetch_count_items:
        lines.append(
            "kufar_query_dataset_upstream_fetch_duration_seconds_count{"
            f'status="{_label(status)}"'
            f"}} {value}"
        )
    return "\n".join(lines) + "\n"


def is_metrics_request_allowed(settings: Settings, authorization: str | None) -> bool:
    configured_token = settings.metrics_bearer_token
    token = configured_token.get_secret_value() if configured_token is not None else ""
    if not token:
        return not is_production_like_deployment(settings)
    scheme, _, credentials = (authorization or "").partition(" ")
    return scheme.lower() == "bearer" and compare_digest(credentials.strip(), token)


def _reset_metrics_for_tests() -> None:
    with _lock:
        _requests.clear()
        _duration_sum.clear()
        _duration_count.clear()
        _query_dataset_events.clear()
        _query_dataset_fetch_duration_sum.clear()
        _query_dataset_fetch_duration_count.clear()
