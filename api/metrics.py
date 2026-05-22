from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter, defaultdict
from secrets import compare_digest
from threading import Lock
from typing import Any

from api.config import Settings, is_production_like_deployment
from api.services.ai_costs import AIUsage, estimate_ai_cost_usd

logger = logging.getLogger(__name__)

_lock = Lock()
_requests: Counter[tuple[str, str, str]] = Counter()
_duration_sum: defaultdict[tuple[str, str], float] = defaultdict(float)
_duration_count: Counter[tuple[str, str]] = Counter()
_query_dataset_events: Counter[str] = Counter()
_query_dataset_fetch_duration_sum: defaultdict[str, float] = defaultdict(float)
_query_dataset_fetch_duration_count: Counter[str] = Counter()
_ai_audit_failures: Counter[str] = Counter()
_ai_feedback: Counter[tuple[str, str, str]] = Counter()
_ai_provider_requests: Counter[tuple[str, str, str]] = Counter()
_ai_provider_tokens: defaultdict[tuple[str, str, str], int] = defaultdict(int)
_ai_provider_estimated_cost_usd: defaultdict[tuple[str, str], float] = defaultdict(float)
_PROCESS_PID = os.getpid()
_PROCESS_START_TIME_SECONDS = time.time()


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _field(values: tuple[str, ...]) -> str:
    return json.dumps(values, separators=(",", ":"), ensure_ascii=False)


def _parse_field(field: str, expected_len: int) -> tuple[str, ...] | None:
    try:
        raw = json.loads(field)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, list) or len(raw) != expected_len:
        return None
    return tuple(str(value) for value in raw)


def _redis_client(cache: Any | None) -> Any | None:
    client = getattr(cache, "_client", None)
    if client is None:
        return None
    needed = ("hgetall", "hincrby", "hincrbyfloat")
    if all(callable(getattr(client, name, None)) for name in needed):
        return client
    return None


async def _redis_hincrby(cache: Any | None, key: str, field: str, amount: int) -> None:
    client = _redis_client(cache)
    if client is None:
        return
    try:
        await client.hincrby(key, field, amount)
    except Exception:
        logger.warning("Redis metrics hincrby failed key=%s", key, exc_info=True)


async def _redis_hincrbyfloat(cache: Any | None, key: str, field: str, amount: float) -> None:
    client = _redis_client(cache)
    if client is None:
        return
    try:
        await client.hincrbyfloat(key, field, amount)
    except Exception:
        logger.warning("Redis metrics hincrbyfloat failed key=%s", key, exc_info=True)


async def _redis_hgetall(cache: Any | None, key: str) -> dict[str, Any] | None:
    client = _redis_client(cache)
    if client is None:
        return None
    try:
        return dict(await client.hgetall(key))
    except Exception:
        logger.warning("Redis metrics hgetall failed key=%s", key, exc_info=True)
        return None


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


