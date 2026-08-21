from __future__ import annotations

import hmac
import ipaddress
import os


def require_loopback_host(host: str) -> None:
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if not loopback:
        raise ValueError(
            "SemInt has no authenticated remote dashboard profile; --host must be loopback"
        )


def mutation_authorized(authorization: str | None) -> bool:
    expected = os.environ.get("SEMINTEL_DASHBOARD_AUTH_TOKEN", "")
    supplied = authorization or ""
    return bool(expected) and hmac.compare_digest(supplied, f"Bearer {expected}")
