from __future__ import annotations

import ipaddress

from fastapi import Request

_CLOUDFLARE_IP_RANGES = [
    # IPv4 (https://www.cloudflare.com/ips-v4)
    ipaddress.ip_network("173.245.48.0/20"),
    ipaddress.ip_network("103.21.244.0/22"),
    ipaddress.ip_network("103.22.200.0/22"),
    ipaddress.ip_network("103.31.4.0/22"),
    ipaddress.ip_network("141.101.64.0/18"),
    ipaddress.ip_network("108.162.192.0/18"),
    ipaddress.ip_network("190.93.240.0/20"),
    ipaddress.ip_network("188.114.96.0/20"),
    ipaddress.ip_network("197.234.240.0/22"),
    ipaddress.ip_network("198.41.128.0/17"),
    ipaddress.ip_network("162.158.0.0/15"),
    ipaddress.ip_network("104.16.0.0/13"),
    ipaddress.ip_network("104.24.0.0/14"),
    ipaddress.ip_network("172.64.0.0/13"),
    ipaddress.ip_network("131.0.72.0/22"),
    # SEC-MEDIUM (issues §1.5): IPv6 (https://www.cloudflare.com/ips-v6).
    # Without these, IPv6 traffic from CF edges fails the trusted-peer
    # check, so CF-Connecting-IP / X-Forwarded-For are ignored and every
    # IPv6 user gets bucketed by the CF edge's IP — rate limits and
    # IDOR-style telemetry break. Refresh from the CF endpoint at the
    # cadence noted in AGENTS.md.
    ipaddress.ip_network("2400:cb00::/32"),
    ipaddress.ip_network("2606:4700::/32"),
    ipaddress.ip_network("2803:f800::/32"),
    ipaddress.ip_network("2405:b500::/32"),
    ipaddress.ip_network("2405:8100::/32"),
    ipaddress.ip_network("2a06:98c0::/29"),
    ipaddress.ip_network("2c0f:f248::/32"),
]

_TRUSTED_PROXY_IP_RANGES = [
    *_CLOUDFLARE_IP_RANGES,
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("172.16.0.0/12"),
]


def _is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _is_cloudflare_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in _CLOUDFLARE_IP_RANGES)
    except ValueError:
        return False


def _is_trusted_proxy_peer(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in _TRUSTED_PROXY_IP_RANGES)
    except ValueError:
        return False


def get_client_ip(request: Request) -> str | None:
    """Extract client IP respecting trusted proxy headers.

    Priority:
    1. CF-Connecting-IP, only when the direct peer is trusted.
    2. X-Forwarded-For first entry, only when the direct peer is trusted.
    3. request.client.host as fallback.

    OPUS-16: prefer the FIRST X-Forwarded-For entry. The standard
    appends as the request flows ``client → proxy1 → proxy2``: XFF
    is ``client, proxy1`` after proxy2 saw it. The first entry is
    the original client; the last entry is the most recent
    intermediate proxy. With our CF-only topology the chain has
    exactly one element so first==last, but adding a second proxy
    in the future (regional CDN, k8s ingress) would silently start
    bucketing every user under the proxy's IP unless we lock the
    first-entry behaviour in now.
    """
    client = getattr(request, "client", None)
    client_ip = client.host if client else None
    trusted_peer = bool(client_ip and _is_trusted_proxy_peer(client_ip))

    cf_ip = request.headers.get("cf-connecting-ip")
    if trusted_peer and cf_ip and _is_valid_ip(cf_ip):
        return cf_ip

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and trusted_peer:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            candidate = parts[0]
            if _is_valid_ip(candidate):
                return candidate

    return client_ip
