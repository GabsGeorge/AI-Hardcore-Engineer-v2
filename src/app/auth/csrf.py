"""Double-submit CSRF: the ``csrf`` cookie is readable by the SPA JS, and every mutation must
echo it back in ``X-CSRF-Token``. ``SameSite=Lax`` on the session cookie is the first barrier;
this is defense in depth."""

from __future__ import annotations

import hmac
import secrets

from fastapi import Request

from app.auth.sessions import CSRF_COOKIE_NAME
from app.core.errors import AppError

TOKEN_BYTES = 32
HEADER_NAME = "X-CSRF-Token"


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def verify(cookie_value: str | None, header_value: str | None) -> bool:
    if not cookie_value or not header_value:
        return False
    # Compare in constant time; both values are plain base64url strings.
    return hmac.compare_digest(cookie_value, header_value)


def request_token_ok(request: Request) -> bool:
    """One request passes the double-submit check: ``X-CSRF-Token`` echoes the ``csrf`` cookie."""
    return verify(request.cookies.get(CSRF_COOKIE_NAME), request.headers.get(HEADER_NAME))


async def require_csrf(request: Request) -> None:
    """Endpoint-level check (the global middleware only covers cookie-authed requests)."""
    if not request_token_ok(request):
        raise AppError(403, "csrf_failed", "Missing or invalid CSRF token.")