async def observe_http_request_with_backend(
    cache: Any | None,
    *,
    method: str,
    path: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    observe_http_request(
        method=method,
        path=path,
        status_code=status_code,
        duration_seconds=duration_seconds,
    )
    method = method.upper()
    status = str(status_code)
    duration = max(duration_seconds, 0.0)
    await _redis_hincrby(
        cache, "metrics:v1:http_requests", _field((method, path, status)), 1
    )
    await _redis_hincrbyfloat(
        cache, "metrics:v1:http_duration_sum", _field((method, path)), duration
    )
    await _redis_hincrby(
        cache, "metrics:v1:http_duration_count", _field((method, path)), 1
    )


def observe_query_dataset_event(event: str) -> None:
    with _lock:
        _query_dataset_events[event] += 1


async def observe_query_dataset_event_with_backend(cache: Any | None, event: str) -> None:
    observe_query_dataset_event(event)
    await _redis_hincrby(cache, "metrics:v1:query_dataset_events", _field((event,)), 1)


def observe_query_dataset_upstream_fetch(
    *,
    status: str,
    duration_seconds: float,
) -> None:
    with _lock:
        _query_dataset_fetch_duration_sum[status] += max(duration_seconds, 0.0)
        _query_dataset_fetch_duration_count[status] += 1


async def observe_query_dataset_upstream_fetch_with_backend(
    cache: Any | None,
    *,
    status: str,
    duration_seconds: float,
) -> None:
    observe_query_dataset_upstream_fetch(status=status, duration_seconds=duration_seconds)
    duration = max(duration_seconds, 0.0)
    await _redis_hincrbyfloat(
        cache, "metrics:v1:query_dataset_fetch_duration_sum", _field((status,)), duration
    )
    await _redis_hincrby(
        cache, "metrics:v1:query_dataset_fetch_duration_count", _field((status,)), 1
    )


def observe_ai_audit_failure(*, endpoint: str) -> None:
    with _lock:
        _ai_audit_failures[endpoint or "unknown"] += 1


def observe_ai_feedback(
    *,
    endpoint: str,
    rating: str,
    reason: str,
) -> None:
    key = (endpoint or "unknown", rating or "unknown", reason or "unknown")
    with _lock:
        _ai_feedback[key] += 1


async def observe_ai_feedback_with_backend(
    cache: Any | None,
    *,
    endpoint: str,
    rating: str,
    reason: str,
) -> None:
    endpoint = endpoint or "unknown"
    rating = rating or "unknown"
    reason = reason or "unknown"
    observe_ai_feedback(endpoint=endpoint, rating=rating, reason=reason)
    await _redis_hincrby(
        cache, "metrics:v1:ai_feedback", _field((endpoint, rating, reason)), 1
    )


def observe_ai_provider_call(
    *,
    endpoint: str,
    model: str,
    status: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    thinking_tokens: int = 0,
    estimated_cost_usd: float = 0.0,
) -> None:
    endpoint = endpoint or "unknown"
    model = model or "unknown"
    status = status or "unknown"
    prompt_tokens = max(0, int(prompt_tokens or 0))
    completion_tokens = max(0, int(completion_tokens or 0))
    thinking_tokens = max(0, int(thinking_tokens or 0))
    total_tokens = max(0, int(total_tokens or 0))
    if not estimated_cost_usd:
        estimated_cost_usd = estimate_ai_cost_usd(
            model,
            AIUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                thinking_tokens=thinking_tokens,
            ),
        )
    with _lock:
        _ai_provider_requests[(endpoint, model, status)] += 1
        if prompt_tokens:
            _ai_provider_tokens[(endpoint, model, "input")] += prompt_tokens
        if completion_tokens:
            _ai_provider_tokens[(endpoint, model, "output")] += completion_tokens
        if thinking_tokens:
            _ai_provider_tokens[(endpoint, model, "thinking")] += thinking_tokens
        if total_tokens:
            _ai_provider_tokens[(endpoint, model, "total")] += total_tokens
        if estimated_cost_usd:
            _ai_provider_estimated_cost_usd[(endpoint, model)] += max(
                estimated_cost_usd, 0.0
            )


def _local_ai_audit_failures() -> list[tuple[str, int]]:
    with _lock:
        return sorted(_ai_audit_failures.items())


def _local_snapshot() -> dict[str, list[tuple[Any, Any]]]:
    with _lock:
        return {
            "requests": sorted(_requests.items()),
            "duration_sum": sorted(_duration_sum.items()),
            "duration_count": sorted(_duration_count.items()),
            "dataset_events": sorted(_query_dataset_events.items()),
            "dataset_fetch_sum": sorted(_query_dataset_fetch_duration_sum.items()),
            "dataset_fetch_count": sorted(_query_dataset_fetch_duration_count.items()),
            "ai_audit_failures": sorted(_ai_audit_failures.items()),
            "ai_feedback": sorted(_ai_feedback.items()),
            "ai_provider_requests": sorted(_ai_provider_requests.items()),
            "ai_provider_tokens": sorted(_ai_provider_tokens.items()),
            "ai_provider_estimated_cost_usd": sorted(
                _ai_provider_estimated_cost_usd.items()
            ),
        }


