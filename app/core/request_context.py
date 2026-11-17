"""Client details for audit and security logs."""

import ipaddress

from fastapi import Request


def client_ip(request: Request) -> str | None:
    """Best-effort client IP behind Vercel's proxies (first valid forwarded address)."""
    candidates = [
        request.headers.get("x-vercel-forwarded-for"),
        request.headers.get("x-forwarded-for"),
        request.headers.get("x-real-ip"),
        request.client.host if request.client else None,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        value = candidate.split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            continue
    return None


def user_agent(request: Request) -> str | None:
    """The request's User-Agent, truncated to a sane length."""
    agent = request.headers.get("user-agent")
    return agent[:500] if agent else None
