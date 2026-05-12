from __future__ import annotations

import ipaddress

from fastapi import Request

_CLOUDFLARE_IP_RANGES = [
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


def get_client_ip(request: Request) -> str | None:
    """Extract client IP respecting Cloudflare headers.

    Priority:
    1. CF-Connecting-IP (set by Cloudflare edge).
    2. X-Forwarded-For last entry — ONLY when the direct peer is a
       Cloudflare IP. Earlier entries may be client-spoofed.
    3. request.client.host as fallback.
    """
    client = getattr(request, "client", None)
    client_ip = client.host if client else None

    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip and _is_valid_ip(cf_ip):
        return cf_ip

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and client_ip and _is_cloudflare_ip(client_ip):
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            candidate = parts[-1]
            if _is_valid_ip(candidate):
                return candidate

    return client_ip