def _render_prometheus_snapshot(
    snapshot: dict[str, list[tuple[Any, Any]]],
    *,
    backend: str,
) -> str:
    lines = [
        "# HELP kufar_process_info API worker process identity. Metrics are per-process.",
        "# TYPE kufar_process_info gauge",
        f'kufar_process_info{{pid="{_PROCESS_PID}"}} 1',
        "# HELP kufar_process_start_time_seconds API worker process start time.",
        "# TYPE kufar_process_start_time_seconds gauge",
        f"kufar_process_start_time_seconds {_PROCESS_START_TIME_SECONDS:.3f}",
        "# HELP kufar_metrics_backend_info Metrics storage backend currently rendered.",
        "# TYPE kufar_metrics_backend_info gauge",
        f'kufar_metrics_backend_info{{backend="{_label(backend)}"}} 1',
        "# HELP kufar_http_requests_total Total HTTP requests by method, route, and status.",
        "# TYPE kufar_http_requests_total counter",
    ]

    for (method, path, status), value in snapshot["requests"]:
        lines.append(
            'kufar_http_requests_total{'
            f'method="{_label(method)}",path="{_label(path)}",status="{_label(status)}"'
            f"}} {value}"
        )
    lines.extend([
        "# HELP kufar_http_request_duration_seconds Request duration seconds.",
        "# TYPE kufar_http_request_duration_seconds summary",
    ])
    for (method, path), value in snapshot["duration_sum"]:
        lines.append(
            'kufar_http_request_duration_seconds_sum{'
            f'method="{_label(method)}",path="{_label(path)}"'
            f"}} {value:.9f}"
        )
    for (method, path), value in snapshot["duration_count"]:
        lines.append(
            'kufar_http_request_duration_seconds_count{'
            f'method="{_label(method)}",path="{_label(path)}"'
            f"}} {value}"
        )
    lines.extend([
        "# HELP kufar_query_dataset_events_total Dataset cache/singleflight events.",
        "# TYPE kufar_query_dataset_events_total counter",
    ])
    for event, value in snapshot["dataset_events"]:
        lines.append(f'kufar_query_dataset_events_total{{event="{_label(event)}"}} {value}')
    lines.extend([
        "# HELP kufar_query_dataset_upstream_fetch_duration_seconds "
        "Kufar dataset upstream fetch duration.",
        "# TYPE kufar_query_dataset_upstream_fetch_duration_seconds summary",
    ])
    for status, value in snapshot["dataset_fetch_sum"]:
        lines.append(
            "kufar_query_dataset_upstream_fetch_duration_seconds_sum{"
            f'status="{_label(status)}"'
            f"}} {value:.9f}"
        )
    for status, value in snapshot["dataset_fetch_count"]:
        lines.append(
            "kufar_query_dataset_upstream_fetch_duration_seconds_count{"
            f'status="{_label(status)}"'
            f"}} {value}"
        )
    lines.extend([
        "# HELP kufar_ai_audit_failures_total Failed best-effort AI audit-log writes.",
        "# TYPE kufar_ai_audit_failures_total counter",
    ])
    for endpoint, value in snapshot["ai_audit_failures"]:
        lines.append(f'kufar_ai_audit_failures_total{{endpoint="{_label(endpoint)}"}} {value}')
    lines.extend([
        "# HELP kufar_ai_feedback_total User feedback for AI output quality.",
        "# TYPE kufar_ai_feedback_total counter",
    ])
    for (endpoint, rating, reason), value in snapshot["ai_feedback"]:
        lines.append(
            'kufar_ai_feedback_total{'
            f'endpoint="{_label(endpoint)}",rating="{_label(rating)}",'
            f'reason="{_label(reason)}"'
            f"}} {value}"
        )
    lines.extend([
        "# HELP kufar_ai_provider_requests_total "
        "AI provider logical calls by endpoint, model, and status.",
        "# TYPE kufar_ai_provider_requests_total counter",
    ])
    for (endpoint, model, status), value in snapshot["ai_provider_requests"]:
        lines.append(
            'kufar_ai_provider_requests_total{'
            f'endpoint="{_label(endpoint)}",model="{_label(model)}",status="{_label(status)}"'
            f"}} {value}"
        )
    lines.extend([
        "# HELP kufar_ai_provider_tokens_total "
        "AI provider token usage by endpoint, model, and token type.",
        "# TYPE kufar_ai_provider_tokens_total counter",
    ])
    for (endpoint, model, token_type), value in snapshot["ai_provider_tokens"]:
        lines.append(
            'kufar_ai_provider_tokens_total{'
            f'endpoint="{_label(endpoint)}",model="{_label(model)}",type="{_label(token_type)}"'
            f"}} {value}"
        )
    lines.extend([
        "# HELP kufar_ai_provider_estimated_cost_usd_total Estimated AI provider cost in USD.",
        "# TYPE kufar_ai_provider_estimated_cost_usd_total counter",
    ])
    for (endpoint, model), value in snapshot["ai_provider_estimated_cost_usd"]:
        lines.append(
            'kufar_ai_provider_estimated_cost_usd_total{'
            f'endpoint="{_label(endpoint)}",model="{_label(model)}"'
            f"}} {value:.9f}"
        )
    return "\n".join(lines) + "\n"


