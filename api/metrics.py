from __future__ import annotations

from collections import Counter, defaultdict
from threading import Lock

_lock = Lock()
_requests: Counter[tuple[str, str, str]] = Counter()
_duration_sum: defaultdict[tuple[str, str], float] = defaultdict(float)
_duration_count: Counter[tuple[str, str]] = Counter()


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


def render_prometheus_metrics() -> str:
    lines = [
        "# HELP kufar_http_requests_total Total HTTP requests by method, route, and status.",
        "# TYPE kufar_http_requests_total counter",
    ]
    with _lock:
        request_items = sorted(_requests.items())
        duration_sum_items = sorted(_duration_sum.items())
        duration_count_items = sorted(_duration_count.items())

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
    return "\n".join(lines) + "\n"


def _reset_metrics_for_tests() -> None:
    with _lock:
        _requests.clear()
        _duration_sum.clear()
        _duration_count.clear()
