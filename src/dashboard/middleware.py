"""Security middleware for the FastAPI dashboard.

Adds security headers and request performance logging.
"""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response: Response = await call_next(request)

        # ── Core security headers ─────────────────────────
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )

        # ── Cache control ─────────────────────────────────
        if "/static/" not in str(request.url.path):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "public, max-age=86400"

        # ── HSTS (only behind a reverse proxy with HTTPS) ─
        if request.headers.get("X-Forwarded-Proto") == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log slow requests (> 1 second)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.time()
        response: Response = await call_next(request)
        duration = time.time() - start

        if duration > 1.0:
            logger.warning(
                "Slow request: %s %s took %.2fs",
                request.method,
                request.url.path,
                duration,
            )

        return response