def render_prometheus_metrics() -> str:
    return _render_prometheus_snapshot(_local_snapshot(), backend="memory")


async def _redis_snapshot(cache: Any | None) -> dict[str, list[tuple[Any, Any]]] | None:
    # PERF-NEW-8: pipelined HGETALL fan-out for metrics (was 6 sequential round-trips).
    if not hasattr(cache, "pipeline_hgetall") or _redis_client(cache) is None:
        return None
    keys = [
        "metrics:v1:http_requests",
        "metrics:v1:http_duration_sum",
        "metrics:v1:http_duration_count",
        "metrics:v1:query_dataset_events",
        "metrics:v1:query_dataset_fetch_duration_sum",
        "metrics:v1:query_dataset_fetch_duration_count",
        "metrics:v1:ai_feedback",
    ]
    try:
        results = await cache.pipeline_hgetall(keys)
    except Exception:
        logger.warning("Redis metrics pipeline_hgetall failed", exc_info=True)
        return None
    hashes = {
        "requests": results[0],
        "duration_sum": results[1],
        "duration_count": results[2],
        "dataset_events": results[3],
        "dataset_fetch_sum": results[4],
        "dataset_fetch_count": results[5],
        "ai_feedback": results[6],
        "ai_audit_failures": {},
        "ai_provider_requests": {},
        "ai_provider_tokens": {},
        "ai_provider_estimated_cost_usd": {},
    }

    snapshot: dict[str, list[tuple[Any, Any]]] = {
        "requests": [],
        "duration_sum": [],
        "duration_count": [],
        "dataset_events": [],
        "dataset_fetch_sum": [],
        "dataset_fetch_count": [],
        "ai_audit_failures": _local_ai_audit_failures(),
        "ai_feedback": [],
        "ai_provider_requests": [],
        "ai_provider_tokens": [],
        "ai_provider_estimated_cost_usd": [],
    }
    for field, value in hashes["requests"].items():
        parsed = _parse_field(field, 3)
        if parsed is not None:
            snapshot["requests"].append((parsed, int(value)))
    for field, value in hashes["duration_sum"].items():
        parsed = _parse_field(field, 2)
        if parsed is not None:
            snapshot["duration_sum"].append((parsed, float(value)))
    for field, value in hashes["duration_count"].items():
        parsed = _parse_field(field, 2)
        if parsed is not None:
            snapshot["duration_count"].append((parsed, int(value)))
    for field, value in hashes["dataset_events"].items():
        parsed = _parse_field(field, 1)
        if parsed is not None:
            snapshot["dataset_events"].append((parsed[0], int(value)))
    for field, value in hashes["dataset_fetch_sum"].items():
        parsed = _parse_field(field, 1)
        if parsed is not None:
            snapshot["dataset_fetch_sum"].append((parsed[0], float(value)))
    for field, value in hashes["dataset_fetch_count"].items():
        parsed = _parse_field(field, 1)
        if parsed is not None:
            snapshot["dataset_fetch_count"].append((parsed[0], int(value)))
    for field, value in hashes["ai_feedback"].items():
        parsed = _parse_field(field, 3)
        if parsed is not None:
            snapshot["ai_feedback"].append((parsed, int(value)))

    with _lock:
        if not snapshot["ai_feedback"]:
            snapshot["ai_feedback"] = sorted(_ai_feedback.items())
        snapshot["ai_provider_requests"] = sorted(_ai_provider_requests.items())
        snapshot["ai_provider_tokens"] = sorted(_ai_provider_tokens.items())
        snapshot["ai_provider_estimated_cost_usd"] = sorted(
            _ai_provider_estimated_cost_usd.items()
        )

    for key in snapshot:
        snapshot[key].sort()
    return snapshot


async def render_prometheus_metrics_with_backend(cache: Any | None = None) -> str:
    snapshot = await _redis_snapshot(cache)
    if snapshot is None:
        return render_prometheus_metrics()
    return _render_prometheus_snapshot(snapshot, backend="redis")


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
        _ai_audit_failures.clear()
        _ai_feedback.clear()
        _ai_provider_requests.clear()
        _ai_provider_tokens.clear()
        _ai_provider_estimated_cost_usd.clear()
